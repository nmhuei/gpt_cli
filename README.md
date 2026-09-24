# gpt-cli: High-Performance Headless Wire Runtime & Telemetry Monitor

Runtime tinh gọn, chuyên biệt, tốc độ cao được xây dựng lại từ đầu cho ChatGPT Web (`gpt-5-6-thinking`), hỗ trợ chế độ Headless / Machine-First dành riêng cho AI điều khiển.

---

## 1. Tính Năng Cốt Lõi

- **0 MB Browser RAM (Wire Conduit)**: Giao tiếp trực tiếp qua kết nối HTTP/2 TLS impersonate bằng `curl_cffi`, giải thuật toán PoW SHA3-512 nội bộ bằng Python thuần.
- **Failover Browser Lifebuoy**: Chromium chỉ được đánh thức trong 3-5s khi gặp HTTP 403 để làm mới token rồi ngủ đông ngay lập tức.
- **Khóa Cứng Flagship Model**: Ghim chặt `gpt-5-6-thinking` với `thinking_effort="extended"` (High Thinking 3/3). Tuyệt đối ngăn chặn downgrade về mini.
- **Hỗ trợ tối đa 5 Workers Song Song**: Đạt 100% throughput đồng thời trên 5 session khác nhau mà không bị dính rate-limit 429.
- **Live Stream Telemetry**: Bóc tách trực tiếp từng frame SSE từ socket (Thinking reasoning, Web Search queries, Tool invocations) xuất ra `live_telemetry.jsonl`.
- **Headless Session Management**: Quản lý cây hội thoại riêng của tool (`tool_sessions.json`), tự động duy trì ngữ cảnh cho các lượt chat nối tiếp.
- **Tách Biệt Hoàn Toàn với BQA**: BQA (`botquanganh_mcp`) đảm nhiệm 100% việc thực thi lệnh host qua Cloudflare; `gpt_cli` chỉ làm nhiệm vụ Gateway, Monitor và Session Keeper.

---

## 2. Cấu Trúc Thư Mục

```text
gpt_cli/
├── docs/               # Bản đặc tả kiến trúc & kế hoạch thi công
├── gpt_cli/
│   ├── config/         # Cấu hình & biến môi trường
│   ├── models/         # Model registry (khóa model & chống hardcode)
│   ├── core/           # Token cache (0600) & domain types
│   ├── transport/      # Lõi mạng Wire Conduit, PoW, Lifebuoy
│   ├── telemetry/      # Live stream telemetry & socket sniffer
│   ├── session/        # Quản lý cây hội thoại cho AI (tool_sessions.json)
│   ├── pool/           # Semaphore worker pool (tối đa 5 workers)
│   ├── server/         # Micro Gateway API (/v1/chat/completions)
│   └── cli/            # Headless CLI entrypoint (gpt run, gpt session)
├── tests/              # Bộ test TDD tự động
└── benchmarks/         # Kịch bản benchmark 5 workers song song
```

---

## 3. Cách Sử Dụng Cho AI / Scripts

```bash
# Chạy prompt nhanh và nhận JSON output
gpt run --prompt "Phân tích file..." --json

# Tạo session mới hoàn toàn
gpt run --new --tag "task-1" --prompt "..." --json

# Tiếp tục session cụ thể
gpt run --session sess_8f3a12 --prompt "..." --json

# Xem danh sách session do tool tạo ra
gpt session list --json

# Quản lý Gateway nền (cổng 18000)
gpt start
gpt status
gpt stop
```

---

## 4. Tài Liệu Kỹ Thuật

- [Bản Thiết Kế Kiến Trúc Chi Tiết](docs/ARCHITECTURE.md)
- [Kế Hoạch Thi Công Từng Task](docs/IMPLEMENTATION_PLAN.md)
