# CLI Session Lifecycle and Steer Design Specification

**Date:** 2026-10-01  
**Status:** Approved design — ready for implementation planning

## 1. Goal

Make every prompt independent by default, make continuation explicit and safe, and let a user add instructions to a running session without losing them.  The Wire runtime remains browserless; this design does not change authentication, PoW, model selection, or the ChatGPT Web wire protocol except for the separately observed native steer request.

## 2. Scope and boundaries

### In scope

- Replace implicit active-session continuation for CLI prompts with explicit resume semantics.
- Make `gpt steer` accept a prompt for a live session, dispatch native steer when the upstream turn exposes an `async_task_id`, and queue it otherwise.
- Ensure one local session has at most one owner turn at a time.
- Simplify duplicate and frozen CLI commands.
- Keep native `@connector` bridge routing, with an explicit disable flag.
- Add deterministic unit/integration tests and a separate benchmark script with a machine-readable result.

### Out of scope

- Creating, configuring, discovering, or granting permissions to MCP connectors. ChatGPT Web owns connector resolution.
- Replacing the Wire runtime with browser automation or making the gateway mandatory for CLI operation.
- Reworking the gateway's OpenAI/Anthropic session contracts, except for shared code that must understand a session lease.
- Sending native steer for ordinary turns that have no upstream `async_task_id`.
- Loop detection, history replay, or client-side DAG reconstruction.

## 3. Public CLI contract

### Prompt creation and continuation

| Invocation | Required behavior |
|---|---|
| `gpt "PROMPT"` | Run one fresh local session and one fresh remote conversation. |
| `gpt run [-p] PROMPT` | Same fresh-by-default behavior, with all supported flags. |
| `gpt` | Open interactive chat on a fresh local session. |
| `gpt chat` | Open interactive chat on a fresh local session. |
| `gpt run --resume PROMPT` | Continue the current local active session. |
| `gpt run --resume SESSION_ID PROMPT` | Continue exactly `SESSION_ID`. |
| `gpt chat --resume [SESSION_ID]` | Open an interactive continuation of the active or named local session. |
| `gpt run --conversation UUID PROMPT` / `gpt chat --conversation UUID` | Attach a newly-created local session to the named ChatGPT Web conversation before running or chatting. |

`--resume` with no identifier requires an active local session. `--resume SESSION_ID` requires that local session to exist. Both errors are explicit and use a nonzero exit status; neither may silently create a session. A prompt path without `--resume` must not read or mutate the active session pointer before creating its own session.

`--resume` and `--conversation` are mutually exclusive. `--conversation` is deliberately named differently from `--resume`: one selects an existing local session; the other imports an upstream ChatGPT Web DAG pointer into a new local session.

### Session administration

`gpt session list`, `show`, `log`, `set`, `new`, and `delete` remain. `set` changes the default target for a later bare `--resume`; it never restores implicit continuation. `session new` is the sole metadata-only session creation command.

### Steer

```text
gpt steer "new instruction"
gpt steer --resume SESSION_ID "new instruction"
```

Without a session ID, `steer` targets exactly one currently live local turn. It errors when there are none or more than one. With `--resume SESSION_ID`, it targets that live turn only.

The response status is one of:

- `steered`: native upstream steer request was accepted for a live turn carrying `async_task_id`.
- `queued`: the owner accepted the prompt into the durable per-session FIFO; it will send it after the current turn completes.
- `rejected`: no matching live owner, a stale lease, invalid input, or an upstream native-steer failure that could not be queued.

The CLI must print the session ID and status. `queued` is a successful exit because the instruction has been durably accepted.

### Removed user-facing forms

- Top-level `gpt new` and `gpt newsession`.
- Root shortcut `gpt -n` / `gpt --new`.
- `gpt run --new` and `gpt run --session`.
- Frozen top-level `plugin`, `mcp`, and `web` commands and their unreachable CLI handlers.
- Documented magic bridge values `--bridge off|none|false`.

Existing internal plugin library code is not removed merely because its CLI handler is removed; it remains only where another import or test uses it.

## 4. Bridge/MCP contract

`--bridge NAME` and `--no-bridge` remain on runnable prompt commands and the harness worker. The configured default remains `DEFAULT_BRIDGE_NAME` (currently `br`). A bridge only rewrites a prompt to native ChatGPT mention syntax, for example `@br inspect status`; it does not prove a connector exists or pass a separate MCP field in the Wire payload.

`--no-bridge` is the sole documented disable mechanism. An explicit leading `@name` in a prompt continues to take precedence over the selected default bridge. The gateway continues to avoid adding its default bridge when the API caller registers external tools.

## 5. Live-turn ownership and control plane

### Lease

Before a resumed turn starts, its process atomically claims a lease keyed by local session ID. The lease records at least the owner PID, process-start identity, creation time, Unix-socket address, current phase, and observed upstream identifiers. A second run against that local session fails as busy and directs the user to `gpt steer`; independently-created sessions remain fully concurrent under `WorkerPool` limits.

