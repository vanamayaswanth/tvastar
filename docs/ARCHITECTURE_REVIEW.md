# Clean Architecture Review — Tvastar

**Score: 6/10** (core is testable but some details leak inward; framework not the skeleton, but boundaries are porous)

---

## Quick Diagnostic

| # | Question | Status | Issue |
|---|----------|--------|-------|
| 1 | Can you test business rules without DB, web, framework? | ✅ Yes | Session, Loop, Detect all testable with InMemoryStore |
| 2 | Do all source dependencies point inward? | ⚠️ Mostly | Cycle: agent→compaction→session→agent. Harness imports serving/deploy (outward) |
| 3 | Can you swap the database without touching business logic? | ✅ Yes | Store interface abstracts InMemory/File/SQLite cleanly |
| 4 | Are Use Cases independent of delivery mechanism? | ⚠️ Mostly | Session/Loop are delivery-agnostic, but Harness knows HTTP+Lambda+CLI |
| 5 | Is the framework confined to outermost circle? | ✅ Yes | No framework dependency in core — only stdlib + anthropic SDK |
| 6 | Is the component graph cycle-free? | ❌ No | agent↔compaction↔session cycle (TYPE_CHECKING workaround) |
| 7 | Does Main (composition root) wire all dependencies? | ⚠️ Mostly | `create_agent()` is the factory, but Harness also constructs internally |

---

## God Nodes (highest coupling = highest risk)

| Node | Edges | Layer | Problem |
|------|-------|-------|---------|
| **Message** | 79 | Entity | Acceptable — it's a core entity used everywhere |
| **Harness** | 76 | ??? | Straddles Use Case + Adapter — knows about deploy, serving, workflow |
| **Session** | 56 | Use Case | 1456 lines, imports from 7+ modules — shallow module |
| **RunResult** | 54 | Entity/DTO | Recently improved (projection), still high coupling |
| **AgentSpec** | 53 | Entity | Configuration god-object — carries 20+ fields |
| **Loop** | 47 | Use Case | Reasonable for an orchestrator at this level |

---

## Findings & Recommendations

### 1. RESOLVED — Harness God Node (76 edges) is a Legitimate Composition Root

**Initial assessment:** Harness appeared to import from serving/, deploy/, outbound/ (Dependency Rule violation).

**Actual finding on deeper inspection:** Harness does NOT import from those modules. The 76 edges are overwhelmingly *inbound* — other modules depend on Harness, not the reverse. Harness imports only from:
- `agent.py`, `session.py` (use cases — correct direction)
- `memory/store.py`, `durable.py`, `observability.py` (infrastructure — same layer)
- `conversation/`, `sandbox/` (domain infrastructure — correct direction)

serving/, deploy/, outbound/ all import FROM Harness — the correct outward→inward direction.

**Conclusion:** Harness is the application's composition root (Clean Architecture's "Main"). High inbound edge count is *expected* for a composition root. No fix needed.

**Updated score: 7/10** (bumped from 6 since this was not a real violation).

---

### 2. HIGH — Import Cycle: agent ↔ compaction ↔ session

**Problem:** `agent.py` defines `AgentSpec` which references `CompactionPolicy`. `compaction.py` needs `Session` (TYPE_CHECKING) to type its `compact_session()` function. `session.py` imports `AgentSpec`. Circular.

**Current workaround:** `TYPE_CHECKING` guard — runtime-safe but architecturally broken.

**Fix:** Compaction is a *strategy* applied by Session, not a peer of Agent. Move `CompactionPolicy` into Session's domain (it's a Session concern — when to compact the *session's* context window):

```
BEFORE: agent.py defines CompactionPolicy fields
        compaction.py imports Session for type hints
        session.py imports AgentSpec which has CompactionPolicy

AFTER:  types.py or a new compaction/policy.py defines CompactionPolicy (data-only)
        agent.py references it (no cycle — types is innermost)
        session.py imports CompactionPolicy from types
        compaction.py imports only types/Message (no Session)
        Session calls compaction functions, passing itself as argument (DIP)
```

**Impact:** Eliminates the only real import cycle. Clean dependency direction restored.

