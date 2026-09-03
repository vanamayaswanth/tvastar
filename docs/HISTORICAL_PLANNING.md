# Historical planning archive

> [!WARNING]
> **Historical archive — not a current product contract.** This record consolidates dated work inventories, application ideas, pricing concepts, and roadmap snapshots. It is retained for decision context only. It does **not** define supported behavior, release commitments, product direction, pricing, or revenue expectations. For maintained information, use the [documentation map](README.md), [root README](../README.md), and [Changelog](../CHANGELOG.md).

## Sources retained

- `ROADMAP.md` — the 2026-07-02 audit-derived work inventory and its later status snapshot.
- `FUTURE_WORK.md` — eight application/template hypotheses and commercial scenarios.
- `COOKBOOK.md` historical product-notes section — earlier product and milestone snapshot.

## Roadmap inventory snapshot

The archived roadmap organized work into P0–P7. Item numbers, statuses, effort estimates, dates, and metrics were claims from that snapshot, not current triage.

| Area | Historical inventory |
|---|---|
| P0: ship-blocking defects | Rate-limit enforcement; suspended-loop false success; concurrent dispatch/session corruption; concurrent virtual-sandbox sync-back corruption; background retry/handoff tasks surviving `Loop.stop()`. |
| P1: high-impact defects | Child-sandbox transaction visibility; scheduler/retry double trigger; concurrent profile mutation; FIFO harness eviction; compaction growing context. |
| P2: technical debt | Memory-cap ordering; governance TOCTOU; zero step-limit handling; overflow compaction; approval feedback; public compaction race; linear receipt lookup; repeated message-size scans; workflow sandbox lifecycle; dispatch observer cleanup. |
| P3: stack slices | Planning, reflection, cost-aware model selection, semantic memory, RAG, memory consolidation, relevant-episode injection, external tool serving, browser automation, stateful REPL, event triggers, rate governance, agent RPC, scaling, webhook intake, dashboard, visual workflow builder, output guardrails, secret rotation, RBAC, alerting, regression detection, and token attribution. |
| P4: hardening | Loop CLI, Fleet persistence/shutdown, shared provider circuit breaking, asynchronous event delivery, async locks, durable defaults, routing performance, dead-letter handling, and a unified audit stream. |
| P5: product ideas | `tvastar-ci`, `tvastar-secure`, `tvastar-deploy`, `tvastar-oncall`, `tvastar-cost`, `tvastar-rollout`, `tvastar-portal`, and `tvastar-cloud`. |
| P6: maintainability | Small type, deduplication, serialization, indexing, cache, connection, and resource-lifecycle improvements. |
| P7: assurance | Property-based coverage for Fleet registry, gateway, state, event bus, budget, observer, deploy, model routing, dependencies, extras, and zero-dependency constraints. |

The source also listed a completed snapshot for Fleet modules/backends, bounded collections, indexes, tracing context isolation, cache/resource caps, cleanup APIs, progressive context compaction, loop supervision, compliance verification, chaos evaluation, subagent permissions, checkpoints, adaptive scheduling, cost metrics, sandboxing, readiness, and memory interchange. Treat each completion marker as historical status only.

The suggested execution sequence was stability, connectivity, intelligence, operations, platform, then application work. Its targets—bug count, coverage, routing latency, memory use, and revenue positioning—are preserved as a planning snapshot rather than active targets.

## Application and template hypotheses

The following were proposed applications built around the harness. Their features, phases, references, pricing, and revenue are historical hypotheses, not current offerings.

| Concept | Historical workflow | Proposed focus |
|---|---|---|
| `tvastar-ci` | Detect a build failure, repair, verify, then create a reviewable result or hand off | Webhook intake, test selection, parallel runners, automated pull requests, loop-health display |
| `tvastar-secure` | Scan, triage, safely remediate, verify, then escalate | Scanner orchestration, deduplication, severity policy, common remediation templates, compliance reports |
| `tvastar-deploy` | Plan, deploy, verify, promote or roll back | Canary/blue-green/rolling selection, metric gates, anomaly detection, approval, environment promotion |
| `tvastar-cost` | Scan accounts, rank savings, approve action, verify savings | Cloud-cost ingestion, idle-resource detection, right-sizing, anomaly/budget visibility |
| `tvastar-oncall` | Ingest alert, correlate change, diagnose, remediate or escalate | Alert ingestion, runbooks, severity classification, complete handoff context, post-incident report |
| `tvastar-rollout` | Progress a flag through metric gates or roll back | Feature-flag integration, cohort metrics, automatic rollback, business-hour schedules, experimentation |
| `tvastar-portal` | Answer from internal knowledge and route governed actions | Documentation/code indexing, service catalog integration, read-only default, approval for effects |
| `tvastar-cloud` | Run hosted agents with controls and observation | Deployment, multi-tenant loop management, dashboard, organization access controls, usage accounting |

The source placed CI and security concepts first, deployment and incident response next, cost and rollout later, and portal/cloud after that. It proposed per-repository, per-scan, per-deployment, per-incident, savings-share, per-seat, and usage-based pricing, along with a combined $480K first-year revenue model. None of these commercial figures is a current forecast or commitment.

## Cookbook product and roadmap snapshot

The former Cookbook archive named the verified `tvastar-fix` test-repair flow as the reference workflow; maintained guidance for it remains in [Getting Started](GETTING_STARTED.md#first-verified-action-ci-repair). It also described six historical product ideas:

- `tvastar-outbound`: research, score, draft, approve, and send outbound messages.
- `tvastar-comply`: record redaction/audit concepts, including a local token-vault proposal.
- `tvastar-review`: a pull-request review automation idea.
- `tvastar-devops`: a production diagnosis and repair-loop idea.
- `tvastar-support`: persistent, multi-channel support automation.
- `tvastar-research`: parallel research and report generation.

The archived milestone table recorded releases and planned versions from web tools and DAG execution through governance, loop engineering, assurance, detector experiments, and future products. Those labels, version numbers, and product claims are intentionally not reproduced as current facts; use the [Changelog](../CHANGELOG.md) for released behavior.
