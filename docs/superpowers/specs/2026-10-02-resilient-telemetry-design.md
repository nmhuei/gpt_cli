# Resilient Telemetry Design

## Goal

Preserve a truthful, readable tool and reasoning-summary timeline when the
ChatGPT SSE connection is interrupted, without replaying a prompt or a tool.

## Sources of truth

The live ChatGPT SSE stream is the low-latency source. The server-side
conversation DAG is the durable recovery source. For an already-bound BR
workspace, BQA's activity stream is an independent host-execution observer;
it is never a source of assistant text or turn completion.

## Recovery contract

After an SSE interruption, `ConversationRecovery.fetch_settled` continues to
poll the expected user-message branch. Once a terminal assistant message is
settled, it returns a normalized ordered timeline for that branch. The
timeline contains only provider-exposed `thinking` summaries, reasoning
recaps, tool starts, and tool results. It never attempts to recover hidden
chain-of-thought.

`WireTransport` de-duplicates recovered events against live event identities,
emits only the missing events, and marks each recovered event with
`source="dag"`. It then returns the recovered final answer as
`completed_turn`; the original SSE error remains an event-log transport
warning, not a completed-turn error or an admission cooldown trigger.

## Rendering and logs

One TTY presenter is used by one-shot and interactive CLI paths. It emits
compact state lines, tool names, safe summaries, and errors; detailed raw
payloads and outputs remain in `events.jsonl`. JSON mode emits exactly one
final JSON object on stdout; telemetry is persisted but never mixed into that
stream.

Every event is durable in `events.jsonl`. A history turn stores paired tool
start/result records and, when applicable, a recovery record (`source`,
`reason`) so agents can use `gpt session log <id> --events --json` without
scraping TTY.

## BQA side channel

Only sessions with a persisted `bqa_chat_id` may subscribe to BQA
`/api/v1/activity/stream?chat_id=...`. Its events are rendered as host
execution progress and logged with `source="bqa"`; they do not update the
ChatGPT DAG pointer or decide whether a turn completed. The client preserves
the BQA `Last-Event-ID` cursor and reconnects. Unbound sessions deliberately
do not consume global BQA audit events because concurrent turns cannot be
correlated safely.

## Constraints

- Never replay a prompt or tool after SSE loss.
- Do not change user prompts or force connector tool selection.
- Never expose hidden reasoning or unredacted BQA command output.
- No automated live prompt tests; fixtures and local mocked transports only.
