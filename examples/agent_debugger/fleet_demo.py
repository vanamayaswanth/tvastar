"""Fleet Demo — Full Harness + Loop + Fleet wiring demonstration.

Shows the complete three-layer stack working together:

    Agent   = Model + Harness
    Loop    = Agent + Schedule + Verify + Handoff
    Fleet   = Loop[] + Gateway + SharedState + Budget

Run with:
    python -m examples.agent_debugger.fleet_demo
    # or from examples/ directory:
    python -m agent_debugger.fleet_demo

This demonstrates:
- Creating real Loop instances backed by Harness (with MockModel)
- Registering Loops in a Fleet
- Fleet lifecycle management (register → deploy → route)
- Shared state between agents
- Event bus coordination
- Budget tracking across the fleet
- Observer health snapshots
- Canary deployment traffic splitting
"""

from __future__ import annotations

import asyncio

from tvastar.agent import create_agent
from tvastar.fleet import (
    Fleet,
    FleetBudgetConfig,
    FleetConfig,
    AlertConfig,
    RateLimitConfig,
    ModelRoutingPolicy,
)
from tvastar.fleet.deploy import DeployManager
from tvastar.loop import Loop, LoopConfig
from tvastar.model.mock import MockModel


def _create_loop(name: str, goal: str, model_responses: list[str] | None = None) -> Loop:
    """Create a real Loop backed by a MockModel + Harness.

    This exercises the full Agent = Model + Harness stack:
    - MockModel simulates LLM responses
    - AgentSpec holds instructions + governance
    - Harness wraps AgentSpec for session management
    - Loop wraps Harness for schedule + verify + handoff
    """
    model = MockModel(script=model_responses or [f"[{name}] Task completed successfully."])

    spec = create_agent(
        name,
        model=model,
        instructions=f"You are the {name} agent. Your goal: {goal}",
        max_steps=5,
    )

    config = LoopConfig(
        name=name,
        goal=goal,
        schedule="@manual",
        max_iterations=2,
    )

    return Loop(spec, config)