The owner releases its lease in `finally` after terminal completion, interruption, or error. Lease lookup validates both owner liveness and socket reachability. Invalid or orphaned leases are removed before reporting state, so a dead process cannot make a session permanently busy.

### Socket protocol

Each live owner hosts a private Unix-domain socket inside the runtime directory. A steer process sends a small authenticated-local control request containing a request ID and prompt, then waits for an acknowledgement. The owner, rather than the caller, is the only process that mutates its live transport and current DAG pointer.

The socket is local-user only and is cleaned up with the lease. The registry/file lock remains the authority for discovery and atomic state transitions; the socket provides delivery to the running event loop.

### Native and queued steering

The SSE parser exposes the current turn's `async_task_id` when upstream emits one. While that ID exists, the owner may submit the observed native steer request with the same authenticated wire context. Native success produces `steered`.

When no async task exists, or native steering is not accepted, the owner commits the new instruction to a durable FIFO and acknowledges `queued`. After the original stream reaches a terminal state and has checkpointed its final conversation/message IDs, the owner removes queued prompts one at a time and sends each as the next turn. It checkpoints after every queued turn, preserving server-side DAG continuity without replaying history.

A steer request is never dropped because a normal Wire turn lacks `async_task_id`.

## 6. Data model and recovery

Normal session records retain local ID, conversation ID, last message ID, model, tag, timestamps, and completed turn count. Live operational state is kept separately from the completed record so `session list` and historical logs are not corrupted by a crash mid-stream.

The live record contains the lease and queued prompt entries. Queue entries carry request ID, prompt, accepted time, and state. The owner commits the dequeue only after it has claimed the queue entry, allowing a restart/recovery command or a later explicit resume to inspect unconsumed work rather than silently losing it. A stale owner does not auto-resume an upstream generation; it is cleaned up and reported. The user then explicitly resumes the local session.

## 7. Error handling

- A nonexistent `--resume ID` is an input error, not session creation.
- An active-session lookup with no active ID is an input error.
- An attempted second turn on a live local session is a busy error; it does not branch the remote DAG.
- A broken socket/PID mismatch becomes a stale-lease diagnostic after cleanup.
- A failed native steer falls back to durable queue whenever the owner remains live; the caller receives `queued` rather than a false `steered` result.
- Existing 401/403 protocol diagnostics remain unchanged: they are not treated as quota exhaustion and must not trigger unrelated browser behavior.

## 8. Test and benchmark requirements

### Automated tests

- Parser tests cover the retained command matrix, removed aliases, `--resume` forms, and mutually-exclusive `--resume`/`--conversation`.
- Registry tests cover strict lookup, fresh creation, exclusive lease acquisition, stale-lease cleanup, FIFO queue persistence, and no same-session race.
- Control-plane tests use a fake Unix socket owner to assert acknowledgement, native dispatch selection, native-failure-to-queue fallback, and ambiguous live-session failure.
- Transport/parser fixtures verify `async_task_id` extraction and the exact native steer payload construction without live credentials.
- CLI integration tests verify a queued prompt is emitted only after the initial turn's final checkpoint and receives that checkpoint's DAG parent.
- Existing gateway and harness session-continuity tests remain passing.

### Benchmark

A standalone benchmark script, not a production `gpt` subcommand, writes JSON plus a concise Markdown report. It measures:

1. five independent fresh turns: total wall time, per-turn time-to-first-delta, completion time, and throughput;
2. a resumed DAG turn: observed conversation and parent IDs;
3. queued steering: acceptance latency, FIFO order, and no-lost-instruction invariant;
4. direct Wire process invariant: no browser child process is started by the benchmark.

The deterministic benchmark uses a fake transport for CI. A separately named opt-in live mode may use the existing credential cache and must state that it consumes live account activity; it records results without claiming a synthetic browser comparison.

## 9. Acceptance criteria

- Every ordinary CLI prompt is fresh unless `--resume` is explicit.
- Resume never auto-creates a session and never races a live owner.
- `gpt steer` always returns a truthful accepted status: native `steered` only when actually accepted, otherwise durable `queued` for a live owner.
- Queued prompts run in FIFO order from the correct post-turn DAG pointer.
- No retained public command has a duplicate meaning, and help text matches actual parser behavior.
- Bridge behavior remains native mention routing and explicit tool-bearing gateway requests remain unprefixed.
- Full unit suite and live five-worker regression pass; benchmark artifact documents its environment and results.

## 10. Self-review

The design intentionally does not combine local session resume with upstream conversation import, does not rely on the active pointer for ordinary prompts, and does not claim native steer for unobserved async identifiers. The queue fallback, lease cleanup, and checkpoint-before-dequeue rules cover the failure modes introduced by concurrent processes without introducing a mandatory daemon or browser dependency.
