# Tvastar v0.3.0

**Paste this into the GitHub Release for the `v0.3.0` tag.**

---

## A big capability release 🚀

Tvastar grows from a focused harness into a full agent platform — workflows,
sub-agents, structured output, extended thinking, auto-compaction, tool retries,
and parallel fan-out — while keeping the zero-dependency core.

```bash
pip install -U tvastar
```

### Highlights

- **Workflows** — `@workflow` functions that guide agent reasoning from input to
  result, with a persistent run history (`WorkflowRun`, `RunRegistry`).
- **Sub-agents** — `define_agent_profile(...)` + `session.task("...", agent="reviewer")`
  to delegate to specialists in isolated child sessions (depth-capped at 4).
- **Structured output** — `await sess.prompt("...", result=MyModel)` → a validated
  Pydantic/dataclass/dict in `RunResult.data`.
- **Extended thinking** — `create_agent(thinking_level="high")`, mapped to
  Anthropic `budget_tokens` and OpenAI `reasoning_effort`.
- **Auto-compaction** — `CompactionPolicy` keeps long sessions within budget.
- **Tool retries** — `ToolRetryPolicy` per-tool or harness-wide, with backoff.
- **Parallel fan-out** — `await harness.fan_out([...])`.
- **Dispatch** — fire-and-observe (`dispatch`, `dispatch_and_wait`) for
  event-driven / webhook agents.

### Still true

- Zero third-party dependencies in the core.
- Code-executing agents with **no Docker** (in-memory sandbox runs real Python).
- `tvastar-fix` — auto-fix failing tests, verified by re-running them.
- Silent-failure detection, MCP client, durable checkpoint/resume, deploy
  adapters, and the CLI.

### Upgrade

```bash
pip install -U tvastar          # or: uv pip install -U tvastar
```

**Full changelog:** [CHANGELOG.md](CHANGELOG.md) ·
**Diff:** https://github.com/vanamayaswanth/tvastar/compare/v0.2.0...v0.3.0
