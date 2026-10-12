# GPT-CLI

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/nmhuei/gpt_cli)
[![Tests](https://img.shields.io/badge/tests-325%20passed-success.svg)](https://github.com/nmhuei/gpt_cli)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-informational.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

GPT-CLI is a high-performance headless runtime and gateway for ChatGPT Web.
The tool uses direct HTTP/2 Wire Conduit transport and consumes 0 MB browser RAM during normal operation.

---

## Key Functions

- **Direct Wire Conduit:** Sends HTTP/2 requests directly to the ChatGPT Web backend with TLS impersonation.
- **Pure Python PoW:** Solves Sentinel Proof-of-Work SHA3-512 challenges locally.
- **Flagship Model:** Targets `gpt-6-thinking` with extended thinking effort.
- **Stealth CloakBrowser Authentication:** Launches CloakBrowser (Chrome 146 stealth) for clean interactive authentication.
- **Live Telemetry Stream:** Parses Server-Sent Events (SSE) in real time for thinking thoughts, tool calls, and citations.
- **DAG Session Tracking:** Maintains conversation continuity with server-side message identifiers.
- **Local API Gateway:** Provides OpenAI and Anthropic compatible API endpoints on port 18000.
- **Dynamic Model Discovery:** Discovers and synchronizes upstream models automatically.

---

## Installation and Requirements

### Prerequisites

- Python 3.11 or higher.
- Linux desktop environment (X11 or Wayland).
- Dependencies: `curl_cffi`, `fastapi`, `uvicorn`, `playwright`, `cloakbrowser`.

```bash
# Automated installation
./install.sh

# Or manual installation via uv
uv venv .venv
uv pip install -r requirements.txt
uv pip install -e .
```

---

## Essential Commands

### 1. Authentication

Clear the old profile and open CloakBrowser to log in to ChatGPT:

```bash
# Open CloakBrowser with a fresh profile and update token cache
gpt login
```

### 2. Run Prompts

Execute prompts directly from the command line:

```bash
# Quick prompt execution
gpt "Explain the RSA algorithm briefly"

# Run a prompt with JSON output
gpt run -p "Analyze the codebase" --json

# Connect to an active MCP bridge connector
gpt run -b br -p "Explore workspace files"

# Continue the active session (preserve DAG context)
gpt run --resume -p "Continue the previous task"

# Continue a specific session by ID
gpt run -s sess_8f3a12 -p "Next step"
```

### 3. Generate Images

Create and automatically save images:

```bash
# Generate image artifact into ~/Pictures/gpt-images/
gpt image -p "A futuristic cybernetic city at night"
```

### 4. Manage Sessions

Inspect and control local sessions:

```bash
# List all tracked sessions
gpt session list

# Show details of a specific session
gpt session show sess_8f3a12

# View session transcript logs
gpt session log sess_8f3a12
```

### 5. Control Gateway Daemon

Manage the local background API server with the unified `gateway` command:

```bash
# Check gateway daemon status (default action)
gpt gateway
gpt gateway status --json

# Start the gateway daemon on 127.0.0.1:18000
gpt gateway start

# Restart the gateway daemon
gpt gateway restart

# Stop the gateway daemon
gpt gateway stop
```

### 6. Model Discovery & Upstream Sync

Sync available ChatGPT Web models and view the top flagship model:

```bash
# Sync models from ChatGPT Web and display capabilities
gpt model

# Output machine-readable model metadata
gpt model --json

# Read locally cached models without network calls
gpt model --offline
```

---

## Technical Specifications

- **Token Cache:** `~/.local/share/webgpt/profiles/personal/webgpt-token-cache.json` (mode `0600`).
- **Session Registry:** `~/.local/share/webgpt/sessions/tool_sessions.json`.
- **Gateway Endpoint:** `http://127.0.0.1:18000`.
- **Default Model:** `gpt-6-thinking` (`thinking_effort="extended"`).

---

## License

MIT License. See [LICENSE](LICENSE) for details.
