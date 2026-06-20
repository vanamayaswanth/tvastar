# Changelog

All notable changes to Tvastar are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.15.0] — 2026-06-20

### Added

- **`tvastar.assurance` — Verifiable Execution** — cryptographically signed,
  chain-linked execution receipts. Every agent run now produces provable proof
  of what was asked, every tool called, the final answer, and the Loop Quality
  score. The first AI agent framework where runs are as verifiable as compiled
  code.

  ```python
  from tvastar.assurance import AssurancePolicy, TrustLog

  agent = create_agent(
      "billing-bot",
      model=model,
      assurance=AssurancePolicy(
          log=TrustLog(".tvastar-trust.jsonl"),
          min_score=80,          # PASS required
          on_fail="escalate",
          on_escalate=lambda r: alert_team(r),
      ),
  )

  result = await harness.run("Charge customer $50")
  print(result.receipt.content_hash)   # sha256:abc123...
  print(result.receipt.verify())       # True — mathematically provable
  ```

- **`ExecutionReceipt`** — immutable, chain-linked record of one run:
  - `run_id` — unique identifier
  - `content_hash` — SHA-256 of every field (prompt, tool calls, answer, score)
  - `signature` — HMAC-SHA256 of the hash using your signing key
  - `prev_hash` — links to the preceding receipt (tamper-evident chain)
  - `verify(key?)` — recomputes hash + HMAC from scratch; returns `False` on any tampering
  - Survives JSON serialisation: `to_json()` / `from_json()` / `from_dict()`

- **`TrustLog`** — append-only, chain-linked ledger:
  - In-memory (`TrustLog()`) or file-backed (`TrustLog(".tvastar-trust.jsonl")`)
  - `append(receipt)` — enforces chain continuity, raises on break
  - `verify_chain()` — walks every entry, recomputes hashes, detects tampering
  - `get(run_id)` — O(n) lookup by run_id
  - `to_jsonl()` — export full log as JSONL string
  - File backend: WORM semantics — existing lines are never overwritten; corrupt
    lines silently skipped on reload

- **`AssurancePolicy`** — one-line config on any `AgentSpec`:
  - `key` — HMAC signing key (or `TVASTAR_RECEIPT_KEY` env var)
  - `log` — `TrustLog` instance; receipts auto-appended after every run
  - `min_score` — Loop Quality SLA floor (0 = disabled)
  - `on_fail` — `"ignore"` | `"raise"` | `"escalate"`
  - `on_escalate` — sync or async callable invoked with the receipt on breach

- **`SLABreached`** — exception raised when `on_fail="raise"` and quality
  drops below `min_score`. Carries `.score`, `.min_score`, `.receipt`.

- **`result.receipt`** — `ExecutionReceipt | None` on every `RunResult`.
  `None` when no `AssurancePolicy` is configured; populated otherwise.

- **93 new tests** in `tests/test_assurance.py` — design-for-failure coverage:
  tampered receipts, broken chains, corrupt JSONL, wrong keys, ugly unicode,
  null bytes, 100-tool-call receipts, full end-to-end through `MockModel`.

### Changed

- `AgentSpec` and `create_agent()` accept a new `assurance=` parameter.
- `RunResult` gains a `receipt: ExecutionReceipt | None` field (default `None`).
- `__version__` bumped to `0.15.0`.

## [0.14.0] — 2026-06-20

### Added

- **`tvastar.wrap` — Loop Quality for any callable** — add Tvastar's
  silent-failure detection to ANY agent loop without changing the loop itself:

  ```python
  import tvastar

  @tvastar.wrap
  async def my_loop(prompt: str) -> str:
      return await some_external_agent(prompt)

  result = await my_loop("fix the failing tests")
  print(result.quality.score)   # 0–100
  print(result.quality.grade)   # "PASS" | "WARN" | "FAIL"
  print(result.ok)              # True if grade is PASS
  ```

  Also works as a one-shot wrapper: `result = await tvastar.wrap(fn)(prompt)`.
  Accepts `detectors=` and `extract_text=` keyword arguments for customisation.

