# CLI Session Lifecycle and Steer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CLI prompts fresh by default, make local-session continuation explicit and race-safe, and implement `gpt steer` with native-or-queued delivery.

**Architecture:** Keep completed session records in `tool_sessions.json`; introduce a separate, lock-protected live-turn registry and a per-owner Unix socket for in-flight control. A shared CLI turn runner owns one Wire transport and serializes the initial prompt plus any durable steer queue. It sends a native `/f/steer_turn` request only after SSE has exposed an `async_task_id`; otherwise it acknowledges and runs the queued prompt after the current upstream turn checkpoints its DAG pointer.

**Tech Stack:** Python 3.14, `asyncio` Unix sockets, `curl_cffi` HTTP/2 Wire transport, `argparse`, `pytest`, existing `fcntl` JSON persistence.

**Spec:** `docs/superpowers/specs/2026-10-01-cli-session-lifecycle-and-steer-design.md`

## Global Constraints

- Do not start or automate a browser; ordinary CLI turns stay Wire-only.
- Treat HTTP 401/403 as a wire/auth invariant failure first; do not label it quota exhaustion or use a browser as a generic retry.
- Keep server-side DAG continuity: only send the new prompt plus the recorded `conversation_id` and `parent_message_id`.
- `DEFAULT_BRIDGE_NAME=br` remains the default native mention route; bridge selection does not become an MCP provisioning API.
- Retain five-worker global concurrency; only serialize turns that target the same resumed local session.
- Use `apply_patch` for source/document changes. Put ad-hoc/manual test artifacts only under a unique `~/Downloads` directory.
- Run the complete offline suite after every task that changes runtime behavior. Run the opt-in live five-worker regression only after all tasks are integrated.

## File Structure

| File | Responsibility |
|---|---|
| `gpt_cli/session/registry.py` | Completed local sessions and strict existing-session lookup. |
| `gpt_cli/session/live_turns.py` | Persisted live lease, durable FIFO entries, stale-owner validation, and atomic transitions. |
| `gpt_cli/session/turn_control.py` | Private Unix-socket owner server and control request protocol. |
| `gpt_cli/cli/turn_runner.py` | One-session turn owner: checkpoint, stream, native steer dispatch, FIFO drain, cleanup. |
| `gpt_cli/cli/main.py` | Public parser/dispatch and presentation callbacks only. |
| `gpt_cli/telemetry/parser.py` | Emit upstream async-task identity event. |
| `gpt_cli/transport/wire.py` | Build and submit the observed native steer request. |
| `gpt_cli/config/defaults.py` | Steer endpoint and private runtime-directory defaults. |
| `tests/test_session_registry.py` | Strict lookup regression coverage. |
| `tests/test_live_turns.py` | Lease, stale-owner, and queue persistence coverage. |
| `tests/test_turn_control.py` | Unix control protocol coverage with a fake owner. |
| `tests/test_turn_runner.py` | FIFO/DAG integration coverage with a fake Wire transport. |
| `tests/test_telemetry.py` / `tests/test_wire_transport.py` | Async-task parsing and steer request fixture coverage. |
| `tests/test_cli_and_gateway.py` | CLI command matrix and unchanged gateway regressions. |
| `benchmarks/session_lifecycle.py` | Deterministic and opt-in live lifecycle benchmark. |
| `README.md` | Accurate command reference and operational caveats. |

---

### Task 1: Strict local-session lookup and runtime defaults

**Files:**
- Modify: `gpt_cli/config/defaults.py:1-53`
- Modify: `gpt_cli/session/registry.py:15-158`
- Modify: `tests/test_session_registry.py`

**Interfaces:**
- Produces `SessionLookupError(LookupError)`.
- Produces `SessionRegistry.require_session(session_id: str | None) -> SessionRecord`.
- Produces `defaults.FCONV_STEER_URL` and `defaults.user_runtime_dir() -> Path`.
- Consumed by CLI parsing, live lease storage, and the turn runner.

- [ ] **Step 1: Write strict-lookup and default-path tests**

