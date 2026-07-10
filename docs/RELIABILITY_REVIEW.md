# Reliability Review — Tvastar (Bhishma Analysis)

*"When this system is on the bed of arrows — what does it still serve, how long does it hold, and who controls when it stops?"*

---

## Current Reliability Score: 6/9

| # | Bhishma Question | Status | Assessment |
|---|-----------------|--------|------------|
| 1 | Bed of arrows — Does it degrade gracefully? | ✅ Yes | ConversationWriter degrades to in-memory on Store failure. Session continues. Loop retries. |
| 2 | Iccha Mrityu — Can it stop cleanly? | ✅ Yes | Loop has `stop()`, drains current run. Session has `close()`. SIGTERM → graceful drain. |
| 3 | Dice game — Does every alert have authority + runbook? | ⚠️ Partial | FleetObserver alerts exist but no runbooks. Tracer spans exist but not actionable alone. |
| 4 | Sahasranama — Is failure knowledge documented? | ⚠️ Partial | Error types are clear (DurableError, ModelError, etc.) but no runbook per failure mode. |
| 5 | Pratigya — Is there an SLO stated as user promise? | ❌ No | No SLO defined anywhere. No error budget. No promise to users. |
| 6 | Vow as chain — Are reliability rules still serving users? | ✅ Yes | Retry/backoff/circuit breaker all have configurable thresholds (not frozen) |
| 7 | Four generations — Can the inheriting team operate it? | ⚠️ Partial | Code is well-structured but no ops guide, no runbook, no failure-mode catalog |
| 8 | Amba's curse — Are operational actions checked for downstream harm? | ✅ Yes | Compaction boundary safety (never splits runs), checkpoint migration (backward compat) |
| 9 | Non-negotiables — Are "never" conditions defined? | ⚠️ Partial | Some exist (never re-execute interrupted tools) but not systematically cataloged |

---

## What Tvastar Already Does Well (Bed of Arrows)

### Layer 1: Session (ConversationWriter)

| Failure | Degraded State | Behavior |
|---------|---------------|----------|
| Store write fails | In-memory only | Session continues, `last_error` set, next append retries |
| Model generate fails (context overflow) | Reactive compaction | Auto-compacts, retries with shorter context |
| Model generate fails (other) | Fallback models | Tries each configured fallback in order |
| Tool execution fails | Error tool result | Returns error to model, model decides next action |
| Tool interrupted mid-flight | Interrupted marker | On resume: marker inserted, model sees it, no re-execution |

**Verdict:** Session layer has excellent graceful degradation. The bed-of-arrows pattern is deeply embedded.

### Layer 2: Loop

| Failure | Degraded State | Behavior |
|---------|---------------|----------|
| Run fails (model error) | FAIL state | Exponential backoff retry up to `max_iterations` |
| Run fails (timeout) | FAIL + TIMEOUT kind | Retry with backoff |
| Run fails (detection) | FAIL + DETECTION kind | Retry with backoff, potentially with improved instructions |
| Max retries exhausted | HANDOFF | Escalates to human via configured HandoffPolicy (Slack, callback, etc.) |
| Consecutive failures accumulate | CIRCUIT BREAKER | Loop suspends after N consecutive failures (configurable) |
| Process crashes mid-run | INTERRUPTED | Checkpoint recovery on restart, orphaned runs detected |
| Event log unavailable on recovery | FAIL | Descriptive error, continue recovering other runs |
| Budget exceeded | SUSPENDED | Loop pauses, does not run more until budget reset |

**Verdict:** Loop has full Iccha Mrityu — it knows when to stop, it degrades through retry stages, and it has a clean handoff exit.

### Layer 3: Fleet

| Failure | Degraded State | Behavior |
|---------|---------------|----------|
| EventBus publish fails | Silently discarded | Best-effort delivery, observer reconciles on restart |
| Observer misses events (downtime) | Stale state | Startup reconciliation reads from Event_Log |
| Store unavailable for health_snapshot | Fallback | Falls back to in-memory loop attributes |
| Agent sessions index corrupted | Self-heals | Re-initializes as empty list on next write |

**Verdict:** Fleet has good degradation via EventBus best-effort + reconciliation. No data is lost permanently.

---

## What's Missing (The Gaps)

### Gap 1: No SLO (Pratigya)

