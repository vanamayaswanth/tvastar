# Failure Mode and Effects Analysis (FMEA)

This document catalogs the top 10 failure modes for Tvastar's Loop engine. Each entry identifies the cause, user-visible effect, how the system detects the failure, the mitigation in place, and severity. Use this as a reference during incident response and capacity planning.

Severity levels: **Critical** (data loss or complete unavailability), **High** (feature unavailable, manual intervention needed), **Medium** (degraded but recoverable automatically), **Low** (minor impact, self-healing).

## FMEA Table

| # | Failure Mode | Cause | Effect | Detection | Mitigation | Severity |
|---|---|---|---|---|---|---|
| 1 | Model API authentication failure | Expired or revoked API credentials | Loop cannot generate responses; transitions immediately to HANDOFF | ErrorClassifier returns AUTH_ERROR; handoff fires | Skip retry (permanent failure), fire handoff, operator rotates credentials | High |
| 2 | Model API rate limiting | Exceeding provider request quota | Increased latency; runs delayed until quota resets | ErrorClassifier returns MODEL_ERROR with retry_after_seconds | Respect Retry-After header, exponential backoff, cap at cancel_after | Medium |
| 3 | Content policy rejection | Prompt or context triggers provider safety filter | Run cannot complete with current content | ErrorClassifier returns CONTENT_POLICY | Attempt fallback model if configured; otherwise skip retry and fire handoff | High |
| 4 | Store write failure (event log) | Disk full, permissions error, or backend unreachable | ConversationWriter enters degraded mode; events buffered in memory | last_error set on writer; "session.degraded" event emitted to EventBus or stderr | In-memory fallback preserves session continuity; alert operator for investigation | Medium |
| 5 | Handoff delivery failure (all 3 attempts) | Notification channel down (Slack, email, webhook unavailable) | Operator not alerted about stuck agent | All 3 delivery attempts raise; fallback file written | Write handoff details to local JSON fallback file; operator polls fallback directory | High |
| 6 | Network timeout to model provider | DNS failure, provider outage, or network partition | Run fails with TIMEOUT; enters retry cycle | asyncio.TimeoutError caught; FailureKind.TIMEOUT assigned | Exponential backoff retry up to max_iterations; then handoff | Medium |
| 7 | Circuit breaker tripped | Consecutive failures exceed threshold | Loop stops scheduling new runs until reset | consecutive_failures counter in circuit breaker store key | Operator resets circuit breaker after resolving root cause; prevents cascading load | High |
| 8 | Budget exhaustion | Token or cost budget exceeded during run | Run aborted with BudgetExceeded exception | on_exceed="raise" triggers BudgetExceeded | Caller handles exception; can increase budget or escalate to human | Medium |
| 9 | Scheduler crash or hang | Unhandled exception in Loop orchestration or deadlock on internal lock | No runs execute; loop appears frozen | External health check or watchdog detects no progress | Restart loop process; _recover() replays from last checkpoint on startup | Critical |
| 10 | Fallback model also fails | Both primary and fallback model reject content | No model can produce a response for this input | Second CONTENT_POLICY classification from fallback model | Transition directly to HANDOFF without further retry; operator reviews content | High |
