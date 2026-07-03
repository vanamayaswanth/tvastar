"""Smoke tests for FleetBudget implementation (tasks 7.1, 7.2, 7.3)."""

from tvastar.fleet import FleetBudgetConfig, BudgetAllocation
from tvastar.fleet.budget import FleetBudget, BudgetWarningEvent, BudgetThrottleEvent
from tvastar.cost import Cost


def test_record_cost_with_cost_dataclass():
    """Task 7.1: Track cumulative spend using Cost dataclass."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    cost = Cost(input_tokens=1000, output_tokens=500, model="claude-opus-4-6")
    budget.record_cost("agent-a", "team-1", cost)

    assert budget.fleet_spent() == cost.usd
    assert budget.agent_spent("agent-a") == cost.usd


def test_record_cost_with_float():
    """Task 7.1: record_cost accepts float for USD."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 5.0)
    assert budget.fleet_spent() == 5.0


def test_record_cost_with_dict():
    """Task 7.1: record_cost accepts dict with 'usd' key."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", {"usd": 3.5})
    assert budget.fleet_spent() == 3.5


def test_fleet_budget_exhausted_blocks_all_agents():
    """Task 7.1: Prevent all agents when fleet budget exhausted."""
    config = FleetBudgetConfig(max_fleet_usd=10.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 10.0)
    assert not budget.check_budget("agent-a")
    assert not budget.check_budget("agent-b")


def test_check_budget_allowed_when_under_limit():
    """Task 7.1: Allow agents when budget available."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 5.0)
    assert budget.check_budget("agent-a")
    assert budget.check_budget("agent-b")


def test_cost_by_agent():
    """Task 7.1: Track cumulative spend per agent."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 5.0)
    budget.record_cost("agent-b", "team-2", 3.0)
    budget.record_cost("agent-a", "team-1", 2.0)

    by_agent = budget.cost_by_agent()
    assert by_agent == {"agent-a": 7.0, "agent-b": 3.0}


def test_cost_by_owner():
    """Task 7.1: Track cumulative spend per owner."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 5.0)
    budget.record_cost("agent-b", "team-2", 3.0)
    budget.record_cost("agent-c", "team-1", 2.0)

    by_owner = budget.cost_by_owner()
    assert by_owner == {"team-1": 7.0, "team-2": 3.0}


def test_cost_by_period():
    """Task 7.1: Group cost events by time period."""
    config = FleetBudgetConfig(max_fleet_usd=100.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 5.0)
    budget.record_cost("agent-a", "team-1", 3.0)

    hourly = budget.cost_by_period("hourly")
    assert len(hourly) >= 1
    assert sum(hourly.values()) == 8.0

    daily = budget.cost_by_period("daily")
    assert len(daily) >= 1
    assert sum(daily.values()) == 8.0


def test_reset():
    """Task 7.1: Reset all tracking state."""
    config = FleetBudgetConfig(max_fleet_usd=100.0, allocations={"agent-a": 50.0})
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 30.0)
    budget.reset()

    assert budget.fleet_spent() == 0.0
    assert budget.agent_spent("agent-a") == 0.0
    assert budget.cost_by_agent() == {}
    assert budget.cost_by_owner() == {}
    assert budget.check_budget("agent-a")


def test_increase_budget():
    """Task 7.1: Increase fleet budget maximum."""
    config = FleetBudgetConfig(max_fleet_usd=10.0)
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 10.0)
    assert not budget.check_budget("agent-a")

    budget.increase_budget(50.0)
    assert budget.config.max_fleet_usd == 60.0
    assert budget.check_budget("agent-a")


# --- Task 7.2: Per-agent allocation ---


def test_per_agent_allocation_enforced():
    """Task 7.2: Agent blocked when allocation exhausted."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        allocations={"agent-a": 10.0},
    )
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 10.0)
    assert not budget.check_budget("agent-a")


def test_per_agent_throttle_others_continue():
    """Task 7.2: Other agents continue when one is throttled by allocation."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        allocations={"agent-a": 10.0, "agent-b": 50.0},
    )
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 10.0)
    assert not budget.check_budget("agent-a")
    assert budget.check_budget("agent-b")