---

### 3. MEDIUM — Session is a Shallow Module (1456 lines)

**Problem:** Session's interface (prompt/run/task) is simple, but its implementation touches compaction, tools, detect, conversation, memory, masking, approval, observability. The "information hidden" ratio is low — callers need to understand Session's behavior deeply to use it correctly.

**Current state after unified-event-bridge:** Better than before (RunResult is now a projection, lifecycle records are event-sourced). But the module is still too large.

**Fix — not urgent, track for next refactor:**
- Extract `_execute_tools()` block into a dedicated `ToolExecutor` class (it's 100+ lines with retry/hook/sandbox logic)
- The stop_predicate, budget enforcement, and memory cap logic could become `RunPolicy` strategies injected via AgentSpec

**Impact:** Session drops from 1456 to ~900 lines. Each extracted piece is independently testable.

---

### 4. MEDIUM — AgentSpec is a Configuration God-Object

**Problem:** 53 edges, 20+ fields. Carries model config, tool config, sandbox config, compaction config, budget config, governance config, skills, middleware, hooks, detectors. Changes to any of these force AgentSpec to change.

**Violates:** SRP (serves multiple actors — model team, security team, ops team).

**Fix — gradual extraction:**
```python
# Instead of one massive AgentSpec with 20 fields:
@dataclass
class AgentSpec:
    name: str
    model: Model
    instructions: str
    execution: ExecutionConfig    # max_steps, temperature, thinking_level
    tools: ToolConfig             # tool specs, pre/post hooks
    safety: SafetyConfig          # sandbox, permissions, governance
    observability: ObsConfig      # detectors, tracer, middleware
```

**Impact:** Each sub-config can evolve independently. Tool changes don't touch safety config.

---

### 5. LOW — Fleet Coupling Triad

**Problem:** FleetBudget (51), FleetRegistry (47), FleetObserver (45) = 143 combined edges. They're already in a `fleet/` package but each exposes a wide interface.

**Already partially fixed:** EventBus subscription (task 7.1-7.3) decoupled Observer from Loop. Agent-sessions index (task 6.1) gave Observer O(1) history access.

**Remaining opportunity:** FleetBudget could be event-driven too (budget events → bus → budget policy) instead of direct per-agent checking. But this is lower priority given the unified-event-bridge work already reduced coupling here.

---

## What's Already Good

| Aspect | Score | Notes |
|--------|-------|-------|
| **Store abstraction** | 10/10 | Clean interface, 3 implementations, no leakage |
| **EventBus decoupling** | 9/10 | Fleet components communicate via events (just completed) |
| **Model abstraction** | 9/10 | Clean Model protocol, multiple providers, no leakage |
| **Conversation event sourcing** | 9/10 | Append-only log, projections, no parallel state |
| **Test independence** | 8/10 | InMemoryStore + MockModel = full test isolation |
| **Tool isolation** | 8/10 | ToolRegistry + ToolSpec protocol, sandbox-contained |
| **113 communities** | 7/10 | High modularity — many small cohesive clusters |

---

## Priority Order

1. ~~**Split Harness**~~ — NOT NEEDED (verified: correct composition root, no Dependency Rule violation)
2. **Break the cycle** ✅ DONE — CompactionPolicy extracted to `compaction_policy.py`
3. **Session deepening** (track for next major refactor, not urgent)
4. **AgentSpec splitting** (gradual — do it as those sub-areas evolve)
5. **Fleet budget events** (nice-to-have after event-bridge stabilizes)

---

## Scoring Rationale

**7/10 because:**
- ✅ Business rules ARE testable in isolation (Session + InMemoryStore + MockModel)
- ✅ Store IS swappable (proven by 3 backends)
- ✅ No framework dependency in core
- ✅ Harness is a clean composition root with correct dependency direction
- ✅ Import cycle FIXED (CompactionPolicy extracted to entity layer)
- ⚠️ Session is still a shallow module (1456 lines, many responsibilities)
- ⚠️ No strict layered enforcement (imports are convention-based)

To reach 8/10: deepen Session (extract ToolExecutor, RunPolicy). To reach 9/10: also split AgentSpec.
