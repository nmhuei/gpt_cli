# Core Operating Rules & Engineering Principles

## 1. Nguyên Tắc Xác Thực & Xử Lý Lỗi 401/403 (Token & Auth Invariants)
- **Tài khoản không bao giờ hết hạn quota thông thường:** Tài khoản của người dùng không có giới hạn quota thông thường.
- **401/403 là LỖI LOGIC GIAO THỨC (99% trường hợp):**
  Khi gặp mã lỗi `HTTP 401 Unauthorized` hoặc `HTTP 403 Forbidden`, **tuyệt đối không được kết luận do "hết token"** và không được vội vàng kích hoạt headless browser bừa bãi.
  Nguyên nhân thực tế luôn nằm ở lỗi logic giao tiếp wire:
  1. *Cookie formatting:* Chuỗi cookie bị thiếu `cf_clearance`, `__Secure-next-auth.session-token`, hoặc `_puid`.
  2. *Header `ChatGPT-Account-ID`:* Bị thiếu, rỗng, hoặc lệch workspace ID giữa personal và workspace.
  3. *Sentinel & PoW Logic:* `_SENTINEL_BUILD_HASH` hoặc `oai-client-version` bị backend OpenAI cập nhật; hoặc kết quả giải SHA3-512 bị sai lệch format fingerprint / seed.
  4. *Device ID Mismatch:* `oai-device-id` không đồng bộ giữa header Sentinel và header SSE stream.
  5. *TLS / Impersonation:* Fingerprint của `curl_cffi` bị lệch so với User-Agent khai báo.
  6. *Access Token Expiration:* JWT access token 1-giờ hết hạn nhưng cơ chế refresh token nội bộ chưa tự kích hoạt.
- **Hành động bắt buộc:** Khi gặp 401/403, phải rà soát và kiểm tra kỹ toàn bộ logic request headers, cookie formatting, PoW handshake, và JWT expiry trước tiên để sửa triệt để mã nguồn.

## 2. Phân Định Rõ Ràng Codex CLI vs GPT-CLI
- **Codex CLI (`/home/light/.local/bin/codex`):**
  - Đóng vai trò **Chuyên gia Cố vấn Kiến trúc & Logic (Read-Only Advisor)**.
  - Tuyệt đối **không yêu cầu Codex code** hoặc sinh code trực tiếp.
- **GPT-CLI (`/home/light/GitHub/gpt_cli`):**
  - Mã nguồn sản phẩm runtime Python của hệ thống.
  - Giao tiếp trực tiếp với backend ChatGPT Web qua Wire Conduit HTTP/2 SSE (0 MB Browser RAM).
- **Antigravity (AI Assistant):**
  - Kỹ sư lập trình chịu trách nhiệm triển khai mã nguồn, sửa bug, viết test, và tối ưu hóa hệ thống.

## 3. Tránh Over-Engineering & Chỉ Giải Quyết Lỗi Thực Tế
- **LoopGuard (Trajectory Hashing):**
  - Trong thực tế vận hành `gpt-cli`, chưa từng có hiện tượng model kẹt vòng lặp vô hạn hay ảo giác lặp lệnh liên tục. Các turn trước đây bị nhầm là lặp do harness ngắt sớm hoặc session bị khởi động lại.
  - **Quy tắc:** Không vội vã triển khai các hệ thống LoopGuard / Trajectory Hashing phức tạp gây over-engineering và phình to codebase khi chưa có bằng chứng lỗi thật.
- **Quản Lý Context Window & DAG:**
  - `gpt-cli` sử dụng cơ chế Server-Side DAG (`conversation_id`, `parent_message_id`), client không gửi lại toàn bộ lịch sử 50 turns.
  - Hiện tượng phình context chủ yếu do bùng nổ output của tool (lệnh bash in hàng nghìn dòng). Giải pháp ưu tiên là cắt gọt, vệ sinh đầu vào tool (Tool Output Hygiene) và sử dụng State Checkpointing khi cần, không can thiệp thô bạo vào cây DAG nếu không có nhu cầu.

## 4. Môi Trường Thử Nghiệm & Test Cô Lập (Dành Riêng Cho Antigravity AI Assistant)
- **Bắt buộc tạo thư mục riêng trong `~/Downloads` khi test:**
  Khi Antigravity cần chạy thử nghiệm (như test CTF, viết script test tạm thời, tạo POC exploit, sinh mock/dummy data, dựng môi trường sandbox thử nghiệm...):
  - **Bắt buộc phải tạo một folder riêng biệt bên trong thư mục `~/Downloads`** (ví dụ: `~/Downloads/test_<tên_nhiệm_vụ>/` hoặc `~/Downloads/ctf_test_<tên_challenge>/`) và thực thi toàn bộ thao tác thử nghiệm tại đó.
  - **Tuyệt đối không tạo file/thư mục rác thử nghiệm trực tiếp tại thư mục gốc của repository** (`/home/light/GitHub/gpt`, `/home/light/GitHub/gpt_cli`, hoặc `~`), nhằm giữ sạch git status và tránh làm ô nhiễm workspace chính.
  - **Phạm vi áp dụng:** Quy tắc này là chỉ thị vận hành dành riêng cho Antigravity (AI Assistant), không phải logic nội bộ của sản phẩm runtime `gpt-cli`.