```python
def test_require_session_rejects_missing_active_and_unknown_id(tmp_path):
    reg = SessionRegistry(storage_dir=tmp_path)
    with pytest.raises(SessionLookupError, match="No active local session"):
        reg.require_session(None)
    with pytest.raises(SessionLookupError, match="sess_missing"):
        reg.require_session("sess_missing")

def test_require_session_uses_existing_active_or_named_session(tmp_path):
    reg = SessionRegistry(storage_dir=tmp_path)
    session = reg.create_session(local_id="sess_known")
    assert reg.require_session(None).local_id == "sess_known"
    assert reg.require_session("sess_known").local_id == "sess_known"
```

Add a default-path test that monkeypatches `XDG_RUNTIME_DIR`, asserts `user_runtime_dir()` uses `<xdg>/webgpt`, and asserts `FCONV_STEER_URL` ends in `/backend-api/f/steer_turn`.

- [ ] **Step 2: Run the focused test and verify the new API is absent**

Run: `.venv/bin/pytest tests/test_session_registry.py -q`

Expected: FAIL because `SessionLookupError`, `require_session`, `user_runtime_dir`, and `FCONV_STEER_URL` do not exist.

- [ ] **Step 3: Implement the strict lookup API and private runtime path**

Add this public error and method to `registry.py`; retain `get_or_create` for gateway/harness compatibility but do not call it from the new CLI resume path.

```python
class SessionLookupError(LookupError):
    pass

def require_session(self, session_id: str | None) -> SessionRecord:
    if session_id is None:
        session = self.get_active()
        if session is None:
            raise SessionLookupError("No active local session; run a fresh prompt or name a session")
        return session
    session = self.get_session(session_id)
    if session is None:
        raise SessionLookupError(f"Local session does not exist: {session_id}")
    return session
```

Implement `user_runtime_dir()` by using nonempty `XDG_RUNTIME_DIR` first and otherwise `user_data_dir() / "runtime"`; create directories only at the call site that needs them and set mode `0o700`. Define `FCONV_STEER_URL` beside the other ChatGPT endpoints.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_session_registry.py tests/test_settings.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated contract**

```bash
git add gpt_cli/config/defaults.py gpt_cli/session/registry.py tests/test_session_registry.py
git commit -m "feat(session): require explicit existing resume targets"
```

### Task 2: Live-turn lease and durable steer queue

**Files:**
- Create: `gpt_cli/session/live_turns.py`
- Modify: `gpt_cli/config/defaults.py`
- Create: `tests/test_live_turns.py`

**Interfaces:**
- Produces `LiveTurnRecord`, `QueuedSteer`, `SessionBusyError`, and `NoLiveTurnError` dataclasses/errors.
- Produces `LiveTurnRegistry(runtime_dir: Path)` with `claim`, `get_live`, `list_live`, `update_upstream`, `enqueue`, `claim_next_queued`, `complete_queued`, `release`, and `cleanup_stale`.
- Consumed by the socket owner and CLI `steer` dispatch.

- [ ] **Step 1: Write registry persistence tests**

Use a temp runtime directory. Cover exclusive ownership, stale cleanup, and FIFO persistence across new registry instances:

```python
def test_same_session_cannot_be_claimed_twice(tmp_path):
    registry = LiveTurnRegistry(tmp_path)
    owner = TurnOwner.create_for_current_process("sess_one", tmp_path / "one.sock")
    registry.claim(owner)
    with pytest.raises(SessionBusyError):
        registry.claim(TurnOwner.create_for_current_process("sess_one", tmp_path / "two.sock"))

def test_queue_is_fifo_and_survives_reload(tmp_path):
    registry = LiveTurnRegistry(tmp_path)
    owner = TurnOwner.create_for_current_process("sess_one", tmp_path / "one.sock")
    registry.claim(owner)
    registry.enqueue("sess_one", request_id="r1", prompt="first")
    registry.enqueue("sess_one", request_id="r2", prompt="second")
    reloaded = LiveTurnRegistry(tmp_path)
    assert reloaded.claim_next_queued("sess_one").prompt == "first"
    reloaded.complete_queued("sess_one", "r1")
    assert reloaded.claim_next_queued("sess_one").prompt == "second"
```

For stale cleanup, seed a record whose PID/start-ticks validator is false and assert it is removed before a new owner claims the session.

- [ ] **Step 2: Run the new test file**

