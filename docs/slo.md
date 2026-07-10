# Service Level Objectives

Tvastar's reliability promises to platform engineers and operators. Each SLO is expressed as a user promise — what you can expect from the system under normal operating conditions.

---

## 1. Run Completion Rate

**User promise:** At least 99.5% of scheduled loop runs will complete successfully (reach PASS state) without requiring human intervention.

| Attribute | Value |
|-----------|-------|
| Target | ≥ 99.5% |
| Measurement window | Rolling 7 days |
| Metric | `runs_passed / runs_scheduled` |
| Excludes | Runs cancelled by operator, runs during planned maintenance |

**What this means in practice:** Out of 1,000 scheduled runs, at most 5 may fail and escalate to handoff. Transient failures that self-recover via retry count as successful. Permanent failures (AUTH_ERROR, CONTENT_POLICY) count against this SLO because they indicate a configuration problem that operators should have caught.

---

## 2. Loop Recovery Time

**User promise:** When a loop run fails due to a transient error, the system will recover and complete a successful run within 5 minutes.

| Attribute | Value |
|-----------|-------|
| Target | ≤ 5 minutes (p95) |
| Measurement window | Rolling 7 days |
| Metric | Time from first FAIL to next PASS for the same loop |
| Applies to | Transient failures only (MODEL_ERROR, TIMEOUT) |

**What this means in practice:** With the default backoff schedule (30s → 60s → 120s) and 3 retry attempts, worst-case transient recovery is ~3.5 minutes. The 5-minute target provides headroom for provider recovery. If recovery consistently exceeds this target, the failure is likely not transient and the error classifier should be updated to escalate it sooner.

---

## 3. Event Log Durability

**User promise:** At least 99.9% of conversation events written to the ConversationWriter will be durably persisted to the configured Store.

| Attribute | Value |
|-----------|-------|
| Target | ≥ 99.9% |
| Measurement window | Rolling 30 days |
| Metric | `events_persisted / events_attempted` |
| Detection | `session.degraded` events on the EventBus or stderr |

**What this means in practice:** Out of 10,000 append operations, at most 10 may fall back to in-memory buffering. When this happens, the ConversationWriter emits a `session.degraded` alert so operators can investigate before the in-memory buffer is lost. Recovery is automatic — once the Store is reachable again, a `session.recovered` event confirms normal operation.

---

## 4. Handoff Delivery Success Rate

**User promise:** At least 99% of handoff notifications will be successfully delivered to operators on the first escalation cycle (3 delivery attempts).

| Attribute | Value |
|-----------|-------|
| Target | ≥ 99% |
| Measurement window | Rolling 30 days |
| Metric | `handoffs_delivered / handoffs_attempted` |
| Fallback | Failed deliveries are persisted to `handoff_fallback/{run_id}.json` |

**What this means in practice:** The system retries handoff delivery 3 times with increasing backoff (10s, 20s, 30s). If all attempts fail, a local fallback file preserves the handoff details so operators can discover stuck agents even when the notification channel is down. Fallback files are retained for 7 days (configurable) and cleaned up on loop startup.

---

## Error Budget

When an SLO is breached over its measurement window, the system is in **error budget exhaustion**. This should trigger:

1. Investigation into root cause (check `docs/failure-modes.md`)
2. Follow the relevant runbook in `docs/runbooks/`
3. Consider whether the circuit breaker limit needs adjustment

These SLOs are internal targets, not contractual commitments. They exist to make tradeoff decisions visible — not to create alert fatigue.
