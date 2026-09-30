---
name: gpt
description: Drive the local `gpt` CLI command — ChatGPT-Web Wire Conduit runtime (0 MB browser RAM) with `gpt-5-6-thinking`. Use when the user asks how to run `gpt`, chat interactively, start or stop the local gateway, run prompts via CLI or Python code, or troubleshoot the `gpt` tool.
---

# GPT Tool (`gpt`)

Runtime tinh gọn thế hệ mới điều khiển ChatGPT Web trực tiếp qua Wire Conduit HTTP/2 (0 MB browser RAM), lock cứng model **`gpt-5-6-thinking`** (`thinking_effort="extended"`), tự giải PoW SHA3-512 nội bộ.

- **Vị trí Source Code & Bảo trì:** [`/home/light/GitHub/gpt_cli`](file:///home/light/GitHub/gpt_cli)
- **Skill chi tiết đầy đủ:** Tham khảo skill `gpt-cli` ([`SKILL.md`](../gpt-cli/SKILL.md))
- **Launcher:** [`/home/light/.local/bin/gpt`](file:///home/light/.local/bin/gpt)

---

## ⚡ Quick Start: Các Lệnh Hay Dùng Nhất

```bash
# 1. Chat tương tác trực tiếp (hỗ trợ /new, /session, /exit)
gpt

# 2. Gửi một câu hỏi / prompt ngắn
gpt "Giải thích ngắn gọn cách thức hoạt động của RSA"

# 3. Chạy turn cho tool / agent (xuất JSON)
gpt run -p "Prompt cho agent" --json

# 4. Tạo session mới có gắn nhãn
gpt run -p "Bắt đầu task CTF" --new --tag "ctf-pwn" --json

# 5. Xem log hội thoại và debug
gpt session list
gpt session log --tail 2

# 6. Gateway background (Port 18000)
systemctl --user status webgpt-gateway.service
systemctl --user restart webgpt-gateway.service
```

Xem toàn bộ tài liệu kiến trúc, SDK Python, và cẩm nang tự sửa lỗi tại skill **`gpt-cli`**.
