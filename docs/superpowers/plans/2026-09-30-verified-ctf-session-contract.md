# Verified CTF Session Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Turn solve plus an absolute path into a policy-valid, locally verified CTF job while retaining one ChatGPT conversation.

**Architecture:** gpt_cli routes the connector, stages source artifacts, records task state, and recovers the upstream DAG. BotQuangAnh MCP enforces a per-chat staged-root lease. A job is complete only after local verification, never merely because an assistant message stopped.

**Tech Stack:** Python 3.14, asyncio, curl_cffi, pytest, FastMCP.

**Spec:** docs/superpowers/specs/2026-09-30-verified-ctf-session-contract.md

## Global Constraints

- User-visible input remains solve plus an absolute path; no solve hints are appended.
- Connector identity and endpoint come from runtime configuration/discovery, never a source-code constant.
- Temporary challenge data is created only below ~/Downloads.
- Web-search telemetry invalidates a candidate. A 401/403 remains a protocol investigation.
- No fixed retry loop or trajectory hash is introduced.
- Existing generic gpt run behavior remains compatible.

---

### Task 1: Native connector routing and CLI health

**Files:**

- Modify: gpt_cli/core/bridge.py
- Modify: gpt_cli/cli/main.py
- Modify: tests/test_bridge_resolution.py
- Modify: tests/test_cli_and_gateway.py

**Interfaces:**

- resolve_prompt_with_bridge(prompt, bridge_name) returns @<resolved-name> followed by prompt only when the runtime resolved an enabled connector.
- gpt status --json uses the module-level json import.

- [ ] Step 1: Write failing tests.

~~~python
def test_native_configured_connector_mention():
    assert resolve_prompt_with_bridge("solve /tmp/c", "live-link") == "@live-link solve /tmp/c"

def test_status_json_is_serializable(capsys, monkeypatch):
    monkeypatch.setattr("gpt_cli.cli.main.status_daemon", lambda: {"running": False})
    main_with_argv(["gpt", "status", "--json"])
    assert json.loads(capsys.readouterr().out) == {"running": False}
~~~

- [ ] Step 2: Run .venv/bin/pytest tests/test_bridge_resolution.py tests/test_cli_and_gateway.py -q. Expected: prose-wrapper and JSON status failures.
- [ ] Step 3: Implement native routing without a literal connector name and remove the function-local import json that shadows the module.
- [ ] Step 4: Re-run the same command. Expected: PASS.
- [ ] Step 5: Commit only these files with message fix: route native connector mentions dynamically.

### Task 2: Contract, staging, and policy evidence

**Files:**

- Create: gpt_cli/ctf_contract.py
- Modify: gpt_cli/core/types.py
- Modify: gpt_cli/session/registry.py
- Create: tests/test_ctf_contract.py

**Interfaces:**

- parse_solve_request(prompt) -> Path | None
- ChallengeStager(stage_root).stage(source) -> StagedChallenge
- TaskContract.observe(event) and TaskContract.classify(result) -> TaskStatus
- States: server_active, unfinished, policy_invalid, candidate_unverified, completed.

- [ ] Step 1: Write failing tests.

~~~python
def test_stage_copies_only_requested_tree(tmp_path):
    source = tmp_path / "source" / "challenge"
    source.mkdir(parents=True)
    (source / "binary").write_bytes(b"x")
    staged = ChallengeStager(tmp_path / "Downloads").stage(source)
    assert (staged.root / "binary").read_bytes() == b"x"

def test_blocker_is_unfinished():
    contract = TaskContract.for_path(Path("/tmp/challenge"))
    result = TurnResult("not solved", "c", "m", "model")
    assert contract.classify(result) is TaskStatus.UNFINISHED

def test_web_search_invalidates_candidate():
    contract = TaskContract.for_path(Path("/tmp/challenge"))
    contract.observe(TelemetryEvent("web_search", {"queries": ["answer"]}))
    assert contract.policy_valid is False
~~~

- [ ] Step 2: Run .venv/bin/pytest tests/test_ctf_contract.py -q. Expected: import failure.
- [ ] Step 3: Implement immutable contract metadata and Downloads-only copytree staging. Add registry checkpoint metadata without incrementing turn count.
- [ ] Step 4: Run .venv/bin/pytest tests/test_ctf_contract.py tests/test_session_registry.py -q. Expected: PASS.
- [ ] Step 5: Commit with message feat: add verified ctf task contracts.

### Task 3: Authoritative checkpoints and conversation recovery

**Files:**

- Create: gpt_cli/transport/conversation.py
- Modify: gpt_cli/transport/wire.py
- Modify: gpt_cli/cli/main.py
- Modify: tests/test_wire_transport.py
- Modify: tests/test_telemetry.py

**Interfaces:**

- SessionRegistry.checkpoint(local_id, conversation_id, message_id, model)
- ConversationRecovery.fetch(conversation_id) -> ConversationState
- Transport state is completed, incomplete, transport_lost, or server_active; it is not task completion.

- [ ] Step 1: Write failing tests.

~~~python
def test_done_is_not_blocked_by_unrelated_tool_node_ids():
    events = [
        TelemetryEvent("tool_call_started", {"id": "assistant-node"}),
        TelemetryEvent("tool_call_finished", {"id": "tool-result-node"}),
        TelemetryEvent("done", {}),
    ]
    assert terminal_stream_state(events) is StreamState.COMPLETED

def test_checkpoint_survives_before_turn_return(tmp_path):
    registry = SessionRegistry(tmp_path)
    session = registry.create_session()
    registry.checkpoint(session.local_id, "conversation", "server-node", "model")
    assert registry.get_session(session.local_id).conversation_id == "conversation"
~~~

