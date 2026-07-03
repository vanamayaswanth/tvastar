# Tvastar Roadmap — Complete Work List

> Generated 2026-07-02 from deep codebase audit + architecture analysis + market stack gap analysis.
> Organized by priority: Critical Bugs → High Bugs → Missing Stack Slices → Features → Polish.

---

## Stack Coverage Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 7: INTERFACE          ██████░░░░░░  (50%) — API ✓, CLI ✓, Canvas ✗    │
│ LAYER 6: FLEET              ████████████  (95%) — Built this session         │
│ LAYER 5: LOOP               ██████████░░  (85%) — Missing event triggers     │
│ LAYER 4: HARNESS            ████████████  (95%) — Mature, well-tested        │
│ LAYER 3: TOOLS              ████████░░░░  (70%) — Missing MCP server, CUA    │
│ LAYER 2: MEMORY             ██████░░░░░░  (55%) — Missing vector/RAG/decay   │
│ LAYER 1: MODEL              ████████░░░░  (75%) — Missing planning/reflect   │
│ RAIL A: SECURITY            ████████░░░░  (75%) — Missing output guardrails  │
│ RAIL B: OBSERVABILITY       ████████░░░░  (70%) — Missing live alerting      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔴 P0: Critical Bugs (Ship-Blocking)

These are real bugs found by tracing execution paths. They will hit production users.

| # | Bug | File | Impact | Fix Size |
|---|-----|------|--------|----------|
| 1 | **Rate limiting NEVER enforced** — `submit()` calls `_check_rate_limit()` (no-op stub) instead of `_check_rate_limits()` (real impl) | `fleet/gateway.py:252` | All rate limit config silently ignored | 2 lines |
| 2 | **SUSPENDED loop = false success** — gateway catches RuntimeError and returns `status="dispatched"` when loop is SUSPENDED | `fleet/gateway.py:357-368` | Silent task loss, user thinks work is happening | 5 lines |
| 3 | **Concurrent dispatch corrupts session** — same `id` + same `session` = both get same Session object, both call `.prompt()` concurrently → interleaved messages | `dispatch.py:120-145` | Data corruption in webhook servers | 10 lines |
| 4 | **Concurrent tools corrupt VirtualSandbox** — `_run_python()` sync-back overwrites files from parallel tool executions | `sandbox/virtual.py` | Silent file loss during multi-tool steps | 8 lines |
| 5 | **Ghost retry tasks after Loop.stop()** — `_delayed_retry` and `_fire_handoff` tasks are NOT tracked in `_bg_tasks`, not cancelled on stop | `loop/__init__.py:449-452` | Retries fire after loop is stopped | 4 lines |

---

## 🟡 P1: High-Impact Bugs

| # | Bug | File | Impact | Fix Size |
|---|-----|------|--------|----------|
| 6 | **Child sandbox independent of parent** — `session.task()` creates fresh sandbox, transaction rollback claim in docstring is false | `session.py:430-440` | Child can't see parent's files | 15 lines |
| 7 | **Delayed retry + scheduler can double-trigger** — both call `trigger()` within the same event loop tick after backoff | `loop/__init__.py:448-455` | Two simultaneous iterations run | 10 lines |
| 8 | **`_profile` attribute race in concurrent fan_out** — shared model object mutated by concurrent task() calls | `session.py:395-415` | Wrong mock script in tests, wrong model profile in prod | 10 lines |
| 9 | **FIFO harness eviction loses active context** — first-inserted harness evicted, not least-recently-used | `dispatch.py:123-126` | Long-running agent loses all session history | 15 lines (LRU) |
| 10 | **Compaction can increase context size** — no size guard, summary may be larger than originals for small conversations | `compaction.py:106-120` | Compaction thrash loop, repeated compaction every step | 5 lines |

---

## 🟠 P2: Medium Bugs & Tech Debt

| # | Issue | File | Impact |
|---|-------|------|--------|
| 11 | Memory cap pop removes wrong message after compaction | `session.py:568-585` | Silent data loss of compaction context |
| 12 | TOCTOU in governance when tools trigger phase changes | `masking.py` + `session.py` | Non-deterministic enforcement |
| 13 | `max_steps=0` on profile treated as falsy → falls through to parent | `session.py:460` | Can't disable step limit via profile |
| 14 | Compaction during overflow recovery may garble user intent | `session.py:520-540` | Model responds to different question |
| 15 | 300s default approval gate timeout freezes session with no feedback | `session.py:608-620` | Session appears frozen for 5 minutes |
| 16 | `compact_session()` is public API — external call during live run → reference swap race | `compaction.py:135-142` | Corrupted message iteration |
| 17 | `TrustLog.get()` is O(n) linear scan | `assurance/log.py:114-119` | Slow at 10K+ receipts |
| 18 | `_messages_size_bytes()` recomputes by iterating all messages on every step | `session.py:635-655` | O(messages × blocks) per loop iteration |
| 19 | `WorkflowHarness.fs` creates new sandbox on every access, never stops it | `workflow.py:230-237` | Resource leak (temp dirs/containers) |
| 20 | `_observers` list in dispatch has no cleanup API | `dispatch.py:84` | Memory leak in long-running servers |

