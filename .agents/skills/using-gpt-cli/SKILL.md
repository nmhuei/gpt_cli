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


## Research vs Host Operations

Separate the task type before starting GPT-CLI:

- **Research (no BR):** Use for public information gathering, documentation review, comparisons, and hypothesis formation. Do not attach BR.
- **Host operations (BR):** Use for filesystem access, terminal commands, workspace edits, local verification, and artifact handling. Attach BR and provide the absolute task path.

Do not use BR as a replacement for research. Do not claim a host change from a research-only session.

## Verified Operational Lessons

- **Bind and chat IDs:** Record the bound workspace and `chat_id` when using BR-backed operations. Keep session identity stable when continuing work.
- **Tool-event checks:** Session status or assistant prose does not prove tool execution. Inspect tool events, results, changed files, and verifier output.
- **Websearch evidence:** Research claims require websearch evidence or another traceable source. A prose-only answer is not evidence.
- **Session IDs:** Resume the same session ID for continued work. Creating a new session loses prior conversation state and evidence continuity.
- **STE100 prompts:** Use short, clear prompts with one observable action and one expected result.

## Right-Size Research and Automation

Use 5x10 only for a new tool error or an unresolved question. Use direct calls for routine research. Each loop turn must test a new hypothesis or inspect new evidence. Do not repeat “research more.”

For a qualifying issue, use five independent GPT-CLI research sessions. Run ten counted turns per session. Use `--no-bridge` for every research turn.

Run the sessions with a task-specific orchestration script. Do not add a product command for this workflow.

- Start all independent sessions in parallel.
- Save each local session ID before the first follow-up.
- Resume the same ID for each deeper turn.
- Ask each turn to test a new hypothesis or inspect a new source.
- Record actual websearch queries, source URLs, turn number, duration, and result.
- Stop a session on repeated failure or no progress. Report the incomplete count.
- Verify ten turns from each session log. Do not infer completion from runtime.
- Keep the script and output outside repository roots, such as `~/Downloads/bqa-trash/`.

Automate repeated launches, turns, and status checks with a temporary script. Do not add a product CLI command. Use one direct invocation for one-off work.

## Monitor Long Operations

- Save the GPT-CLI session ID and process or job ID before waiting.
- Poll the same session or process until it finishes. Inspect its events and output.
- If a response is unclear, check the existing job before relaunching it.
- Do not start duplicate work while the first job may still run.
- Do not use fixed sleeps as proof of progress. Check process state, output, or events.
- Record elapsed time and final status for each long operation.

If research leads to host work, finish and record research first. Then start a BR-backed host session with the exact absolute path. Tell the agent: `Use BR tools. Do not use container.exec.`

Examples:

```text
Check file X. Report changed lines.
Test Y. Return pass or fail with evidence.
Read log Z. Find tool failures.
```

## Scenario Test Guidance

### Research scenario (no BR)

Prompt:

```text
Research <topic>. Return sources and findings.
```

Check:

- No BR attachment is used.
- Claims include traceable evidence.
- No local file change is claimed.

### Host-operation scenario (BR)

Prompt:

```text
Verify /absolute/path. Report commands, tool events, and results.
```

Check:

- BR is attached.
- The same session ID is used for follow-up checks.
- Tool events and resulting artifacts verify the operation.

## Prompt and Session Notes

- Use `gpt run --no-bridge` for research. Do not use `-b br` for websearch.
- Use `gpt run -b br` for host reads, writes, commands, and local tests.
- Every BR-backed host prompt must say: `Use BR tools only. Do not use container.exec.`
- A BR session must bind the requested workspace before workspace tools run.
- Pass the returned `chat_id` to later workspace tools. Do not repeat a successful bind.
- A connector selection is not proof of a tool call. Verify the tool name and result in the log.
- If several tasks can run independently, automate their launches and status checks outside the product. Do not add a new CLI subcommand unless the user asks for a product feature.
- Keep prompts short. Name the action, path, and evidence to return.

## Safe Bind and chat_id Flow

For BR-backed work:

1. Bind once and record the returned workspace identity and `chat_id`.
2. Use that same `chat_id` for workspace reads, writes, and verification calls.
3. Do not invent a `chat_id`, reuse an unrelated session identity, or repeat a successful bind.
4. If binding fails, stop workspace operations until a valid bind succeeds.

Example flow:

```text
Bind workspace.
Record: workspace_path=<returned path>, chat_id=<returned id>.
Read or edit files using the same chat_id.
Verify the resulting artifact with tool events and file output.
```

## Exact 5x10 Research Log Verification

The 5x10 research loop means exactly five independent no-BR sessions with ten verified research turns per session.

Verification checklist:

- Confirm 5 unique local session IDs.
- Confirm each session has turns numbered 1 through 10.
- Confirm every turn uses `--no-bridge` and has no BR attachment.
- Confirm every turn records: session ID, turn number, query or hypothesis, source URL/evidence, duration, and result.
- Confirm each session log contains exactly 10 completed research turns before counting the loop complete.
- Confirm the aggregate report contains 50 verified turns, grouped by session.
- Record incomplete sessions separately; do not substitute runtime duration for missing turns.

Keep orchestration scripts, generated logs, and reports outside repository roots, such as `~/Downloads/bqa-trash/`. Example names:

```text
~/Downloads/bqa-trash/research_5x10.py
~/Downloads/bqa-trash/research_5x10_report.json
```


## Default Repetitive GPT-CLI Orchestration

When GPT-CLI work is repetitive (multiple similar runs, repeated investigations, batch follow-ups, or repeated session turns), use a temporary orchestration script by default instead of manual operator turns.

- Keep the temporary orchestration script outside repository roots.
- Persist every result, including session IDs, turn outputs, logs, errors, and completion state.
- Resume saved session IDs for follow-up work; do not create replacement sessions unless the existing session is invalid or contaminated.
- Enforce the configured concurrency limit from the run configuration. Do not exceed configured parallel sessions.
- Verify logs after execution. Check that expected turns, session IDs, results, and tool events are present before reporting completion.
- Do not add product CLI features to support this workflow. Use external temporary orchestration scripts and existing GPT-CLI capabilities.