Run: `.venv/bin/pytest tests/test_live_turns.py -q`

Expected: FAIL because `live_turns.py` does not exist.

- [ ] **Step 3: Implement separate live state with atomic JSON writes**

Use `live_turns.json` and `.live_turns.lock` below the runtime directory, the same `fcntl.flock` plus temporary-file/`os.replace` discipline used by `SessionRegistry`. Do not add live fields to `SessionRecord` or `tool_sessions.json`.

`TurnOwner` must record `session_id`, a random `owner_id`, PID, Linux `/proc/<pid>/stat` start ticks when available, socket path, and creation timestamp. Liveness requires `os.kill(pid, 0)` and matching start ticks when available. On systems without `/proc`, later socket handshake ownership validates PID reuse. Queue entries contain `request_id`, nonempty prompt, acceptance timestamp, and state `queued` or `claimed`.

`claim_next_queued` changes the first queued item to `claimed` atomically. `complete_queued` removes only the matching claimed item. `release` removes the record only when its `owner_id` matches, preventing a stale process from deleting a replacement lease.

- [ ] **Step 4: Run focused persistence and existing session tests**

Run: `.venv/bin/pytest tests/test_live_turns.py tests/test_session_registry.py -q`

Expected: PASS.

- [ ] **Step 5: Commit lease storage**

```bash
git add gpt_cli/config/defaults.py gpt_cli/session/live_turns.py tests/test_live_turns.py
git commit -m "feat(session): add durable live-turn lease and steer queue"
```

### Task 3: Parse async-task identity and submit native steer

**Files:**
- Modify: `gpt_cli/telemetry/parser.py:207-365`
- Modify: `gpt_cli/transport/wire.py:21-227`
- Modify: `gpt_cli/core/types.py`
- Modify: `tests/test_telemetry.py`
- Modify: `tests/test_wire_transport.py`

**Interfaces:**
- Produces telemetry event `async_task` with `async_task_id` and optional `async_task_turn_id`.
- Produces `SteerContext(conversation_id, parent_message_id, model, turn_trace_id, async_task_id, prepare_state)`.
- Produces `WireTransport.build_steer_payload(prompt: str, context: SteerContext) -> dict[str, Any]` and `await WireTransport.send_steer(prompt: str, context: SteerContext) -> bool`.
- Consumed by `SessionTurnRunner` only while it owns an active `WireTransport`.

- [ ] **Step 1: Write SSE and outbound-request tests**

Add an SSE classic-message fixture with `message.metadata.async_task_id="task_1"` and `async_task_turn_id="turn_1"`; assert one `async_task` event carries those values.

Add a Wire unit test that stubs `transport.session.post`, calls `send_steer`, and asserts:

```python
assert request_url == defaults.FCONV_STEER_URL
assert headers["x-openai-target-path"] == "/backend-api/f/steer_turn"
assert headers["x-openai-target-route"] == "/backend-api/f/steer_turn"
assert body["async_task_id"] == "task_1"
assert body["conversation_id"] == "conv_1"
assert body["parent_message_id"] == "msg_1"
assert body["messages"][0]["content"]["parts"] == ["answer with the risks"]
```

Also assert a non-2xx response returns `False`, never raises a fabricated success, and does not call browser lifebuoy code.

- [ ] **Step 2: Run telemetry and transport tests**

Run: `.venv/bin/pytest tests/test_telemetry.py tests/test_wire_transport.py -q`

Expected: FAIL because the async event and steer APIs do not exist.

- [ ] **Step 3: Implement identity extraction and the observed request shape**

In the classic-message branch, read async fields from `metadata` before normal role rendering and emit one deduplicated `async_task` event whenever an ID changes.

Extend `_send_once` so it keeps the prepared stream headers, turn trace ID, and currently observed conversation/parent IDs available to the owner callback; do not make a new generic conversation request to steer. `send_steer` must copy the active prepared headers, set `Accept: application/json`, replace only `x-openai-target-path` and `x-openai-target-route` with `/backend-api/f/steer_turn`, and POST `FCONV_STEER_URL`.

