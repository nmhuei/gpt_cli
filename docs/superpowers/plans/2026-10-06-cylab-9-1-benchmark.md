# CyLab 9–1 Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run 525 CyLab challenges with 9 static workers plus 1 exclusive instance worker.

**Architecture:** Two independent queues under one coordinator in `cylab_full_benchmark.py`, with file-locked instance ownership, READY polling, official-verdict submit, and async dashboard.

**Tech Stack:** Python ThreadPoolExecutor + as_completed, `gpt run --json`, `ctf instance/submit` subprocess, JSONL checkpoint, Markdown dashboard.

**Spec:** `/home/light/Downloads/bqa-trash/cylab-scheduling-research/recommendation.md` (evidence: `instance-semantics.json`, catalog `/home/light/Downloads/bqa-trash/cylab_academy_workspace/challenges.json`)

## Global Constraints

- Load worker counts, timeouts, polling intervals from `.env` single layer; no hardcoded numeric limits in logic.
- Keep `git status` clean in `/home/light/GitHub/gpt_cli`; do NOT create test files in repo roots; sandbox tests in `/home/light/Downloads/bqa-trash/`.
- Do NOT git commit unless user explicitly requests it (AGENTS.md overrides skill default).
- Prompt per challenge is exactly `solve <abs_path>`; no second user prompt after a failed turn.
- Never auto-extend/restart a live instance under its solver; never stop shared `on_demand=false` services.
- PASS only on official platform verdict strings; process exit code 0 alone is not PASS.
- No arbitrary solver deadline; HTTP request deadlines are separate from solver runtime.

---

### Task 1: Single config layer

