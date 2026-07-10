# Runbook: Model Rate Limited

## Trigger Condition

The ErrorClassifier identifies a rate-limit response from the model provider (Anthropic `RateLimitError` or OpenAI `RateLimitError`). The Loop respects the provider's `Retry-After` value and backs off accordingly. This alert fires when rate limiting is sustained or when the Retry-After delay exceeds the Loop's `cancel_after` timeout.

## Severity

**Medium** — The system handles rate limits gracefully via Retry-After, but sustained rate limiting degrades throughput and may eventually exhaust the retry budget.

## Impact

- Loop runs are delayed by the provider's requested backoff period.
- If Retry-After exceeds `cancel_after`, the delay is capped and a warning is logged — but the run may still fail if the provider continues to reject requests.
- Sustained rate limiting across multiple loops can cascade into budget exhaustion and handoff.
- The Loop Recovery Time SLO (≤ 5 minutes p95) may be breached if Retry-After values are large.

## Investigation

1. **Check stderr for Retry-After warnings** — look for `[reliability] Retry-After {N}s exceeds cancel_after` messages. These indicate the provider is requesting longer backoffs than the Loop's timeout allows.
2. **Identify the scope** — is one loop being rate limited, or all loops using the same provider/key? Multiple loops = account-level rate limit. Single loop = likely request-level or per-minute burst limit.
3. **Review request volume** — check how many runs are scheduled concurrently. Rate limits often trigger when multiple loops fire simultaneously.
4. **Check provider dashboard** — review your API usage against the provider's published rate limits. Are you near the tier ceiling?
5. **Look at the Retry-After values** — are they short (1-5s, indicating burst limiting) or long (30-60s, indicating sustained overload or tier exhaustion)?

## Resolution

### For burst rate limits (short Retry-After, resolves quickly):
1. **No action needed** — the Loop automatically respects the Retry-After value and retries. This is the system working as designed.
2. **Consider staggering schedules** — if multiple loops fire at the same time (e.g., all on the hour), offset their schedules to reduce burst load.

### For sustained rate limits (long Retry-After, repeated occurrences):
1. **Reduce concurrency** — lower the number of loops executing simultaneously against the same provider.
2. **Upgrade your API tier** — contact the provider to increase your rate limit allocation.
3. **Distribute across keys** — if you have multiple API keys, configure different loops to use different keys.
4. **Add a fallback model** — configure `fallback_model` in LoopConfig to use an alternate provider when the primary is rate limited.

### For Retry-After exceeding cancel_after:
1. **Increase `cancel_after`** — if the provider regularly requests long backoffs that are acceptable for your use case.
2. **Accept the failure** — if the backoff is unreasonably long, the run will eventually fail and hand off. This is appropriate if the provider is in a degraded state.

## Escalation

- **First responder:** On-call platform engineer — monitor for 15 minutes. If rate limiting self-resolves, no action needed.
- **Escalate to:** Team lead — if rate limiting persists for more than 30 minutes or affects more than 50% of active loops.
- **Escalate to:** Provider support — if rate limits are being applied unexpectedly (well below documented tier limits), open a support ticket with the provider.
