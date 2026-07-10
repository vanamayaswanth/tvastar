# Error Handling Philosophy

Tvastar classifies every operation into one of two error-handling categories.
This contract is the foundation of the library's reliability model: **extension
points and observability must never crash a run**, while **user-contractual
operations must surface failures loudly**.

---

## "Must-Not-Break-a-Run" — Swallow + Warn

These operations wrap user-supplied code or are purely observational. When they
fail, the agent loop continues and the failure is logged or silently swallowed
so that the run reaches a usable result regardless of hook/middleware issues.

| Operation | Behavior on Error |
|-----------|-------------------|
| `pre_tool_hook` | Log warning, use original args |
| `post_tool_hook` | Log warning, use original result |
| `step_callback` | Log warning, continue loop |
| `stop_predicate` | Log warning, continue loop (treat as `False`) |
| `middleware` (each) | Log warning, skip that middleware |
| `system_prompt_hook` | Log warning, return un-hooked prompt |
| `_checkpoint()` | Emit tracer event, set `last_checkpoint_error`, continue |
| Compaction | Return original messages, continue |
| Masking / ToolPolicy | Fall back to exposing all tools |
| Tracing / observability | Silently swallow |
| Observer callbacks (dispatch) | Silently swallow |
| Reflection | Silently swallow, return un-reflected result |

### Rationale

Extension points exist to *observe* or *lightly transform* behavior. If they
break, it is safer to fall back to the default path (original args, original
result, all tools visible, etc.) than to abort the entire agent run. Users can
detect these failures through warnings and the `last_checkpoint_error` attribute
without losing their run.

---

## "User-Facing Error" — Raise

These operations represent user-contractual behavior where the correct action
on failure is to inform the caller. Raising ensures the user can handle the
situation programmatically (retry, escalate, prompt the human, etc.).

| Operation | Exception |
|-----------|-----------|
| Budget exceeded (`on_exceed="raise"`) | `BudgetExceeded` |
| Approval gate denied | `ApprovalDenied` |
| Approval gate timeout | `ApprovalTimeout` |
| Tool not found | `ToolNotFound` |
| Structured output strict mode parse failure (after retries) | `StructuredOutputError` |
| Task depth exceeded | `RuntimeError` |
| Unknown governance phase | `ValueError` |
| Model generate (no fallback / compaction path) | Provider exception (pass-through) |
| Cancel timeout | `asyncio.TimeoutError` |

### Rationale

These represent boundaries the user explicitly configured (budgets, approvals,
governance phases) or irrecoverable situations (tool missing, depth blown). The
library has no safe default to fall back to, so it raises and lets the caller
decide what to do.

---

## Fallback Model Error Handling

The fallback chain (`fallback_models`) follows specific rules:

1. **Context-overflow exceptions** bypass the fallback chain entirely — they are
   handled by the compaction system instead.
2. **Non-overflow exceptions** from the primary model trigger the fallback
   chain. Each fallback model is tried in declaration order.
3. If **all fallback models fail**, the **primary model's original exception**
   is raised to preserve error context and stack trace.

---

## Error Classification — Permanent vs Transient Failures

The Loop engine uses an `ErrorClassifier` to distinguish permanent failures from transient ones:

| Classification | Behavior | Examples |
|---|---|---|
| `AUTH_ERROR` | Skip retry, immediate HANDOFF | Expired API key, revoked credentials |
| `CONTENT_POLICY` | Skip retry (or try fallback model once) | Content safety filter triggered |
| `MODEL_ERROR` with `retry_after_seconds` | Use provider's Retry-After instead of exponential backoff | Rate limiting |
| `None` (unrecognized) | Default behavior (MODEL_ERROR + exponential backoff) | Unknown exceptions |

**Classifier failure handling:** If the configured `error_classifier` itself raises, the Loop catches silently and returns None — classifier bugs never break a run.

---

## ConversationWriter Degraded Mode

When the Store backend fails during `append()`:

1. `last_error` is set to a `DurableError` (includes session_id and operation)
2. A `"session.degraded"` event is emitted **once** per None→Error transition
3. Subsequent failures while already degraded do NOT emit duplicate events
4. When the Store recovers (next successful write), `"session.recovered"` is emitted
5. The writer continues operating in-memory — records are still returned to callers

This is best-effort durability. With `InMemoryStore`, events are not durable across restarts. With `FileStore`/`SQLiteStore`, events survive crashes up to the last successful `Store.set()`.

---

## Guidelines for Contributors

When adding a new operation or extension point:

1. Ask: *"If this fails, is there a safe default that preserves the run?"*
   - **Yes** → Swallow + warn. Wrap in `try/except`, log via `warnings.warn()`,
     and fall back to the default behavior.
   - **No** → Raise. Let the exception propagate to the caller.

2. Never catch `BaseException` — always catch `Exception` so that
   `KeyboardInterrupt` and `SystemExit` propagate normally.

3. For "swallow" operations, prefer `warnings.warn(...)` over silent passes so
   that users can discover issues via Python's warnings infrastructure
   (`-W error` to promote to exceptions in testing, for example).

4. For "raise" operations, use domain-specific exception types
   (`BudgetExceeded`, `ToolNotFound`, etc.) so callers can handle them
   selectively.