Build the request from the observed web-client fields: a new user message ID, `action: "next"`, model, conversation ID, parent message ID, `client_prepare_state`, `conversation_mode`, timezone fields, `supports_buffering`, `supported_encodings`, `async_task_id`, and `steer_turn_action`. Pass the action as an explicit `SteerContext` value rather than guessing from arbitrary prompts. Return `True` only on a 2xx response. This transport method is invoked only after parser evidence supplied `async_task_id`; a normal turn always takes the queue path.

- [ ] **Step 4: Run focused tests and full offline transport regression**

Run: `.venv/bin/pytest tests/test_telemetry.py tests/test_wire_transport.py tests/test_pow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit protocol support**

```bash
git add gpt_cli/telemetry/parser.py gpt_cli/transport/wire.py gpt_cli/core/types.py tests/test_telemetry.py tests/test_wire_transport.py
git commit -m "feat(wire): expose async tasks and native steer request"
```

### Task 4: Local Unix control protocol

**Files:**
- Create: `gpt_cli/session/turn_control.py`
- Modify: `gpt_cli/session/live_turns.py`
- Create: `tests/test_turn_control.py`

**Interfaces:**
- Produces `TurnControlServer(owner: TurnOwner, registry: LiveTurnRegistry, on_steer: Callable[[str, str], Awaitable[dict[str, str]]])`.
- Produces `await TurnControlServer.start()`, `await close()`, and `request_steer(socket_path, owner_id, request_id, prompt) -> dict[str, str]`.
- Consumes `LiveTurnRegistry` and is consumed by the CLI turn runner and `gpt steer` command.

- [ ] **Step 1: Write socket protocol tests**

Use `tmp_path / "turn.sock"`; start a fake owner callback that records requests. Send newline-delimited JSON through `request_steer` and assert its acknowledgement is returned only after the callback durably accepts the request.

```python
async def fake_steer(request_id: str, prompt: str) -> dict[str, str]:
    accepted.append((request_id, prompt))
    return {"status": "queued", "session_id": "sess_one"}

