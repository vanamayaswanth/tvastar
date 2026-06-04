# Changelog

All notable changes to Tvastar are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0] — 2026-06-04

### Added

- **Benchmark runner** (`tvastar.bench`) — `BenchSuite` / `BenchTask` /
  `BenchResult` / `BenchReport`: run an agent against standardised task sets
  and get a *resolve rate* (fraction of tasks where a real verifier — not the
  model's claim — reports success). Same "verify with real signals" principle
  as `tvastar-fix`.
- **SWE-bench adapter** (`swe_bench_tasks`) — loads tasks from
  `princeton-nlp/SWE-bench_Lite` via HuggingFace (`pip install datasets`) or
  a local JSONL file. Each task's verifier runs pytest on the workspace and
  reports the real exit code. Results are labelled `swe_lite_local` to
  distinguish from the official Docker-based harness.
- **`tvastar bench` CLI command** — `tvastar bench agent.py:agent
  --suite swe-lite --max-tasks 10 --out report.json` runs a benchmark,
  prints a resolve-rate report, and optionally writes JSON.

## [0.5.0] — 2026-06-04

Harness-engineering round, measured against the field's taxonomy
([awesome-harness-engineering](https://github.com/walkinglabs/awesome-harness-engineering)).
These deepen pillars Tvastar already has rather than adding new surface — and
each ships honestly scoped, with tests.

### Added

- **Tool masking** — `create_agent(tool_policy=...)` filters the visible toolset
  *per turn* so the model only sees the tools that matter right now (cuts context
  and tool-confusion on long runs). Helpers: `allow_only`, `deny`, `phases`, or
  any `Callable[[MaskContext], list[str]]`. A policy can only hide available
  tools, never grant new ones, and a misbehaving policy never breaks the run.
- **OpenTelemetry GenAI semantic conventions** — the `model.generate` span now
  emits standard `gen_ai.*` attributes (`gen_ai.system`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.response.finish_reasons`,
  …), so traces drop into Braintrust / Honeycomb / Datadog without custom mapping.
  `Model.system` names the provider (`anthropic` / `openai` / `mock`).
- **Untrusted content & injection detection** (honest mitigation, *not* a shield)
  — `wrap_untrusted(content, source=...)` fences external content as data, and
  the new `prompt_injection` detector flags tool output that matches injection
  signatures as a `WARNING` finding. Also exported: `scan_for_injection`,
  `looks_like_injection`.
- **`AGENTS.md`** contributor guide and a **12-Factor Agents map**
  (`docs/twelve-factor-agents.md`) with honest ✅/🟡/⬜ verdicts.

### Notes

- We deliberately did **not** add the more speculative items from the taxonomy
  (context backpressure, KV-cache locality). They'd be feature-for-the-checklist;
  they wait for a real need. Benchmark integration (SWE-bench/Terminal-Bench) is
  the planned next focused effort.

## [0.4.0] — 2026-06-04

### Added

- **Cost tracking** — every `RunResult` now carries a `.cost` (model-priced from
  token usage; see `COST_TABLE`).
- **Budgets** — `create_agent(budget=BudgetPolicy(max_usd=...))` enforces a cost
  ceiling during the run: `on_exceed="raise"` raises `BudgetExceeded`,
  `on_exceed="stop"` ends the run cleanly with `stopped="budget"`.
- **Human-in-the-loop approval** — `create_agent(approval_gate=ApprovalGate(...))`
  is now exposed to tools via `ToolContext`; `require_approval(..., ctx=ctx)`
  uses the agent's gate (CLI / webhook / event backends).
- **Eval harness** — `EvalSuite` / `Case` with built-in checks; `Harness.run`
  and `Session.prompt` now accept `cancel_after` (fixes eval timeouts).

### Removed

- **Semantic memory** (`tvastar.memory.semantic`) — dropped to keep the library
  focused; it was unintegrated and TF-IDF "semantic" oversold what it did.
  Bring your own vector store and wire it via a tool if you need retrieval.

## [0.3.2] — 2026-06-04

### Changed

- Docs: further README revisions. Refreshes the project description on PyPI.

## [0.3.1] — 2026-06-04

### Changed

- Docs: reworked the README with clearer positioning and comparisons
  (vs. LangGraph / LangChain / Agno / CrewAI). No code changes — this release
  refreshes the project description shown on PyPI.

## [0.3.0] — 2026-06-04

### Added

- **Workflows** (`@workflow`, `Workflow`, `WorkflowContext`, `WorkflowHarness`,
  `WorkflowRun`, `RunRegistry`, `RunStatus`) — code-guided agent automations with
  a persistent run history.
- **Dispatch** (`dispatch`, `dispatch_and_wait`, `observe_dispatch`,
  `cancel_dispatch`, `DispatchInput`, `DispatchEvent`) — fire-and-observe agent
  invocations for event-driven / webhook use.
- **Sub-agent profiles** (`define_agent_profile`, `AgentProfile`,
  `create_agent(subagents=...)`, `session.task(agent="name")`) — delegate work to
  named specialists in isolated child sessions, capped at `MAX_TASK_DEPTH` (4).
- **Structured output** — pass `result=` (Pydantic v2/v1, dataclass, `dict`, or
  any callable) to `prompt`/`skill`/`task` and read the validated object from
  `RunResult.data`.
- **Extended thinking** — `create_agent(thinking_level="low"|"medium"|"high")`,
  mapped per provider (Anthropic `budget_tokens`, OpenAI `reasoning_effort`).
- **Auto-compaction** (`CompactionPolicy`, `compact_session`, `should_compact`)
  — keep long sessions under a token/message budget automatically.
- **Tool retries** (`ToolRetryPolicy`) — per-tool (`@tool(retry=...)`) or
  harness-wide (`create_agent(tool_retry=...)`), with backoff + jitter.
- **`Harness.fan_out([...])`** — run many prompts concurrently with an optional
  concurrency cap.
- Expanded docs and the test suite (77 tests).

## [0.2.0] — 2026-06-04

### Added

- **`tvastar-fix`** — a flagship application built on Tvastar: a CLI and a
  GitHub Action that auto-fix a failing test suite. An agent edits the source
  and iterates; Tvastar re-runs the suite itself and reports success from the
  real exit code (never the model's claim). Free-model friendly (auto-selects
  Groq / OpenAI / Anthropic / local Ollama, or any OpenAI-compatible endpoint).
  Includes a composite GitHub Action (`action/action.yml`) and an example
  PR-opening workflow.

## [0.1.0] — 2026-06-04

Initial release. Tvastar is a programmable agent harness for Python:
`Agent = Model + Harness`.

### Added

- **Core harness** — the model↔tool agent loop, `Session`, `Harness`, and
  `create_agent` / `AgentSpec`.
- **Model layer** — a provider-agnostic `Model` interface with adapters for
  Anthropic (Claude), OpenAI (and any OpenAI-compatible endpoint via `base_url`:
  Cloudflare Workers AI, Groq, Together, Ollama, vLLM, …), and a scripted
  `MockModel` for offline/testing.
- **Tools** — the `@tool` decorator with automatic JSON-Schema generation from
  type hints, a registry, and a built-in toolset (bash, read/write/edit, list,
  glob, grep).
- **Sandboxes** — pluggable execution: `VirtualSandbox` (in-memory, runs real
  Python with no Docker), `LocalSandbox` (jailed subprocess), and external
  adapters (`DockerSandbox`, generic `RemoteSandbox` for E2B/Daytona/Modal),
  governed by a `SecurityPolicy`.
- **Skills** — Markdown-with-frontmatter expertise packages, loaded on demand.
- **Memory & durable execution** — in-memory and JSON-on-disk stores; full
  transcript + filesystem checkpointing with crash-safe resume.
- **MCP** — a Model Context Protocol client over stdio (local servers) and
  streamable HTTP/SSE (remote servers); MCP tools mount as native tools.
- **Failure detection** — in-process detectors for silent failures
  (`unknown_tool`, `schema_mismatch`, `thrash_loop`, `ignored_tool_error`,
  `unverified_completion`, `empty_answer`, `step_limit`), attached to
  `RunResult.findings`.
- **Observability** — span tracing with console, JSONL, and OpenTelemetry
  exporters.
- **Serving & deploy** — a CLI (`tvastar chat/serve/run/info`), a FastAPI
  HTTP+WebSocket server, and deploy adapters for ASGI hosts, AWS Lambda,
  GitHub Actions / GitLab CI, and generic FaaS.
- Examples, a test suite, CI (lint + format + tests on Python 3.10–3.13), and a
  live real-model proof run.

[Unreleased]: https://github.com/vanamayaswanth/tvastar/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vanamayaswanth/tvastar/releases/tag/v0.1.0
