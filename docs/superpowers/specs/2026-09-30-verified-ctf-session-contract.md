# Verified CTF Session Contract

**Date:** 2026-09-30  
**Status:** Approved design; implementation pending plan review

## Goal

Make `gpt run -p "solve <absolute-path>"` a verified local-CTF job rather
than a one-message chat request. A run remains one ChatGPT conversation/DAG,
but it is successful only after a local verifier accepts its answer.

## Evidence driving this design

- The current prose bridge wrapper can leave the connector unavailable. The
  native dynamic mention form attaches it.
- A final assistant message can be a valid blocker report, a tool-unavailable
  report, or a solution. The existing CLI marks all non-empty text as `ok`.
- A connector rooted at `/home/light` lets the model read sibling solution
  artifacts. A clean challenge-only staging directory prevents that access.
- SSE message IDs are not universally a tool-call correlation key. Upstream
  DAG state is the authority when stream state is ambiguous.

## Required behavior

### 1. Connector routing

The user-facing prompt remains exactly `solve <absolute-path>`. Before a turn,
the runtime resolves an enabled connector from live/configured connector
metadata and sends its native mention syntax. The resolver never contains a
provider-specific connector name or fixed filesystem path.

If no enabled connector resolves, the run ends as `connector_unavailable`; it
does not ask the model to diagnose tool availability and does not claim task
success.

### 2. Challenge isolation

For a solve request the runtime derives a challenge manifest from the supplied
path and stages only that path's descendants in a fresh directory below
`~/Downloads`. The connector receives a per-run allowed-root lease for that
directory. Path traversal, sibling paths, and unrelated roots are denied by
the connector rather than requested from the model.

No policy identifies directories by names such as `solver` or `writeup`: the
allowed root itself is the boundary. The staging process copies artifacts but
does not modify the source challenge.

### 3. Evidence and answer verification

Each job records a `TaskContract` with:

- original and staged paths;
- challenge-discovered answer format and local verifier, if either exists;
- connector and web-search policy events;
- terminal state and reason.

An answer is `completed` only when it has no policy violation and a local,
challenge-derived verifier accepts it. If the challenge exposes no verifier,
the result is `candidate_unverified`, never `completed`.

Any detected web-search event makes the current candidate `policy_invalid`.
The client stops accepting its output and records the evidence. This is a
runtime policy decision, not a keyword prompt instruction.

### 4. Conversation durability and completion

`conversation_id` and the latest server node are checkpointed as soon as
their SSE events arrive. On a stream drop or prolonged silence, the client
queries the conversation DAG using the normal integrity-header path.

`finish_details.type = "stop"` represents one message's end only. The job
controller distinguishes `message_finished`, `server_active`,
`transport_lost`, and verified `task_completed`. It must not infer task
completion from a non-empty response or from synthetic matching of unrelated
tool message IDs.

When a model gives a non-answer final while the contract remains unmet, the
controller continues in the same `conversation_id` only after recording a
new, observable state transition. Repeated identical non-answer finals become
`unfinished_no_progress`; they are not blindly replayed and no new ChatGPT
session is created.

## Non-goals

- No CTF-specific solve hints, payload templates, or word lists.
- No inspection of pre-existing solution directories.
- No automatic browser launch or permission escalation.
- No claim that the client can force a backend model generation to continue
  after it chooses EOS.

## Components

| Component | Responsibility |
| --- | --- |
| `ConnectorResolver` | Discover the enabled connector and produce native mention routing. |
| `ChallengeStager` | Derive allowed source tree and create a clean Downloads staging tree. |
| `TaskContract` / verifier | Represent task state, policy evidence, and challenge-local acceptance. |
| `ConversationRecovery` | Persist event checkpoints and reconcile SSE state with DAG state. |
| MCP allowed-root lease | Enforce the staged-root boundary for file and command operations. |

## Acceptance tests

1. A connector name supplied through discovery/configuration yields native
   mention routing without embedding that name in source code.
2. A staged CTF run cannot list or read files outside its staged root.
3. A non-empty blocker result is `unfinished`, not `ok` or `completed`.
4. A valid local verifier promotes only the matching answer to `completed`.
5. A web-search telemetry event invalidates the answer regardless of text.
6. Mid-turn interruption preserves the server conversation pointer and the
   next invocation resumes that same conversation.
7. `gpt status --json` emits JSON and never shadows the module binding.

## Risks and decisions

- The upstream API does not expose model logits or an internal reason for EOS;
  the design treats it as message state, not an error code.
- Some challenges have no local verifier. They deliberately remain
  `candidate_unverified`; lowering the standard would reintroduce false
  success.
- The allowed-root lease requires a compatible connector-side change. The CLI
  and connector will be tested as one integration boundary.