---

## ✅ Already Fixed (This Session)

| Item | Status |
|------|--------|
| Fleet module implemented (all 8 sub-modules) | ✅ Done |
| Fleet exported from top-level `tvastar.__init__.py` | ✅ Done |
| SQLite LTM Store (zero-dep long-term memory) | ✅ Done |
| SQLite State Backend for Fleet | ✅ Done |
| NATS Event Backend for Fleet | ✅ Done |
| Redis State Backend for Fleet | ✅ Done |
| Fleet → Loop.trigger() bridge | ✅ Done |
| Deque caps on all unbounded lists (gateway, budget, observer, state, loop) | ✅ Done |
| Active-set index for O(k) active_agents() | ✅ Done |
| Tracer span stack uses contextvars (async safety) | ✅ Done |
| Harness cache cap with eviction in dispatch | ✅ Done |
| AgentPruner score history cap (100/profile) | ✅ Done |
| WorkflowRun event cap (1000/run) | ✅ Done |
| Loop.stop() cancels bg improvement tasks | ✅ Done |
| `unobserve_dispatch()` cleanup API | ✅ Done |

---

## � P3: Missing Stack Slices (Gap Fill)

These are capabilities the 2026 production agent stack requires that Tvastar doesn't have yet.

### Layer 1: Model / Reasoning

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 21 | **Planning / decomposition** | Built-in planner that breaks a complex goal into subtasks before execution. Input: high-level goal. Output: ordered list of subtasks fed to TaskGraph. | 3 days |
| 22 | **Reflection / self-critique** | Optional inner loop: after agent produces output, a "critic" prompt reviews it and can request a redo before returning to the user. | 2 days |
| 23 | **Cost-aware model downgrade** | When budget is 80% spent, auto-switch to a cheaper model (e.g., Opus → Sonnet) for remaining steps instead of hard-stopping. | 1 day |

### Layer 2: Memory / Context

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 24 | **Vector/semantic search** | Optional embedding-based retrieval for LTM knowledge. Use `sqlite-vec` extension (still zero infra). `pip install tvastar[vectors]` extra. | 2 days |
| 25 | **RAG pipeline** | Document chunking → embed → store → retrieve → inject into context. A `RAGStore` class that wraps LTM + embeddings. | 3 days |
| 26 | **Memory consolidation** | Background process that merges duplicate facts, decays old ones, deduplicates episodes. Runs periodically or on LTM size threshold. | 2 days |
| 27 | **Auto-inject relevant episodes** | Before each Loop trigger, pull the N most relevant past episodes and inject them into the prompt context. | 1 day |

### Layer 3: Tools / Actions

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 28 | **MCP server (expose agents as tools)** | Implement MCP server protocol so external frameworks can call Tvastar agents as MCP tools. | 3 days |
| 29 | **Browser/CUA automation** | Playwright-based browser tool. Agent can navigate, click, fill forms, screenshot. Behind `tvastar[browser]` extra. | 4 days |
| 30 | **Stateful REPL** | Persistent Python REPL session (not temp-dir per execution). Agent builds up state across multiple code executions. | 2 days |

### Layer 5: Loop / Scheduling

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 31 | **Event-driven triggers** | `LoopConfig.trigger_on = "webhook"` — register an HTTP endpoint that fires the loop on POST. Or trigger_on = "event:topic_name" via EventBus. | 2 days |
| 32 | **Loop rate governance** | Separate from retry backoff: "don't run this loop more than N times per hour" regardless of trigger frequency. | 4 hrs |

### Layer 6: Fleet / Orchestration

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 33 | **Agent-to-agent RPC** | Structured request-response between agents. Agent A sends a typed request to Agent B, waits for response. Built on EventBus but with futures/correlations. | 2 days |
| 34 | **Dynamic scaling** | Auto-spawn/retire agent instances based on queue depth. `FleetConfig.auto_scale = {"min": 1, "max": 10, "metric": "queue_depth"}`. | 3 days |

