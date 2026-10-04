# GPT-CLI: vận hành agent 60 phút

## Mục tiêu đo được

Một lượt 60 phút là **thời lượng upstream nhìn thấy được** trong cùng local
session và cùng server-side DAG; không phải thời lượng Chain-of-Thought nội
bộ. Báo cáo tối thiểu gồm `session_id`, tổng `duration_ms`, số turn, số tool
call, trạng thái SSE recovery, và bằng chứng đầu ra.

Ví dụ live đã ghi nhận: một session có 105 turn, 4,506,966 ms và 587 tool
calls. Mốc 60 phút được xác nhận bằng history local, không suy từ thời gian
wall clock của terminal.

## Baseline failures đã quan sát

| Áp lực | Hành vi cũ | Kết luận vận hành |
|---|---|---|
| Turn kết thúc sớm | Client trước đây có thể coi SSE reset là failure | Phải để DAG recovery settle trước khi kết luận lỗi. |
| Chạy liên tục | `gpt agent` chỉ nối bằng prompt `continue` | Thời lượng không chứng minh tiến triển hay solved. |
| Token `...`, `???`, `>>>>` | 9--16 giây/turn, 0 tool-call | Token ký hiệu không phải steering đáng tin cậy khi model mất hướng. |
| BR có full host access | Model tìm được workspace gốc có `solver/result.json` | Prompt cấm đọc không phải sandbox; benchmark đã bị nhiễm phải bỏ. |
| Candidate flag | Model có thể suy đoán từ artifact/ghi chú cũ | Không submit hoặc đánh dấu solved khi chưa có verifier độc lập. |

## Quy trình 60 phút

1. Tạo **workspace sạch**, chỉ có đề và artifact nguồn. Nếu BR nhìn thấy cả
   home directory thì workspace chưa cô lập; phải dùng boundary cấp filesystem
   hoặc container riêng. Không để `solver/`, `result.json`, writeup hay flag
   chuẩn trong vùng agent đọc được.
2. Tạo session mới và lưu local id ngay từ đầu. Một challenge chỉ có một DAG
   hợp lệ; nếu contamination xảy ra, hủy session đó và bắt đầu session sạch.
3. Gửi prompt mở đầu ngắn nhưng có nghĩa và chỉ rõ absolute path, kết quả cần
   kiểm chứng và BR. Prompt hành động rõ ràng gọi tool tốt hơn token ký hiệu.
4. Chạy 30 phút trước, sau đó resume **đúng local session** thêm 30 phút:

   ```bash
   gpt agent -b br -p "solve /absolute/path"
   gpt agent --resume-session sess_x -b br --min-runtime-minutes 30 -p "continue"
   ```

5. Sau mỗi chặng, đọc log; không nhìn transcript chữ đơn thuần:

   ```bash
   gpt session log sess_x --json | jq '{turns:(.history|length), duration_ms:([.history[].duration_ms]|add), tool_calls:([.history[].tool_calls|length]|add)}'
   ```

6. Với SSE reset, chờ `Recovering completed server activity` và kiểm tra node
   mới trong history. Không restart hoặc tạo session khác khi backend vẫn active.
7. Với steer, target chính xác live local session:

   ```bash
   gpt steer --resume-session sess_x -p "Use BR tools; verify the next hypothesis."
   ```

   `steered` nghĩa là native async steer được nhận; `queued` nghĩa là bền vững
   và sẽ chạy ở parent DAG kế tiếp. Cả hai không tự chứng minh model đã làm việc.

## Kiểm tra tiến triển

Một turn có ích phải tạo ít nhất một trong các dấu vết sau:

- tool call với output mới;
- artifact/script mới trong workspace sạch;
- hypothesis có lệnh kiểm chứng và kết quả;
- verifier độc lập pass.

Hai turn liên tiếp chỉ có prose, không có tool/evidence mới, là **stalled**,
không phải “agent đang suy nghĩ”. Dừng/chờ steer thay vì tiếp tục vô hạn. Đây
là quy tắc vận hành hiện tại; progress guard tự động chưa được implement.

## Định nghĩa kết thúc

`solved` chỉ hợp lệ khi candidate khớp format **và** có verifier độc lập hoặc
phản hồi submit được người vận hành cho phép. Không auto-submit. `blocked` chỉ
được dùng sau khi log nêu rõ evidence đã thử, điều kiện thiếu, và steer tiếp
theo cần thiết.

## Prompt lessons

- Dùng câu ngắn có nghĩa: `Use BR tools. Solve <goal>.`
- `continue` giữ workflow khi turn trước đã có tool/evidence.
- Không dùng `...`, `???`, `>>>>` làm cơ chế tự trị chính; chúng đã tạo các
  lượt không tool-call trong test live.
- Không yêu cầu hiển thị CoT. Có thể quan sát summary/tool event và duration,
  nhưng không đo được hidden reasoning tokens.

## Giới hạn hiện tại

Transport, DAG, SSE recovery và BR attachment đã hoạt động. Nhưng runtime chưa
có policy sandbox, progress detector, candidate verifier hay state machine
`working -> evidence -> verified -> solved`. Vì vậy nó là agent có giám sát,
chưa phải autonomous solver an toàn.