- **`WrappedResult`** — drop-in companion to `RunResult` returned by all
  adapter entry points: `.text`, `.quality`, `.findings`, `.ok`, `.warnings`,
  `.errors`, `.duration`, `.raw`.

- **`tvastar.adapters.openai`** — wrap a raw OpenAI function-calling loop:

  ```python
  from tvastar.adapters.openai import OpenAILoopWrapper, score_openai_messages

  # Context-manager — you own the loop, Tvastar scores on exit
  with OpenAILoopWrapper() as loop:
      loop.messages.append({"role": "user", "content": "Fix the tests."})
      while True:
          resp = client.chat.completions.create(model="gpt-4o", messages=loop.messages, tools=...)
          loop.messages.append(resp.choices[0].message.model_dump())
          if resp.choices[0].finish_reason == "stop":
              break
  print(loop.result.quality.grade)

  # Post-hoc — score a messages list you already have
  result = score_openai_messages(messages)
  ```

  Converts OpenAI tool call / tool result message shapes to Tvastar's internal
  format so the full detector suite fires (`thrash_loop`, `ignored_tool_error`,
  `unverified_completion`, etc.), not just text-level checks.

- **`tvastar.adapters.langgraph`** — wrap a compiled LangGraph graph:

  ```python
  from tvastar.adapters.langgraph import LangGraphWrapper

  graph = build_my_graph().compile()
  wrapped = LangGraphWrapper(graph)

  result = await wrapped.ainvoke({"messages": [HumanMessage(content="Fix tests.")]})
  print(result.quality.score)
  ```

  Automatically converts `HumanMessage`, `AIMessage`, and `ToolMessage` objects
  to Tvastar types. Supports custom `extract_text=` and `extract_messages=`
  callables for non-standard state shapes.

- **`tvastar.adapters.agentcore`** — wrap an AWS AgentCore (Bedrock Agents)
  `invoke_agent` call:

  ```python
  from tvastar.adapters.agentcore import AgentCoreWrapper, score_agentcore_response

  import boto3
  client = boto3.client("bedrock-agent-runtime")
  wrapper = AgentCoreWrapper(client)

  result = wrapper.invoke(
      agent_id="ABCDEF1234", agent_alias_id="TSTALIASID",
      session_id="s1", input_text="Fix the failing tests.",
  )
  print(result.quality.grade)
  ```

  Parses Bedrock's event stream, extracts tool invocations and results from
  orchestration trace events, and scores the full interaction.

- **59 new tests** across `tests/test_wrap.py` and `tests/test_adapters.py`
  covering all three adapters, the `wrap()` decorator, `WrappedResult`, and
  `_default_extract_text` — 499 total tests now passing.

### Changed

- `__version__` bumped to `0.14.0`.

## [0.13.0] — 2026-06-20

### Added

- **Loop Quality scoring** (`tvastar.quality`) — `score_run(result)` computes a
  0–100 quality score and `PASS / WARN / FAIL` grade from a `RunResult`'s findings
  and stop reason. Returned as `LoopQualityReport(score, grade, summary)`.

  ```python
  from tvastar.quality import score_run

  result = await harness.run("fix the failing tests")
  report = score_run(result)
  print(report.score)    # 40
  print(report.grade)    # "FAIL"
  print(report.summary)  # "1 error — final answer claims success but last tool result shows failure"
  ```

  Scoring deductions:
  - `-30` per ERROR finding
  - `-10` per WARNING finding
  - `-20` when stopped by `max_steps` or `budget`
  - `-50` when stopped by `error`

  Grades: ≥ 80 → `PASS`, ≥ 60 → `WARN`, < 60 → `FAIL`.

- **`tvastar quality` CLI command** — score any agent run from the terminal:

  ```bash
  tvastar quality my_agent.py:agent "fix the failing tests"
  # Loop Quality: 40/100  [FAIL]
  # exit 1 on FAIL, exit 0 on PASS/WARN
  ```