- [ ] Step 2: Run .venv/bin/pytest tests/test_wire_transport.py tests/test_telemetry.py tests/test_session_registry.py -q. Expected: missing reducer/checkpoint failures.
- [ ] Step 3: Treat upstream done and final-node state as terminal. Retain unmatched tool IDs only as telemetry. Checkpoint every conversation/message event. Fetch DAG state through integrity headers and the conversation endpoint on ambiguity.
- [ ] Step 4: Re-run the focused suite. Expected: PASS.
- [ ] Step 5: Commit with message fix: checkpoint and recover wire conversations.

### Task 4: MCP staged-root lease

**Files:**

- Create: /home/light/GitHub/botquanganh_mcp/app/ctf_scope.py
- Modify: /home/light/GitHub/botquanganh_mcp/app/tools/workspace_tools.py
- Modify: /home/light/GitHub/botquanganh_mcp/app/tools/host.py
- Modify: /home/light/GitHub/botquanganh_mcp/app/host/paths.py
- Create: /home/light/GitHub/botquanganh_mcp/tests/test_ctf_scope.py
- Modify: /home/light/GitHub/botquanganh_mcp/tests/test_scope_permissions.py

**Interfaces:**

- install_ctf_scope(chat_id, root) -> ScopeLease
- scope_for_chat(chat_id) -> Path | None
- Every file path and command CWD uses the active chat lease when present.

- [ ] Step 1: Write failing tests.

~~~python
def test_leased_chat_cannot_read_outside_staged_root(tmp_path):
    root, outside = tmp_path / "stage", tmp_path / "source"
    root.mkdir(); outside.mkdir()
    install_ctf_scope("chat123", root)
    with pytest.raises(PermissionError):
        resolve_for_chat("chat123", outside / "answer.txt")

def test_leased_command_cwd_is_bounded(tmp_path):
    root = tmp_path / "stage"; root.mkdir()
    install_ctf_scope("chat123", root)
    with pytest.raises(PermissionError):
        command_cwd_for_chat("chat123", tmp_path)
~~~

- [ ] Step 2: Run pytest tests/test_ctf_scope.py tests/test_scope_permissions.py -q. Expected: import failure.
- [ ] Step 3: Store server-issued staged-root references in existing chat workspace metadata. Bind only references under configured Downloads staging root. Enforce the lease for every file operation and command CWD, on lexical and resolved paths, including symlink escape.
- [ ] Step 4: Run pytest tests/test_ctf_scope.py tests/test_scope_permissions.py tests/test_host_tools.py -q. Expected: PASS.
- [ ] Step 5: Commit in the host repository with message feat: scope ctf chats to staged roots.

### Task 5: Verified job orchestration and truthful output

**Files:**

- Modify: gpt_cli/cli/main.py
- Modify: gpt_cli/harness/worker.py
- Modify: gpt_cli/server/app.py
- Create: tests/test_verified_ctf_job.py
- Modify: tests/test_cli_and_gateway.py
- Modify: tests/test_worker_pool.py

**Interfaces:**

- run_verified_solve(...) -> VerifiedSolveResult
- JSON provides job_status, policy_valid, verification, conversation_id, message_id, and text.
- Generic turns retain legacy output; solve jobs never report ok for a blocker.

- [ ] Step 1: Write failing tests.

~~~python
def test_blocker_reports_unfinished():
    assert run_verified_solve_for_test(fake_turn("not solved")).job_status == "unfinished"

def test_search_reports_policy_invalid():
    outcome = run_verified_solve_for_test(fake_turn("K17{candidate}"), [TelemetryEvent("web_search", {})])
    assert outcome.job_status == "policy_invalid"

def test_local_verifier_reports_completed():
    outcome = run_verified_solve_for_test(fake_turn("K17{accepted}"), verifier=lambda v: v == "K17{accepted}")
    assert outcome.job_status == "completed"
~~~

- [ ] Step 2: Run .venv/bin/pytest tests/test_verified_ctf_job.py tests/test_cli_and_gateway.py tests/test_worker_pool.py -q. Expected: missing controller failures.
- [ ] Step 3: Recognize only absolute-path solve requests, stage the challenge, resolve the connector, install the allowed-root lease, and feed telemetry to contract/checkpoint. Recover the same DAG once after ambiguity. Continue only after an observed DAG or staged-workspace state change; otherwise return unfinished_no_progress. Derive worker finish_reason from job_status.
- [ ] Step 4: Re-run the focused suite. Expected: PASS.
- [ ] Step 5: Commit with message feat: report verified ctf job completion.

### Task 6: End-to-end verification

**Files:**

- Modify: tests/test_e2e_live.py
- Modify: docs/superpowers/specs/2026-09-30-verified-ctf-session-contract.md

- [ ] Step 1: Add an opt-in live test that creates all temporary artifacts below ~/Downloads, requires a local verifier fixture, and asserts that a completed job has neither web-search evidence nor an out-of-scope tool path.
- [ ] Step 2: Run .venv/bin/pytest tests/ -v. Expected: all offline tests PASS.
- [ ] Step 3: Run WEBGPT_LIVE_TEST=1 .venv/bin/pytest tests/test_e2e_live.py -v -s. Expected: connector availability, staged-root use, and only locally verified completion.
- [ ] Step 4: Run pytest tests/test_ctf_scope.py tests/test_scope_permissions.py tests/test_host_tools.py -q in the MCP repository. Expected: PASS.
- [ ] Step 5: Commit verification updates with message test: verify ctf session contracts end to end.

## Plan self-review

- Tasks 1-5 map directly to routing, isolation, policy evidence, checkpoints, DAG recovery, and verified completion in the approved spec.
- Every production change has a preceding failing focused test.
- No task relies on a fixed connector name, solve hint, keyword list, or fixed continuation count.

