# Resilient Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover and render the provider-exposed tool/reasoning timeline after an SSE interruption without replaying work.

**Architecture:** The DAG produces durable normalized recovery events, `WireTransport` de-duplicates and emits them, and one presenter renders compact human output while session logs retain structured detail. A BQA reader is restricted to already-bound workspace chat IDs.

**Tech Stack:** Python 3.14, `curl_cffi`, `pytest`, ChatGPT conversation DAG, BQA activity SSE.

**Spec:** `docs/superpowers/specs/2026-10-02-resilient-telemetry-design.md`

## Global Constraints

- Never send automated live prompts or replay a turn after an SSE error.
- Preserve unrelated dirty changes in the current worktree.
- `--json` stdout contains one final JSON document only.
- Recover only backend-exposed summaries; never synthesize hidden reasoning.

---

### Task 1: Durable DAG timeline extraction

**Files:**
- Modify: `gpt_cli/transport/conversation.py`
- Test: `tests/test_wire_transport.py`

**Interfaces:**
- Produce `ConversationState.timeline: tuple[TelemetryEvent, ...]`.
- Consume an expected user message id and only walk that ancestor branch.

- [ ] Write a fixture with user → `api_tool.list_resources` → tool → `api_tool.call_tool` → tool → thoughts → recap → final.
- [ ] Verify it fails because `ConversationState` has no timeline.
- [ ] Add branch traversal ordered from expected user node to current node and normalize only tool start/result, thoughts summary, and recap events with `source="dag"`.
- [ ] Run focused recovery tests.

### Task 2: Emit missing recovered telemetry

**Files:**
- Modify: `gpt_cli/transport/wire.py`
- Test: `tests/test_wire_transport.py`

**Interfaces:**
- Consume `ConversationState.timeline`.
- Emit only events absent from the live stream before returning a recovered completed turn.

- [ ] Write a failing SSE-error recovery test asserting recovered tools appear in order and duplicate live tool ids do not reappear.
- [ ] Add stable event identity/deduplication and emit a `recovery_started` warning plus missing DAG events through `on_event`.
- [ ] Preserve `completed_turn`, no admission cooldown, and no replay.
- [ ] Run focused wire tests.

### Task 3: Unified compact presenter and pure JSON output

**Files:**
- Create: `gpt_cli/telemetry/presenter.py`
- Modify: `gpt_cli/cli/main.py`, `gpt_cli/telemetry/formatter.py`
- Test: `tests/test_cli_and_gateway.py`, `tests/test_telemetry.py`

**Interfaces:**
- `TerminalTelemetryPresenter.emit(event) -> None` writes compact TTY output.
- `summarize_tool_result(tool, output) -> str` returns redacted one-line status.

- [ ] Write failing tests for recovery/tool lines, compact health summary, and JSON mode producing no telemetry on stdout.
- [ ] Move duplicated run/chat tool formatting into the presenter; leave gateway structured events unchanged.
- [ ] Render source-aware recovery, running, finished, and error states; raw data remains log-only.
- [ ] Run presenter and CLI tests.

### Task 4: Structured timeline persistence and readable logs

**Files:**
- Modify: `gpt_cli/session/logger.py`, `gpt_cli/cli/turn_runner.py`
- Test: `tests/test_turn_runner.py`, `tests/test_session_logger.py`

**Interfaces:**
- History stores `tool_timeline` and `recovery` fields.
- `events.jsonl` retains every normalized live, DAG, and BQA event.

- [ ] Write failing tests that a recovered tool result and recovery provenance persist.
- [ ] Accumulate start/result pairs and compact summaries in `SessionTurnRunner`.
- [ ] Make readable session logs show a compact timeline; keep `--events --json` as the machine contract.
- [ ] Run logging and runner tests.

### Task 5: Bound BQA workspace activity observer

**Files:**
- Create: `gpt_cli/telemetry/bqa_activity.py`
- Modify: `gpt_cli/cli/turn_runner.py`, `gpt_cli/config/settings.py`
- Test: `tests/test_bqa_activity.py`, `tests/test_turn_runner.py`

**Interfaces:**
- `BQAActivityReader(chat_id, base_url).events(last_event_id)` yields normalized `bqa_activity` events.
- Reader starts only when `SessionRecord.bqa_chat_id` is populated.

- [ ] Write a local SSE fixture asserting `Last-Event-ID` resumes and an unbound session creates no reader.
- [ ] Parse BQA `workspace_log`, heartbeat, reset, and replay controls; emit only safe normalized operation state.
- [ ] Attach the reader to a running bound turn, cancel it with the turn, and never alter result/DAG state from BQA events.
- [ ] Run reader and runner tests.

### Task 6: Verification and documentation

**Files:**
- Modify: `README.md` only if existing session-log documentation needs the new recovery fields.
- Test: full `tests/` suite excluding permanently skipped live test.

- [ ] Run focused tests for each task.
- [ ] Run the complete unit suite; do not set a live-test environment variable and do not invoke `gpt -p`.
- [ ] Check `git diff --check` only on files changed by this implementation.
- [ ] Document the DAG/BQA source distinction and recovery log fields.