- **70-test quality suite** (`tests/test_quality.py`) — full coverage of scoring
  logic, grading thresholds, summary generation, all stop reasons, ugly inputs
  (unicode, null bytes, SQL injection), and IndexError regression for stop-only runs.

### Fixed (security hardening — Bandit + pip-audit audit)

- **B310 — scheme whitelist on `urlopen` callers** — three `urllib.request.urlopen`
  call sites now validate that the URL starts with `http://` or `https://` before
  opening it, preventing `file:///` and custom-scheme SSRF vectors:
  - `fix/models.py`: Ollama health-check URL
  - `mcp/transport.py`: `StreamableHttpTransport._post()` — raises `MCPError` on
    non-http/https URLs
  - `tools/builtin.py`: `_http_get()` (used by `web_browse` / `web_search`) — returns
    `[error]` string instead of fetching dangerous schemes

- **B615 — HF dataset revision pinning** (`bench/swebench.py`) — `load_dataset()`
  now passes `revision="main"` to prevent silent dataset drift if the upstream
  HuggingFace dataset is updated without a version bump.

- **CVE-2025-71176** — `pytest` floor bumped to `>=9.0.3` in `pyproject.toml`.
  The vulnerability allowed local privilege escalation via predictable `/tmp/pytest-of-{user}`
  directory names on UNIX.

### Fixed (source bugs — design-for-failure audit)

- **`loop/schedule.py`** — two bugs:
  1. Missing `f` prefix on second line of error message left `{len(parts)}` as
     literal text instead of interpolating the field count.
  2. Zero or negative cron step (`*/0`) caused `range(0, 60, 0)` → `ValueError`
     inside the scheduler; now raises a clear `ValueError("Cron step must be positive")`.

- **`sandbox/local.py`** — two bugs:
  1. `if t` filter excluded `timeout=0.0` from the effective-timeout min; fixed to
     `if t is not None`.
  2. `proc.returncode or 0` silently masked non-zero exit codes (e.g. `-1 → 0`);
     fixed to `proc.returncode if proc.returncode is not None else 0`.

- **`outbound/leads.py`** — `open(..., encoding="utf-8")` → `encoding="utf-8-sig"`
  so Excel-exported CSVs with a UTF-8 BOM no longer corrupt the first column header.

- **`graph.py`** — `asyncio.CancelledError` (a `BaseException` subclass, not `Exception`)
  was not caught in the task-failure path, causing a `KeyError` in dependents instead of
  a clean cancellation. Fixed to `except BaseException`.

- **`outbound/campaign.py`** — two bugs:
  1. `Path` objects were not accepted as the `leads` argument; fixed with
     `isinstance(leads, (str, Path))`.
  2. `max_leads=0` processed all leads instead of zero; fixed to
     `if max_leads is not None`.

- **`masking.py`** — `GovernancePolicy.copy()` used `dataclasses.replace(self)` which
  shallow-copied the `phases` dict, sharing mutable sets across sessions. Fixed to deep-copy
  each phase set: `phases={k: set(v) for k, v in self.phases.items()}`.

- **`ui/server.py`** — two bugs:
  1. `get_stats()` returned `{"total_runs": 0}` when there were no runs, causing `KeyError`
     for callers expecting the full schema. Now returns all five keys with zero values.
  2. `finish_reasons=[]` (empty list from SSE events) caused `IndexError` in
     `_group_into_runs()`; now safely returns `None` for empty lists.

- **`mcp/transport.py`** — `asyncio.get_event_loop()` deprecated in Python 3.10+ and
  raises `RuntimeError` in 3.12+. Replaced with `asyncio.get_running_loop()`.

- **`bench/swebench.py`** — `--- a/` (old side of a unified diff) was incorrectly
  collected as a test file path, causing false `FAIL` verdicts on renamed or deleted
  test files. Only `+++ b/` (new side) is now collected.

