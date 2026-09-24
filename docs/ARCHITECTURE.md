# Spec Thiết kế Kiến trúc: WebGPT Remake (Smart Hybrid Core)
Ngày: 2026-09-24  
Trạng thái: Đã xác thực thực nghiệm (Validated Live)

---

## 1. Mục tiêu Kiến trúc & Triết lý Tinh Gọn
Remake toàn bộ codebase `gpt` (vốn đang cồng kềnh với hơn 120 files và nhiều nợ kỹ thuật) thành một công cụ mới tinh gọn, chuyên biệt, theo triết lý Unix ("Do one thing and do it well"):
- **Tiêu thụ tài nguyên tối thiểu**: Giảm RAM từ ~1GB xuống ~35MB - 50MB trong trạng thái hoạt động thông thường.
- **Tốc độ phản hồi cực nhanh**: TTFT và streaming trực tiếp qua HTTP client thuần trong ~5-6s/turn (thay vì 10-15s qua DOM).
- **Khóa cứng Model Flagship**: Đảm bảo 100% request chạy trên mô hình mạnh nhất (`GPT-5.6 Sol Thinking High`), ngăn chặn hoàn toàn rủi ro Downgrade về `5.5-mini`.
- **Cơ chế Phao cứu sinh (Failover Resilience)**: Vừa có tốc độ của HTTP client thuần, vừa có sự bất tử của Browser trước Cloudflare WAF.
- **Không phát minh lại bánh xe**: Phân định ranh giới rõ ràng với BQA (`botquanganh_mcp`).

---

## 2. Ranh giới Hệ Thống: BQA (Executor) vs. Tool Remake (Monitor & Gateway)

Hệ thống được tổ chức thành 2 thành phần độc lập, tương hỗ hoàn hảo:

```text
                  ┌──────────────────────────────────────────────┐
                  │            OPENAI CHATGPT WEB                │
                  │   (Model GPT-5.6 Sol Extended Thinking)      │
                  └───────────────┬──────────────────────────────┘
                                  │
         ┌────────────────────────┴────────────────────────┐
         │                                                 │
(1) Đường Stream SSE / Socket                      (2) Đường Tool Execution
         │                                                 │
         ▼                                                 ▼
┌─────────────────────────────────┐               ┌─────────────────────────────────┐
│       TOOL REMAKE MỚI           │               │       BQA (botquanganh_mcp)     │
│   (THE MONITOR & GATEWAY)       │               │          (THE EXECUTOR)         │
├─────────────────────────────────┤               ├─────────────────────────────────┤
│ • Transport: curl_cffi Wire     │               │ • Nhận tool call từ Cloudflare  │
│   (0 MB RAM, không mở browser)  │               │ • Chạy lệnh thật trên Linux     │
│ • Quản lý Token & Session:      │               │ • Đọc / ghi file thật           │
│   Tự refresh, chống 403         │               │ • Trả output về cho OpenAI      │
│ • LIVE MONITOR (Sniffer):       │               │                                 │
│   Bắt từng frame SSE, soi       │               │                                 │
│   prompt, suy nghĩ, từ khóa     │               │                                 │
│ • WATCHDOG: Phát hiện kẹt lệnh, │               │                                 │
│   session chết, nghẽn mạng      │               │                                 │
└─────────────────────────────────┘               └─────────────────────────────────┘
```

### 2.1. BQA (`botquanganh_mcp`) — Vai trò: The Host Executor
- Đã có sẵn plugin kết nối với ChatGPT Web, Cloudflare tunnel, logic sandbox, policy check và chat workspace.
- Chịu trách nhiệm 100% việc nhận lệnh từ OpenAI, chạy lệnh shell/file trên máy tính và trả output về.
- **Quy tắc thiết kế**: Tool Remake **TUYỆT ĐỐI KHÔNG** làm lại tính năng chạy lệnh của BQA.

### 2.2. Tool Remake — Vai trò: The Observer & Gateway
- Chuyên tâm làm "Mắt thần" (Monitor/Sensor) và "Cầu nối" (Gateway):
  1. Duy trì session ChatGPT Web luôn sống bằng `curl_cffi` Wire Conduit (0 MB browser).
  2. Bắt sống (sniff) toàn bộ gói tin SSE từ socket backend của GPT để hiển thị telemetry.
  3. Giám sát sức khỏe phiên (Watchdog), phát hiện nghẽn mạch hoặc kẹt lệnh.
  4. Cung cấp API chuẩn OpenAI (`/v1/chat/completions`) và Claude (`/v1/messages`) cho các client khác (như Claude Code).
- **Loại bỏ khỏi codebase cũ**: Xóa sạch `gpt/agent/runner.py`, `gpt/tools/process.py`, `gpt/tools/patch.py`, `gpt/tools/shell.py`. Giảm 70% dung lượng mã nguồn thừa.

---

## 3. Lõi Vận hành: Smart Hybrid Architecture

```text
                  [Nhận Request Chat]
                           │
                           ▼
          Kiểm tra Token Cache & Gửi curl_cffi
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
        [Thành công]                 [Gặp lỗi 403]
    (95% các trường hợp)                 │
    Streaming trả về tức thì             ▼
    Browser ngủ đông 0MB          Thử Refresh Token 1 lần
                                         │
                           ┌─────────────┴─────────────┐
                           ▼                           ▼
                     [curl_cffi hết 403]       [Vẫn dính 403 do IP]
                     Tiếp tục bình thường              │
                                                       ▼
                                         CHUYỂN HẲN SANG BROWSER RUNNER
                                         (Chạy trực tiếp qua Chromium)
                                         → Đảm bảo công việc KHÔNG gián đoạn
```

