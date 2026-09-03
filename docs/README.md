# Tvastar documentation

This is the canonical map for Tvastar documentation. Start with **Current documentation** for maintained behavior; **Historical reviews and roadmaps** are retained only for context and do not define product commitments.

Tvastar is a durable Python agent harness. Begin with one agent and one harness, then add durable state, control, coordination, and assurance only where the work requires them.

## Current documentation

Follow this path for supported behavior, a first implementation, and versioned changes.

| If you need to… | Read | What you will learn |
|---|---|---|
| Understand the product and the four product racks | [Root README](../README.md) | Tvastar Core, Control, Assurance, and Reference Solutions |
| Run your first agent or verified test repair | [Getting Started](GETTING_STARTED.md) | Installation, a minimal harness, `tvastar-fix`, persistent sessions, and sandbox boundaries |
| Choose the smallest runtime or control API | [Usage Guide](USAGE.md) | When to use a harness, session, workflow, dispatch, loop, or Fleet |
| Start from working code | [Examples](../examples/README.md) | Runnable examples, including the self-healing CI workflow |
| See versioned, user-visible changes | [Changelog](../CHANGELOG.md) | Released behavior and migration-relevant changes |

## Build and reference

Use these documents after the first run when you need a public API, a reusable pattern, or a focused implementation guide.

| Topic | Document | Audience / use |
|---|---|---|
| Public APIs and contracts | [API Reference](API.md) | Look up exported types, constructors, methods, and semantics |
| Recipes and capability reference | [Cookbook](COOKBOOK.md) | Use maintained examples and capability guidance for a supported API |
| Multi-agent coordination | [Fleet](fleet.md) | Add centralized routing, shared state, budgets, and observation only when multiple loops need it |
| Runtime practices checklist | [12-Factor Agents](twelve-factor-agents.md) | Assess what the harness supports and what remains application responsibility |
| Event-format draft | [Event Specification](../spec/README.md) | Consume or produce the draft interoperable event format |
| Version-specific upgrade guidance | [Exception hierarchy migration](migration/exception-hierarchy.md) | Update affected exception handling before the documented breaking change |

## Architecture and decisions

These records explain how the system is shaped and why it has those boundaries.

| Topic | Document | Use it to… |
|---|---|---|
| Components, layers, and data flow | [Architecture Map](ARCHITECTURE_MAP.md) | Understand agent declaration, runtime, capabilities/state, control, Fleet, and trust/operations |
| Major design trade-offs | [Architecture Decision Records](ARCHITECTURE.md) | Understand the decisions that remain relevant to the current architecture |
| Zero-dependency core ADR | [ADR 0001](adr/0001-zero-deps-core.md) | Review the core dependency constraint |
| Lazy Fleet imports ADR | [ADR 0002](adr/0002-lazy-fleet-imports.md) | Review optional Fleet import behavior |
| Dataclasses ADR | [ADR 0003](adr/0003-dataclass-over-pydantic.md) | Review the public data-model choice |
| Security exception migration ADR | [ADR 0004](adr/0004-security-violation-migration.md) | Understand the security exception hierarchy decision |
| Detector experiment and limits | [Benchmarks](BENCHMARKS.md) | Evaluate the scope, method, and limitations of the recorded detector experiment |

## Operations and security

Use these documents to operate a deployed system and understand its explicit trust and evidence boundaries.

| Topic | Document | Use it to… |
|---|---|---|
| Security boundaries and residual risk | [Threat Model](threat-model.md) | Identify actors, trust boundaries, mitigations, and risks that remain |
| Vulnerability reporting | [Security Policy](../SECURITY.md) | Report a security issue responsibly |
| Error behavior and degraded mode | [Error Handling](error-handling.md) | Decide whether an operation must raise or may fail open with a warning |
| Loop failure catalog | [Failure Modes (FMEA)](failure-modes.md) | Identify failure effects, detection, mitigation, and severity |
| Reliability targets | [Service Level Objectives](slo.md) | Measure the documented internal operating targets |
| Budget exhaustion | [Runbook: Budget exhausted](runbooks/budget-exhausted.md) | Respond when an SLO error budget is exhausted |
| Circuit breaker | [Runbook: Circuit breaker open](runbooks/circuit-breaker-open.md) | Safely investigate and reset a suspended loop |
| Cost anomaly | [Runbook: Cost spike](runbooks/cost_spike.md) | Contain and investigate unexpected spend |
| Elevated errors | [Runbook: Error rate](runbooks/error_rate.md) | Triage an error-rate alert |
| Escalation delivery | [Runbook: Handoff triggered](runbooks/handoff-triggered.md) | Respond to a failed or exhausted loop handoff |
| Provider throttling | [Runbook: Model rate limited](runbooks/model-rate-limited.md) | Restore capacity after a model rate-limit alert |
| Quality degradation | [Runbook: Quality degradation](runbooks/quality_degradation.md) | Investigate a quality signal or threshold breach |
| Persistent-store outage | [Runbook: Store unreachable](runbooks/store-unreachable.md) | Restore durable storage and assess degraded-session risk |

> **Evidence boundary:** detection is post-hoc, and named verification proves only the condition it checks. Neither is universal proof of correctness, security, or safety. `VirtualSandbox` is not an isolation boundary.

## Community and releases

| Topic | Document |
|---|---|
| Contributing code and documentation | [Contributing](../CONTRIBUTING.md) |
| Release history | [Changelog](../CHANGELOG.md) |
| Vulnerability reporting | [Security Policy](../SECURITY.md) |
| Issue and pull-request conventions | [GitHub templates](../.github/) |

## Historical reviews and roadmaps

These dated assessments, proposals, onboarding notes, and planning records are retained for decision context. They **do not** define current behavior, supported security guidance, release commitments, or roadmap commitments.

- [Historical product analysis](HISTORICAL_PRODUCT_ANALYSIS.md) — product assessment and forward scenario analysis
- [Historical planning archive](HISTORICAL_PLANNING.md) — work inventory, application hypotheses, and past milestone snapshots
- [Historical technical review](HISTORICAL_TECHNICAL_REVIEW.md) — architecture and reliability review context

## Documentation maintenance

Update the narrowest canonical document when behavior changes:

- Public product contract or first-run behavior → `README.md` and, if needed, `GETTING_STARTED.md`
- Public API signature or semantics → `API.md`
- Runnable behavior → the matching file under `examples/`
- Architecture or a lasting trade-off → `ARCHITECTURE_MAP.md`, `ARCHITECTURE.md`, or the relevant ADR
- Operational behavior → the matching SLO, FMEA entry, or runbook
- Versioned user-visible change → `CHANGELOG.md`

Keep claims bounded to evidence in this repository. In particular, do not describe detector results as universal correctness or describe `VirtualSandbox` as an isolation boundary.