- **`eval.py`** — `EvalSuite(concurrency=0)` created `asyncio.Semaphore(0)`, deadlocking
  every case permanently. Now raises `ValueError("concurrency must be >= 1, got 0")` at
  construction time.

- **`tools/builtin.py`** — `web_browse` concatenated the Jina Reader prefix with the
  raw URL without percent-encoding, breaking URLs containing spaces or special characters.
  Fixed to `urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=")` (matching `web_search`).

- **`sandbox/virtual.py`** — `_cmd_wc`, `_cmd_head`, and `_cmd_tail` raised an
  uncaught `FileNotFoundError` on missing files. Each now returns `ExecResult(1, "", "cmd: path: No such file")`.

- **`tools/schema.py`** — `_parse_arg_docs` missed section headers (`Returns:`,
  `Raises:`) that appeared without a preceding blank line, causing their content to bleed
  into argument descriptions. Fixed operator precedence in the `if` guard.

### Changed

- `__version__` bumped to `"0.13.0"`.
- `tests/test_detect.py` expanded from 11 to **65 tests** — four detectors that had
  zero coverage (`unknown_tool`, `ignored_tool_error`, `empty_answer`, `prompt_injection`)
  now have full test coverage including boundary cases, fault-isolation of crashing
  detectors, `thrash_loop` off-by-one, and `validate()` union / array / nested-object types.

## [0.12.2] — 2026-06-16

### Fixed (Munger standards deep audit — 4 gaps)

- **`score.py` injection vectors** — `research.summary` and `lead.display()` are now
  wrapped with `wrap_untrusted()` before being embedded in the scoring prompt. Web-scraped
  research data was previously inserted raw, allowing a malicious web page to inject
  instructions into the scorer.

- **`email.py` injection vectors (critical)** — `scored.research.summary`,
  `scored.rationale`, and `lead.display()` are now all wrapped with `wrap_untrusted()`
  before being inserted into the email-writing prompt. `scored.rationale` is model output
  that itself processed web-scraped content — wrapping it prevents second-order injection.
  This completes the Case A prompt-injection defense started in v0.12.1.

- **Async fire-and-forget task orphan** — `asyncio.create_task(self._improve_instructions(run))`
  previously returned a task with no stored reference, which Python's GC could collect
  before it completed (especially if the calling context exited quickly). The task is now
  added to `self._bg_tasks` (a set) and removed via `add_done_callback` when it finishes,
  following the standard Python idiom for safe fire-and-forget.

- **`CredentialFilter` pattern gaps** — Added `*_URL`, `*_URI`, `*_DSN`, `PGPASSWORD`,
  and `PGPASSFILE` to the default strip patterns. Previously `DATABASE_URL`, `REDIS_URL`,
  `MONGO_URI`, `SENTRY_DSN`, and `PGPASSWORD` were not covered.

### Not fixed (architectural decisions — require discussion)

- **`cancel_after` optional everywhere** — No default timeout on `sess.prompt()`,
  `sess.task()`, or `harness.run()`. Rule 1 says mandatory; current design leaves it to
  callers. Recommendation: document as a required caller responsibility.
- **`budget` optional** — `BudgetPolicy` is `None` by default, meaning unlimited spend.
  Rule 1 requires budget declaration. Recommendation: add `BudgetPolicy` to the
  production-agent quickstart guide with a hard ceiling.
- **`SecurityPolicy` is denylist-based** — Only 4 commands denied by default; everything
  else is allowed. An allowlist model would be safer for untrusted agents.
- **No budget reduction by task depth** — `sess.task()` child sessions inherit the
  parent's full `max_steps` with no reduction. At MAX_TASK_DEPTH=4 with default 20 steps,
  worst-case exponential cost is 20^4 = 160,000 steps.

## [0.12.1] — 2026-06-16

### Fixed

- **Race condition in `_improve_instructions`** — the meta-agent harness replacement now
  acquires `self._lock` before writing to `self._harness` and `self._current_instructions`,
  preventing concurrent `trigger()` calls from reading a partially-replaced harness.