assert await request_steer(path, owner.owner_id, "req_1", "check tests first") == {
    "status": "queued", "session_id": "sess_one"
}
assert accepted == [("req_1", "check tests first")]
```

Add cases for wrong owner ID, malformed JSON, blank prompt, missing socket, and a stale record whose socket responds with a different owner ID.

- [ ] **Step 2: Run the control test**

Run: `.venv/bin/pytest tests/test_turn_control.py -q`

Expected: FAIL because `turn_control.py` does not exist.

- [ ] **Step 3: Implement minimal private JSON-lines server**

Create the socket only after the lease claim succeeds, set its parent directory to mode `0o700`, and remove an old path only after validating it belongs to a stale lease. The request schema is exactly:

```json
{"type":"steer","owner_id":"<uuid>","request_id":"<uuid>","prompt":"<nonempty text>"}
```

The response schema is exactly:

```json
{"status":"steered|queued|rejected","session_id":"sess_x","message":"<diagnostic>"}
```

Require the owner ID match before calling `on_steer`; return `rejected` for malformed/nonmatching input. Add a `ping` request that returns owner ID so `LiveTurnRegistry.cleanup_stale` can distinguish an old PID from a live replacement owner.

- [ ] **Step 4: Run focused control and live-state tests**

Run: `.venv/bin/pytest tests/test_turn_control.py tests/test_live_turns.py -q`

Expected: PASS.

- [ ] **Step 5: Commit control plane**

```bash
git add gpt_cli/session/turn_control.py gpt_cli/session/live_turns.py tests/test_turn_control.py
git commit -m "feat(session): add local steer control socket"
```

### Task 5: Shared turn runner, checkpoint-before-drain, and queued delivery

**Files:**
- Create: `gpt_cli/cli/turn_runner.py`
- Modify: `gpt_cli/cli/main.py:111-505`
- Modify: `gpt_cli/session/logger.py` only if a queue-origin annotation is needed in existing turn records
- Create: `tests/test_turn_runner.py`

**Interfaces:**
- Produces `SessionTurnRunner(registry, live_registry, log_manager, token_bundle, session, model, effort, bridge, emit_event)`.
- Produces `await SessionTurnRunner.run_initial(prompt: str) -> TurnResult` and `await request_steer(request_id: str, prompt: str) -> dict[str, str]`.
- Consumes Wire `async_task` events and `send_steer`; emits the existing CLI telemetry callbacks.
- Replaces duplicated direct `WireTransport.send_turn` loops in `interactive_chat` and `handle_run`.

- [ ] **Step 1: Write fake-transport integration tests**

Build a fake transport that records every `send_turn(prompt, conversation_id, parent_id)` call, emits `conversation_id` and `message_id`, and pauses the first response on an `asyncio.Event`.

```python
await runner.start("initial")
assert await runner.request_steer("r1", "first queued") == {"status": "queued", "session_id": "sess_one"}
assert await runner.request_steer("r2", "second queued") == {"status": "queued", "session_id": "sess_one"}
release_first_turn.set()
await runner.wait_closed()
assert transport.calls == [
    ("initial", None, None),
    ("first queued", "conv_1", "msg_1"),
    ("second queued", "conv_1", "msg_2"),
]
```

Add a native-ready fake where `send_steer` returns `True`; assert no queued `send_turn` is added and response status is `steered`. Add native-failure case where `send_steer` returns `False`; assert `queued`. Add a second runner claim for the same session and assert `SessionBusyError` before it can send a request.

- [ ] **Step 2: Run runner tests**

Run: `.venv/bin/pytest tests/test_turn_runner.py -q`

Expected: FAIL because `SessionTurnRunner` does not exist.

- [ ] **Step 3: Implement one owner loop**

`run_initial` claims the local-session lease, starts `TurnControlServer`, and creates one `WireTransport` context. Its event wrapper must:

1. persist conversation/message checkpoints immediately using `SessionRegistry.checkpoint`;
2. update the live record with current conversation ID, message ID, model, trace context, and async-task identity;
3. forward the unchanged event to the CLI display/logger callback.

After each completed or terminal turn, call the existing `log_turn` and `update_turn`, then atomically claim one queued entry. Only then call `send_turn` with the newly checkpointed conversation/message IDs. Mark the entry complete only after its turn has a recorded terminal result. On exceptions or Ctrl-C, retain unclaimed queue entries, close the socket, and release the lease in `finally`.

`request_steer` first tries `WireTransport.send_steer` only if live async-task data exists. A `True` result returns `steered`. Every unavailable/false/error native result appends the prompt through `LiveTurnRegistry.enqueue` and returns `queued` while the owner remains alive. It must never report `steered` from an HTTP status it did not receive.

- [ ] **Step 4: Refactor both CLI paths to use the runner without changing display format**

Move the duplicated stateful sending/checkpoint/logging portion of `interactive_chat` and `handle_run` into `SessionTurnRunner`. Keep existing event rendering and JSON response presentation as callbacks in `main.py`; do not route normal CLI turns through the ASGI gateway. Ensure interactive `/new` starts a fresh runner/session only after the old runner is idle.

- [ ] **Step 5: Run runner, CLI, and gateway regressions**

Run: `.venv/bin/pytest tests/test_turn_runner.py tests/test_cli_and_gateway.py tests/test_session_logger.py -q`

Expected: PASS.

- [ ] **Step 6: Commit runner integration**

```bash
git add gpt_cli/cli/turn_runner.py gpt_cli/cli/main.py gpt_cli/session/logger.py tests/test_turn_runner.py
git commit -m "feat(cli): serialize live sessions and drain steer queue"
```

### Task 6: Replace the public CLI contract and remove duplicate commands

**Files:**
- Modify: `gpt_cli/cli/main.py:19-106, 807-918`
- Modify: `gpt_cli/core/bridge.py`
- Modify: `tests/test_cli_and_gateway.py:32-80,535-555`
- Modify: `tests/test_bridge_resolution.py`
- Modify: `README.md:35-100`

**Interfaces:**
- Produces `gpt run [--resume | --resume-session SESSION_ID | --conversation UUID] [--bridge NAME | --no-bridge] PROMPT`.
- Produces `gpt chat [--resume | --resume-session SESSION_ID | --conversation UUID]`.
- Produces `gpt steer [--resume | --resume-session SESSION_ID] PROMPT`.
- Removes `new`, `newsession`, root `-n/--new`, run `--new`, run `--session`, and frozen `plugin|mcp|web` CLI routes.

- [ ] **Step 1: Replace parser expectations with the new command matrix**

Write parser tests for these exact cases:

```python
assert parser.parse_args(["run", "inspect"]).resume is None
assert parser.parse_args(["run", "--resume", "continue"]).resume is True
assert parser.parse_args(["run", "--resume-session", "sess_a", "continue"]).resume_session == "sess_a"
assert parser.parse_args(["steer", "--resume-session", "sess_a", "check tests"]).command == "steer"
with pytest.raises(SystemExit): parser.parse_args(["run", "--new", "x"])
with pytest.raises(SystemExit): parser.parse_args(["new", "x"])
with pytest.raises(SystemExit): parser.parse_args(["run", "--resume", "--conversation", "web_uuid", "x"])
```

Add dispatch tests with fake cache/runner showing an ordinary `run` calls `create_session`, while `--resume-session sess_missing` exits without calling `create_session`. Add bare-root `gpt "prompt"` test showing fresh behavior. Replace the old `test_cli_parser_newsession_command` rather than preserving obsolete aliases.

- [ ] **Step 2: Run CLI tests and observe expected failures**

Run: `.venv/bin/pytest tests/test_cli_and_gateway.py tests/test_bridge_resolution.py -q`

Expected: FAIL because old parser flags and aliases are still present.

- [ ] **Step 3: Implement parser and dispatch semantics**

Use a boolean `--resume` for the active local session and a string `--resume-session SESSION_ID` for a named local session. This avoids the unsound optional-value grammar where argparse cannot tell a one-word prompt from a session ID. Put `--resume`, `--resume-session`, and `--conversation` in one argparse mutually exclusive group. Normalize active resume to `SessionRegistry.require_session(None)` and named resume to `SessionRegistry.require_session(args.resume_session)`.

For ordinary run and bare-root prompt paths, call `create_session(tag=...)` unconditionally. For resume paths, call `require_session`; never call `get_or_create`. Preserve `gpt` and `gpt chat` interactive entry points, but both create a fresh session unless resume is explicit. Rename `--conv` to `--conversation` without a hidden alias.

`gpt steer` locates one validated live turn when neither resume flag is supplied; if zero or multiple live turns exist, return code 2 with IDs in the diagnostic. `--resume` selects the active local session, while `--resume-session` selects the named local session. If the selected session has no live lease, return code 2. It calls `request_steer` and prints JSON when `--json` is selected.

Remove frozen-command interception and the unreachable `handle_plugin`/`handle_web` CLI functions. Keep `gpt_cli.plugins` library imports that are independently tested. Do not leave deleted commands in `known_commands`, parser help, README, or error examples.

Keep `--bridge NAME` and `--no-bridge`. Validate empty/reserved `off`, `none`, and `false` bridge values at CLI normalization with an actionable error directing the user to `--no-bridge`; preserve explicit `@name` prompt precedence in `resolve_prompt_with_bridge`.

- [ ] **Step 4: Update README command reference**

Replace every `--new`, `--session`, `newsession`, `--conv`, and frozen plugin command example. Include short examples of fresh run, named resume, `gpt steer`, queue fallback semantics, `--conversation`, bridge selection, and `gpt session` administration. State that `--bridge` is native mention routing, not connector provisioning.

- [ ] **Step 5: Run CLI, bridge, plugin-library, worker, and gateway tests**

Run: `.venv/bin/pytest tests/test_cli_and_gateway.py tests/test_bridge_resolution.py tests/test_plugin_manager.py tests/test_worker_pool.py -q`

Expected: PASS.

- [ ] **Step 6: Smoke-check published help**

Run: `gpt --help`

Expected: lists only `run`, `chat`, `steer`, `session`, `start`, `stop`, `restart`, and `status`; no `new`, `newsession`, `plugin`, `mcp`, or `web`.

Run: `gpt run --help`

Expected: lists `--resume`, `--conversation`, `--bridge`, and `--no-bridge`; no `--new`, `--session`, or `--conv`.

- [ ] **Step 7: Commit public surface migration**

```bash
git add gpt_cli/cli/main.py gpt_cli/core/bridge.py tests/test_cli_and_gateway.py tests/test_bridge_resolution.py README.md
git commit -m "feat(cli): make prompts fresh and resume explicit"
```

### Task 7: Benchmark and end-to-end verification

**Files:**
- Create: `benchmarks/session_lifecycle.py`
- Create: `tests/test_session_lifecycle_benchmark.py`
- Modify: `README.md`

**Interfaces:**
- Produces `python benchmarks/session_lifecycle.py --output DIR` deterministic JSON and Markdown output.
- Produces opt-in `--live` mode that uses the existing cache only after an explicit warning.
- Consumes `WorkerPool`, `SessionTurnRunner`, and fake transport adapters without adding a `gpt benchmark` production command.

- [ ] **Step 1: Write benchmark output-schema test**

```python
report = build_deterministic_report(output_dir=tmp_path)
assert report["mode"] == "deterministic"
assert report["fresh_parallel"]["turn_count"] == 5
assert report["resume_dag"]["conversation_id"] == "conv_bench"
assert report["queued_steer"]["prompts"] == ["initial", "first", "second"]
assert report["wire_runtime"]["browser_lifebuoy_invocations"] == 0
assert (tmp_path / "session-lifecycle.json").is_file()
assert (tmp_path / "session-lifecycle.md").is_file()
```

- [ ] **Step 2: Run the benchmark test**

Run: `.venv/bin/pytest tests/test_session_lifecycle_benchmark.py -q`

Expected: FAIL because the benchmark module does not exist.

- [ ] **Step 3: Implement deterministic and opt-in live modes**

The deterministic fake transport must yield an explicit first delta timestamp and completion timestamp for five fresh sessions through `WorkerPool(max_workers=5)`. It must separately run one resumed turn and one initial-plus-two-queued sequence, asserting DAG parent progression and FIFO order before writing results.

Write exactly `session-lifecycle.json` and `session-lifecycle.md` to the supplied output directory. Include Python/platform info, worker count, total wall time, per-turn first-delta/completion milliseconds, throughput, conversation/parent IDs, queue order, and `browser_lifebuoy_invocations`. The latter is an instrumented count, not an unreliable process-name scan.

`--live` must require `--confirm-live`, use the normal cache/Wire path, write to the supplied directory, and state in the report that it consumes account activity. It must not manufacture a browser baseline or launch a browser.

- [ ] **Step 4: Run deterministic benchmark and its test**

Run: `.venv/bin/pytest tests/test_session_lifecycle_benchmark.py -q`

Expected: PASS.

Run: `.venv/bin/python benchmarks/session_lifecycle.py --output /home/light/Downloads/test_gpt_cli_benchmark`

Expected: two report files exist in `/home/light/Downloads/test_gpt_cli_benchmark`.

- [ ] **Step 5: Update README benchmark section**

Document the deterministic command, report locations, metrics, and the `--live --confirm-live` safety gate. Do not market deterministic numbers as live-network performance.

- [ ] **Step 6: Run full verification**

Run: `.venv/bin/pytest tests/ -q`

Expected: PASS.

Run: `.venv/bin/pytest tests/test_e2e_live.py -v -s`

Expected: PASS with five concurrent independent Wire workers. If it fails with HTTP 401/403, inspect cookies, workspace account ID, Sentinel/PoW, device ID, and TLS/header consistency before any browser action.

- [ ] **Step 7: Commit benchmark and final documentation**

```bash
git add benchmarks/session_lifecycle.py tests/test_session_lifecycle_benchmark.py README.md
git commit -m "test: benchmark CLI session lifecycle and steer queue"
```

## Plan Self-Review

- **Spec coverage:** Task 1 establishes strict resume. Tasks 2, 4, and 5 cover lease, socket, stale recovery, native-or-queue, and checkpointed FIFO delivery. Task 3 covers upstream async identity and native protocol. Task 6 covers every public CLI/bridge cleanup. Task 7 covers benchmark and full verification.
- **No placeholders:** Tasks name concrete files, commands, interfaces, test cases, success conditions, and commits. The only live-network action is explicitly gated and uses the existing test suite.
- **Type consistency:** `SessionLookupError`, `LiveTurnRegistry`, `TurnOwner`, `TurnControlServer`, `SteerContext`, and `SessionTurnRunner` are introduced before their consumers. The control response uses the same `steered|queued|rejected` statuses specified in the design.