There is no stated user promise anywhere. No SLI. No error budget. No documented threshold for "this is broken."

**Why this matters:** Without an SLO, you can't know whether you're degrading vs broken. You can't tell users what to expect. You can't make informed tradeoff decisions.

**Proposed SLOs (user promises):**

| SLI | Target | User Promise |
|-----|--------|-------------|
| Run completion rate | 99% | "99 out of 100 runs complete without unhandled crash" |
| Quality detection accuracy | 99.9% | "If the agent silently failed, Tvastar will tell you" |
| Loop recovery after crash | < 60s | "If the process crashes, the loop resumes within 60 seconds" |
| Event log durability (FileStore/SQLite) | 99.99% | "Your conversation history is never silently lost" |
| Handoff delivery | 99.5% | "If max retries are exhausted, a human is notified" |

### Gap 2: No Runbooks (Sahasranama Not Written Before Battle)

FleetObserver can emit alerts. But there's no documented response for:
- "What do I do when the circuit breaker opens?"
- "What do I do when budget is exhausted?"
- "What do I do when the Store becomes unreachable?"
- "What do I do when reconciliation finds inconsistencies?"

**Proposed: Create `docs/runbooks/` with one file per alert:**

```
docs/runbooks/
  circuit-breaker-open.md
  budget-exhausted.md
  store-unreachable.md
  reconciliation-inconsistency.md
  handoff-triggered.md
  model-rate-limited.md
```

### Gap 3: No FMEA (Failure Mode Catalog)

The system has many failure paths but they're only discoverable by reading code. There's no catalog a new operator can read that says "here are all the ways this breaks and what happens."

**Proposed FMEA table (top 10 failure modes):**

| # | Failure | Cause | Effect | Detection | Mitigation | Severity |
|---|---------|-------|--------|-----------|------------|----------|
| 1 | Model returns garbage | Rate limit, overload, jailbreak | Findings triggered, low quality score | `unverified_completion` detector | Retry with fallback model | HIGH |
| 2 | Store write fails | Disk full, permissions, SQLite lock | Session degrades to in-memory only | `ConversationWriter.last_error` set | Continue serving, alert ops | MEDIUM |
| 3 | Tool hangs forever | External service timeout, deadlock | Run hits `cancel_after` timeout | LoopRun timeout + FAIL state | Timeout kills run, retry with backoff | HIGH |
| 4 | Context window overflow | Long conversation, large tool outputs | Model rejects request | `_is_context_overflow()` check | Reactive compaction, then retry | MEDIUM |
| 5 | Agent infinite loop (thrashing) | Model stuck in retry-fail cycle | Burns tokens, no progress | `thrash_loop` detector, `max_steps` | Detector stops run with FAIL quality | HIGH |
| 6 | Process crash mid-run | OOM, signal, hardware | Orphaned run in RUNNING state | `_recover()` on startup | Checkpoint → mark INTERRUPTED → resume | LOW |
| 7 | Handoff delivery fails | Slack/webhook down | Human not notified of stuck agent | No detection currently ⚠️ | **GAP — need retry on handoff delivery** | HIGH |
| 8 | Budget exhausted silently | Cost accumulates across runs | Loop suspended, no runs execute | `SUSPENDED` state | Alert on budget suspension (exists in Fleet) | MEDIUM |
| 9 | Event log corrupted | Partial write, race condition | Projection returns wrong data | No detection currently ⚠️ | **GAP — need checksum/validation on read** | MEDIUM |
| 10 | Model API key expired/invalid | Credential rotation, billing | All runs fail with ModelError | Loop FAIL state, retry won't help | **GAP — distinct error kind for "auth" vs "transient"** | HIGH |

### Gap 4: Handoff Delivery Has No Retry (Amba's Curse Potential)

The Loop has `_fire_handoff()` which calls `HandoffPolicy.fire()`. If the handoff target (Slack webhook, email) is down, the notification is silently lost. The user never knows their agent is stuck.

**This is a silent failure in the reliability system itself** — the exact kind of problem Tvastar is designed to detect in others.

**Fix:** Add retry with backoff to handoff delivery. If all retries fail, write to a local file as last-resort (the bed-of-arrows state for handoff itself).

### Gap 5: No Distinction Between Transient and Permanent Failures

