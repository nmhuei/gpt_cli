---
name: using-gpt-cli
description: Use when running GPT-CLI (`gpt`) for automated tasks, CTF challenges, terminal operations, or Python integration; when resuming sessions via Server-Side DAG; or when steering stalled turns.
---

# Using GPT-CLI

## Overview
GPT-CLI is a high-performance Wire Conduit runtime for ChatGPT Web backend over HTTP/2 SSE. It uses 0 MB browser RAM, solves PoW SHA3-512 internally, and locks model `gpt-5-6-thinking` (`thinking_effort="extended"`).

Sessions use Server-Side DAG tracking (`conversation_id`, `parent_message_id`). Context remains on the server without client token bloat.

## When to Use
- **Use when:**
  - Running multi-turn autonomous problem-solving or CTF challenges.
  - Executing host commands and file operations via Bridge (`-b br`).
  - Conducting web research without host access (`--no-bridge`).
  - Resuming existing conversation sessions without losing state.
  - Calling GPT-CLI inside Python scripts.

- **Do NOT use when:**
  - Answering simple single-turn questions that need no tools.
  - Performing untrusted code execution without sandbox review.

## Quick Reference

| Action | Command |
| :--- | :--- |
| **New turn** | `gpt run -p "Prompt" --json` |
| **Start CTF challenge** | `gpt run -b br -p "solve <abs_path>"` |
| **Resume session** | `gpt run --resume-session <local_id> -p "Prompt" --json` |
| **Pure research (no host)** | `gpt run --no-bridge -p "Research prompt"` |
| **Host operations (Bridge)** | `gpt run -b br -p "Host task"` |
| **Steer stalled turn** | `gpt steer --resume-session <local_id> -p "Action prompt"` |
| **Generate image** | `gpt image -p "Image prompt" --json` |
| **List sessions** | `gpt session list --json` |
| **Inspect session turns** | `gpt session log <local_id> --tail 3` |
| **Inspect raw events** | `gpt session log <local_id> --events` |

## Core Procedures

### 1. CTF and Host Operations Workflow
- Attach the Bridge connector using `-b br`.
- Provide the absolute workspace path.
- **Mandatory Prompt Rule for CTF:** Keep the initial prompt extremely simple: `"solve <abs_path>"`. Never write verbose instructions.
- Save the returned `local_id`.
- Resume subsequent turns using `gpt run --resume-session <local_id> -p "Next instruction"`.

### 2. Research Workflow
- Run research queries with `--no-bridge`.
- Do not attach Bridge tools for pure information gathering.
- Use this mode for web searches, documentation queries, and algorithmic proofs.

### 3. Monitoring and Steering Workflow
- **Detect Stalled State:** A session is stalled when it produces **2 consecutive prose-only turns** without tool calls.
- **Steer Action:** Send one concrete, observable verification command:
  ```bash
  gpt steer --resume-session <local_id> -p "Run verification command and report output."
  ```
- **Stop Policy:** If the model repeats the same error or produces prose-only turns after steering, stop the session immediately. Do NOT spam `continue`.

### 4. Python In-Code Integration
Call the runtime directly inside Python scripts:

```python
import asyncio
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.wire import WireTransport
from gpt_cli.session.registry import SessionRegistry

async def ask_gpt(prompt: str) -> str:
    cache = TokenCacheManager("~/.local/share/webgpt/profiles/personal")
    bundle = cache.load_disk_cache()
    if not bundle:
        raise RuntimeError("Token cache not found or expired.")

    reg = SessionRegistry()
    sess = reg.get_or_create(tag="worker")

    async with WireTransport(bundle) as transport:
        res = await transport.send_turn(
            prompt=prompt,
            conversation_id=sess.conversation_id,
            parent_id=sess.last_message_id,
        )
    reg.update_turn(sess.local_id, res.conversation_id, res.message_id, res.model)
    return res.text

# asyncio.run(ask_gpt("Hello"))
```

## Common Pitfalls & Troubleshooting

### 1. HTTP 401 and 403 Errors
- 401 and 403 errors indicate wire protocol faults.
- Do NOT assume quota exhaustion. The account has unlimited standard quota.
- Do NOT launch a headless browser.
- Inspect `cf_clearance`, `ChatGPT-Account-ID`, PoW hash, and `oai-device-id`.

### 2. Spamming Continue Prompts
- Sending repeated `continue` prompts to a stalled turn causes infinite empty loops.
- Always use `gpt steer` with an explicit observable action.

### 3. Workspace Hygiene and Sandbox
- Keep temporary orchestration scripts and scratch data in `~/Downloads/bqa-trash/`.
- Never create scratch files in repository roots. Keep git status clean.
