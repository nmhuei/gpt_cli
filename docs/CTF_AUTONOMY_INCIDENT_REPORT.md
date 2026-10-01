# CTF autonomy incident report

Last updated: 2026-10-01

This is the operational record for the `CSS_CTF_Semester_2_2026` run.  It
separates protocol defects from normal long-running model work.  A flag-looking
string is never a solved state; it needs local/protocol validation and then
explicit human submission.

## Active run

| Field | Value |
| --- | --- |
| Challenge | `Chrono_II` |
| Local GPT session | `sess_51f01bdf` |
| Conversation | `6abdf956-0468-83ec-ae83-71942742aefa` |
| Bound BQA workspace | `cw-20261001-Chrono_II-548a47f1` |
| State | Turn active; one claimed and one pending `continue` request |
| Current evidence | Connector tools execute successfully; no valid flag verified yet |

## Confirmed defects fixed

### INC-001 — `-b br` only produced text, not a usable MCP attachment

**Observed:** The model saw `@br` in the prompt but could not call `br` tools.

**Root cause:** The conversation payload carried only a textual system hint.
The ChatGPT web protocol requires the resolved connector object in the user
message's `metadata.selected_mcp_sources` field.

**Fix:** `c5fcd01` resolves the native connector metadata through
`PluginManager.get_connector_source()` and passes it through the CLI runner,
Wire transport, and conversation payload.

**Verification:** A live `bind Chrono_II` turn invoked
`host_workspace_bind` successfully.  Unit coverage verifies that the full
connector source is propagated.

### INC-002 — compact `solve <path>` discarded the challenge path

**Observed:** The `br` shortcut became `@br bind Chrono_II; solve`; the model
bound the workspace but correctly asked the user to supply the challenge
input.

**Root cause:** `resolve_prompt_with_bridge()` retained only the basename to
choose the BQA workspace, then dropped the absolute challenge path that the
solver needs.

**Fix:** `a6f70ef` keeps the compact imperative command while sending
`@br bind <workspace>; solve <absolute-path>`.

**Verification:** `tests/test_bridge_resolution.py` passes all 13 tests.

## Investigated observations — not defects

### OBS-001 — a long host command was mistaken for a stalled SSE turn

The turn emitted `tool_call_started` for a bounded time-series collection.
The SSE connection remained established and later emitted further tool events;
there was no transport error, 401/403, timeout, or abandoned process.  The
correct state is **tool-running**, not **model-waiting** and not **dead**.

**Controller rule:** retain the session and DAG while a tool is pending.  Do
not start a second `gpt run` on the same session.  `gpt steer --resume-session
<id> -p continue` appends a durable queued turn, which runs after completion.

### OBS-002 — decryption output matched the flag shape but failed validation

The solver explicitly reported `verify=False` for an offset hypothesis.
Several decoded strings happened to start with `CSSCTF{`, but they were
inconsistent across samples.  These are rejected candidates, not flags.

**Controller rule:** only record a candidate after it passes the challenge's
reproducible solver/known-plaintext checks.  Never submit based only on a
regular-expression match.

### OBS-003 — `ctf status 3 --solver` reported an unknown challenge ID

This was observed once after the solver overview displayed Chrono II as ID 3.
It has not been reproduced or traced through the CTF command parser, so it is
not classified as a product defect yet.  The GPT run is independent of that
read-only status command.

### OBS-004 — delayed MCP execution can exceed the requested host-command SLA

A collection request declared a 130-second workload and a 160-second MCP
timeout.  Its `tool_call_started` event preceded the actual FastMCP child
process by several minutes.  Once the child began, it ran normally and the
same SSE turn emitted subsequent results.

**Root cause:** this is queueing/dispatch latency inside the external BQA
FastMCP service, before the host command starts.  `gpt_cli` retained a live
SSE connection and received normal events; it did not drop the timeout or
lose the turn.

**Disposition:** no `gpt_cli` code change is appropriate.  The future
controller should surface this as `tool-queued` after a configurable
no-progress threshold, keep the session alive, and avoid launching a second
same-session turn.  An actual `tool-timeout` requires a timeout/error event
or a recovered terminal lifecycle state, not elapsed time alone.

### INC-003 — a queued steer can be discarded after an incomplete turn

**Observed:** Two `continue` requests were accepted while `Chrono_II` was
live.  The first became turn 5; after that turn the runner exited with the
second request never executed.  No live record remained, so the in-memory
queue could no longer be recovered through `gpt steer`.

**Root cause:** `SessionTurnRunner.run_initial()` stops draining when a queued
turn is not classified `completed` or `terminal`.  Its `finally` block calls
`LiveTurnRegistry.release()`, which deletes the live record and all remaining
queued requests.  A response may be classed incomplete when an upstream tool
call lacks a matching completion event, even though useful text and a new DAG
parent were recorded.

**Immediate recovery:** resume the saved local session with `continue`.  The
session registry has already checkpointed its new parent message and bound BQA
workspace, so this creates no new conversation.

**Required fix:** persist unconsumed queued instructions outside the
owner-scoped live record before release, and drain them only after lifecycle
recovery says the upstream turn is safe to continue.  Do not blindly send them
on an active server turn.

## Operating invariants

1. A new prompt starts a new conversation by default.  Resuming requires
   `--resume-session <local-session-id>`; `--session-id` is not a valid flag.
2. A live session accepts a short queued instruction through `gpt steer`.
   The queue preserves the same `conversation_id`, parent message DAG, bound
   BQA chat, and connector context.
3. Classify each active run using telemetry before recovery:
   `tool-running`, `server-active`, `completed`, `terminal`,
   `connector-unavailable`, `tool-timeout`, or `transport-lost`.
4. Investigate 401/403 as a wire/auth invariant failure (cookies, account
   header, Sentinel/PoW, device identity, TLS fingerprint, or JWT refresh),
   never as ordinary quota exhaustion.
5. Do not auto-submit flags.  Keep validated candidates and their evidence;
   submission remains an explicit user action.

## Remaining autonomy gap

The current runner serializes initial and queued turns correctly, but it does
not yet own a CTF-specific policy loop that evaluates a completed response for
`flag verified`, `needs continue`, `tool timeout`, or `connector failure` and
chooses the next short instruction.  That controller must use the lifecycle
classification already produced by `WireTransport`; it must not infer a
failure merely from elapsed wall-clock time or flag-shaped text.

Before implementing that subsystem, keep this decision table as the required
acceptance contract:

| Evidence after a turn | Controller action |
| --- | --- |
| Validated flag and reproducible proof | Record candidate and request human submission |
| Normal terminal response, no flag | Queue `continue` on the same session |
| `tool-running` or `server-active` | Wait; retain session and pending steer queue |
| `tool-timeout` | Preserve logs, queue a short recovery instruction on the same session |
| `connector-unavailable` | Re-resolve connector metadata and retry only after attachment is confirmed |
| `transport-lost` | Recover the upstream DAG, then resume only from the recovered parent |
| 401/403 | Stop retries; diagnose protocol/auth invariants first |