### Layer 7: Interface

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 35 | **Webhook receiver** | Built-in `/webhook` endpoint that maps incoming events → loop triggers. GitHub, Slack, PagerDuty webhook adapters. | 2 days |
| 36 | **Dashboard UI** | Web UI showing fleet health, costs, quality scores, active loops, recent runs. Reads from Observer + Budget. | 1 week |
| 37 | **Canvas (visual workflow builder)** | Full implementation per CANVAS_PRD.md — schema layer, bidirectional sync, React Flow frontend. | 2-3 months |

### Rail A: Security

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 38 | **Output guardrails** | Scan model output before returning to user. Detect harmful content, PII leaks, injection relay, markdown exfiltration. | 2 days |
| 39 | **Secret rotation** | `SecretProvider` protocol with refresh callbacks. Long-running loops auto-rotate API keys when provider signals expiry. | 1 day |
| 40 | **RBAC** | Per-user/role permissions on fleet operations. `FleetConfig.rbac = {"admin": ["*"], "viewer": ["read"]}`. | 3 days |

### Rail B: Observability

| # | Slice | What's Needed | Effort |
|---|-------|---------------|--------|
| 41 | **Live alerting integration** | Fire alerts to Slack/PagerDuty/email when Fleet Observer detects threshold breaches. `AlertConfig.notify = [SlackWebhook(...)]`. | 1 day |
| 42 | **Regression detection** | Compare recent N runs to historical baseline. Emit alert when quality drops >10% or cost rises >20% vs trailing average. | 2 days |
| 43 | **Token-level attribution** | Track cost per tool call, per model call, per agent step — not just per-run totals. | 1 day |

---

## 🚀 P4: Integration & Hardening

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 44 | **`tvastar loop` CLI** — `loop init\|run\|status\|audit` subcommands (PRD promise) | 2 days | User-facing, docs reference it |
| 45 | **Fleet registry persistence** — persist fleet state to store on shutdown, reload on start | 3 hrs | Fleet survives process restarts |
| 46 | **Global ModelCircuitBreaker** — shared across all agents, coordinated backoff | 3 hrs | Prevents thundering herd on provider outage |
| 47 | **Async EventBus publish** — `asyncio.gather` for handler delivery | 1 hr | Parallel event delivery, prevents slow-handler cascade |
| 48 | **SharedStateStore asyncio.Lock** — replace threading.Lock | 15 min | Unblocks event loop in async fleet |
| 49 | **Fleet.shutdown()** — graceful drain of in-flight tasks + persist registry | 2 hrs | Clean deploys, rolling restarts |
| 50 | **FileStore as default** instead of InMemoryStore for Harness/Loop | 30 min | Durability without explicit config |
| 51 | **Pre-computed TF-IDF routing vectors** — replace O(n×m²) SequenceMatcher | 4 hrs | 50-100x faster routing at 100+ agents |
| 52 | **Dead-letter queue for failed EventBus handlers** | 2 hrs | Visibility into silent handler failures |
| 53 | **Unified audit stream** — merge TrustLog, gateway audit, loop transitions into one queryable log | 3 days | Complete compliance trail |

---

## 🌟 P5: SaaS Products (Revenue Path)

| # | Product | Timeline | Revenue Target |
|---|---------|----------|---------------|
| 54 | **tvastar-ci** — autonomous CI agent (extends CISweeper) | Q3 2026 | $50K ARR |
| 55 | **tvastar-secure** — SAST/SCA auto-fix agent | Q3 2026 | $30K ARR |
| 56 | **tvastar-deploy** — canary deployment + metric verification | Q4 2026 | $40K ARR |
| 57 | **tvastar-oncall** — incident triage + auto-remediation | Q4 2026 | $60K ARR |
| 58 | **tvastar-cost** — cloud cost optimization agent | Q1 2027 | $100K ARR |
| 59 | **tvastar-rollout** — feature flag progressive delivery | Q1 2027 | Per-flag/month |
| 60 | **tvastar-portal** — AI developer portal (knowledge + action) | Q2 2027 | Per-seat/month |
| 61 | **tvastar-cloud** — managed agent runtime platform | Q2 2027 | Usage-based |

---

## 🧹 P6: Code Quality & Maintainability

