# Fleet — Multi-Agent Orchestration

Fleet manages multiple agent Loops as a single cohesive unit with centralized routing, shared state, cost governance, and observability.

## Conceptual Overview

A Fleet wires six components together:

```
Fleet = Registry + Gateway + SharedState + EventBus + Budget + Observer
```

| Component | Responsibility |
|-----------|---------------|
| **FleetRegistry** | Agent registration, lifecycle transitions (registered → active → paused → retired), dependency tracking, version history |
| **FleetGateway** | Semantic routing of tasks to the best-matching ACTIVE agent, rate limiting, model routing policy |
| **SharedStateStore** | Cross-agent key/value state with optimistic locking for consistency |
| **EventBus** | Pub/sub for fleet-wide events — alerts, budget warnings, custom coordination |
| **FleetBudget** | Cost governance with per-agent allocations, warning/throttle thresholds |
| **FleetObserver** | Quality/error/cost monitoring with threshold-based alerts and runbook references |

The `Fleet` class instantiates all components from a single `FleetConfig` and exposes them as properties. You interact with them directly or through Fleet's convenience methods (`register`, `submit`, `shutdown`).

## Working Examples

### Creating a Fleet

```python
from tvastar.fleet import Fleet, FleetConfig, FleetBudgetConfig

config = FleetConfig(
    name="research-fleet",
    budget=FleetBudgetConfig(max_fleet_usd=50.0),
)
fleet = Fleet(config)
```

### Registering Agents

```python
from tvastar import create_agent, Harness

# Create a Loop (agent) using the standard API
loop = create_agent(model="gpt-4o", tools=[])

# Register it in the fleet
entry = fleet.register(loop, name="researcher", version="1.0.0", owner="ml-team")

# Activate so it can receive tasks
fleet.registry.activate("researcher")
```

Agents start in `REGISTERED` state — call `activate()` to make them routable.

### Submitting Tasks

```python
import asyncio

async def main():
    result = await fleet.submit("Summarize the Q3 earnings report")
    print(result["agent_name"])   # the agent that handled it
    print(result["routing_score"])  # semantic match confidence

asyncio.run(main())
```

To route to a specific agent explicitly:

```python
result = await fleet.submit("Draft the email", agent="writer")
```

### Configuring Budgets

```python
config = FleetConfig(
    name="governed-fleet",
    budget=FleetBudgetConfig(
        max_fleet_usd=100.0,
        allocations={"researcher": 40.0, "writer": 30.0},
        warn_threshold=0.8,       # warn at 80% spend
        throttle_threshold=0.9,   # throttle at 90% spend
        exempt_agents=["monitor"],
    ),
)
fleet = Fleet(config)
```

Record costs as they occur:

```python
fleet.budget.record_cost("researcher", 2.50)

# Check if an agent is still within budget
can_proceed = fleet.budget.check_budget("researcher")
```

### Handling Events

```python
from tvastar.fleet import FleetEvent

def on_alert(event: FleetEvent):
    print(f"Alert from {event.source_agent}: {event.payload}")

# Subscribe to a topic
sub_id = fleet.bus.subscribe("fleet.alert.quality", on_alert)

# Or pass handlers at config time for all alert topics
config = FleetConfig(
    name="observed-fleet",
    alert_handlers=[on_alert],
)
```

### Persistence and Shutdown

```python
async with Fleet(config) as fleet:
    fleet.register(loop, name="worker", version="1.0.0")
    fleet.registry.activate("worker")
    await fleet.submit("do the thing")
# On exit: state persisted, loops stopped, backends closed

# Manual persist/load
fleet.persist("state.json")
fleet.load("state.json")
```

## Troubleshooting

### RegistrationError

**Symptoms:** Exception raised during `fleet.register()`.

**Causes:**
- Duplicate `(name, version)` pair — an agent with that exact name and version is already registered.
- Dependency cycle — the `dependencies` list creates a circular reference (A → B → A).

**Resolution:**
- Use a different version string if re-registering an updated agent.
- Review `dependencies` lists for cycles. The registry rejects cycles of any length (2–10+ agents).

### BudgetExhaustedError

**Symptoms:** Raised when recording cost or submitting a task for an agent whose budget is spent.

**Causes:**
- Fleet-level spend reached `max_fleet_usd`.
- Per-agent allocation exhausted.

**Resolution:**
- Increase `FleetBudgetConfig.max_fleet_usd` or the agent's allocation.
- Add the agent to `exempt_agents` if it should bypass budget checks.
- Check current spend: `fleet.budget.fleet_spent()`.

### RoutingError

