# Historical technical review archive

> [!WARNING]
> **Historical archive — not a current product contract.** This record consolidates dated architecture and reliability reviews. It preserves diagnostic reasoning, proposed remedies, and snapshots of then-current gaps; it does **not** replace maintained architecture, error, SLO, failure-mode, or runbook documentation.

Use [Architecture Map](ARCHITECTURE_MAP.md) and [Architecture Decision Records](ARCHITECTURE.md) for current system structure and decisions. Use [Error Handling](error-handling.md), [Failure Modes](failure-modes.md), [SLOs](slo.md), and [runbooks](runbooks/) for maintained operational guidance.

## Sources retained

- `ARCHITECTURE_REVIEW.md` — clean-architecture diagnostic.
- `RELIABILITY_REVIEW.md` — reliability and operability gap analysis.

## Architecture review snapshot

### Score evolution and corrected conclusion

The review initially scored the implementation **6/10**, then updated it to **7/10** after tracing dependencies. The key correction was that `Harness` was a high-inbound **composition root**, not a dependency-rule violation: outer serving, deployment, and application modules depended on it, while it did not import those outer modules. The archive preserves that correction so the early concern is not mistaken for an unresolved defect.

### Observations and recommendations

| Area | Historical observation | Historical recommendation |
|---|---|---|
| Import cycle | `AgentSpec`, compaction, and session typing formed a cycle, hidden at runtime by `TYPE_CHECKING` | Move compaction policy data to an inner data-only location and keep session-specific behavior in Session |
| Session | A large Session implementation spanned tool execution, compaction, detection, memory, approval, masking, and observation | Consider extracting tool execution and run-policy strategies when the boundary needs deepening |
| AgentSpec | One configuration object carried model, tools, sandbox, compaction, budget, governance, skills, middleware, hooks, and detectors | Split gradually into execution, tooling, safety, and observation configuration as those areas evolve |
| Fleet | Budget, registry, and observation had broad interfaces | Consider event-driven budget handling after the event bridge stabilizes |

The source marked the compaction-cycle fix as completed at that point, left Session deepening and AgentSpec splitting as non-urgent refactor candidates, and described Fleet budget events as optional. Its positive findings were store abstraction, event-bus decoupling, model abstraction, conversation event sourcing, test isolation, and tool isolation.

## Reliability review snapshot

### 6/9 diagnostic

The review rated reliability **6/9**. It credited graceful degradation, clean stop control, configurable retry/circuit-breaking, and several implicit safety rules. It marked alert authority/runbooks, failure knowledge, explicit SLO promises, inheritor operability, and a complete invariant catalogue as partial or missing at the time.

### Historical degradation inventory

| Layer | Recorded degradation behavior |
|---|---|
| Session | Fall back to in-memory behavior after a store-write failure, compact after context overflow, try model fallbacks, return tool errors to the model, and mark interrupted tool work to prevent re-execution. |
| Loop | Retry model, timeout, and detection failures; hand off after exhaustion; suspend after consecutive failures or budget exhaustion; recover interrupted work from checkpoints. |
| Fleet | Use best-effort event delivery with restart reconciliation, fall back to in-memory health data, and rebuild a corrupt session index. |

### Gaps and proposed remedies

The review proposed user-facing SLOs for completion, detection, crash recovery, event-log durability, and handoff delivery. It recommended runbooks for circuit breakers, budgets, stores, reconciliation, handoffs, and provider throttling; the maintained documentation now owns those concerns.

Its historical FMEA called out model errors, store failures, hung tools, context overflow, thrashing, process crash, failed handoff delivery, budget exhaustion, event-log corruption, and invalid credentials. Two gaps received special emphasis: retrying or durably recording a failed handoff, and distinguishing transient provider failures from permanent authentication or policy failures so the loop does not spend retries pointlessly.

The source listed the following safety invariants: do not re-execute interrupted tools; do not split a run boundary during compaction; surface event-log failure; charge a run once; obey retry limits; bound shutdown; checkpoint before retry; write a terminal run record; preserve legacy checkpoints for the supported period; and ensure handoff delivery rather than merely attempting it. These are historical review criteria, not a substitute for current tests or operation contracts.

### Priority record

The dated recommendation order was: define SLOs, distinguish permanent failures, retry/record handoff delivery, add runbooks, publish an FMEA, and alert on event-log degradation. It estimated roughly ten hours of documentation plus three hours of code for a higher score. That estimate and score are archived context only.
