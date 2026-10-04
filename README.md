# gpt-cli: High-Performance Headless Wire Runtime & Telemetry Monitor

Runtime tinh gọn, chuyên biệt, tốc độ cao được xây dựng lại từ đầu cho ChatGPT Web (`gpt-5-6-thinking`), hỗ trợ chế độ Headless / Machine-First dành riêng cho AI điều khiển.

---

## 1. Tính Năng Cốt Lõi

- **0 MB Browser RAM (Wire Conduit)**: Giao tiếp trực tiếp qua kết nối HTTP/2 TLS impersonate bằng `curl_cffi`, giải thuật toán PoW SHA3-512 nội bộ bằng Python thuần.
- **Failover Browser Lifebuoy**: chỉ là phương án cuối khi JWT thực sự thiếu/hết hạn; lỗi 401/403 phải được chẩn đoán cookie, workspace, Sentinel/PoW, device ID và TLS trước.
- **Khóa Cứng Flagship Model**: Ghim chặt `gpt-5-6-thinking` với `thinking_effort="extended"` (High Thinking 3/3). Tuyệt đối ngăn chặn downgrade về mini.
- **Hỗ trợ tối đa 5 Workers Song Song**: Đạt 100% throughput đồng thời trên 5 session khác nhau mà không bị dính rate-limit 429.
- **Live Stream Telemetry**: Bóc tách trực tiếp từng frame SSE từ socket (Thinking reasoning, Web Search queries, Tool invocations) xuất ra `live_telemetry.jsonl`.
- **Headless Session Management**: mỗi prompt mặc định tạo local session/remote conversation mới; chỉ `--resume` hoặc `--resume-session` mới tiếp tục DAG đã có.
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

# Mỗi prompt mặc định là session/conversation mới
gpt run --tag "task-1" --prompt "..." --json

# Tiếp tục active session hoặc một session cụ thể
gpt run --resume --prompt "Tiếp tục kiểm tra kết quả"
gpt run --resume-session sess_8f3a12 --prompt "Tiếp tục bài trước" --json

# Thêm chỉ dẫn vào session đang thực sự chạy.
# Nếu upstream không có native async steer, nó được xếp FIFO sau turn hiện tại.
gpt steer "Ưu tiên chạy test trước"
gpt steer --resume-session sess_8f3a12 --json "Không sửa file cấu hình"

# Chọn native ChatGPT connector mention; đây không provision MCP connector.
gpt run --bridge br "Kiểm tra dịch vụ"
gpt run --no-bridge "Trả lời thuần văn bản"

# Tạo ảnh và tự lưu artifact vào ~/Pictures/gpt-images/
gpt image -p "A blue bird perched on a moonlit branch"
gpt image -p "A blue bird perched on a moonlit branch" --json

# Xem danh sách session do tool tạo ra
gpt session list --json
gpt session new --tag "ctf:challenge-1" --json

# Quản lý Gateway nền (cổng 18000)
gpt start
gpt status
gpt stop
```

---

## 4. Benchmark lifecycle

Benchmark deterministic không gọi network, ghi cả JSON và Markdown để so sánh các thay đổi lifecycle:

```bash
python benchmarks/session_lifecycle.py --output ~/Downloads/test_gpt_cli_benchmark
```

Nó đo 5 fresh turns chạy song song, DAG-resume fixture, thứ tự steer FIFO và invariant không gọi Browser Lifebuoy. Đây là benchmark cơ chế nội bộ; không được diễn giải như latency mạng ChatGPT thực tế.

---

## 5. Tài Liệu Kỹ Thuật

- [Bản Thiết Kế Kiến Trúc Chi Tiết](docs/ARCHITECTURE.md)
- [Kế Hoạch Thi Công Từng Task](docs/IMPLEMENTATION_PLAN.md)
