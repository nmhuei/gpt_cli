# Wire Lifecycle Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Detect the actual reason an observed GPT turn stopped or appeared to stop, then preserve and recover its same server conversation without solver-specific control.

**Architecture:** Bridge routing uses a configured native connector mention. A small lifecycle reducer consumes SSE evidence and a normalized upstream DAG snapshot. CLI and worker expose that reducer's status separately from model text.

**Tech Stack:** Python 3.14, asyncio, curl_cffi, pytest.

**Spec:** docs/superpowers/specs/2026-09-30-wire-lifecycle-diagnosis.md

## Global Constraints

- User prompt remains solve plus an absolute path.
- No hardcoded connector/tool names, solve content, or continuation loop.
- No sandbox, verifier, web policy, or solution-artifact handling.
- 401/403 diagnostics remain protocol-first.

---

### Task 1: Native connector mention and CLI status regression

**Files:**

- Modify: gpt_cli/core/bridge.py
- Modify: gpt_cli/cli/main.py
- Modify: tests/test_bridge_resolution.py
- Modify: tests/test_cli_and_gateway.py

- [ ] Write failing tests:

~~~python
def test_bridge_uses_native_configured_mention():
    assert resolve_prompt_with_bridge("solve /tmp/c", "live-link") == "@live-link solve /tmp/c"

def test_status_json_serializes_without_shadowing(capsys, monkeypatch):
    monkeypatch.setattr("gpt_cli.cli.main.status_daemon", lambda: {"running": False})
    main_with_argv(["gpt", "status", "--json"])
    assert json.loads(capsys.readouterr().out) == {"running": False}
~~~

- [ ] Run .venv/bin/pytest tests/test_bridge_resolution.py tests/test_cli_and_gateway.py -q. Expected: bridge wrapper and status JSON failures.
- [ ] Implement native configured mention and remove the local json import that shadows the module.
- [ ] Re-run the same tests. Expected: PASS.
- [ ] Commit only this focused change.

### Task 2: Lifecycle state and durable event checkpoint

**Files:**

- Create: gpt_cli/transport/lifecycle.py
- Modify: gpt_cli/core/types.py
- Modify: gpt_cli/session/registry.py
- Modify: tests/test_wire_transport.py
- Modify: tests/test_session_registry.py

**Interfaces:**

- LifecycleStatus: connector_unavailable, server_active, tool_timeout, transport_lost, model_terminal, upstream_error, completed_turn.
- classify_lifecycle(events, dag_state) -> LifecycleStatus.
- SessionRegistry.checkpoint(local_id, conversation_id, message_id, model).

- [ ] Write failing tests:

~~~python
def test_tool_timeout_is_classified_from_structured_result():
    event = TelemetryEvent("tool_call_finished", {"output": "exit:124"})
    assert classify_lifecycle([event], None) is LifecycleStatus.TOOL_TIMEOUT

def test_checkpoint_does_not_increment_completed_turns(tmp_path):
    registry = SessionRegistry(tmp_path)
    session = registry.create_session()
    registry.checkpoint(session.local_id, "conversation", "node", "model")
    saved = registry.get_session(session.local_id)
    assert (saved.conversation_id, saved.last_message_id, saved.turns) == ("conversation", "node", 0)
~~~

- [ ] Run .venv/bin/pytest tests/test_wire_transport.py tests/test_session_registry.py -q. Expected: missing module/API failures.
- [ ] Implement only the reducer and checkpoint API. Tool IDs remain telemetry; they are not treated as a universal correlation key.
- [ ] Re-run the focused tests. Expected: PASS.
- [ ] Commit only this focused change.

### Task 3: Upstream DAG recovery

**Files:**

- Create: gpt_cli/transport/conversation.py
- Modify: gpt_cli/transport/wire.py
- Modify: tests/test_wire_transport.py

**Interfaces:**

- ConversationRecovery.fetch(conversation_id) -> ConversationState.
- ConversationState exposes current node role, recipient, content type, message status, and finish details.
- Recovery uses ConduitHandshake.build_integrity_headers and the actual conversation target path.

- [ ] Write failing tests:

~~~python
def test_tool_current_node_is_server_active():
    state = ConversationState(role="assistant", recipient="api_tool.call_tool", content_type="code")
    assert classify_lifecycle([], state) is LifecycleStatus.SERVER_ACTIVE

def test_final_text_current_node_is_model_terminal():
    state = ConversationState(role="assistant", recipient="all", content_type="text", finish_type="stop")
    assert classify_lifecycle([], state) is LifecycleStatus.MODEL_TERMINAL
~~~

- [ ] Run .venv/bin/pytest tests/test_wire_transport.py -q. Expected: missing ConversationState/recovery failures.
- [ ] Implement normalized DAG fetch and invoke it only after a stream interruption or sustained quiet period. Do not replay the prompt and do not append a continuation turn.
- [ ] Re-run the focused tests. Expected: PASS.
- [ ] Commit only this focused change.

### Task 4: CLI and worker truthful lifecycle output

**Files:**

- Modify: gpt_cli/cli/main.py
- Modify: gpt_cli/harness/worker.py
- Modify: gpt_cli/server/app.py
- Modify: tests/test_cli_and_gateway.py
- Modify: tests/test_worker_pool.py

- [ ] Write failing tests:

~~~python
def test_nonempty_blocker_is_model_terminal_not_ok():
    output = run_turn_for_test(text="I could not solve it", lifecycle=LifecycleStatus.MODEL_TERMINAL)
    assert output["lifecycle_status"] == "model_terminal"
    assert output["status"] == "incomplete"

def test_worker_finish_reason_is_not_hardcoded_stop():
    emitted = worker_result_for_test(LifecycleStatus.TOOL_TIMEOUT)
    assert emitted["finish_reason"] == "tool_timeout"
~~~

- [ ] Run .venv/bin/pytest tests/test_cli_and_gateway.py tests/test_worker_pool.py -q. Expected: hardcoded success and stop failures.
- [ ] Wire event checkpoint callbacks, recovery classification, and explicit lifecycle JSON/NDJSON fields. Preserve generic successful final turns as completed_turn; never label a blocker task-complete.
- [ ] Re-run the focused tests. Expected: PASS.
- [ ] Commit only this focused change.

### Task 5: Full verification and live diagnostic probe

**Files:**

- Modify: tests/test_e2e_live.py
- Modify: docs/superpowers/specs/2026-09-30-wire-lifecycle-diagnosis.md

- [ ] Add an opt-in live probe that records only lifecycle evidence for a local challenge and asserts a known terminal classification; it must not inspect/write challenge solution artifacts.
- [ ] Run .venv/bin/pytest tests/ -v. Expected: all offline tests PASS.
- [ ] Run WEBGPT_LIVE_TEST=1 .venv/bin/pytest tests/test_e2e_live.py -v -s. Expected: report connector availability, server activity, and final lifecycle class.
- [ ] Commit verification updates.

## Plan self-review

- Every implementation task has a preceding failing test.
- Every spec requirement maps to Tasks 1 through 4.
- No task contains a CTF solution policy, a static tool name, or a blind continuation mechanism.