| # | Item | File | Type |
|---|------|------|------|
| 62 | Extract `_compute_grade(score) → str` helper (duplicated) | `quality.py:61-68, 131-137` | Dedup |
| 63 | Type `LoopRun.findings` as `list[Finding]` not bare `list` | `loop/__init__.py:87` | Type safety |
| 64 | Type `LoopConfig.optimizer` properly (currently `Any`) | `loop/__init__.py:121` | Type safety |
| 65 | Remove dead `_parse_structured` function | `session.py:618` | Dead code |
| 66 | Normalize LoopRun serialization (dicts OR objects, not both) | `loop/__init__.py:330-345` | Maintainability |
| 67 | Add `run_id → position` index to TrustLog for O(1) lookup | `assurance/log.py` | Performance |
| 68 | Maintain running `_total_bytes` counter in Session | `session.py` | Avoid O(n) recompute |
| 69 | Add connection pool for SQLite LTM (concurrent reads) | `contrib/ltm/store.py` | Scalability |
| 70 | Use Welford's algorithm for canary/AB quality averaging | `fleet/deploy.py` | O(1) per check |
| 71 | Cache WorkflowHarness sandbox instance, stop on close() | `workflow.py:230-237` | Resource leak |
| 72 | Add `max_runs` config to RunRegistry for LRU eviction | `workflow.py:135-155` | Memory cap |

---

## 📊 P7: Property-Based Tests (Fleet)

| # | Test File | Properties Covered |
|---|-----------|-------------------|
| 73 | `tests/fleet/test_registry_props.py` | P1-P5, P31 (registration, FSM, defaults) |
| 74 | `tests/fleet/test_gateway_props.py` | P5-P9 (routing, audit, rate limits) |
| 75 | `tests/fleet/test_state_props.py` | P11, P15, P16 (state isolation, locking) |
| 76 | `tests/fleet/test_bus_props.py` | P12 (event delivery) |
| 77 | `tests/fleet/test_budget_props.py` | P17-P22, P32 (budget enforcement) |
| 78 | `tests/fleet/test_observer_props.py` | P23-P26, P30 (health, alerts, correlation) |
| 79 | `tests/fleet/test_deploy_props.py` | P27-P29 (traffic split, canary, rollback) |
| 80 | `tests/fleet/test_model_routing_props.py` | P10 (model override) |
| 81 | `tests/fleet/test_dependency_props.py` | P13-P14 (cycle detection, ordering) |
| 82 | `tests/fleet/test_extras_props.py` | P33 (ImportError on missing extras) |
| 83 | `tests/fleet/test_zero_deps.py` | No third-party imports in fleet core |

---

## Execution Order Recommendation

```
Week 1 — Stability:
  P0 bugs #1-5 (critical — 1 day)
  P1 bugs #6-10 (high — 1 day)
  #48 asyncio.Lock (15 min)
  #45 fleet persistence (3 hrs)
  #49 Fleet.shutdown() (2 hrs)

Week 2 — Connectivity:
  #31 event-driven triggers (2 days)
  #35 webhook receiver (2 days)
  #41 alerting integration (1 day)
  #44 tvastar loop CLI (2 days)

Week 3 — Intelligence:
  #21 planning/decomposition (3 days)
  #22 reflection/self-critique (2 days)
  #24 vector search for LTM (2 days)

Week 4 — Operations:
  #46 model circuit breaker (3 hrs)
  #38 output guardrails (2 days)
  #42 regression detection (2 days)
  #51 TF-IDF routing (4 hrs)

Week 5 — Platform:
  #28 MCP server (3 days)
  #33 agent-to-agent RPC (2 days)
  #36 dashboard UI (1 week)

Week 6+:
  #54 tvastar-ci (first revenue product)
  #37 Canvas (visual builder — ongoing)
  P6 code quality batch
  P7 property-based tests
```

---

## Metrics to Track

| Metric | Current | Target |
|--------|---------|--------|
| Critical bugs | 5 | 0 |
| Stack coverage | 75% | 90% |
| Test count | 1642 | 2000+ (with PBT) |
| Fleet test coverage | 116 unit | 116 + 33 PBT properties |
| Routing latency (50 agents) | ~100ms (SequenceMatcher) | <5ms (TF-IDF) |
| Max memory at 24hr runtime | Capped (deque) | <200MB |
| Dispatch safety | Race-able | Lock-protected |
| Missing stack slices | 14 | <5 |

---

## Competitive Position After Executing This Roadmap

```
After Week 1:  Stable enough for early adopters (bugs fixed)
After Week 2:  Connected to the real world (webhooks, alerts, CLI)
After Week 3:  Smarter than competitors (planning, reflection, semantic memory)
After Week 4:  Safer than competitors (guardrails, regression detection, circuit breaker)
After Week 5:  Interoperable (MCP server, agent RPC, dashboard)
After Week 6+: Revenue-generating (tvastar-ci, Canvas)
```

---

*Last updated: 2026-07-04 (v0.20.0 + fleet-engineering + LTM + backends)*
