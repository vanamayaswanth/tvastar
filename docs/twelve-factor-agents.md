# Tvastar and the 12-Factor Agents checklist

[HumanLayer's *12-Factor Agents*](https://github.com/humanlayer/12-factor-agents)
is a widely-cited checklist of practices that separate a demo from a production
agent. This page maps each factor to **what Tvastar actually ships** — with an
honest verdict (✅ supported · 🟡 partial · ⬜ your responsibility). Where a
factor is the *app's* job, not the harness's, we say so rather than claim credit.

| # | Factor | Verdict | How Tvastar supports it |
|---|--------|---------|--------------------------|
| 1 | **Natural language → tool calls** | ✅ | The agent loop turns model output into typed tool invocations; `@tool` derives a JSON schema from your function signature (`tools/schema.py`). |
| 2 | **Own your prompts** | ✅ | Prompts are plain `instructions` strings + Markdown skills. No hidden prompt magic; `AgentSpec.build_system_prompt()` is the whole story. |
| 3 | **Own your context window** | ✅ | `CompactionPolicy` (token/message budgets, `keep_last`) and **tool masking** (`tool_policy`) let you control exactly what the model sees each turn. |
| 4 | **Tools are structured outputs** | ✅ | Tool results are strings by contract; pass `result=` (Pydantic/dataclass/dict) to get validated structured output in `RunResult.data`. |
| 5 | **Unify execution & business state** | 🟡 | `Session.messages` is the execution state; `Memory` (scoped KV) holds business state. They're separate stores you can checkpoint together, not a single unified log. |
| 6 | **Launch / pause / resume** | ✅ | `Harness.run` / `session.prompt` to launch; `Checkpointer` + `FileStore` checkpoint after every turn; `harness.resume(session_id)` reloads transcript + filesystem. |
| 7 | **Contact humans with tool calls** | ✅ | `ApprovalGate` + `require_approval(ctx=ctx)` — a tool can block on human approval (CLI / webhook / event backends) mid-run. |
| 8 | **Own your control flow** | ✅ | `@workflow` gives deterministic, code-guided orchestration; `dispatch` for fire-and-observe; `session.task` for delegated sub-agents. You write the control flow in Python. |
| 9 | **Compact errors into context** | 🟡 | Tool errors are returned to the model as `ToolResultBlock(is_error=True)` so it can recover; the **failure detectors** surface ignored errors. Automatic error *summarisation* is via `CompactionPolicy`, not error-specific. |
| 10 | **Small, focused agents** | ✅ | `AgentProfile` + `subagents=[...]` + `session.task(agent="name")` compose narrow specialists, capped at `MAX_TASK_DEPTH=4`. |
| 11 | **Trigger from anywhere** | ✅ | FastAPI server (HTTP/WS/SSE), CLI (`tvastar`), and deploy adapters for ASGI, AWS Lambda, GitHub Actions/GitLab CI, and generic FaaS. |
| 12 | **Stateless reducer** | 🟡 | `AgentSpec` is immutable and a run is `(spec, messages) → RunResult`, which is reducer-shaped. Sandboxes hold side-effecting state, so it isn't *purely* stateless — but the spec/run split is the same idea. |

## Honest gaps

- **Factor 5 / 12** are 🟡 by design: Tvastar keeps a sandbox for real side
  effects, so a run isn't a pure function. The `AgentSpec`-vs-`Harness` split is
  as close as we get and we don't pretend otherwise.
- **Prompt injection** is not on this checklist and is *not solved by anyone*.
  Tvastar offers detection (`prompt_injection` detector) and a content boundary
  (`wrap_untrusted`) — mitigation you can see and reason about, not a shield.

If you spot a factor where the code and this table disagree, that's a bug in one
of them — please open an issue.
