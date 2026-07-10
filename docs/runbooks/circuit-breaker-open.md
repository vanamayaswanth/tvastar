# Runbook: Circuit Breaker Open

## Trigger Condition

The Loop's consecutive failure counter exceeds the configured threshold. The circuit breaker key (`loop:{name}:circuit_breaker`) in the Store holds the current count. When open, the Loop will not schedule new runs until the breaker is reset.

## Severity

**High** — No new runs will execute for the affected loop until the breaker resets or is manually cleared.

## Impact

- Scheduled runs for the affected loop stop executing.
- Users relying on the loop's output receive stale or no results.
- If multiple loops share the same model provider and that provider is the cause, multiple breakers may trip simultaneously.

## Investigation

1. **Identify the affected loop** — check which loop name appears in the alert. Inspect the Store key `loop:{name}:circuit_breaker` for the current failure count.
2. **Review recent failure kinds** — examine the last N LoopRun records for the loop. Look at `failure_kind` values: are they transient (MODEL_ERROR, TIMEOUT) or permanent (AUTH_ERROR, CONTENT_POLICY)?
3. **Check model provider status** — verify the upstream provider's status page. If AUTH_ERROR: check API key validity. If TIMEOUT: check provider latency and your network.
4. **Look for fallback files** — check `{store_data_path}/handoff_fallback/` for recent JSON files from this loop. The `delivery_errors` field shows whether handoff also failed.
5. **Check structured logs** — filter stderr output for `[reliability]` prefixed messages around the time the breaker tripped.

## Resolution

1. **Fix the root cause** — if AUTH_ERROR, rotate or refresh the API key. If TIMEOUT, wait for provider recovery or switch to a fallback model.
2. **Reset the circuit breaker** — delete the Store key `loop:{name}:circuit_breaker` or set it to `"0"`. The loop will resume on its next scheduled tick.
3. **Verify recovery** — trigger a manual run or wait for the next scheduled execution. Confirm it reaches PASS state.
4. **Clear old fallback files** — if the issue generated fallback files, they'll auto-clean after 7 days. No manual action needed unless disk space is a concern.

## Escalation

- **First responder:** On-call platform engineer — triage within 15 minutes.
- **Escalate to:** Infrastructure team — if the root cause is network connectivity or credential rotation infrastructure.
- **Escalate to:** Team lead — if the breaker has been open for more than 30 minutes or affects production-critical loops.
