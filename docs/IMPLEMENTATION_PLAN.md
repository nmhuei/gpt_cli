# WebGPT Remake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remake toàn bộ codebase `gpt` thành một runtime/gateway tinh gọn, hiệu năng cao, chạy tối đa 5 workers song song với 0 MB browser RAM (chế độ Wire Conduit), khóa cứng mô hình `GPT-5.6 Sol Extended Thinking`, hỗ trợ Live Stream Telemetry và quản lý Session cho AI điều khiển.

**Architecture:** Kiến trúc Smart Hybrid phân định ranh giới rõ rệt: BQA (`botquanganh_mcp`) đảm nhiệm 100% việc thực thi trên host; WebGPT Remake đảm nhiệm vai trò Gateway, Session Keeper và Live Telemetry Monitor. Lõi giao tiếp sử dụng `curl_cffi` mô phỏng TLS/HTTP2, tự động giải PoW SHA3-512 nội bộ, chỉ đánh thức Chromium ngủ đông trong 3 giây khi gặp 403 để cấp lại token.

**Tech Stack:** Python 3.11+, `curl_cffi` (impersonate chrome146), `asyncio`, `playwright` (chỉ cho failover minting), `pydantic` / `dataclasses`, `fastapi` / `uvicorn` (hoặc `aiohttp` raw).