- **Meta-agent timeout** — `_improve_instructions` now wraps the `Harness.run()` call with
  `asyncio.wait_for(timeout=min(cancel_after or 120.0, 120.0))`. A hanging meta-agent can
  no longer block future loop iterations indefinitely.

- **`cancel_after` missing from all 6 loop patterns** — `CISweeper`, `PRBabysitter`,
  `DailyTriage`, `DependencySweeper`, `PostMergeCleanup`, and `ChangelogDrafter` all
  previously left `cancel_after=None` in their `LoopConfig`, meaning production loops
  could hang forever. All six now expose `cancel_after=` as a constructor parameter with
  sensible defaults (120s–600s depending on task complexity). Pass `cancel_after=None`
  explicitly to disable.

- **Prompt-injection in outbound research data** — `run_campaign()` now scans each
  research summary with `scan_for_injection()` before it reaches the scorer or email
  writer. Leads with detected injection patterns are quarantined (skipped + logged) rather
  than passed to the LLM. Clean summaries are wrapped with `wrap_untrusted()` so the model
  treats them as opaque data, not instructions. Closes the "Case A: Outbound Email
  Lollapalooza" failure mode described in `munger_standards.md`.

## [0.12.0] — 2026-06-16

### Added

- **Self-Improving Loops** (`LoopConfig.meta_model`) — Hyperagents-inspired prompt evolution.
  Set `meta_model=` on any `LoopConfig` and the loop will run a meta-agent after each FAIL
  to rewrite its own instructions based on failure evidence. The improved instructions are
  persisted to `FileStore` and applied to every subsequent run automatically.

  ```python
  loop = CISweeper(
      model=AnthropicModel("claude-haiku-4-5-20251001"),
      schedule="*/15 * * * *",
      cancel_after=300.0,
  )
  # Override the LoopConfig after construction to enable meta-improvement:
  from tvastar.loop import LoopConfig
  loop._config = LoopConfig(
      name=loop._config.name,
      goal=loop._config.goal,
      schedule=loop._config.schedule,
      cancel_after=loop._config.cancel_after,
      meta_model=AnthropicModel("claude-sonnet-4-6"),
  )
  ```

  Or pass it directly to any custom Loop:
  ```python
  config = LoopConfig(
      name="my-loop",
      goal="Keep the build green.",
      schedule="*/15 * * * *",
      cancel_after=300.0,
      meta_model=AnthropicModel("claude-sonnet-4-6"),
  )
  ```

  Design: meta-improvement fires as a background task after the FAIL is recorded, so the
  next retry (already scheduled after backoff) benefits. Never raises — a meta-improvement
  failure must not affect the main loop lifecycle.

- **Generational Archive** (`loop.generation_archive`, `loop.best_generation()`) — every
  `LoopRun` is recorded as a `LoopGeneration` with a fitness score (1.0 = PASS, 0.0 = FAIL)
  and the instructions snapshot that produced it. The archive persists to `FileStore` across
  restarts (last 100 generations kept).

  ```python
  archive = loop.generation_archive   # list[LoopGeneration]
  best = loop.best_generation()       # LoopGeneration with highest score
  print(f"Best run: gen {best.gen_id} scored {best.score} ({best.state})")
  ```

  New public type: `LoopGeneration` (exported from `tvastar`).

- **`MakerChecker` persistent rejection memory** — Checker `REJECTED` verdicts are now
  persisted to `FileStore` across runs (last 5 kept). The Maker's prompt on the next
  `trigger()` includes a "Cross-Run Rejection History" section so it learns from patterns
  that caused rejection in previous sessions — not just the current round.

## [0.11.0] — 2026-06-15

### Added

