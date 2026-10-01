# CTF Session Contract

`gpt-cli` provides the durable session contract used by the CTF scheduler.
This document describes only the local CLI/session boundary; the scheduler is
implemented in `/home/light/Workspace/Project/auto_download_ctf_challenge`.

## Creating and resuming a worker session

Create the local record before starting a worker:

```bash
gpt session new --tag "ctf:7:crypto-clockwork" --json
```

The JSON response contains `session_id`.  A scheduler stores it before sending
the first prompt, then resumes exactly that ID:

```bash
gpt run --resume-session sess_example -b br --json -p "solve /absolute/challenge/path"
```

`--resume-session` is strict: an unknown ID is an error and must not create a
replacement session.  Ordinary `gpt -p` or `gpt run -p` starts a fresh session.

## Persisted identities

Each local session record contains these independent pointers:

| Field | Source | Purpose |
|---|---|---|
| `local_id` | GPT CLI | Stable resume target. |
| `conversation_id` / `last_message_id` | ChatGPT SSE | Server-side DAG continuation. |
| `bqa_chat_id` | `host_workspace_bind` result | Resume the same BQA workspace. |
| `bqa_workspace_path` | `host_workspace_bind` result | Diagnostic path only. |

`SessionTurnRunner` parses successful tool results.  A result containing
`chat_id` and `workspace` updates the local session atomically through
`SessionRegistry.bind_workspace`.  The final `gpt --json` output exposes the
same BQA fields for external schedulers.

## Live steering

For a live local session, `gpt steer --resume-session <id> -p <prompt>` either:

- returns `steered` after the observed upstream async task accepts native steer;
- returns `queued` after the owner durably records a follow-up turn; or
- returns `rejected` when no matching live owner exists.

These values report delivery state.  A caller must inspect final lifecycle
status and workspace journal before treating work as completed.

## Debugging a resumed CTF worker

```bash
gpt session show sess_example --json
gpt session log sess_example --events --json
```

If `bqa_chat_id` is absent after a completed first turn, the CTF scheduler must
not guess a workspace for a continuation.  It records `E_WORKSPACE_MAPPING`
and requires the bind/result path to be fixed first.