async def main() -> None:
    """Run the full three-layer demonstration."""
    print("=" * 70)
    print("  TVASTAR FLEET DEMO: Harness + Loop + Fleet")
    print("=" * 70)
    print()

    # =========================================================================
    # Layer 1: Create Loops (each wraps Agent = Model + Harness internally)
    # =========================================================================
    print("── Layer 1: Creating Loops (Agent = Model + Harness) ──")

    researcher = _create_loop(
        "researcher",
        "Research and summarize academic papers on AI safety",
        model_responses=["Found 3 relevant papers on AI alignment. Key findings: ..."],
    )
    writer = _create_loop(
        "writer",
        "Write technical documentation and reports",
        model_responses=["## Report\n\nBased on the research findings, here is the analysis..."],
    )
    reviewer = _create_loop(
        "reviewer",
        "Review code and documents for quality and correctness",
        model_responses=["Review complete. Score: 85/100. Suggestions: ..."],
    )

    print(f"  ✓ {researcher.name}: goal={researcher.config.goal!r}")
    print(f"  ✓ {writer.name}: goal={writer.config.goal!r}")
    print(f"  ✓ {reviewer.name}: goal={reviewer.config.goal!r}")
    print()

    # =========================================================================
    # Layer 2: Create Fleet (Fleet = Loop[] + Gateway + SharedState + Budget)
    # =========================================================================
    print("── Layer 2: Creating Fleet ──")

    fleet = Fleet(
        FleetConfig(
            name="ml-research-team",
            budget=FleetBudgetConfig(
                max_fleet_usd=50.0,
                allocations={"researcher": 25.0, "writer": 15.0},
                warn_threshold=0.7,
                throttle_threshold=0.9,
            ),
            fleet_rate_limit=RateLimitConfig(requests_per_window=100, window_seconds=60.0),
            model_policy=ModelRoutingPolicy(
                fleet_default_model="claude-sonnet-4-20250514",
                agent_models={"researcher": "claude-opus-4-20250514"},
            ),
            alert_config=AlertConfig(quality_threshold=60.0),
            routing_threshold=0.2,
        )
    )

    print(f"  ✓ Fleet '{fleet.config.name}' created")
    print(f"    Budget: ${fleet.config.budget.max_fleet_usd}")
    print(f"    Rate limit: {fleet.config.fleet_rate_limit.requests_per_window}/min")
    print()

    # =========================================================================
    # Layer 3: Register Loops → Fleet (composition, no modification)
    # =========================================================================
    print("── Layer 3: Registering Loops into Fleet ──")

    fleet.register(researcher, name="researcher", version="1.0.0", owner="ml-team")
    fleet.register(writer, name="writer", version="1.0.0", owner="ml-team")
    fleet.register(reviewer, name="reviewer", version="1.0.0", owner="qa-team")

    print(f"  ✓ {fleet.registry.count()} agents registered")

    # Deploy all agents (registered → active)
    fleet.registry.deploy("researcher")
    fleet.registry.deploy("writer")
    fleet.registry.deploy("reviewer")

    active = fleet.registry.active_agents()
    print(f"  ✓ {len(active)} agents active: {[a.name for a in active]}")
    print()

    # =========================================================================
    # Exercise: Gateway routing (semantic matching)
    # =========================================================================
    print("── Fleet Gateway: Semantic Routing ──")

    result = await fleet.submit("Find papers on transformer architectures")
    print("  Task: 'Find papers on transformer architectures'")
    print(f"  → Routed to: {result['agent_name']} (score: {result['routing_score']:.3f})")

    result = await fleet.submit("Write a summary report of the findings")
    print("  Task: 'Write a summary report of the findings'")
    print(f"  → Routed to: {result['agent_name']} (score: {result['routing_score']:.3f})")

    result = await fleet.submit("Review the code for security issues")
    print("  Task: 'Review the code for security issues'")
    print(f"  → Routed to: {result['agent_name']} (score: {result['routing_score']:.3f})")
    print()

    # =========================================================================
    # Exercise: Explicit routing (bypass matching)
    # =========================================================================
    print("── Fleet Gateway: Explicit Routing ──")

    result = await fleet.submit("Do anything", agent="writer")
    print(f"  Task: 'Do anything' → explicitly routed to: {result['agent_name']}")
    print()

    # =========================================================================
    # Exercise: Shared State Store
    # =========================================================================
    print("── Shared State Store ──")

    fleet.state.set("research_status", "3 papers found", agent="researcher")
    fleet.state.set("research_topics", ["transformers", "alignment", "RLHF"], agent="researcher")

    # Writer reads what researcher wrote
    status = fleet.state.get("research_status")
    topics = fleet.state.get("research_topics")
    print(f"  researcher wrote: research_status = {status!r}")
    print(f"  researcher wrote: research_topics = {topics}")
    print("  (writer can read both → shared knowledge)")
    print(f"  Total keys in state: {len(fleet.state.keys())}")
    print()

    # =========================================================================
    # Exercise: Event Bus (agent coordination)
    # =========================================================================
    print("── Event Bus ──")

    events_received = []
    fleet.bus.subscribe(
        "research.complete",
        lambda e: events_received.append(f"{e.source_agent}: {e.payload}"),
    )
    fleet.bus.subscribe(
        "research.complete",
        lambda e: events_received.append("writer notified: will start report"),
    )

    fleet.bus.publish(
        "research.complete",
        {"papers_found": 3, "quality": "high"},
        source_agent="researcher",
    )

    print("  Published: 'research.complete' event")
    for ev in events_received:
        print(f"    → {ev}")
    print()

    # =========================================================================
    # Exercise: Budget Tracking
    # =========================================================================
    print("── Budget Tracking ──")

    fleet.budget.record_cost("researcher", "ml-team", 8.50)
    fleet.budget.record_cost("writer", "ml-team", 3.20)
    fleet.budget.record_cost("reviewer", "qa-team", 1.50)

    print(f"  Fleet total spent: ${fleet.budget.fleet_spent():.2f} / ${fleet.config.budget.max_fleet_usd:.2f}")
    print(f"  By agent: {fleet.budget.cost_by_agent()}")
    print(f"  By team:  {fleet.budget.cost_by_owner()}")
    print(f"  researcher within allocation: {fleet.budget.check_budget('researcher')}")
    print(f"  writer within allocation: {fleet.budget.check_budget('writer')}")
    print()

    # =========================================================================
    # Exercise: Observer Health Dashboard
    # =========================================================================
    print("── Observer: Health Dashboard ──")

    fleet.observer.record_quality_score("researcher", 92.0)
    fleet.observer.record_quality_score("writer", 78.0)
    fleet.observer.record_quality_score("reviewer", 85.0)

    snapshot = fleet.observer.health_snapshot()
    for agent in snapshot:
        score = fleet.observer.quality_scores.get(agent.name, "N/A")
        print(f"  {agent.name}: state={agent.state.value}, quality={score}")

    fleet_score = fleet.observer.fleet_quality_score()
    print(f"  Fleet quality score: {fleet_score:.1f}")
    print()

    # =========================================================================
    # Exercise: Canary Deployment
    # =========================================================================
    print("── Canary Deployment ──")

    deploy_mgr = DeployManager(fleet.registry)
    canary = deploy_mgr.start_canary(
        "researcher",
        new_version="2.0.0",
        traffic_pct=0.2,
        config={"model": "claude-opus-4-20250514", "temperature": 0.3},
        min_quality=50.0,
        eval_period=0.0,  # immediate evaluation for demo
    )

    print(f"  Started canary for 'researcher': {canary.stable_version} → {canary.canary_version}")
    print(f"  Traffic split: {int((1 - canary.traffic_pct) * 100)}% stable / {int(canary.traffic_pct * 100)}% canary")

    # Simulate quality observations
    deploy_mgr.record_canary_quality("researcher", is_canary=False, score=85.0)
    deploy_mgr.record_canary_quality("researcher", is_canary=True, score=91.0)

    should_promote = deploy_mgr.should_promote_canary("researcher")
    print("  Canary avg quality: 91.0 vs stable avg: 85.0")
    print(f"  Should promote: {should_promote}")

    if should_promote:
        deploy_mgr.promote_canary("researcher")
        entry = fleet.registry.get("researcher")
        print(f"  ✓ Promoted! researcher now at version {entry.version}")
    print()

    # =========================================================================
    # Exercise: Lifecycle management
    # =========================================================================
    print("── Lifecycle Management ──")

    fleet.registry.pause("writer")
    print(f"  writer paused: state={fleet.registry.get('writer').state.value}")

    # Verify paused agent is excluded from routing
    result = await fleet.submit("Write a report about AI safety")
    print(f"  Task 'Write a report about AI safety' → routed to: {result['agent_name']} (writer excluded)")

    fleet.registry.resume("writer")
    print(f"  writer resumed: state={fleet.registry.get('writer').state.value}")
    print()

    # =========================================================================
    # Exercise: Cross-agent trace correlation
    # =========================================================================
    print("── Cross-Agent Trace Correlation ──")

    correlation_id = fleet.observer.create_correlation_id()
    print(f"  Correlation ID: {correlation_id[:12]}...")

    # Simulate attaching correlation to spans
    class FakeSpan:
        def __init__(self):
            self.attributes = {}

    span1 = FakeSpan()
    span2 = FakeSpan()
    fleet.observer.attach_correlation(span1, correlation_id)
    fleet.observer.attach_correlation(span2, correlation_id)

    correlated = fleet.observer.spans_by_correlation(correlation_id)
    print(f"  Spans with this correlation: {len(correlated)}")
    print()

    # =========================================================================
    # Final summary
    # =========================================================================
    print("=" * 70)
    print("  SUMMARY: All Three Layers Working Together")
    print("=" * 70)
    print()
    print("  Agent (Model + Harness)")
    print("    └─ MockModel provides responses")
    print("    └─ Harness manages sessions, sandbox, checkpoints")
    print()
    print("  Loop (Agent + Schedule + Verify + Handoff)")
    print("    └─ LoopConfig defines goal, schedule, retries")
    print("    └─ Loop wraps Harness internally")
    print("    └─ Crash recovery, circuit breaker, meta-improvement")
    print()
    print("  Fleet (Loop[] + Gateway + SharedState + Budget)")
    print("    └─ FleetRegistry: lifecycle FSM, dependency tracking")
    print("    └─ FleetGateway: semantic routing, rate limiting, model routing")
    print("    └─ SharedStateStore: cross-agent knowledge sharing")
    print("    └─ EventBus: pub/sub coordination")
    print("    └─ FleetBudget: per-agent + fleet-wide cost governance")
    print("    └─ FleetObserver: health, alerting, trace correlation")
    print("    └─ DeployManager: canary, A/B, rollback")
    print()
    print(f"  Audit trail: {len(fleet.gateway.audit_log())} entries")
    print(f"  Total fleet cost: ${fleet.budget.fleet_spent():.2f}")
    print(f"  Fleet quality: {fleet.observer.fleet_quality_score():.1f}")
    print()
    print("  ✓ Everything is wired together. No missing layers.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