- **Loop Engineering layer** — `Loop`, `LoopConfig`, `LoopState`, `LoopRun`, `LoopEvent`,
  `FailureKind`. A first-class primitive for agents that run on a schedule with
  automatic verify, retry, and handoff.

  ```python
  loop = CISweeper(
      model=AnthropicModel("claude-sonnet-4-6"),
      schedule="*/15 * * * *",
      cancel_after=300.0,
  )
  await loop.start()   # runs forever — trigger → run → verify → handoff if stuck
  ```

  Lifecycle: `IDLE → TRIGGERED → RUNNING → VERIFYING → PASS/FAIL → RETRY/HANDOFF → IDLE`

  Werner-hardened failure modes:
  - Crash recovery: `_recover()` on startup detects orphaned RUNNING runs → `INTERRUPTED`
  - Exponential backoff: `base * 2^(iteration-1)` between retries (default: 30s → 60s → 120s)
  - Circuit breaker: N consecutive HANDOFF cycles → `SUSPENDED`; `loop.reset()` to resume
  - Handoff durability: persisted to store before firing, retried 3× → `HANDOFF_FAILED` if all fail
  - Scheduler watchdog: `add_done_callback` restarts dead scheduler task automatically
  - Memory-safe history: `LoopRun` stores metadata only, never full message history
  - Config validated at construction: `LoopConfig.__post_init__` checks cron schedule before 2am

- **7 pre-built loop patterns** in `tvastar.loop.patterns`:
  - `CISweeper` — fixes red builds every 15 minutes; escalates if unfixable
  - `PRBabysitter` — resolves trivial merge conflicts, flags stale PRs every 30 minutes
  - `DailyTriage` — classifies new issues by severity at 9am UTC daily
  - `DependencySweeper` — bumps patch versions, runs tests, commits if green at 3am UTC daily
  - `PostMergeCleanup` — reports TODOs + stale references after merges land
  - `ChangelogDrafter` — drafts CHANGELOG entries from commit history every Monday
  - `MakerChecker` — two-agent verification (see below)

  Every pattern ships with `_VERIFY_FOOTER` requiring explicit SUCCESS/PARTIAL/FAILURE
  and `extra_instructions=` for project-specific customisation without replacing the base prompt.

- **MakerChecker pattern** (`tvastar.loop.patterns.MakerChecker`) — two-agent verification loop:
  Maker proposes a change, Checker independently reviews it. Only `APPROVED` from the Checker
  advances to `PASS`. `REJECTED` feeds structured feedback back to Maker for the next round.

  ```python
  loop = MakerChecker(
      maker_model=AnthropicModel("claude-haiku-4-5-20251001"),
      checker_model=AnthropicModel("claude-sonnet-4-6"),
      goal="Fix the failing test in tests/test_auth.py",
      max_rounds=3,
      cancel_after=600.0,
  )
  ```

  Failure modes: Checker timeout/error → `MODEL_ERROR` (not swallowed); no verdict in
  output → treated as `REJECTED` (fail safe); `retry_backoff_base=0.0` so feedback is
  addressed immediately.

- **Handoff policies** (`tvastar.loop.handoff`):
  - `LogHandoff` — structured report to stderr with full run history
  - `CallbackHandoff` — async function `fn(run, history)`
  - `MultiHandoff` — fires all policies, reports all failures independently

- **L0→L3 Readiness Audit** (`tvastar.loop.audit`):
  `audit_loop(loop)` is a pure function that scores any Loop against 5 production-readiness
  checks and returns a `ReadinessLevel` (level 0–3, name, description, passes, gaps, warnings).

  | Level | Name | Gate conditions |
  |-------|------|----------------|
  | L0 | MANUAL | Loop exists |
  | L1 | OBSERVE | + schedule + handoff |
  | L2 | GATED | + cancel_after timeout |
  | L3 | AUTONOMOUS | + detectors + circuit breaker |