**Symptoms:** Raised during `fleet.submit()`.

**Causes:**
- No ACTIVE agents match the task above the routing threshold.
- Explicit `agent=` name doesn't exist or isn't in ACTIVE state.

**Resolution:**
- Ensure at least one agent is activated: `fleet.registry.activate("name")`.
- Check agent states: `fleet.registry.get("name").state`.
- Lower `FleetConfig.routing_threshold` if semantic scores are too conservative (default 0.3).

### ConflictError

**Symptoms:** Raised during `SharedStateStore.put()` with optimistic locking.

**Causes:**
- Another agent wrote to the same key between your read and write — the `expected_version` doesn't match the current version.

**Resolution:**
- Re-read the key to get the latest version, then retry the write.
- This is expected under concurrent access — implement a read-modify-write retry loop.

```python
from tvastar.fleet import ConflictError

for _ in range(3):
    entry = fleet.state.get("shared-counter")
    try:
        fleet.state.put("shared-counter", entry.value + 1, expected_version=entry.version)
        break
    except ConflictError:
        continue  # retry with fresh version
```

---

## Versioning and Rollback

The FleetRegistry tracks version history for each registered agent. Versions are recorded on registration and accessible for rollback.

```python
# View version history
history = fleet.registry.version_history("my-agent")
for v in history:
    print(f"{v.version}: {v.config_snapshot}")

# Rollback to a previous version
entry = fleet.registry.rollback("my-agent", "1.0.0")
assert entry.version == "1.0.0"
```

**Rollback** restores the agent's version identifier and config_overrides from the target version's snapshot. The agent continues accepting tasks at the rolled-back configuration.


---

## Sandbox Lifecycle Observability

Fleet automatically tracks sandbox lifecycle transitions and resource allocations through EventBus subscriptions.

### Querying Sandbox States

```python
counts = fleet.sandbox_state_counts()
# {"running": 3, "hibernated": 1, "stopped": 0}
```

Returns the count of sandboxes in each lifecycle state across the fleet. States are tracked via `"sandbox.lifecycle"` events published by `LifecycleMixin._emit_transition()`.

### Querying Resource Allocation

```python
totals = fleet.sandbox_resource_totals()
# {"memory_mb": 8192, "cpu_count": 16}
```

Returns aggregate memory and CPU of running sandboxes. Only sandboxes in `"running"` state are counted. Resources are tracked via `"sandbox.scale"` events.

### Event Wiring

The `LifecycleMixin` publishes to the EventBus on every state transition:

```python
bus.publish(
    topic="sandbox.lifecycle",
    payload={
        "sandbox_id": "...",
        "prev_state": "running",
        "new_state": "hibernated",
    },
    source_agent="lifecycle_mixin",
)
```

Subscribe to these events for custom monitoring:

```python
fleet.bus.subscribe("sandbox.lifecycle", my_handler)
fleet.bus.subscribe("sandbox.scale", my_scale_handler)
```


---

## Swarm — Decoupled Multi-Worker Coordination

Swarm runs multiple workers concurrently with shared state coordination via SignalBus. Workers escalate to a rule-based Coordinator when stuck, and degrade gracefully when no guidance arrives.

### Quick Example

```python
import asyncio
from tvastar.fleet.swarm import Swarm
from tvastar.memory.store import FileStore

async def research():
    # ... do research work ...
    return "research findings"

async def summarize():
    # ... do summarization ...
    return "executive summary"

async def main():
    swarm = Swarm(
        goal="Research competitors and produce a strategy report",
        tasks=[research, summarize],
        store=FileStore("./checkpoints"),  # crash recovery
    )
    result = await swarm.run()
    print(result.worker_results)  # {"worker_0": "research findings", "worker_1": "executive summary"}

asyncio.run(main())
```

### Architecture

```
Swarm
├── SignalBus (shared reactive state)
├── Coordinator (rule-based escalation matching)
├── Checkpointer (periodic SignalBus → Store)
└── Workers (Loop instances with escalation_policy)
```

Workers communicate through SignalBus — they never message each other directly. The Coordinator watches for escalations and responds with deterministic directives via a configurable rule table. No LLM in the coordination path.

### Custom Escalation Rules

```python
from tvastar.fleet.models import EscalationRule

rules = [
    EscalationRule(
        match_reason="retries_exhausted",
        match_error_type="rate_limit",
        directive={"action": "wait_and_retry", "wait_seconds": 60},
    ),
    EscalationRule(
        match_reason="retries_exhausted",
        directive={"action": "skip_and_continue"},
    ),
]

swarm = Swarm(goal="...", tasks=[...], rules=rules)
```
