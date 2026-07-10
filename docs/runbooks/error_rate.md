# Runbook: Error Rate Alert

## Trigger Condition

FleetObserver emits `fleet.alert.error_rate` when the ratio of error outcomes to total outcomes within the configured window (`window_seconds`) exceeds `error_rate_threshold` (default: 0.5).

## Diagnosis Steps

1. **Check the error rate and volume** — review `error_rate`, `error_count`, and `total_count` from the alert payload to understand severity and whether it represents a spike or sustained degradation.
2. **Identify error patterns** — inspect structured logs filtered by `correlation_id` for recent requests. Look for common exception types (ModelError, ToolError, SandboxError) to narrow the failure category.
3. **Check dependency health** — verify model provider connectivity (circuit breaker state), MCP server status (degraded state tracker), and sandbox availability. A single upstream failure can cause correlated errors across agents.
4. **Review recent changes** — check if any deployments, config changes, or SecurityPolicy updates were applied within the alert window that could explain the spike.

## Remediation Actions

1. **Restart affected dependency** — if errors trace to a single upstream (MCP server disconnect, sandbox overload), restart or reconnect the dependency and confirm the degraded state clears.
2. **Pause high-error agents** — transition agents with sustained error rates to `PAUSED` state to prevent further user impact while investigating root cause.
3. **Widen the alert window** — if the spike is transient (e.g., brief network blip), consider whether `window_seconds` is too narrow, causing noisy alerts.

## Escalation Path

- **First responder:** On-call platform engineer — triage within 10 minutes.
- **Escalate to:** Infrastructure team — if errors correlate with network, sandbox, or compute resource issues.
- **Escalate to:** Fleet team lead — if error rate persists after remediation or affects more than 30% of active agents.
