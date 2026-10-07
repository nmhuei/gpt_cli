# Core Operating Rules & Engineering Principles (ASD-STE100 Standard)

## 1. Authentication and Protocol Logic Invariants (HTTP 401/403)
- **Account Quota Rule:** The user account has unlimited standard quota.
- **Protocol Fault Rule:** HTTP 401 and HTTP 403 status codes indicate protocol wire errors (99% probability).
  - Do NOT conclude that quota is exhausted.
  - Do NOT start a headless browser.
- **Root Cause Analysis:** Inspect the following items when 401 or 403 occurs:
  1. *Cookie formatting:* Missing `cf_clearance`, `__Secure-next-auth.session-token`, or `_puid`.
  2. *Header `ChatGPT-Account-ID`:* Missing, empty, or mismatched workspace identifier.
  3. *Sentinel and PoW logic:* Outdated `_SENTINEL_BUILD_HASH` or `oai-client-version`; invalid SHA3-512 fingerprint format.
  4. *Device ID mismatch:* Inconsistent `oai-device-id` between Sentinel and SSE headers.
  5. *TLS impersonation:* Mismatched `curl_cffi` fingerprint and User-Agent header.
  6. *Access token expiration:* Expired 1-hour JWT without automatic token refresh.
- **Mandatory Action:** Correct wire protocol logic, request headers, cookie formatting, PoW handshake, and JWT lifecycle in source code.

## 2. Separation of System Roles
- **Codex CLI (`/home/light/.local/bin/codex`):**
  - Acts as Read-Only Architecture and Logic Advisor.
  - Do NOT request Codex to generate code or edit files.
- **GPT-CLI (`/home/light/GitHub/gpt_cli`):**
  - Python runtime product of the local system.
  - Communicates directly with ChatGPT Web backend via Wire Conduit HTTP/2 SSE (0 MB browser RAM).
- **Antigravity (AI Coding Assistant):**
  - Implements source code, debugs faults, writes automated tests, and optimizes systems.

## 3. Anti-Over-Engineering and Evidence-Based Debugging
- **LoopGuard Policy:**
  - Operating history proves no infinite tool loops occurred.
  - Do NOT build complex LoopGuard or Trajectory Hashing systems without verified failure evidence.
- **Context Window Management:**
  - Systems use Server-Side DAG tracking (`conversation_id`, `parent_message_id`).
  - Context growth occurs through large tool output text.
  - Apply Tool Output Hygiene and State Checkpointing.
  - Do NOT alter Server-Side DAG logic without technical justification.

## 4. Isolated Test Environment & Sandbox Rule (bqa-trash)
- **Mandatory Sandbox Location (`~/Downloads/bqa-trash/`):**
  1. *Sân chơi nghịch ngợm thoải mái:* Cho phép tải file tạm, chạy script nháp, cào dữ liệu rác, bung file nén trong `~/Downloads/bqa-trash/`.
  2. *Không bị mất khi tắt máy:* Nằm trên ổ cứng thật nên dữ liệu không bị xóa khi shutdown (ưu điểm hơn `/tmp`).
  3. *Thùng rác một chạm:* Người dùng có thể xóa sạch toàn bộ thư mục này bất kỳ lúc nào mà không sợ xóa nhầm mã nguồn chính.
- **Workspace Cleanliness:**
  - Do NOT create test files or folders in repository roots (`/home/light/GitHub/gpt_cli`, `/home/light/GitHub/botquanganh_mcp`, `/home/light/Workspace/Project/auto_download_ctf_challenge`).
  - Keep git status clean before and after task execution.

## 5. ASD-STE100 Documentation and Communication Standard
- **Scope:** All technical documentation, artifacts, guidelines, commit messages, and agent reports MUST comply with ASD-STE100 principles.
- **Grammar and Vocabulary Rules:**
  - Write short, direct sentences (< 25 words per sentence).
  - Use active voice and imperative mood for instructions.
  - Provide one clear instruction per step.
  - Avoid ambiguous words, idioms, colloquialisms, and duplicate synonyms.
  - Use standardized technical terms consistently across all repositories.

## 6. Zero-Hardcoding and Dynamic Extensibility Standard
- **Configuration Source of Truth:**
  - All operational thresholds (limits, quotas, timeouts, workers, models, retention days) MUST be loaded from `.env`.
  - Caching and fallback logic MUST use one unified configuration layer (`app.config` / `settings.py`).
  - Do NOT embed hardcoded numeric limits or endpoint strings in business logic.
- **Extensible Components (Platforms, Tools, Plugins, Engines):**
  - Components that expand over time MUST provide standard base interfaces and declarative templates.
  - System discovery MUST be automatic (file scanning, entry points, or decorator registration).
  - Do NOT require manual registry file modifications (such as regex replacements) to add new components.
