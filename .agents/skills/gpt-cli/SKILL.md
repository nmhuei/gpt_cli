---
name: gpt-cli
description: Use when needing to invoke, automate, control, or troubleshoot the local `gpt-cli` tool (ChatGPT-Web Wire Conduit runtime), run prompts with `gpt-5-6-thinking` (0 MB browser RAM), chat interactively with `gpt`, manage tool-specific sessions, start/stop the background gateway (OpenAI & Anthropic API), or diagnose, fix bugs, and improve the codebase at `/home/light/GitHub/gpt_cli`.
---

# GPT-CLI Tool & Maintenance Guide

## Overview

`gpt-cli` là runtime tinh gọn, hiệu năng cao thay thế toàn bộ codebase ChatGPT Web cũ. Tool sử dụng cơ chế **Wire Conduit** qua `curl_cffi` (impersonate Chrome 146 trên Windows) để giao tiếp trực tiếp với backend OpenAI (`/backend-api/f/conversation`), tiêu thụ **0 MB Browser RAM** khi vận hành bình thường, tự giải Proof-of-Work SHA3-512 nội bộ, và khóa cứng model flagship **`gpt-5-6-thinking`** (`thinking_effort="extended"`).

- **Vị trí Source Code:** [`/home/light/GitHub/gpt_cli`](file:///home/light/GitHub/gpt_cli)
- **Virtual Environment Python:** `/home/light/GitHub/gpt_cli/.venv/bin/python` (Python 3.14.5)
- **Launcher trong PATH:** [`/home/light/.local/bin/gpt`](file:///home/light/.local/bin/gpt)
- **Token Cache:** `~/.local/share/webgpt/profiles/personal/webgpt-token-cache.json` (quyền `0600`)
- **Sessions Storage:** `~/.local/share/webgpt/sessions/tool_sessions.json`
- **Granular Session Logs:** `~/.local/share/webgpt/sessions/<session_id>/` (`history.jsonl`, `events.jsonl`)
- **Telemetry & Gateway Logs:** `~/.local/share/webgpt/logs/` (`live_telemetry.jsonl`, `gateway.log`)
- **Systemd User Service:** `webgpt-gateway.service` (chạy micro gateway ASGI trên port 18000)

---

## 1. Quick Reference: Lệnh CLI Cơ Bản (Machine-First & Interactive)

Mọi lệnh CLI đều hỗ trợ cờ `--json` để AI agent dễ dàng parse kết quả.

### Chế độ Chat Tương Tác & Gửi Prompt Trực Tiếp

```bash
# 1. Mở giao diện chat tương tác trực tiếp trong terminal (hỗ trợ /new, /session, /exit)
gpt
# hoặc: gpt chat

# 2. Gửi nhanh một prompt và stream kết quả ngay lập tức
gpt "Xin chào, hãy giải thích ngắn gọn thuật toán RSA"
```

### Gửi Prompt & Nối Tiếp Hội Thoại (DAG Continuity)

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

### Quản Lý Session & Xem Log Debug Phục Vụ Phân Tích

Tool **chỉ quản lý các session do chính tool tạo** trong `tool_sessions.json`, tuyệt đối không can thiệp hay làm rác các đoạn chat cá nhân khác của người dùng.

Mỗi session được lưu trữ tách biệt tại thư mục `~/.local/share/webgpt/sessions/<local_id>/`:
- `history.jsonl`: Lưu toàn bộ lịch sử turn (prompt, response text, model, duration_ms, thinking thoughts, tool calls, error nếu có).
- `events.jsonl`: Lưu chuỗi SSE events dạng thô thời gian thực phục vụ phân tích sâu.

```bash
# Xem danh sách session (kèm số turns, tag, ngày cập nhật)
gpt session list --json

# Xem chi tiết một session cụ thể
gpt session show sess_e3affde1 --json

# Đổi session active
gpt session set sess_e3affde1

# Đọc toàn bộ lịch sử transcript (prompt, response, thinking, duration) của session
gpt session log sess_e3affde1

# Đọc log transcript dạng JSON (cho AI agent parse phân tích khi debug)
gpt session log sess_e3affde1 --json

# Đọc chuỗi telemetry events chi tiết thời gian thực (thoughts, web queries, tool calls)
gpt session log sess_e3affde1 --events

# Chỉ đọc N turns gần nhất hoặc turn cụ thể
gpt session log sess_e3affde1 --tail 3
gpt session log sess_e3affde1 --turn 2

# Xóa một session
gpt session delete sess_e3affde1
```

### Quản Lý Gateway Nền (Tương Thích Cả OpenAI & Anthropic Messages API)

Micro Gateway ASGI Starlette chạy ngầm phục vụ chuẩn:
- `POST /v1/chat/completions` (OpenAI format, hỗ trợ stream)
- `POST /v1/messages` (Anthropic Messages format, hỗ trợ stream)
- `GET /healthz` & `GET /v1/models`

```bash
# Quản lý qua systemd (khuyên dùng vì tự khởi động cùng máy):
systemctl --user status webgpt-gateway.service
systemctl --user restart webgpt-gateway.service
systemctl --user stop webgpt-gateway.service

# Hoặc quản lý trực tiếp bằng CLI:
gpt start --port 18000
gpt status --json
gpt stop
gpt restart --port 18000
```

---

## 2. Sử Dụng Trực Tiếp Trong Code Python (Python In-Code)

Khi viết solver script hoặc tool con cần gọi GPT không qua CLI:

```python
import asyncio
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.wire import WireTransport
from gpt_cli.session.registry import SessionRegistry
from gpt_cli.session.logger import SessionLogManager

async def ask_gpt(prompt: str, session_tag: str = "worker"):
    # 1. Đọc token từ cache đĩa an toàn
    cache_mgr = TokenCacheManager("~/.local/share/webgpt/profiles/personal")
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        raise RuntimeError("Token bundle không tồn tại hoặc hết hạn!")

    # 2. Lấy hoặc tạo session
    reg = SessionRegistry()
    sess = reg.get_or_create(tag=session_tag)
    log_mgr = SessionLogManager()

    # 3. Gửi turn qua WireTransport (0 MB RAM)
    async with WireTransport(bundle) as transport:
        result = await transport.send_turn(
            prompt=prompt,
            conversation_id=sess.conversation_id,
            parent_id=sess.last_message_id,
            model_alias="gpt-5-6-thinking",
            effort="extended"
        )

    # 4. Ghi log turn
    log_mgr.log_turn(
        local_id=sess.local_id,
        turn_index=sess.turns + 1,
        prompt=prompt,
        response=result.text,
        model=result.model,
        duration_ms=result.duration_ms,
        conversation_id=result.conversation_id,
        message_id=result.message_id
    )

    # 5. Cập nhật cây DAG session
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

## 3. Bản Đồ Kiến Trúc Codebase (Dành Cho Agent Sửa Lỗi)

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
| [`gpt_cli/session/logger.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/session/logger.py) | Quản lý lưu log `history.jsonl` và `events.jsonl` theo session |
| [`gpt_cli/pool/worker_pool.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/pool/worker_pool.py) | Giới hạn concurrency `asyncio.Semaphore(5)` |
| [`gpt_cli/server/app.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/server/app.py) | Micro Gateway Starlette tương thích `/v1/chat/completions` & `/v1/messages` |
| [`gpt_cli/cli/main.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/cli/main.py) | Entrypoint CLI `gpt`, `gpt run`, `gpt session`, `gpt start/stop` |

---

## 4. Cẩm Nang Chẩn Đoán & Tự Sửa Lỗi (Agent Troubleshooting & Self-Healing)

### Lỗi 1: HTTP 401 Unauthorized / HTTP 403 Forbidden (BẮT BUỘC KIỂM TRA LOGIC)
- **Quy tắc cốt lõi:** Tài khoản người dùng **KHÔNG CÓ GIỚI HẠN QUOTA THÔNG THƯỜNG / KHÔNG CÓ CHUYỆN HẾT HẠN TOKEN TỰ NHIÊN**.
- **Triệu chứng:** `RuntimeError: ChatGPT Web POST returned HTTP 401/403` hoặc Sentinel prepare trả về 401/403.
- **Nguyên nhân thực tế (99% là lỗi logic giao thức):**
  1. *Cookie formatting:* Chuỗi header `Cookie` bị thiếu các trường thiết yếu (`cf_clearance`, `__Secure-next-auth.session-token`, `_puid`).
  2. *Header `ChatGPT-Account-ID`:* Bị rỗng hoặc mang ID không khớp với token.
  3. *Sentinel & PoW:* Hash build `_SENTINEL_BUILD_HASH` lỗi thời, hoặc seed/difficulty SHA3-512 bị giải sai, format prefix `gAAAAAB...` không chuẩn.
  4. *Device ID Mismatch:* `oai-device-id` bị lệch giữa Sentinel handshake và endpoint `/backend-api/f/conversation`.
  5. *TLS ClientHello / User-Agent:* Fingerprint `curl_cffi` bị lệch với User-Agent Windows.
- **Cách xử lý chuẩn (Engineering First):**
  1. **Bước 1 (Ưu tiên số 1):** In toàn bộ headers, cookies, và payload request ra để kiểm tra định dạng logic trong [`gpt_cli/transport/conduit.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/conduit.py) và [`gpt_cli/transport/wire.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/wire.py).
  2. **Bước 2:** Kiểm tra hạn JWT `accessToken` và thử tự động refresh qua token cache API.
  3. **Bước 3 (Chỉ dùng khi Cloudflare Turnstile tương tác thật sự):** Nếu và chỉ nếu Cloudflare chặn IP hoặc yêu cầu giải captcha tương tác người dùng, mới sử dụng Browser để đồng bộ lại clearance:
     ```bash
     .venv/bin/python -m gpt_cli.transport.lifebuoy --interactive
     ```

### Lỗi 2: HTTP 404 Not Found Trên URL `http://127.0.0.1:18000/v1/messages`
- **Triệu chứng:** `HTTPStatusError: Client error '404 Not Found' for url 'http://127.0.0.1:18000/v1/messages'`
- **Nguyên nhân:** Tool cũ hoặc tiến trình gateway cũ (ở repo `/home/light/GitHub/gpt`) đang chạy chiếm cổng 18000 thay vì repo mới `gpt_cli`.
- **Cách khắc phục:**
  ```bash
  # 1. Khởi động lại service gateway qua systemd:
  systemctl --user daemon-reload
  systemctl --user restart webgpt-gateway.service

  # 2. Kiểm tra lại launcher:
  which gpt # Đảm bảo trỏ về /home/light/.local/bin/gpt thực thi gpt_cli
  ```

### Lỗi 3: Sentinel Chat-Requirements Prepare Thất Bại (HTTP 400 hoặc No Token)
- **Triệu chứng:** `ProtocolChanged: Sentinel chat-requirements prepare failed`.
- **Nguyên nhân:** OpenAI thay đổi `_SENTINEL_BUILD_HASH` hoặc định dạng fingerprint.
- **Kiểm tra & Sửa chữa:**
  1. Mở file [`gpt_cli/transport/pow.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/pow.py).
  2. Kiểm tra hằng số `_SENTINEL_BUILD_HASH` (ví dụ `prod-a696433ddfe0489db6696cae8c5778c2128f26e8`). Nếu OpenAI deploy build mới, inspect header `oai-client-version` từ traffic web để update hash này.
  3. Đảm bảo User-Agent trong [`defaults.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/config/defaults.py) và Client Hints `sec-ch-ua-platform: "Windows"` hoàn toàn đồng nhất.

### Lỗi 4: Stream Bị Đứt Trước Khi Sinh Xong Text (Empty Response / IncompleteRead)
- **Triệu chứng:** Response trả về rỗng `""` hoặc gặp lỗi `IncompleteRead` / `HTTP/2 stream reset (error 0x2)`.
- **Nguyên nhân:** Server đóng stream sớm hoặc parser bắt nhầm tín hiệu kết thúc từ frame trung gian.
- **Lưu ý đã fix:** 
  - Trong ChatGPT Web, tin nhắn `role: "assistant"` đầu tiên có `content_type: "model_editable_context"` mang `status: "finished_successfully"` nhưng chưa phải text trả lời.
  - Bộ phân tích [`gpt_cli/telemetry/parser.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/telemetry/parser.py) chỉ phát tín hiệu `done` khi:
    - Frame chứa `data: [DONE]`, HOẶC
    - Tin nhắn assistant có `content_type == "text"` và status thuộc `COMPLETION_STATUSES`.
  - [`gpt_cli/transport/wire.py`](file:///home/light/GitHub/gpt_cli/gpt_cli/transport/wire.py) đã trang bị cơ chế tự động thử lại (max 2 retries) khi gặp ngắt kết nối mạng thoáng qua.

---

## 5. Quy Trình Kiểm Thử Toàn Diện (Verification Workflow)

Sau khi chỉnh sửa bất kỳ code nào trong repo [`/home/light/GitHub/gpt_cli`](file:///home/light/GitHub/gpt_cli), bắt buộc chạy quy trình xác minh:

```bash
cd /home/light/GitHub/gpt_cli

# Bước 1: Chạy toàn bộ Unit Tests (offline)
.venv/bin/pytest tests/ -v

# Bước 2: Chạy kiểm thử Live 5 Workers Concurrency
.venv/bin/pytest tests/test_e2e_live.py -v -s
```
Chỉ coi là hoàn thành khi toàn bộ 45+ tests đều **PASS 100%**.

---

## 6. Hợp Nhất Với DeepSeek Harness (`@deepseek-ai/dsh`) & Agent Harnesses

`gpt_cli` đóng vai trò là backend suy luận cục bộ siêu tốc (0 MB browser RAM) cho **DeepSeek Harness** (`dsh`) và các agent harness chuẩn Anthropic Messages API / OpenAI Chat Completions.

### 6.1. Đặc Tính Kỹ Thuật Nổi Bật:
1. **Native Chain-of-Thought (CoT) Không Ép Buộc Prompt:**
   - Hoàn toàn không nhồi prompt template XML/JSON làm hỏng logic CoT tự nhiên của `gpt-5-6-thinking`.
   - Streaming chuẩn Anthropic Messages: phát trực tiếp block `thinking` (`thinking_delta`) để DeepSeek Harness hiển thị khung suy luận gập mở (`dsh: reasoning:`).
2. **Bộ Phân Giải Tên Tool Trong Suốt (`_resolve_tool_name`):**
   - Tự động chuyển đổi các namespace tool của ChatGPT Web (`functions.exec`, `api_tool.call_tool (health_check)`) sang đúng tên tool harness đăng ký (`bash`, `health_check`, `execute_command`...) và unwrap tham số lồng nhau.
3. **Duy Trì Hội Thoại Đa Lượt Cây DAG (Native Session Continuity):**
   - Tự động bắt `x-deepseek-harness-session-id` từ DeepSeek Harness.
   - Lượt 1 khởi tạo conversation; các lượt sau chỉ gửi đúng tin nhắn mới nhất (`messages[-1]`) gắn với `parent_id`, tiết kiệm 95% token và giữ trọn bộ nhớ hội thoại.

### 6.2. Các Lệnh Khởi Chạy DeepSeek Harness:

```bash
# Thiết lập biến môi trường trỏ về gateway gpt_cli
export DEEPSEEK_BASE_URL="http://127.0.0.1:18000"
export DEEPSEEK_API_KEY="sk-webgpt-local"

# 1. Chế độ dòng lệnh đơn lượt (Headless)
npx @deepseek-ai/dsh headless "mcp còn sống k"

# 2. Chế độ Terminal Tương Tác (TUI)
npx @deepseek-ai/dsh tui

# 3. Chế độ Giao Diện Web (Web UI trên cổng 3080)
npx @deepseek-ai/dsh web --no-open --port 3080
```