- **`tvastar loop` CLI subcommands** (Phase 3):
  - `tvastar loop init <Pattern>` — scaffold `.tvastar/loops/<name>.py` from any pattern
  - `tvastar loop run  <ref>` — trigger once, blocking; exit 0=PASS / 1=FAIL (CI-safe)
  - `tvastar loop status <ref>` — show state + last run + next scheduled time
  - `tvastar loop audit <ref>` — L0→L3 score; exits 0 only at L3 (pre-deploy gate)

- **Zero-dependency cron evaluator** (`tvastar.loop.schedule.next_run_time`):
  Supports `@yearly/@monthly/@weekly/@daily/@hourly` aliases and full 5-field cron
  (`MIN HOUR DOM MON DOW`) including ranges, steps, and comma lists. Pure stdlib.

### Changed

- `tvastar.__init__` docstring updated to the loop engineering tagline:
  *"Tvastar — the framework for loop engineering. Agent = Model + Harness / Loop = Agent + Schedule + Verify + Handoff"*
- `__version__` bumped to `"0.11.0"`
- New public exports: `Loop`, `LoopConfig`, `LoopState`, `LoopRun`, `LoopEvent`,
  `FailureKind`, `HandoffPolicy`, `LogHandoff`, `CallbackHandoff`, `MultiHandoff`,
  `CISweeper`, `PRBabysitter`, `DailyTriage`, `DependencySweeper`, `PostMergeCleanup`,
  `ChangelogDrafter`, `MakerChecker`, `ReadinessLevel`, `audit_loop`

## [0.10.0] — 2026-06-15

### Added

- **Dynamic Capability Governance** (`GovernancePolicy`) — phase-based tool enforcement
  at invocation time, after the model requests a tool call. Tamper-proof against prompt
  injection because it runs in Python code, not as a prompt instruction.

  ```python
  from tvastar import create_agent, GovernancePolicy

  gov = GovernancePolicy(
      phases={"read": {"grep", "read_file"}, "write": {"grep", "read_file", "bash"}},
      current_phase="read",
  )
  agent = create_agent("assistant", model=..., governance=gov)
  gov.set_phase("write")   # elevate at runtime
  ```

  - `is_allowed()` fails **closed** — unknown/uninitialised phase denies all calls.
  - `GovernancePolicy(phases={})` raises `ValueError` — empty policies rejected at construction.
  - `as_tool_policy()` returns a live `ToolPolicy` mirroring the current phase so masking
    and governance stay in sync from a single object.
  - `copy()` gives each `Harness.session()` an independent phase state — concurrent
    sessions cannot race on `set_phase()`.
  - Optional `approval_gate=` routes blocked calls to a human for real-time elevation.

- **Transactional Sandbox** (`harness.transaction()`) — atomic rollback of filesystem
  changes on exception.

  ```python
  async with harness.transaction(session) as sess:
      await sess.prompt("refactor this module")
      # If anything raises, the sandbox workspace rolls back to pre-prompt state
  ```

  - `VirtualSandbox.snapshot()` / `restore()` — in-memory, < 150 ms on ~1 MB.
  - `LocalSandbox.snapshot()` / `restore()` — real filesystem walk, < 500 ms on ~500 KB.
  - `workspace_rollback` and `workspace_rollback_failed` tracer spans for full observability.

- **`system_prompt_hook`** on `AgentSpec` — `Callable` applied to the system prompt
  before each model call. Supports basic `(prompt) -> str` and extended
  `(prompt, *, last_user_text="") -> str` signatures. Hook failures warn and fall back
  gracefully — they cannot crash a live session.

- **`tvastar.contrib.ltm`** — Long-Term Memory consolidation (no extra deps by default).
  Extracts factual and procedural nodes after successful sessions; injects retrieved
  context via `system_prompt_hook`. BM25 retrieval by default; optional cosine
  similarity with `sentence-transformers`. Includes injection sanitization, credential
  redaction, and model caching. Consolidation gates on `result.stopped == "end_turn"`.

- **`memory_cap_mb`** on `AgentSpec` — session memory ceiling in MB. Over limit →
  force-compact first, then stop with `stopped="memory_cap"`.

