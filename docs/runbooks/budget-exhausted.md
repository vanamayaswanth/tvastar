# Runbook: Budget Exhausted

## Trigger Condition

An SLO error budget is exhausted — the measured reliability over the rolling window has dropped below the SLO target. Refer to `docs/slo.md` for the specific thresholds:

- Run Completion Rate < 99.5% (rolling 7 days)
- Loop Recovery Time > 5 minutes at p95 (rolling 7 days)
- Event Log Durability < 99.9% (rolling 30 days)
- Handoff Delivery Success Rate < 99% (rolling 30 days)

## Severity

**High** — The system's reliability contract is broken. Continued degradation risks data loss or undetected stuck agents.

## Impact

- Operators cannot trust the system to self-recover from failures.
- Users may experience missed schedules, stale outputs, or lost conversation events.
- If handoff delivery budget is exhausted, operators may not be notified of agent failures at all.

## Investigation

1. **Identify which SLO is breached** — check the alert payload for the specific metric that crossed its threshold.
2. **Determine the failure pattern** — is this a sudden spike (incident) or gradual degradation (systemic issue)?
   - Spike: look for a specific time when failures started. Correlate with deployments, config changes, or provider outages.
   - Gradual: review the trend over the measurement window. Are failures evenly distributed or concentrated in specific loops?
3. **Check failure-mode catalog** — consult `docs/failure-modes.md` to identify which failure mode is active.
4. **Review related alerts** — check if circuit-breaker-open, store-unreachable, or model-rate-limited alerts fired around the same time. Budget exhaustion is usually a symptom of an unresolved underlying alert.
5. **Measure current rate** — calculate the current failure rate to determine how far below target the system is and how quickly it can recover.

## Resolution

1. **Address the underlying cause** — budget exhaustion is never the root cause. Follow the runbook for whichever specific alert triggered the failures (circuit breaker, store unreachable, rate limited, etc.).
2. **Tighten alerting** — if budget exhaustion was the first alert (meaning the underlying issue went undetected), add or tune alerts for the specific failure mode that caused it.
3. **Consider temporary mitigations:**
   - Increase retry budget (`max_iterations`) if transient failures are close to recovering but timing out.
   - Add a fallback model if CONTENT_POLICY failures are common.
   - Reduce `cancel_after` if long waits are inflating recovery time.
4. **Document the incident** — once resolved, update `docs/failure-modes.md` if a new failure mode was discovered.

## Escalation

- **First responder:** On-call platform engineer — triage within 15 minutes, identify the root-cause alert.
- **Escalate to:** Team lead — if the budget has been exhausted for more than 1 hour without a clear resolution path.
- **Escalate to:** Engineering leadership — if the SLO breach is sustained for more than 24 hours, as this may require architectural changes or provider migration.