### 3.1. Tuyến chính (Primary Route): Pure `curl_cffi` Wire + Conduit Prepare
- **Cơ chế**:
  1. Đọc credentials từ file cache đĩa bảo mật `0600` (`webgpt-token-cache.json`): `accessToken`, `cookies` (`cf_clearance`, session), `oai_device_id`.
  2. Bắt tay Conduit:
     - `POST /backend-api/sentinel/chat-requirements/prepare`
     - Giải SHA3-512 PoW bằng Python thuần (`solve_sentinel_pow` đạt 1.65M hash/s, mất ~0.19s).
     - `POST /backend-api/f/conversation/prepare` (với 15-field context body) để lấy `conduit_token`.
  3. Bắn `POST /backend-api/f/conversation` mang header `X-Conduit-Token` và consume SSE stream UTF-8.
- **Thực nghiệm chứng thực**: Đạt 200 OK trong 5.84s, trả về đúng `GPT-5.6 Sol`, không mở trình duyệt (0 MB RAM).

### 3.2. Tuyến phụ (Secondary / Failover Route): Browser Lifebuoy
- **Cơ chế**:
  - Khi token hết hạn (TTL > 10 ngày): Tự động đánh thức CloakBrowser trong 3-5s để gọi `/api/auth/session` lấy token mới rồi tắt ngay.
  - Khi IP bị Cloudflare WAF nghi ngờ (đòi hỏi Web Crypto HMAC `x-oai-is-client-observation` từ DOM thật): Tự động định tuyến turn đó sang Chromium Fast-Dispatch để gõ trực tiếp qua `#prompt-textarea`, đảm bảo không bao giờ bị đứt quãng luồng làm việc.

---

## 4. Khóa Model & Chống Downgrade
- **Canonical Model**: `gpt-5-6-thinking`.
- **Model Router**: Tự động chuyển đổi các alias (`5.6`, `sol`, `5.6 sol`, `gpt-5`, `thinking`, `default`) thành `gpt-5-6-thinking`.
- **Effort Lock**: Tự động ghim `thinking_effort="extended"` (tương đương `High 3 of 3`).
- **Downgrade Validator**: Kiểm tra `model_slug` trong SSE stream response. Báo lỗi và từ chối nếu backend OpenAI cố tình chuyển luồng về model mini.

---

## 5. Live Stream Telemetry & In-Flight Action Interceptor

Module Telemetry đóng vai trò bắt trọn gói các sự kiện từ dòng SSE của socket backend GPT theo thời gian thực (thực nghiệm xác nhận đạt độ trễ mili-giây):

### 5.1. Bắt các trạng thái nội tại của Model:
- **Thinking / Reasoning**: Nhận diện `content_type: "thoughts"` hoặc `channel: "thought"` -> Xuất nội dung CoT summary theo thời gian thực.
- **Commentary**: Nhận diện `channel: "commentary"` -> Xuất lời thoại định hướng của model trước khi chạy tool hoặc sinh output.
- **Web Search**: Nhận diện `recipient: "web.run"` -> Bóc tách query tìm kiếm trong `metadata.search_model_queries.queries` ngay tại thời điểm GPT vừa hình thành từ khóa.
- **Tool Invocation**: Nhận diện frame gọi plugin/tool (`recipient: "functions.exec"` hoặc `host_run_command`).

### 5.2. Chuẩn hóa Event Output (Telemetry Contract):
Ngay khi bắt được frame gọi tool, module tự động phát ra JSON chuẩn hóa:

```json
{
  "timestamp": "2026-09-24T18:29:16.120456+07:00",
  "source": "gpt_socket",
  "tool": "host_run_command",
  "command": "printf 'kernel='; uname -r; printf 'arch='; uname -m; printf 'pwd='; pwd",
  "cwd": "Downloads/bqa",
  "phase": "started"
}
```

Và khi tool hoàn thành / stream kết thúc:
```json
{
  "timestamp": "2026-09-24T18:29:16.145890+07:00",
  "source": "gpt_socket",
  "tool": "host_run_command",
  "duration_ms": 25,
  "exit_code": 0,
  "phase": "completed"
}
```

---

## 6. Liveness, Health & Dead-Session Watchdog

Watchdog bảo vệ phiên làm việc khỏi tình trạng treo im lặng (Silent Hangs):
- **Stalled Stream Timeout**: Nếu sau khi gửi request hoặc sau một frame mà socket không có bất kỳ byte mới nào quá 30 giây -> Kích hoạt cảnh báo socket stall, chủ động reconnect.
- **In-Flight Tool Hang**: Nếu phát hiện `phase: "started"` của một tool call nhưng quá 120 giây không có sự kiện tiếp theo từ server -> Cảnh báo lệnh host đang bị chặn bởi prompt interactive hoặc vòng lặp vô hạn.
- **Context Size Guard**: Giám sát độ dài ngữ cảnh (`conversation_chars`), cảnh báo trước khi đạt ngưỡng tràn token của ChatGPT Web.