The Loop treats all model errors the same (`FailureKind.MODEL_ERROR`). But:
- Rate limit → transient → retry makes sense
- Invalid API key → permanent → retry is pointless
- Content policy → permanent for this input → skip/handoff

**Fix:** Add `FailureKind.AUTH_ERROR` and `FailureKind.CONTENT_POLICY` to the enum. Don't retry permanent failures.

---

## Resilience Patterns Already Implemented

| Pattern | Where | Status |
|---------|-------|--------|
| **Timeout** | `cancel_after` in LoopConfig and Session | ✅ Implemented |
| **Retry with backoff** | Loop._handle_fail → exponential backoff | ✅ Implemented |
| **Circuit breaker** | Loop._consecutive_failures threshold | ✅ Implemented |
| **Fallback** | Session fallback_models + degraded mode | ✅ Implemented |
| **Checkpoint/Recovery** | Loop._checkpoint / _recover | ✅ Implemented |
| **Saga (compensating actions)** | Harness.transaction (rollback on exception) | ✅ Implemented |
| **Bulkhead** | Fleet per-agent budget isolation | ✅ Implemented |
| **Graceful shutdown** | Loop.stop() drains current run | ✅ Implemented |
| **Event reconciliation** | FleetObserver.reconcile() on startup | ✅ Implemented |

---

## Safety Properties (What Must Never Happen)

From the codebase, these are the implicit safety invariants. They should be made explicit:

| # | Property | Currently Enforced? |
|---|----------|-------------------|
| 1 | **Never re-execute an interrupted tool** | ✅ Yes — Interrupted_Marker prevents re-execution |
| 2 | **Never split a run_start/run_end pair during compaction** | ✅ Yes — `_is_mid_run()` check |
| 3 | **Never lose event log writes silently** | ⚠️ Partial — `last_error` is set but no alert |
| 4 | **Never charge budget twice for the same run** | ✅ Yes — budget attributed once in session |
| 5 | **Never run more than `max_iterations` retries** | ✅ Yes — loop state machine enforces |
| 6 | **Never block shutdown for more than `cancel_after` seconds** | ✅ Yes — asyncio.wait_for |
| 7 | **Always checkpoint before retry** | ✅ Yes — `_checkpoint()` called before state transition |
| 8 | **Always produce a run_end record (success or error)** | ✅ Yes — try/except guarantees it |
| 9 | **Never delete a legacy checkpoint format** | ✅ Yes — v1 reads supported for one major version |
| 10 | **Always handoff after max retries exhausted** | ⚠️ Partial — handoff fires but delivery not guaranteed |

---

## Recommendations (Priority Order)

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| 1 | **Define SLOs** — write them as user promises in a `docs/slo.md` | 2 hours | HIGH — enables all other reliability decisions |
| 2 | **Distinguish transient vs permanent failures** — add AUTH_ERROR, CONTENT_POLICY to FailureKind | 1 hour | HIGH — prevents pointless retries on permanent errors |
| 3 | **Add retry to handoff delivery** — don't silently lose the escalation | 2 hours | HIGH — fixes silent failure in the reliability system itself |
| 4 | **Create runbooks** for top 5 alert scenarios | 3 hours | MEDIUM — enables "four generations" operability |
| 5 | **Write FMEA table** as a living doc in `docs/failure-modes.md` | 2 hours | MEDIUM — makes reliability visible to operators |
| 6 | **Alert on event log write failures** — don't just set `last_error` silently | 30 min | MEDIUM — currently a silent degradation |

**Total estimated effort to reach 8/9: ~10 hours of documentation + 3 hours of code changes.**

---

## What the System Does Right (Acknowledgment)

Tvastar already has deeper reliability engineering than most AI frameworks:
- **Graceful degradation at every layer** (ConversationWriter → Session → Loop → Fleet)
- **Checkpoint/recovery survives process death** with backward-compatible migration
- **Event reconciliation** ensures consistency after downtime
- **Circuit breaker + exponential backoff + handoff** is a complete failure escalation chain
- **Compaction boundary safety** prevents data corruption
- **Transactional sandbox** with rollback on failure

The bed-of-arrows pattern is deeply embedded. The system knows how to degrade before dying. The gaps are mostly in **documentation and operability** — the Sahasranama hasn't been written from the arrows yet.
