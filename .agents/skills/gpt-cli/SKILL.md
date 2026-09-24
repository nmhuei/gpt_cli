---
name: gpt-cli
description: Use when needing to invoke, automate, control, or troubleshoot the local `gpt-cli` tool (ChatGPT-Web Wire Conduit runtime), run prompts with `gpt-5-6-thinking` (0 MB browser RAM), manage tool-specific sessions, start/stop the background gateway, or diagnose, fix bugs, and improve the codebase at `/home/light/GitHub/gpt_cli`.
---

# GPT-CLI Tool & Maintenance Guide

## Overview

`gpt-cli` là runtime tinh gọn, hiệu năng cao thay thế toàn bộ codebase ChatGPT Web cũ. Tool sử dụng cơ chế **Wire Conduit** qua `curl_cffi` (impersonate Chrome 146 trên Windows) để giao tiếp trực tiếp với backend OpenAI (`/backend-api/f/conversation`), tiêu thụ **0 MB Browser RAM** khi vận hành bình thường, tự giải Proof-of-Work SHA3-512 nội bộ, và khóa cứng model flagship **`gpt-5-6-thinking`** (`thinking_effort="extended"`).

- **Vị trí Source Code:** [`/home/light/GitHub/gpt_cli`](file:///home/light/GitHub/gpt_cli)
- **Virtual Environment Python:** `/home/light/GitHub/gpt_cli/.venv/bin/python` (Python 3.14.5)
- **Token Cache:** `~/.local/share/webgpt/profiles/personal/webgpt-token-cache.json` (quyền 0600)
- **Sessions Storage:** `~/.local/share/webgpt/sessions/tool_sessions.json`
- **Telemetry & Gateway Logs:** `~/.local/share/webgpt/logs/` (`live_telemetry.jsonl`, `gateway.log`)

---

## 1. Quick Reference: Lệnh CLI Cơ Bản (Machine-First)

Mọi lệnh CLI đều hỗ trợ cờ `--json` để AI agent dễ dàng parse kết quả.

### Gửi Prompt & Nối tiếp Hội thoại (DAG Continuity)

```bash
# 1. Chạy prompt tiếp nối trong session đang active (tự duy trì parent_message_id)
gpt run -p "Phân tích đoạn mã sau: ..." --json

# 2. Tạo một session mới hoàn toàn với tag đánh dấu
gpt run -p "Nhiệm vụ mới" --new --tag "ctf-solver-1" --json

# 3. Chạy prompt trên một session cụ thể đã có
gpt run -p "Tiếp tục bài trước" -s "sess_e3affde1" --json

# 4. Chỉ định mức độ suy nghĩ (default: extended)
gpt run -p "Câu hỏi khó" -e "extended" --json
```

### Quản lý Session

Tool **chỉ quản lý các session do chính tool tạo** trong `tool_sessions.json`, tuyệt đối không can thiệp hay làm rác các đoạn chat cá nhân khác của người dùng.

```bash
# Xem danh sách session (kèm số turns, tag, ngày cập nhật)
gpt session list --json

# Xem chi tiết một session cụ thể
gpt session show sess_e3affde1 --json

# Đổi session active
gpt session set sess_e3affde1

# Xóa một session
gpt session delete sess_e3affde1
```

### Quản lý Gateway Nền (Tương thích OpenAI API)

Micro Gateway ASGI Starlette chạy ngầm phục vụ chuẩn `/v1/chat/completions`:

```bash
# Khởi động gateway nền (mặc định port 18000)
gpt start --port 18000

# Kiểm tra trạng thái gateway
gpt status --json

# Tắt gateway
gpt stop

# Khởi động lại gateway
gpt restart --port 18000
```

---

## 2. Sử dụng Trực tiếp trong Code Python (Python In-Code)

Khi viết solver script hoặc tool con cần gọi GPT không qua CLI:

```python
import asyncio
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.wire import WireTransport
from gpt_cli.session.registry import SessionRegistry

async def ask_gpt(prompt: str, session_tag: str = "worker"):
    # 1. Đọc token từ cache đĩa an toàn
    cache_mgr = TokenCacheManager("~/.local/share/webgpt/profiles/personal")
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        raise RuntimeError("Token bundle không tồn tại hoặc hết hạn!")

    # 2. Lấy hoặc tạo session
    reg = SessionRegistry()
    sess = reg.get_or_create(tag=session_tag)

    # 3. Gửi turn qua WireTransport (0 MB RAM)
    async with WireTransport(bundle) as transport:
        result = await transport.send_turn(
            prompt=prompt,
            conversation_id=sess.conversation_id,
            parent_id=sess.last_message_id,
            model_alias="gpt-5-6-thinking",
            effort="extended"
        )

    # 4. Cập nhật cây DAG session
    reg.update_turn(
        local_id=sess.local_id,
        conversation_id=result.conversation_id,
        last_message_id=result.message_id,
        model=result.model
    )
    return result.text

# asyncio.run(ask_gpt("Xin chào!"))
```

### Chạy Đa Luồng Song Song (Tối đa 5 Workers)

Hệ thống đã kiểm chứng chịu tải **tối đa 5 workers đồng thời**:

```python
from gpt_cli.pool.worker_pool import WorkerPool

pool = WorkerPool(max_workers=5)

async def worker(prompt):
    async def task():
        # do turn...
        pass
    return await pool.execute(task)
```

---

## 3. Bản đồ Kiến trúc Codebase (Dành cho Agent Sửa Lỗi)

Khi gặp bug hoặc cần nâng cấp tính năng, đọc và sửa đúng file theo ranh giới sau:

| Module / File | Trách nhiệm |
|---|---|
| [`gpt_cli/config/defaults.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/config/defaults.py) | URL endpoints, timeouts, User-Agent, paths mặc định |
| [`gpt_cli/core/token_cache.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/core/token_cache.py) | Đọc/ghi `webgpt-token-cache.json` với phân quyền `0600` |
| [`gpt_cli/models/registry.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/models/registry.py) | Mapping alias model, đảm bảo lock `gpt-5-6-thinking` |
| [`gpt_cli/transport/pow.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/pow.py) | Giải PoW SHA3-512 (18-element fingerprint) & Bootstrap `gAAAAAC...` |
| [`gpt_cli/transport/conduit.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/conduit.py) | Handshake Sentinel prepare & Conduit prepare token |
| [`gpt_cli/transport/wire.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/wire.py) | `curl_cffi` HTTP/2 POST stream, payload builder, auto-retry |
| [`gpt_cli/telemetry/parser.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/telemetry/parser.py) | Bóc tách SSE stream (thoughts, commentary, tools, deltas, done) |
| [`gpt_cli/transport/lifebuoy.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/lifebuoy.py) | Chromium ngủ đông, chỉ mở 3s re-mint credentials khi 403 |
| [`gpt_cli/session/registry.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/session/registry.py) | Quản lý `tool_sessions.json`, lưu cây DAG message |
| [`gpt_cli/pool/worker_pool.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/pool/worker_pool.py) | Giới hạn concurrency `asyncio.Semaphore(5)` |
| [`gpt_cli/server/app.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/server/app.py) | Micro Gateway Starlette tương thích `/v1/chat/completions` |
| [`gpt_cli/cli/main.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/cli/main.py) | Entrypoint CLI `gpt run`, `gpt session`, `gpt start/stop` |

---

## 4. Cẩm nang Chẩn đoán & Tự Sửa Lỗi (Agent Troubleshooting & Self-Healing)

### Lỗi 1: HTTP 403 Forbidden / Token Hết Hạn / Cloudflare Clearance Stale
- **Triệu chứng:** `RuntimeError: ChatGPT Web POST returned HTTP 403: ...` hoặc Sentinel prepare trả về 403.
- **Nguyên nhân:** Token truy cập hết hạn hoặc Cloudflare clearance cookie bị xoay.
- **Cách khắc phục:**
  1. Kích hoạt `BrowserLifebuoy` để tự động mở Chromium ngầm trong 3 giây và re-mint token:
     ```python
     from gpt_cli.core.token_cache import TokenCacheManager
     from gpt_cli.transport.lifebuoy import BrowserLifebuoy
     
     cache_mgr = TokenCacheManager()
     lifebuoy = BrowserLifebuoy(cache_mgr)
     bundle = await lifebuoy.remint_credentials(headless=True)
     ```
  2. Nếu headless thất bại do Cloudflare hCAPTCHA/Turnstile tương tác người dùng, mở trình duyệt bằng script profile để đăng nhập lại:
     ```bash
     cd /home/light/GitHub/gpt_cli
     .venv/bin/python -m gpt_cli.transport.lifebuoy --interactive
     ```

### Lỗi 2: Sentinel Chat-Requirements Prepare Thất Bại (HTTP 400 hoặc No Token)
- **Triệu chứng:** `ProtocolChanged: Sentinel chat-requirements prepare failed`.
- **Nguyên nhân:** OpenAI thay đổi `_SENTINEL_BUILD_HASH` hoặc định dạng fingerprint.
- **Kiểm tra & Sửa chữa:**
  1. Mở file [`gpt_cli/transport/pow.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/pow.py).
  2. Kiểm tra hằng số `_SENTINEL_BUILD_HASH` (ví dụ `prod-a696433ddfe0489db6696cae8c5778c2128f26e8`). Nếu OpenAI deploy build mới, inspect header `oai-client-version` từ traffic web để update hash này.
  3. Đảm bảo User-Agent trong [`defaults.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/config/defaults.py) và Client Hints `sec-ch-ua-platform: "Windows"` hoàn toàn đồng nhất.

### Lỗi 3: Stream Bị Đứt Trước Khi Sinh Xong Text (Empty Response / IncompleteRead)
- **Triệu chứng:** Response trả về rỗng `""` hoặc gặp lỗi `IncompleteRead` / `HTTP/2 stream reset (error 0x2)`.
- **Nguyên nhân:** Server đóng stream sớm hoặc parser bắt nhầm tín hiệu kết thúc từ frame trung gian.
- **Lưu ý đã fix:** 
  - Trong ChatGPT Web, tin nhắn `role: "assistant"` đầu tiên có `content_type: "model_editable_context"` mang `status: "finished_successfully"` nhưng chưa phải text trả lời.
  - Bộ phân tích [`gpt_cli/telemetry/parser.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/telemetry/parser.py) chỉ phát tín hiệu `done` khi:
    - Frame chứa `data: [DONE]`, HOẶC
    - Tin nhắn assistant có `content_type == "text"` và status thuộc `COMPLETION_STATUSES`.
  - [`gpt_cli/transport/wire.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/wire.py) đã trang bị cơ chế tự động thử lại (max 2 retries) khi gặp ngắt kết nối mạng thoáng qua.

### Lỗi 4: Xung đột Port 18000 khi Khởi Động Gateway
- **Triệu chứng:** `gpt start` không bind được port 18000 do tiến trình cũ đang chiếm.
- **Cách khắc phục:**
  ```bash
  # Tìm và kill tiến trình cũ đang chiếm port
  lsof -i :18000
  kill -9 <PID>
  
  # Hoặc khởi động gateway trên port khác:
  gpt start --port 18001
  ```

---

## 5. Quy trình Kiểm thử Toàn diện (Verification Workflow)

Sau khi chỉnh sửa bất kỳ code nào trong repo [`/home/light/GitHub/gpt_cli`](file:///home/light/GitHub/gpt_cli), bắt buộc chạy quy trình xác minh 2 bước:

```bash
cd /home/light/GitHub/gpt_cli

# Bước 1: Chạy toàn bộ Unit Tests (offline)
.venv/bin/pytest tests/ -v

# Bước 2: Chạy kiểm thử Live 5 Workers Concurrency
.venv/bin/pytest tests/test_e2e_live.py -v -s
```
Chỉ coi là hoàn thành khi toàn bộ 22+ tests đều **PASS 100%**.