def test_unallocated_agent_draws_from_pool():
    """Task 7.2: Unallocated agents draw from pool (fleet_max - sum of allocations)."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        allocations={"agent-a": 40.0, "agent-b": 30.0},
        # Pool = 100 - 40 - 30 = 30
    )
    budget = FleetBudget(config)

    # Unallocated agent-c can spend up to pool (30)
    budget.record_cost("agent-c", "team-3", 25.0)
    assert budget.check_budget("agent-c")

    budget.record_cost("agent-c", "team-3", 5.0)
    assert not budget.check_budget("agent-c")


# --- Task 7.3: Auto-throttle and warnings ---


def test_budget_warning_at_threshold():
    """Task 7.3: Emit warning event at warn_threshold."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.9,
    )
    budget = FleetBudget(config)

    # Spend 80% of budget
    budget.record_cost("agent-a", "team-1", 80.0)

    assert len(budget.warning_events) == 1
    assert budget.warning_events[0].fleet_spent == 80.0
    assert budget.warning_events[0].threshold == 0.8


def test_warning_emitted_once():
    """Task 7.3: Warning only emitted once per threshold crossing."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.95,
    )
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 80.0)
    budget.record_cost("agent-a", "team-1", 5.0)

    assert len(budget.warning_events) == 1


def test_auto_throttle_highest_spender():
    """Task 7.3: Auto-pause highest-spending non-exempt agent at throttle threshold."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.9,
        exempt_agents=["monitor"],
    )
    budget = FleetBudget(config)

    # Agent-a spends the most
    budget.record_cost("agent-a", "team-1", 60.0)
    budget.record_cost("agent-b", "team-2", 20.0)
    budget.record_cost("monitor", "ops", 10.0)

    # At this point: 90% of budget spent, throttle threshold reached
    assert "agent-a" in budget.throttled_agents
    assert "monitor" not in budget.throttled_agents
    assert not budget.check_budget("agent-a")


def test_exempt_agents_never_throttled():
    """Task 7.3: Exempt agents cannot be auto-throttled."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.9,
        exempt_agents=["critical"],
    )
    budget = FleetBudget(config)

    # critical agent is the only one spending
    budget.record_cost("critical", "ops", 95.0)

    assert "critical" not in budget.throttled_agents
    assert budget.check_budget("critical")


def test_throttle_events_stored():
    """Task 7.3: Throttle events stored for observer/event bus."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.9,
    )
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 91.0)

    assert len(budget.throttle_events) >= 1
    assert "agent-a" in budget.throttle_events[0].throttled_agents


def test_tracer_exceptions_swallowed():
    """Task 7.3: Tracer exceptions never break operations."""

    class BrokenTracer:
        def span(self, name, **kwargs):
            raise RuntimeError("tracer broken!")

    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.5,
    )
    budget = FleetBudget(config, tracer=BrokenTracer())

    # Should not raise despite broken tracer
    budget.record_cost("agent-a", "team-1", 60.0)
    assert budget.fleet_spent() == 60.0


def test_increase_budget_unthrottles_agents():
    """Task 7.3: Agents unthrottled when budget increased below throttle ratio."""
    config = FleetBudgetConfig(
        max_fleet_usd=100.0,
        warn_threshold=0.8,
        throttle_threshold=0.9,
    )
    budget = FleetBudget(config)

    budget.record_cost("agent-a", "team-1", 91.0)
    assert "agent-a" in budget.throttled_agents

    # Increase budget so ratio drops below throttle threshold
    budget.increase_budget(900.0)  # now max is 1000, ratio = 91/1000 = 0.091
    assert "agent-a" not in budget.throttled_agents
    assert budget.check_budget("agent-a")
