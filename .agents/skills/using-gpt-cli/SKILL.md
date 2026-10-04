---
name: using-gpt-cli
description: Use when running a local gpt CLI agent for a long-lived CTF, reverse-engineering, or tool-use task; resuming its server-side session; steering a live turn; or assessing stalled, recovered, contaminated, or verified work.
---

# Running GPT-CLI Agents Reliably

Use this for long-lived tool work, not a one-turn chat. The durable object is
one local `session_id` bound to one server-side DAG. Runtime proves service
availability, not a correct solution.

**Required reference:** read
`/home/light/GitHub/gpt_cli/docs/GPT_CLI_60_MINUTE_AGENT_OPERATION.md` before
a 30+ minute run.

## Preflight: a valid experiment

- Create a clean workspace in `~/Downloads` containing only source artifacts
  and task material: no `solver/`, prior answer, flag, writeup or result file.
- A prompt cannot sandbox BR. If host-wide BR access can reach excluded paths,
  enforce a filesystem/container boundary before claiming a clean evaluation.
- Use one goal, workspace and session. If it reads excluded material, stop it;
  that DAG is contaminated and cannot evidence autonomous solving.

## Launch and resume one DAG

Use an absolute path and a brief action prompt:

```bash
gpt agent -b br -p "solve /absolute/path"
gpt agent --resume-session sess_x -b br --min-runtime-minutes 30 -p "continue"
```

Record the local id immediately. Do not create a replacement session merely
because SSE resets. `gpt agent` measures provider-visible `duration_ms`, not
private Chain-of-Thought; judge work through tool events and evidence.

## 60-minute acceptance

Run 30 minutes, inspect history, then resume the **same** session for 30 more.

```bash
gpt session log sess_x --json | jq '{
  turns:(.history|length),
  duration_ms:([.history[].duration_ms]|add),
  tool_calls:([.history[].tool_calls|length]|add)
}'
```

Pass requires one uncontaminated session and `duration_ms >= 3600000`. It does
not mean the task is solved.

## State and operator action

| Observation | Action |
|---|---|
| `Recovering completed server activity` | Wait for DAG recovery and inspect history; do not restart. |
| `steered` | Native steer accepted; inspect the following evidence. |
| `queued` | Persisted for a safe next parent; not yet evidence of execution. |
| Tool output or fresh artifact | Continue in the same session. |
| Two prose-only turns with no tool/evidence | Stalled: send one concrete, evidence-based steer to the same session. |
| Same generic prose or same tool failure after that steer | Stop that run, preserve its logs, and restart the challenge in a fresh clean workspace/session; do not keep sending `continue`. |
| Candidate flag/answer | For this user's authorized CTF tasks, submit a format-valid candidate through the official page immediately. If rejected, steer that rejection into the same challenge session and continue. Never use the `ctf submit` CLI. |

Inspect events, not only terminal prose:

```bash
gpt session log sess_x --tail 3
gpt session log sess_x --events
```

## Steering and completion

Target an explicit live local session:

```bash
gpt steer --resume-session sess_x -p "Use BR tools; verify the next hypothesis."
```

Use short instructions with an observable action. `...`, `???`, and `>>>>`
are not dependable recovery prompts: they produced short zero-tool-call turns
in live testing.

For a generic/stalled turn, choose a prompt tied to the last verified evidence,
for example: `Use BR tools to test <specific hypothesis>; report the command and
output.` If one such steer produces another prose-only turn or repeats the same
tool error, treat that DAG as stalled: preserve its history, then start a fresh
session with the original `solve <absolute path>` prompt in a clean workspace.
Keep no more than one active session per challenge. If the old workspace or
conversation may contain a flag, writeup, reference answer, or unrelated task,
do not reuse it as the clean restart workspace.

Declare `solved` only when a format-valid candidate is validated by a fresh
independent verifier or an authorized submission, and the session log records
the evidence and commands. Otherwise report `working` or `blocked` with the
last verified result.

## Red flags

- 401/403 is a wire/auth invariant problem, not quota.
- Do not claim hidden CoT is observable.
- Do not continue forever after prose-only turns.
- Do not reuse a session that saw reference results.

GPT-CLI currently provides DAG continuity, BR attachment, telemetry, steer and
SSE recovery. Sandbox policy, progress classification, verification and
solved/blocked state transitions are operator policy until implemented.
