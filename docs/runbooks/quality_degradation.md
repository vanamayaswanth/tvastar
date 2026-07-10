# Runbook: Quality Degradation Alert

## Trigger Condition

FleetObserver emits `fleet.alert.quality` when an agent's quality score drops below the configured `quality_threshold` (default: 70). The score is computed from the most recent evaluation window.

## Diagnosis Steps

1. **Identify the affected agent** — check the `agent_name` field in the alert payload and pull its recent quality scores from the observer's `quality_scores` snapshot.
2. **Review recent deployments** — check if a canary or new version was deployed for the agent around the time quality dropped. Use `DeployManager.version_history()` to correlate.
3. **Inspect task outcomes** — query the EventBus for recent task completion events from the affected agent. Look for increased error rates, timeouts, or degraded response patterns.
4. **Check model availability** — verify the circuit breaker state on the agent's `ModelRetryPolicy`. A half-open or open circuit may indicate upstream model provider issues causing quality degradation.

## Remediation Actions

1. **Rollback the agent** — if a recent deployment correlates with the drop, trigger `DeployManager.rollback_canary()` to revert to the previous stable version.
2. **Pause the agent** — if the root cause is unclear, transition the agent to `PAUSED` lifecycle state via the FleetRegistry to stop routing new tasks while investigating.
3. **Adjust thresholds** — if the alert is a false positive due to transient load, consider raising `AlertConfig.quality_threshold` temporarily while validating the scoring pipeline.

## Escalation Path

- **First responder:** On-call platform engineer — diagnose within 15 minutes.
- **Escalate to:** Fleet team lead — if quality remains below threshold after rollback or if multiple agents are affected simultaneously.
- **Escalate to:** ML/AI team — if degradation traces back to model provider issues or prompt regression.