- **`ModelRetryPolicy` on `OpenAIModel`** — exponential backoff retry matching
  `AnthropicModel`. Pass `retry=ModelRetryPolicy(max_attempts=3)` to `OpenAIModel`.

- **`TaskGraph.run(concurrency=8)`** — semaphore-bounded concurrency (default 8, `0`
  = unlimited). Uses typed `_UpstreamSkipError` for clean skip propagation.

- **`fan_out` default concurrency = 8** (was `None`).

### Changed

- **`AnthropicModel` backoff** → full-jitter (`uniform(0, cap)`) to decorrelate retries.
- **`FileStore` encoding** — `/` → `%2F`, `\` → `%5C` (was `__` for both); PID-unique
  temp files; cross-process advisory lock (`msvcrt` / `fcntl`).
- **Overflow compaction** — now requires a `CompactionPolicy` and enforces 30 s cooldown.

### Fixed

- `GovernancePolicy.is_allowed()` returned `True` for unknown phase. Now fails closed.

## [0.9.0] — 2026-06-14

### Added

- **`tvastar-outbound`** — AI-powered outbound email campaign agent (new product).
  Give it a CSV of leads; it researches each one in parallel using a `TaskGraph`
  (company site via `web_browse`, news + contact via `web_search`), scores every
  lead against your Ideal Customer Profile, writes a personalised cold email for
  each qualified lead, waits for human approval via `ApprovalGate`, then sends.

  Key types and entry points:
  - `run_campaign(leads, *, model, icp, sender_name, ...) → CampaignResult`
  - `Lead`, `parse_csv()`, `parse_leads()` — flexible CSV / dict ingestion
  - `ResearchResult`, `research_lead()` — parallel TaskGraph research per lead
  - `ScoredLead`, `score_lead()` — ICP fit scoring (0.0–1.0), Pydantic-structured
  - `EmailDraft`, `write_draft()` — personalised cold email generation
  - `StdoutSender` (dev/demo) and `EmailSender` base class for SMTP/SendGrid
  - `CampaignResult` — full audit trail (researched, qualified, drafted, sent)
  - All types exported from `tvastar.outbound` and the top-level `tvastar` namespace
  - `tvastar-outbound` CLI: `--csv`, `--icp`, `--sender-*`, `--min-score`,
    `--dry-run`, `--max-leads`, `--concurrency`

## [0.8.4] — 2026-06-14

### Added

- **`CredentialFilter`** — strips secret-looking env vars from the subprocess
  environment before any command runs. Any var matching a glob pattern
  (case-insensitive) is removed so the agent cannot read or leak it.
  Default patterns cover `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`,
  `*_PASS`, `*_CREDENTIAL`, `*_CREDENTIALS`. Pass `patterns=[]` to disable.
  Available on both `LocalSandbox` and `VirtualSandbox` via the new
  `credential_filter=` constructor argument. Exported from `tvastar.sandbox`
  and the top-level `tvastar` namespace.

- **`BudgetPolicy(on_exceed="approve")`** — a third budget-exceeded mode that
  pauses the run and routes to the agent's `ApprovalGate` for human sign-off,
  rather than raising or stopping silently. The gate is presented with the
  current spend and limit; if approved the run continues (and is not prompted
  again); if denied or timed-out the run stops with `stopped="budget"`. If no
  `approval_gate` is configured, falls back to raising `BudgetExceeded`.

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

[Unreleased]: https://github.com/vanamayaswanth/tvastar/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.12.2...v0.13.0
[0.12.2]: https://github.com/vanamayaswanth/tvastar/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/vanamayaswanth/tvastar/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.8.4...v0.9.0
[0.8.4]: https://github.com/vanamayaswanth/tvastar/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/vanamayaswanth/tvastar/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/vanamayaswanth/tvastar/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/vanamayaswanth/tvastar/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/vanamayaswanth/tvastar/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vanamayaswanth/tvastar/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vanamayaswanth/tvastar/releases/tag/v0.1.0
