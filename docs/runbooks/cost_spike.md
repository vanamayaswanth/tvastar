# Runbook: Cost Spike Alert

## Trigger Condition

FleetObserver emits `fleet.alert.cost_spike` when the cost in the current window exceeds `cost_spike_threshold` times the cost in the previous window of equal length (`window_seconds`). Default threshold ratio: 2.0x.

## Diagnosis Steps

1. **Quantify the spike** — review `current_cost`, `previous_cost`, and `cost_ratio` from the alert payload. Determine whether the spike is a brief burst or sustained increase.
2. **Identify cost contributors** — check `FleetBudget.agent_allocations` to find which agents consumed the most budget in the current window. A single runaway agent often accounts for the spike.
3. **Review task volume** — determine if cost increase correlates with legitimate traffic growth (more tasks submitted) versus inefficiency (same tasks costing more due to retries, longer model calls, or larger context windows).
4. **Check retry and circuit breaker state** — excessive retries on a failing model can inflate cost rapidly. Verify `ModelRetryPolicy` consecutive failure counts and circuit breaker state across agents.

## Remediation Actions

1. **Throttle high-spend agents** — if `FleetBudget.check_budget()` is returning False for agents near their allocation, verify throttling is effective. For runaway agents, manually reduce their allocation or pause them.
2. **Enable budget exhaustion mode** — if fleet-wide spend approaches `max_fleet_usd`, confirm the budget governance is halting new requests. Adjust `throttle_threshold` downward if the spike is outpacing the existing guard.
3. **Investigate and fix the root cause** — if cost is driven by retries (model instability), address the upstream issue. If driven by traffic, coordinate with the product team on capacity planning.

## Escalation Path

- **First responder:** On-call platform engineer — assess within 10 minutes whether spend is accelerating.
- **Escalate to:** Fleet team lead — if spend is projected to exceed `max_fleet_usd` within the next window period.
- **Escalate to:** Engineering management — if cost spike requires budget reallocation or emergency spend approval beyond configured limits.