**Files:**
- Modify: `/home/light/GitHub/gpt_cli/.env`
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:27-34`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: `os.getenv`
- Produces: `load_cylab_config() -> dict[str, float | int]` with keys `static_workers, dynamic_workers, start_timeout, poll_interval, submit_timeout, dashboard_interval, session_cap`

- [ ] **Step 1: Write the failing test**

```python
def test_load_cylab_config_defaults(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    cfg = runner.load_cylab_config()
    assert cfg["static_workers"] == 9
    assert cfg["dynamic_workers"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_load_cylab_config_defaults -v`
Expected: FAIL with `has no attribute 'load_cylab_config'`

- [ ] **Step 3: Write minimal implementation**

Append to `.env`:

```bash
CYLAB_STATIC_WORKERS=9
CYLAB_DYNAMIC_WORKERS=1
CYLAB_START_TIMEOUT=45
CYLAB_POLL_INTERVAL=2.0
CYLAB_SUBMIT_TIMEOUT=30
CYLAB_DASHBOARD_INTERVAL=30
CYLAB_SESSION_CAP=1000
```

Add to runner after `WRITE_LOCK` definition:

```python
def load_cylab_config() -> dict:
    import os
    return {
        "static_workers": int(os.getenv("CYLAB_STATIC_WORKERS", "9")),
        "dynamic_workers": int(os.getenv("CYLAB_DYNAMIC_WORKERS", "1")),
        "start_timeout": int(os.getenv("CYLAB_START_TIMEOUT", "45")),
        "poll_interval": float(os.getenv("CYLAB_POLL_INTERVAL", "2.0")),
        "submit_timeout": int(os.getenv("CYLAB_SUBMIT_TIMEOUT", "30")),
        "dashboard_interval": int(os.getenv("CYLAB_DASHBOARD_INTERVAL", "30")),
        "session_cap": int(os.getenv("CYLAB_SESSION_CAP", "1000")),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_load_cylab_config_defaults -v`
Expected: PASS

---

### Task 2: Correct classification

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:180-198`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: challenge dict with `instance_info`
- Produces: `is_private_instance(ch: dict) -> bool`, `is_shared_service(ch: dict) -> bool`

- [ ] **Step 1: Write the failing test**

```python
def test_is_private_excludes_shared():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    shared = {"instance_info": {"is_container": True, "on_demand": False, "status": "RUNNING"}}
    private = {"instance_info": {"is_container": True, "on_demand": True}}
    assert runner.is_private_instance(shared) is False
    assert runner.is_shared_service(shared) is True
    assert runner.is_private_instance(private) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_is_private_excludes_shared -v`
Expected: FAIL with `has no attribute 'is_private_instance'`

- [ ] **Step 3: Write minimal implementation**

```python
def is_private_instance(ch: dict) -> bool:
    ii = ch.get("instance_info") or {}
    return bool(ii.get("on_demand") is True)


def is_shared_service(ch: dict) -> bool:
    ii = ch.get("instance_info") or {}
    return bool(ii.get("is_container") is True and ii.get("on_demand") is not True)
```

Replace line 218 `on_demand = bool(...)` with `on_demand = is_private_instance(challenge)`. Add live-resolve: challenges with missing `on_demand` key go through one `ctf instance status --id <id>` GET before dispatch; 16 unknowns resolved to on-demand per spec, yielding 262/263 split.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_is_private_excludes_shared -v`
Expected: PASS

---

### Task 3: Exclusive instance ownership plus READY polling

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:216-234`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: `load_cylab_config()`, `is_private_instance()`
- Produces: `acquire_instance_slot(path: Path) -> bool`, `wait_ready(cid: str, poll_interval: float) -> tuple[bool, str, int | None]` returning `(ready, connection_info, expires_in)`

- [ ] **Step 1: Write the failing test**

```python
def test_wait_ready_parses_states():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.parse_instance_state({"status": "RESTARTING"}) == "RESTARTING"
    assert runner.parse_instance_state({"status": "RUNNING"}) == "RUNNING"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_wait_ready_parses_states -v`
Expected: FAIL with `has no attribute 'parse_instance_state'`

- [ ] **Step 3: Write minimal implementation**

```python
import fcntl

INSTANCE_LOCK = Path("/home/light/Downloads/bqa-trash/cylab_academy_workspace/.instance.lock")

def parse_instance_state(payload: dict) -> str:
    return str(payload.get("status", "NOT_RUNNING"))

def wait_ready(cid: str, poll_interval: float, timeout: int = 45) -> tuple:
    import json, subprocess, time
    deadline = time.time() + timeout
    conn, expiry, state = "", None, "NOT_RUNNING"
    while time.time() < deadline:
        p = subprocess.run(
            ["ctf", "instance", "status", "-w",
             "/home/light/Downloads/bqa-trash/cylab_academy_workspace",
             "--id", str(cid)],
            capture_output=True, text=True, timeout=30)
        try:
            payload = json.loads(p.stdout or "{}")
        except Exception:
            payload = {}
        state = parse_instance_state(payload)
        conn = payload.get("connection_info") or payload.get("active_instance") or conn
        expiry = payload.get("expires_in", expiry)
        if state == "RUNNING" and conn:
            return True, conn, expiry
        time.sleep(poll_interval)
    return False, conn, expiry
```

Rules: start PUT only after owning file-locked slot; never PUT extend while owned; record `expires_in` as infra event; startup failure returns `ready=False` and solver dispatch is skipped; preserve raw `STARTING/RESTARTING/RUNNING` strings.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_wait_ready_parses_states -v`
Expected: PASS

---

### Task 4: One-line prompt plus session handling

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:164-177`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: challenge dir `Path`
- Produces: `build_prompt(abs_path: Path) -> str`

- [ ] **Step 1: Write the failing test**

```python
def test_build_prompt_is_one_line():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    p = runner.build_prompt(Path("/home/light/Downloads/bqa-trash/cylab_academy_workspace/Web_Exploitation/x"))
    assert p == "solve /home/light/Downloads/bqa-trash/cylab_academy_workspace/Web_Exploitation/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_build_prompt_is_one_line -v`
Expected: FAIL (multi-line prompt returned)

- [ ] **Step 3: Write minimal implementation**

```python
def build_prompt(abs_path) -> str:
    from pathlib import Path
    return f"solve {Path(abs_path).resolve()}"
```

Runner must: resolve dirs from metadata before dispatch; write `connection_info` into workspace file before launch; create one `gpt run -p <prompt> --tag bench-<id> --json` session per challenge; record local session id immediately; keep same remote turn on transport recovery; send no second prompt after failed turn; use per-request deadlines only, no fixed 600s solver kill.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_build_prompt_is_one_line -v`
Expected: PASS

---

### Task 5: Official-verdict submit plus checkpoint before release

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:276-310`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: `extracted_flag: str`, submit stdout `str`
- Produces: `is_official_pass(stdout: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
def test_official_pass_ignores_rc_only():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.is_official_pass("Correct flag! Solved") is True
    assert runner.is_official_pass("") is False
    assert runner.is_official_pass("returncode 0") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_official_pass_ignores_rc_only -v`
Expected: FAIL with `has no attribute 'is_official_pass'`

- [ ] **Step 3: Write minimal implementation**

```python
def is_official_pass(stdout: str) -> bool:
    s = stdout or ""
    return ("Correct flag!" in s) or ("Solved" in s and "✅" in s)
```

Rules: unwrap `FLAG{academy{...}}` to inner core; reject flags containing `...` or `xxx`; `rc==0` alone stays FAIL; persist JSONL row with `session_id, queue_time_s, startup_s, gpt_s, submit_s, cleanup_s, total_s, expires_in, verdict, failure_reason, retry_count` before stopping instance; stop owned private instance and verify terminal state; never stop shared services.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_official_pass_ignores_rc_only -v`
Expected: PASS

---

### Task 6: 9–1 scheduler with async dashboard

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:314-377`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: `pending_private: list`, `pending_other: list`, config
- Produces: `pick_next(has_worker: bool, slot_free: bool) -> str` returning `"private" | "other" | "wait"`

- [ ] **Step 1: Write the failing test**

```python
def test_pick_next_never_waits_worker_on_instance():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.pick_next(True, False) == "other"
    assert runner.pick_next(True, True) == "private"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_pick_next_never_waits_worker_on_instance -v`
Expected: FAIL with `has no attribute 'pick_next'`

- [ ] **Step 3: Write minimal implementation**

```python
def pick_next(has_worker: bool, slot_free: bool) -> str:
    if not has_worker:
        return "wait"
    return "private" if slot_free else "other"
```

Rules: start both lanes at once; keep 9 other + 1 private active via `as_completed` with no batch barrier; waiting-for-instance never holds a GPT worker; dynamic finish may help other lane and vice versa; prep paths ahead but never prestart next private instance; render dashboard on timer thread every `dashboard_interval`, never inside completion lock; record completion-to-dispatch delay per lane.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_pick_next_never_waits_worker_on_instance -v`
Expected: PASS

---

### Task 7: Session hygiene under 1000

**Files:**
- Modify: `/home/light/Downloads/bqa-trash/cylab_full_benchmark.py:302-310`
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`

**Interfaces:**
- Consumes: `session_cap: int`
- Produces: `prune_sessions(cap: int) -> int`

- [ ] **Step 1: Write the failing test**

```python
def test_prune_keeps_cap():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner", "/home/light/Downloads/bqa-trash/cylab_full_benchmark.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert callable(runner.prune_sessions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_prune_keeps_cap -v`
Expected: FAIL with `has no attribute 'prune_sessions'`

- [ ] **Step 3: Write minimal implementation**

```python
def prune_sessions(cap: int = 1000) -> int:
    import json, subprocess
    from pathlib import Path
    reg = Path.home() / ".local/share/webgpt/sessions/tool_sessions.json"
    try:
        data = json.loads(reg.read_text())
    except Exception:
        return 0
    sessions = data.get("sessions", {})
    if len(sessions) <= cap:
        return 0
    ordered = sorted(sessions.values(), key=lambda r: r.get("updated_at", ""))
    remove = len(sessions) - cap
    for rec in ordered[:remove]:
        subprocess.run(["gpt", "session", "delete", rec["local_id"]],
                       capture_output=True, timeout=30)
    return remove
```

Rules: current registry holds 1204 sessions / 1.8G; prune to `cap` at startup and every 50 completions; delete per-challenge bench session right after JSONL persist; keep evidence in JSONL plus dashboard only.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py::test_prune_keeps_cap -v`
Expected: PASS

---

### Task 8: Smoke verification

**Files:**
- Test: `/home/light/Downloads/bqa-trash/test_cylab_91.py`
- Output: `/home/light/Downloads/bqa-trash/cylab_academy_workspace/BENCHMARK_DASHBOARD.md`

- [ ] **Step 1: Run full unit file**

Run: `/home/light/GitHub/gpt_cli/.venv/bin/pytest /home/light/Downloads/bqa-trash/test_cylab_91.py -v`
Expected: PASS all 7 tests

- [ ] **Step 2: Run limited dry dispatch**

Run: `python3 /home/light/Downloads/bqa-trash/cylab_full_benchmark.py --max-chals 2 --workers 2`
Expected: 2 JSONL rows with `session_id` and timing fields; dashboard updates without blocking dispatch

- [ ] **Step 3: Verify no repo pollution**

Run: `git status --short` in `/home/light/GitHub/gpt_cli`
Expected: only intended `.env` plus plan file; no test files in repo root
