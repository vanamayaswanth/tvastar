# Changelog

All notable changes to Tvastar are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.8.3] — 2026-06-14

### Added

- **`dispatch()` / `dispatch_and_wait()` tracer hookup** — both now accept a
  `tracer: Tracer | None` parameter that is forwarded to the internal `Harness`.
  Dispatched runs now emit full observability spans into any attached exporter
  (JSONL, OTel, console).

- **`GraphResult.findings`** — `TaskGraph.run()` now collects `RunResult.findings`
  from every task and surfaces them on `GraphResult.findings: dict[str, list[Finding]]`.
  `GraphResult.ok` returns `False` when any task has warnings. New
  `GraphResult.all_findings` property returns a flat list across all tasks.

- **`Workflow.run(tracer=...)` hookup** — `@workflow` now accepts a `tracer`
  keyword argument that is threaded through `WorkflowContext` into every
  `ctx.init()` call, so all harnesses created inside a workflow share the same
  tracer and emit spans to the same exporter.

- **`VirtualSandbox.audit`** — `VirtualSandbox` now maintains `audit: list[AuditEntry]`
  just like `LocalSandbox`. Every `exec()` call appends an entry (blocked
  commands via `AuditEntry.blocked()`, completed commands via
  `AuditEntry.executed()`), giving the two sandboxes a consistent API.

- **`tvastar-fix` resource limits** — `fix_tests()` now accepts
  `max_cpu_seconds` and `max_memory_mb` keyword arguments, forwarded to a
  `ResourcePolicy` on the `LocalSandbox`. The CLI gains `--max-cpu SECS` and
  `--max-memory MB` flags.

- **`assert_no_findings(min_severity="warning")`** — new eval check that fails
  a `Case` when the run produced any `Finding` at or above the given severity
  threshold. Exported from `tvastar.eval` and the top-level `tvastar` namespace.

## [0.8.2] — 2026-06-14

### Added

- **`ResourcePolicy`** — per-sandbox hard resource limits: `max_cpu_seconds`
  (asyncio timeout, cross-platform), `max_memory_mb` (`ulimit -v` on Linux/macOS,
  silently ignored on Windows), `max_output_chars` (output truncation),
  `allowed_domains` (documents intent for firewall/proxy enforcement).
- **`AuditEntry`** — immutable record written to `LocalSandbox.audit` after every
  command: `command`, `timestamp`, `allowed`, `violation` (if blocked by
  `SecurityPolicy`), `exit_code`, `duration_ms`. Factory classmethods
  `AuditEntry.blocked()` and `AuditEntry.executed()`.
- **`LocalSandbox.audit`** — `list[AuditEntry]` accumulates the full command
  history for the lifetime of the sandbox. Blocked commands are recorded before
  `SecurityViolation` is raised; timed-out commands are recorded with
  `exit_code=124`.
- **`LocalSandbox(resources=...)`** — new keyword argument accepts a
  `ResourcePolicy`; defaults to `ResourcePolicy()` (30 s CPU, 50 k output chars,
  no memory cap).
- `ResourcePolicy` and `AuditEntry` exported from `tvastar.sandbox` and the
  top-level `tvastar` namespace.

## [0.8.1] — 2026-06-14

### Added

- **Web tools** (`web_browse`, `web_search`) — zero-dependency internet access for
  agents using Jina AI Reader (`r.jina.ai`) and Jina AI Search (`s.jina.ai`). No
  API key required. Both use stdlib `urllib` + `asyncio.to_thread`; no new package
  dependencies.
- **`web_toolset()`** — returns `[web_browse, web_search]`, composable with
  `default_toolset()`: `tools=[*default_toolset(), *web_toolset()]`.
- HTTP errors and network failures return a `[http N]` / `[error]` string instead
  of raising, so the agent can handle failures gracefully.
- `max_chars` parameter on both tools truncates long pages before they fill context.

## [0.8.0] — 2026-06-14

### Added

- **DAG-based parallel task execution** (`tvastar.graph`) — `TaskGraph` lets you
  define tasks with explicit dependencies and executes them at maximum parallelism.
  Independent tasks run concurrently via `asyncio.gather`; a task starts the moment
  every dependency completes. Wall-clock time equals the critical path, not the sum
  of all tasks.
- **Automatic result injection** — by default, each dependency's output is prepended
  to the downstream task's prompt so the model has full context without extra wiring.
  Pass `inject_results=False` to disable.
- **`GraphResult`** — returned by `TaskGraph.run()`; supports `result["task_name"]`,
  `.text` (dict of all outputs), `.ok` (True when every task finished cleanly).
- **Cycle detection and validation** — raises `ValueError` on duplicate task names,
  unknown dependencies, or dependency cycles before any tasks are started.
- **Fluent API** — `TaskGraph.task()` returns `self` for chaining:
  `TaskGraph(harness).task("a", "…").task("b", "…", depends_on=["a"]).run()`

## [0.7.0] — 2026-06-14

### Added

- **Local trace viewer UI** (`tvastar.ui`) — a self-contained FastAPI + vanilla-JS
  single-page app that reads any `JSONLExporter` trace file and renders runs as an
  interactive timeline. Left panel lists runs with status dots, step/tool counts, and
  duration. Right panel shows per-run token counts, findings cards, and an expandable
  step-by-step timeline (model generate / tool invoke / events). Reads the OTel GenAI
  semantic-convention attributes emitted since 0.5.0.
- **`tvastar ui` CLI command** — `tvastar ui --trace my-run.jsonl --port 7878`
  starts the viewer and auto-opens it in the browser. Defaults to
  `tvastar-trace.jsonl` in the current directory. Auto-refreshes every 5 s.
- **`run_ui` / `create_ui_app`** exported from `tvastar` top-level for programmatic
  use: `from tvastar import run_ui; run_ui("trace.jsonl")`.
- `run_ui_demo.py` — generates a 3-run demo trace (coding agent / devops agent /
  research agent) and opens the UI; useful for evaluating the viewer without a live
  agent run.

### Fixed

- `tvastar/ui/server.py`: HTML served with explicit `encoding="utf-8"` to avoid
  `UnicodeDecodeError` on Windows systems using cp1252 as the default locale.

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