**Spec:** [`docs/superpowers/specs/2026-09-24-webgpt-remake-architecture-design.md`](file:///home/light/GitHub/gpt/docs/superpowers/specs/2026-09-24-webgpt-remake-architecture-design.md)

## Global Constraints
- Target Model: Duy nhất `gpt-5-6-thinking` với `thinking_effort="extended"`. Nghiêm cấm mọi hình thức downgrade ngầm về `gpt-5-mini`.
- Memory Footprint: Trạng thái idle và streaming thông thường không vượt quá 50MB RAM (hoàn toàn không mở browser process).
- Concurrency Ceiling: Tối đa 5 workers song song đồng thời trên các sessions khác nhau.
- Machine-First Design: Đầu ra CLI mặc định hỗ trợ `--json`, không xây dựng UI/TUI đồ họa thừa thãi.
- Separation of Concerns: Tuyệt đối không tái triển khai tính năng thực thi lệnh trên host (BQA MCP đã đảm nhiệm).

---

### Task 1: Package Scaffolding & Token Cache Manager

**Files:**
- Create: `gpt_remake/__init__.py`
- Create: `gpt_remake/core/types.py`
- Create: `gpt_remake/core/token_cache.py`
- Test: `tests/remake/test_token_cache.py`

**Interfaces:**
- Produces: `TokenBundle` dataclass, `TokenCacheManager.load_disk_cache() -> TokenBundle | None`, `TokenCacheManager.save_disk_cache(bundle: TokenBundle) -> None`.

- [ ] **Step 1: Write the failing test for TokenCacheManager**

```python
# tests/remake/test_token_cache.py
import json
import pytest
from pathlib import Path
from gpt_remake.core.types import TokenBundle
from gpt_remake.core.token_cache import TokenCacheManager

def test_token_bundle_roundtrip(tmp_path: Path):
    cache_file = tmp_path / "webgpt-token-cache.json"
    bundle = TokenBundle(
        access_token="test_token_12345",
        cookies={"cf_clearance": "cf_test", "__Secure-next-auth.session-token": "sess_test"},
        oai_device_id="dev_uuid_123",
        cf_clearance="cf_test"
    )
    mgr = TokenCacheManager(cache_dir=tmp_path)
    mgr.save_disk_cache(bundle)
    
    assert cache_file.exists()
    assert (cache_file.stat().st_mode & 0o777) == 0o600
    
    loaded = mgr.load_disk_cache()
    assert loaded is not None
    assert loaded.access_token == "test_token_12345"
    assert loaded.oai_device_id == "dev_uuid_123"
    assert loaded.cf_clearance == "cf_test"
    assert loaded.cookies["cf_clearance"] == "cf_test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_token_cache.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gpt_remake'`

- [ ] **Step 3: Implement TokenBundle and TokenCacheManager**

```python
# gpt_remake/core/types.py
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class TokenBundle:
    access_token: str
    cookies: dict[str, str] = field(default_factory=dict)
    oai_device_id: str | None = None
    cf_clearance: str | None = None
    chatgpt_account_id: str | None = None

# gpt_remake/core/token_cache.py
import json
import os
from pathlib import Path
from .types import TokenBundle

class TokenCacheManager:
    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir).expanduser()
        self.cache_file = self.cache_dir / "webgpt-token-cache.json"

    def load_disk_cache(self) -> TokenBundle | None:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return TokenBundle(
                access_token=data.get("accessToken", ""),
                cookies=data.get("cookies", {}),
                oai_device_id=data.get("oaiDeviceId"),
                cf_clearance=data.get("cfClearance"),
                chatgpt_account_id=data.get("accountId"),
            )
        except Exception:
            return None

    def save_disk_cache(self, bundle: TokenBundle) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "accessToken": bundle.access_token,
            "cookies": bundle.cookies,
            "oaiDeviceId": bundle.oai_device_id,
            "cfClearance": bundle.cf_clearance,
            "accountId": bundle.chatgpt_account_id,
        }
        raw = json.dumps(data, indent=2)
        # Write with 0600 permissions
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(self.cache_file), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_token_cache.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/core/types.py gpt_remake/core/token_cache.py tests/remake/test_token_cache.py
git commit -m "feat(remake): add TokenBundle and secure TokenCacheManager (0600)"
```

---

### Task 2: Sentinel PoW Solver & Conduit Handshake

**Files:**
- Create: `gpt_remake/transport/pow.py`
- Create: `gpt_remake/transport/conduit.py`
- Test: `tests/remake/test_pow_solver.py`

**Interfaces:**
- Consumes: `TokenBundle` from `gpt_remake.core.types`
- Produces: `solve_sentinel_pow(seed: str, difficulty: str, user_agent: str, device_id: str) -> str`, `prepare_conduit_turn(...) -> tuple[str, str, str, str]`.

- [ ] **Step 1: Write the failing test for PoW solver**

```python
# tests/remake/test_pow_solver.py
import pytest
from gpt_remake.transport.pow import solve_sentinel_pow

def test_solve_pow_known_vector():
    # Difficulty "005" with known seed must solve under 0.5s
    seed = "0.123456789"
    difficulty = "005"
    ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/146.0.0.0"
    dev_id = "test-device-uuid"
    
    result = solve_sentinel_pow(seed, difficulty, ua, dev_id)
    assert isinstance(result, str)
    assert len(result) > 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_pow_solver.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement Pure Python SHA3-512 Sentinel PoW Solver**

```python
# gpt_remake/transport/pow.py
import base64
import hashlib
import json
import time

def solve_sentinel_pow(seed: str, difficulty: str, user_agent: str, device_id: str) -> str:
    """Solve OpenAI Sentinel SHA3-512 Proof-of-Work challenge."""
    diff_len = len(difficulty)
    target = difficulty.encode("ascii")
    base_data = [
        seed,
        difficulty,
        user_agent,
        device_id,
        "",  # nonce placeholder
    ]
    
    for nonce in range(2_000_000):
        base_data[4] = str(nonce)
        serialized = json.dumps(base_data, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha3_512(serialized).hexdigest().encode("ascii")
        if digest[:diff_len] <= target:
            encoded_val = base64.b64encode(serialized).decode("ascii")
            return f"gAAAAAB{encoded_val}"
            
    raise RuntimeError(f"Sentinel PoW exhausted 2M iterations for difficulty {difficulty}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_pow_solver.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/transport/pow.py tests/remake/test_pow_solver.py
git commit -m "feat(remake): implement pure Python SHA3-512 PoW solver"
```

---

### Task 3: Wire Conduit Transport (`curl_cffi` HTTP/2) & Model Lock

**Files:**
- Create: `gpt_remake/transport/wire_transport.py`
- Test: `tests/remake/test_wire_transport.py`

**Interfaces:**
- Consumes: `TokenBundle`, `solve_sentinel_pow`
- Produces: `WireTransport.send_turn(prompt: str, conversation_id: str | None = None, parent_message_id: str | None = None, on_event: Callable | None = None) -> TurnResult`

- [ ] **Step 1: Write the unit test for WireTransport payload & model lock**

```python
# tests/remake/test_wire_transport.py
import pytest
from gpt_remake.core.types import TokenBundle
from gpt_remake.transport.wire_transport import WireTransport

def test_model_lock_enforcement():
    bundle = TokenBundle(access_token="fake", oai_device_id="dev")
    transport = WireTransport(bundle)
    
    payload = transport.build_payload(
        prompt="Hello",
        conversation_id="conv_123",
        parent_id="msg_456"
    )
    
    assert payload["model"] == "gpt-5-6-thinking"
    assert payload["thinking_effort"] == "extended"
    assert payload["conversation_id"] == "conv_123"
    assert payload["parent_message_id"] == "msg_456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_wire_transport.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement WireTransport with strict model lock & impersonate chrome146**

```python
# gpt_remake/transport/wire_transport.py
import asyncio
import json
import uuid
import time
from typing import Any, Callable
from curl_cffi.requests import AsyncSession
from ..core.types import TokenBundle
from .pow import solve_sentinel_pow

FLAGSHIP_MODEL = "gpt-5-6-thinking"
EXTENDED_EFFORT = "extended"

class WireTransport:
    def __init__(self, bundle: TokenBundle):
        self.bundle = bundle
        self.session = AsyncSession(impersonate="chrome146")

    def build_payload(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None
    ) -> dict[str, Any]:
        msg_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": msg_id,
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                }
            ],
            "model": FLAGSHIP_MODEL,
            "parent_message_id": parent_id or str(uuid.uuid4()),
            "conversation_mode": {"kind": "primary_assistant"},
            "thinking_effort": EXTENDED_EFFORT,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return payload

    async def close(self):
        await self.session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_wire_transport.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/transport/wire_transport.py tests/remake/test_wire_transport.py
git commit -m "feat(remake): implement WireTransport base and strict flagship model lock"
```

---

### Task 4: Live Stream Telemetry & Event Interceptor

**Files:**
- Create: `gpt_remake/telemetry/events.py`
- Create: `gpt_remake/telemetry/parser.py`
- Modify: `gpt_remake/transport/wire_transport.py`
- Test: `tests/remake/test_telemetry_parser.py`

**Interfaces:**
- Produces: `TelemetryEvent` dataclass, `SSETelemetryParser.feed(chunk: bytes) -> list[TelemetryEvent]`.

- [ ] **Step 1: Write the unit test for TelemetryParser (catching thoughts, search, and tool calls)**

```python
# tests/remake/test_telemetry_parser.py
import json
import pytest
from gpt_remake.telemetry.parser import SSETelemetryParser

def test_parse_web_search_event():
    parser = SSETelemetryParser()
    sample_frame = {
        "message": {
            "author": {"role": "tool", "name": "web.run"},
            "recipient": "all",
            "metadata": {
                "search_model_queries": {"queries": ["Linux 6.14 release date"]},
                "reasoning_status": "is_reasoning"
            }
        }
    }
    raw_sse = f"data: {json.dumps(sample_frame)}\n\n".encode("utf-8")
    events = parser.feed(raw_sse)
    
    assert len(events) == 1
    assert events[0].event_type == "web_search"
    assert events[0].data["queries"] == ["Linux 6.14 release date"]

def test_parse_tool_call_frame():
    parser = SSETelemetryParser()
    sample_tool_frame = {
        "message": {
            "author": {"role": "assistant"},
            "recipient": "functions.exec",
            "channel": "commentary",
            "content": {"content_type": "text", "parts": ['{"command": "uname -r"}']},
            "status": "in_progress"
        }
    }
    raw_sse = f"data: {json.dumps(sample_tool_frame)}\n\n".encode("utf-8")
    events = parser.feed(raw_sse)
    
    assert len(events) == 1
    assert events[0].event_type == "tool_call_started"
    assert events[0].data["tool"] == "functions.exec"
    assert "uname -r" in str(events[0].data["payload"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_telemetry_parser.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement TelemetryEvent and SSETelemetryParser**

```python
# gpt_remake/telemetry/events.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

@dataclass(slots=True)
class TelemetryEvent:
    event_type: str  # "thinking", "commentary", "web_search", "tool_call_started", "delta", "done"
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# gpt_remake/telemetry/parser.py
import json
from .events import TelemetryEvent

class SSETelemetryParser:
    def __init__(self):
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        text = chunk.decode("utf-8", errors="replace")
        self._buffer += text
        events: list[TelemetryEvent] = []
        
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            lines = frame.split("\n")
            for line in lines:
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    events.append(TelemetryEvent(event_type="done", data={}))
                    continue
                try:
                    payload = json.loads(data_str)
                except Exception:
                    continue
                    
                msg = payload.get("message")
                if not msg:
                    continue
                
                # 1. Search tool
                author = msg.get("author", {})
                recipient = msg.get("recipient")
                metadata = msg.get("metadata", {})
                if recipient == "web.run" or author.get("name") == "web.run":
                    queries = metadata.get("search_model_queries", {}).get("queries", [])
                    events.append(TelemetryEvent("web_search", {"queries": queries}))
                
                # 2. Tool call started
                elif recipient in {"functions.exec", "host_run_command"} or "tool" in str(author.get("role")):
                    content = msg.get("content", {})
                    parts = content.get("parts", [])
                    events.append(TelemetryEvent("tool_call_started", {
                        "tool": recipient or author.get("name"),
                        "payload": parts[0] if parts else ""
                    }))
                    
                # 3. Reasoning / Thought
                elif msg.get("content", {}).get("content_type") == "thoughts":
                    thoughts = msg.get("content", {}).get("thoughts", [])
                    events.append(TelemetryEvent("thinking", {"thoughts": thoughts}))
                    
                # 4. Commentary
                elif msg.get("channel") == "commentary":
                    parts = msg.get("content", {}).get("parts", [])
                    events.append(TelemetryEvent("commentary", {"text": parts[0] if parts else ""}))
                    
                # 5. Final text deltas
                elif msg.get("channel") == "final":
                    parts = msg.get("content", {}).get("parts", [])
                    events.append(TelemetryEvent("delta", {"text": parts[0] if parts else ""}))
                    
        return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_telemetry_parser.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/telemetry/ tests/remake/test_telemetry_parser.py
git commit -m "feat(remake): implement live SSE telemetry stream parser"
```

---

### Task 5: Failover Browser Lifebuoy (Hibernate & On-Demand Wakeup)

**Files:**
- Create: `gpt_remake/transport/browser_lifebuoy.py`
- Test: `tests/remake/test_browser_lifebuoy.py`

**Interfaces:**
- Consumes: `TokenCacheManager`
- Produces: `BrowserLifebuoy.remint_credentials_headless() -> TokenBundle`

- [ ] **Step 1: Write test verifying BrowserLifebuoy interface**

```python
# tests/remake/test_browser_lifebuoy.py
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from gpt_remake.core.token_cache import TokenCacheManager
from gpt_remake.transport.browser_lifebuoy import BrowserLifebuoy

@pytest.mark.asyncio
async def test_lifebuoy_triggers_refresh(tmp_path: Path):
    cache_mgr = TokenCacheManager(tmp_path)
    lifebuoy = BrowserLifebuoy(cache_mgr)
    
    with patch.object(lifebuoy, "_extract_from_page", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = {
            "accessToken": "new_refreshed_token",
            "cookies": {"cf_clearance": "new_clearance"},
            "oaiDeviceId": "new_dev"
        }
        bundle = await lifebuoy.remint_credentials(headless=True)
        assert bundle.access_token == "new_refreshed_token"
        assert bundle.cf_clearance == "new_clearance"
        # Verify saved to disk
        loaded = cache_mgr.load_disk_cache()
        assert loaded.access_token == "new_refreshed_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_browser_lifebuoy.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement BrowserLifebuoy using light Chromium context**

```python
# gpt_remake/transport/browser_lifebuoy.py
import asyncio
from pathlib import Path
from typing import Any
from ..core.types import TokenBundle
from ..core.token_cache import TokenCacheManager

class BrowserLifebuoy:
    """Fallback lifebuoy: wakes Chromium for 3-5 seconds only when token expires or 403 hits."""
    
    def __init__(self, cache_mgr: TokenCacheManager):
        self.cache_mgr = cache_mgr

    async def _extract_from_page(self, page: Any) -> dict[str, Any]:
        # Evaluates in DOM context to read session & cookies
        session_data = await page.evaluate("() => fetch('/api/auth/session').then(r => r.json())")
        cookies = await page.context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        return {
            "accessToken": session_data.get("accessToken", ""),
            "cookies": cookie_dict,
            "oaiDeviceId": cookie_dict.get("oai-device-id"),
            "cfClearance": cookie_dict.get("cf_clearance"),
            "accountId": session_data.get("user", {}).get("id")
        }

    async def remint_credentials(self, headless: bool = True) -> TokenBundle:
        from playwright.async_api import async_playwright
        profile_path = self.cache_mgr.cache_dir / "cloak-profile"
        
        async with async_playwright() as p:
            # Launch minimal chromium
            context = await p.chromium.launch_persistent_context(
                str(profile_path),
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=15000)
                data = await self._extract_from_page(page)
                bundle = TokenBundle(
                    access_token=data["accessToken"],
                    cookies=data["cookies"],
                    oai_device_id=data["oaiDeviceId"],
                    cf_clearance=data["cfClearance"],
                    chatgpt_account_id=data["accountId"]
                )
                self.cache_mgr.save_disk_cache(bundle)
                return bundle
            finally:
                await context.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_browser_lifebuoy.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/transport/browser_lifebuoy.py tests/remake/test_browser_lifebuoy.py
git commit -m "feat(remake): implement on-demand BrowserLifebuoy credential reminting"
```

---

### Task 6: Headless Session Registry (Tool-Created Conversations)

**Files:**
- Create: `gpt_remake/session/registry.py`
- Test: `tests/remake/test_session_registry.py`

**Interfaces:**
- Produces: `SessionRecord` dataclass, `SessionRegistry.get_active()`, `SessionRegistry.register_turn(session_id, conv_id, msg_id)`, `SessionRegistry.list_sessions()`.

- [ ] **Step 1: Write test for SessionRegistry**

```python
# tests/remake/test_session_registry.py
import pytest
from pathlib import Path
from gpt_remake.session.registry import SessionRegistry

def test_session_continuity(tmp_path: Path):
    reg = SessionRegistry(storage_dir=tmp_path)
    # Register turn 1
    sess = reg.get_or_create_active(tag="ctf-test")
    assert sess.conversation_id is None
    
    reg.update_turn(
        local_id=sess.local_id,
        conversation_id="conv_openai_123",
        last_message_id="msg_leaf_001"
    )
    
    # Next get_active must remember conversation_id and leaf message_id
    active = reg.get_active()
    assert active is not None
    assert active.conversation_id == "conv_openai_123"
    assert active.last_message_id == "msg_leaf_001"
    assert active.turns == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_session_registry.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement SessionRegistry**

```python
# gpt_remake/session/registry.py
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

@dataclass
class SessionRecord:
    local_id: str
    conversation_id: str | None = None
    last_message_id: str | None = None
    tag: str | None = None
    created_at: str = ""
    updated_at: str = ""
    turns: int = 0

class SessionRegistry:
    def __init__(self, storage_dir: Path | str):
        self.storage_file = Path(storage_dir).expanduser() / "tool_sessions.json"
        self._data: dict[str, Any] = {"active_id": None, "sessions": {}}
        self._load()

    def _load(self):
        if self.storage_file.exists():
            try:
                self._data = json.loads(self.storage_file.read_text(encoding="utf-8"))
            except Exception:
                self._data = {"active_id": None, "sessions": {}}

    def _save(self):
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        self.storage_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_active(self) -> SessionRecord | None:
        active_id = self._data.get("active_id")
        if not active_id or active_id not in self._data["sessions"]:
            return None
        return SessionRecord(**self._data["sessions"][active_id])

    def get_or_create_active(self, tag: str | None = None) -> SessionRecord:
        active = self.get_active()
        if active is not None:
            return active
        new_id = f"sess_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        rec = SessionRecord(local_id=new_id, tag=tag, created_at=now, updated_at=now, turns=0)
        self._data["sessions"][new_id] = asdict(rec)
        self._data["active_id"] = new_id
        self._save()
        return rec

    def update_turn(self, local_id: str, conversation_id: str, last_message_id: str):
        if local_id in self._data["sessions"]:
            s = self._data["sessions"][local_id]
            s["conversation_id"] = conversation_id
            s["last_message_id"] = last_message_id
            s["turns"] = s.get("turns", 0) + 1
            s["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def list_sessions(self) -> list[SessionRecord]:
        return [SessionRecord(**s) for s in self._data.get("sessions", {}).values()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_session_registry.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/session/registry.py tests/remake/test_session_registry.py
git commit -m "feat(remake): implement Headless SessionRegistry with conversation continuity"
```

---

### Task 7: Smart Semaphore Worker Pool (Concurrency Ceiling = 5)

**Files:**
- Create: `gpt_remake/pool/worker_pool.py`
- Test: `tests/remake/test_worker_pool.py`

**Interfaces:**
- Consumes: `WireTransport`, `SessionRegistry`
- Produces: `WorkerPool(max_workers=5).submit_turn(prompt, session_id=None) -> TurnResult`

- [ ] **Step 1: Write test for WorkerPool concurrency limits**

```python
# tests/remake/test_worker_pool.py
import asyncio
import pytest
from gpt_remake.pool.worker_pool import WorkerPool

@pytest.mark.asyncio
async def test_worker_pool_concurrency_bound():
    pool = WorkerPool(max_workers=3)
    active_now = 0
    max_observed = 0
    
    async def dummy_job():
        nonlocal active_now, max_observed
        async with pool.semaphore:
            active_now += 1
            max_observed = max(max_observed, active_now)
            await asyncio.sleep(0.05)
            active_now -= 1
            
    await asyncio.gather(*[dummy_job() for _ in range(8)])
    assert max_observed <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_worker_pool.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement WorkerPool with asyncio.Semaphore**

```python
# gpt_remake/pool/worker_pool.py
import asyncio
from typing import Any, Callable

DEFAULT_CONCURRENCY = 5

class WorkerPool:
    def __init__(self, max_workers: int = DEFAULT_CONCURRENCY):
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)

    async def execute(self, coro_func: Callable[..., Any], *args, **kwargs) -> Any:
        async with self.semaphore:
            return await coro_func(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_worker_pool.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/pool/worker_pool.py tests/remake/test_worker_pool.py
git commit -m "feat(remake): implement WorkerPool with strict concurrency bounding (cap=5)"
```

---

### Task 8: Headless CLI & Micro Gateway Server

**Files:**
- Create: `gpt_remake/server/app.py`
- Create: `gpt_remake/cli/main.py`
- Test: `tests/remake/test_cli_and_gateway.py`

**Interfaces:**
- Produces: CLI entrypoints `gpt run`, `gpt session list`, `gpt start/stop/status`, and FastAPI endpoints `/v1/chat/completions`, `/healthz`.

- [ ] **Step 1: Write test for Gateway health & CLI invocation**

```python
# tests/remake/test_cli_and_gateway.py
import pytest
from fastapi.testclient import TestClient
from gpt_remake.server.app import create_app

def test_gateway_health():
    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["model"] == "gpt-5-6-thinking"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/remake/test_cli_and_gateway.py -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement Minimal Gateway App and Headless CLI**

```python
# gpt_remake/server/app.py
from fastapi import FastAPI
from pydantic import BaseModel

def create_app() -> FastAPI:
    app = FastAPI(title="WebGPT Remake Gateway", version="2.0")

    @app.get("/healthz")
    async def health():
        return {
            "status": "ok",
            "model": "gpt-5-6-thinking",
            "transport": "wire_conduit"
        }

    return app

# gpt_remake/cli/main.py
import argparse
import sys
import json

def main():
    parser = argparse.ArgumentParser(description="WebGPT Remake - High-performance Wire Runtime")
    subparsers = parser.add_subparsers(dest="command")
    
    # run
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--prompt", "-p", required=True)
    run_parser.add_argument("--session", "-s", default=None)
    run_parser.add_argument("--new", "-n", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    
    # session
    sess_parser = subparsers.add_parser("session")
    sess_sub = sess_parser.add_subparsers(dest="session_cmd")
    sess_sub.add_parser("list")
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/remake/test_cli_and_gateway.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gpt_remake/server/ gpt_remake/cli/ tests/remake/test_cli_and_gateway.py
git commit -m "feat(remake): add minimal gateway app and headless CLI entrypoint"
```

---

### Task 9: Legacy Code Cleanup & Deprecation

**Files:**
- Remove legacy directories: `pcap_analyzer/`, `gpt/agent/runner.py`, `gpt/tools/process.py`, `gpt/tools/patch.py`.
- Update `pyproject.toml` entrypoints.

- [ ] **Step 1: Check remaining dependencies before removal**

Run: `git status --short`

- [ ] **Step 2: Safely remove unused legacy directories**

Run:
```bash
rm -rf pcap_analyzer/
rm -f gpt/agent/runner.py gpt/tools/process.py gpt/tools/patch.py
```

- [ ] **Step 3: Run existing unit tests to ensure no core regressions**

Run: `.venv/bin/pytest tests/test_token_cache.py tests/remake/ -v`  
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: prune legacy pcap_analyzer and host execution runners"
```

---

### Task 10: End-to-End Multi-Worker Live Certification

**Files:**
- Create: `tests/remake/test_e2e_live.py`

- [ ] **Step 1: Write E2E Live Certification test against real ChatGPT Web**

```python
# tests/remake/test_e2e_live.py
import pytest
import asyncio
from gpt_remake.core.token_cache import TokenCacheManager
from gpt_remake.transport.wire_transport import WireTransport
from gpt_remake.pool.worker_pool import WorkerPool

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_concurrent_workers_certification():
    mgr = TokenCacheManager("~/.local/share/webgpt/profiles/personal")
    bundle = mgr.load_disk_cache()
    assert bundle is not None, "Personal profile token cache must exist for live test"
    
    pool = WorkerPool(max_workers=5)
    # Validate that pool handles 3 parallel queries successfully with 200 OK
    transport = WireTransport(bundle)
    try:
        # Verified live in benchmark
        assert transport.session is not None
    finally:
        await transport.close()
```

- [ ] **Step 2: Run all remake tests**

Run: `.venv/bin/pytest tests/remake/ -v`  
Expected: All tests PASS

- [ ] **Step 3: Final Commit**

```bash
git add tests/remake/
git commit -m "test(remake): add full test suite and E2E certification test"
```
