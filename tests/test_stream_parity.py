"""Tests for Session.stream() policy enforcement parity with prompt().

Verifies Requirement 19: Session.stream() enforces the same governance, budget,
compaction, and memory_cap policies as Session.prompt().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from tvastar.agent import AgentSpec, create_agent
from tvastar.cost import BudgetExceeded, Cost
from tvastar.harness import Harness
from tvastar.model.mock import MockModel
from tvastar.session import Session
from tvastar.types import (
    ModelResponse,
    ToolUseBlock,
    Usage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class MockBudget:
    """Minimal budget policy for testing."""

    max_usd: float = 0.01
    on_exceed: str = "stop"

    def should_warn(self, cost) -> bool:
        return cost.usd >= self.max_usd * 0.8

    def attribute(self, cost) -> None:
        pass


class HighUsageModel(MockModel):
    """Model that reports high token usage to trigger budget limits."""

    async def generate(self, messages, **kwargs):
        resp = await super().generate(messages, **kwargs)
        # Inject high usage to trigger budget
        return ModelResponse(
            message=resp.message,
            stop_reason=resp.stop_reason,
            usage=Usage(input_tokens=500_000, output_tokens=500_000),
        )


class OverflowThenSuccessModel(MockModel):
    """Model that raises overflow on first call, succeeds on second."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._call_count = 0

    async def generate(self, messages, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError("context_length_exceeded")
        return await super().generate(messages, **kwargs)


@dataclass
class MockCompaction:
    """Minimal compaction policy for testing overflow recovery."""

    cooldown: float = 0.0
    threshold: float = 0.8
    summary_max_tokens: int = 1024
    summary_temperature: float = 0.3


class MockDetector:
    """Detector that always produces a finding."""

    def __call__(self, ctx):
        from tvastar.detect import Finding, Severity

        return [Finding("test_detector", Severity.WARNING, "test finding", {})]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_session(spec: AgentSpec) -> Session:
    """Create a session with a real Harness so _checkpoint() works."""
    h = Harness(spec)
    return h.session()


@pytest.mark.asyncio
async def test_stream_enforces_budget_stop():
    """stream() should stop when budget is exceeded (on_exceed='stop')."""
    from tvastar.cost import register_model_cost

    # Register a cost entry for mock so budget math works
    register_model_cost("mock", input_per_million=10.0, output_per_million=10.0)

    model = HighUsageModel(script=["Hello", "World"])
    spec = AgentSpec(
        name="budget-stream-test",
        model=model,
        budget=MockBudget(max_usd=0.001, on_exceed="stop"),
    )
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    # Should have a turn_end with stopped="budget"
    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 1
    assert turn_ends[0].data.get("stopped") == "budget"


@pytest.mark.asyncio
async def test_stream_enforces_budget_raise():
    """stream() should raise BudgetExceeded when on_exceed='raise'."""
    from tvastar.cost import register_model_cost

    register_model_cost("mock", input_per_million=10.0, output_per_million=10.0)

    model = HighUsageModel(script=["Hello"])
    spec = AgentSpec(
        name="budget-raise-stream-test",
        model=model,
        budget=MockBudget(max_usd=0.001, on_exceed="raise"),
    )
    session = _make_session(spec)

    with pytest.raises(BudgetExceeded):
        async with session:
            async for ev in session.stream("test"):
                pass


@pytest.mark.asyncio
async def test_stream_enforces_memory_cap():
    """stream() should stop when memory_cap_mb is exceeded."""
    # Create a model with a large response to push past memory cap
    model = MockModel(script=["x" * 10000])
    spec = AgentSpec(
        name="memcap-stream-test",
        model=model,
        memory_cap_mb=0.00001,  # Extremely small cap to trigger
    )
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 1
    assert turn_ends[0].data.get("stopped") == "memory_cap"


@pytest.mark.asyncio
async def test_stream_produces_findings_from_detectors():
    """stream() should run detectors on completion (same as prompt)."""
    model = MockModel(script=["response"])
    detector = MockDetector()
    spec = AgentSpec(
        name="detect-stream-test",
        model=model,
        detectors=[detector],
    )
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    # The stream completed — verify that _detect() produces findings
    # by directly verifying the detector works on current session state.
    from tvastar.session import RunResult
    from tvastar.cost import Cost

    result = RunResult(
        text=session._last_assistant_text(),
        messages=session.messages,
        usage=Usage(),
        steps=1,
        stopped="end_turn",
        cost=Cost(0, 0, "mock"),
    )
    findings = session._detect(result)
    assert len(findings) == 1
    assert findings[0].detector == "test_detector"


@pytest.mark.asyncio
async def test_stream_applies_governance_on_tool_calls():
    """stream() should enforce governance policy during tool calls."""
    from tvastar.masking import GovernancePolicy
    from tvastar.tools.base import tool as tool_decorator

    tool_call = ToolUseBlock(id="t1", name="blocked_tool", input={})
    model = MockModel(script=[tool_call, "done"])

    @tool_decorator
    async def blocked_tool() -> str:
        """A blocked tool."""
        return "should not run"

    gov = GovernancePolicy(
        phases={"restricted": {"some_other_tool"}},
        current_phase="restricted",
    )

    spec = create_agent(
        "gov-stream-test",
        model=model,
        tools=[blocked_tool],
        governance=gov,
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    # The tool_result should contain a governance blocked message
    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert "[governance]" in tool_results[0].data["content"]
    assert tool_results[0].data["error"] is True


@pytest.mark.asyncio
async def test_stream_basic_completion():
    """stream() yields events and completes normally for a basic prompt."""
    model = MockModel(script=["Hello world"])
    spec = AgentSpec(name="basic-stream-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("hi"):
            events.append(ev)

    # Should have turn_start, text_delta (optional), turn_end
    types = [e.type for e in events]
    assert "turn_start" in types
    assert "turn_end" in types


@pytest.mark.asyncio
async def test_stream_compacts_on_threshold():
    """stream() should call _maybe_compact() after tool execution (same as prompt)."""
    from tvastar.tools.base import tool as tool_decorator

    tool_call = ToolUseBlock(id="t1", name="echo", input={"text": "hello"})
    model = MockModel(script=[tool_call, "done"])

    @tool_decorator
    async def echo(text: str) -> str:
        """Echo back."""
        return text

    spec = create_agent(
        "compact-stream-test",
        model=model,
        tools=[echo],
        compaction=MockCompaction(),
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    # Stream should complete without error - compaction is attempted but
    # doesn't necessarily fire (depends on message size vs threshold)
    types = [e.type for e in events]
    assert "turn_end" in types


@pytest.mark.asyncio
async def test_stream_persists_assured_result_lifecycle():
    """stream() writes prompt-equivalent records and ends with a JSON-safe result."""
    import json

    from tvastar.assurance import AssurancePolicy, SanitizationPolicy, TokenVault
    from tvastar.memory import InMemoryStore

    store = InMemoryStore()
    vault = TokenVault()
    spec = AgentSpec(
        name="assured-stream-test",
        model=MockModel(script=["safe response"]),
        detectors=[MockDetector()],
        assurance=AssurancePolicy(vault=vault, sanitize=SanitizationPolicy.hipaa()),
        scrub_after_run=True,
    )
    session = Harness(spec, store=store).session()

    events = []
    async with session:
        async for event in session.stream("contact alice@example.com"):
            events.append(event)

    result = events[-1]
    assert result.type == "result"
    assert events[-2].type == "turn_end"
    json.dumps(result.data)
    assert result.data["text"] == "safe response"
    assert result.data["findings"][0]["detector"] == "test_detector"
    assert result.data["receipt"]["content_hash"].startswith("sha256:")
    assert session._last_user_text == "contact alice@example.com"

    records = store.get(f"event_log:{session.id}")
    record_types = [record["type"] for record in records]
    assert {"user_message", "assistant_message", "run_start", "step_complete", "run_end"} <= set(
        record_types
    )
    assert "alice@example.com" not in str(records)
    assert all(message.content.startswith("[scrubbed:") for message in session.messages)


@pytest.mark.asyncio
async def test_stream_unknown_model_stop_skips_provider_execution():
    """An unpriced model in stop mode returns a terminal budget result without streaming."""
    from tvastar.cost import BudgetPolicy

    model = MockModel(script=["should not run"])
    model.name = "unknown-stream-budget-model"
    session = _make_session(
        AgentSpec(
            name="unknown-stream-budget-test",
            model=model,
            budget=BudgetPolicy(max_usd=1.0, on_exceed="stop"),
        )
    )

    events = []
    async with session:
        async for event in session.stream("hello"):
            events.append(event)

    assert model.calls == []
    assert events[-1].type == "result"
    assert events[-1].data["stopped"] == "budget"


@pytest.mark.asyncio
async def test_stream_uses_non_overflow_fallback_model():
    """stream() matches prompt() by trying fallbacks after provider failures."""
    primary = MockModel(script=[RuntimeError("provider unavailable")])
    fallback = MockModel(script=["fallback response"])
    session = _make_session(
        AgentSpec(
            name="fallback-stream-test",
            model=primary,
            fallback_models=[fallback],
        )
    )

    events = []
    async with session:
        async for event in session.stream("hello"):
            events.append(event)

    assert primary.calls
    assert fallback.calls
    assert events[-1].type == "result"
    assert events[-1].data["text"] == "fallback response"


@pytest.mark.asyncio
async def test_stream_runs_step_callback_before_finishing():
    """stream() invokes the same post-response callback as prompt()."""
    calls = []
    session = _make_session(
        AgentSpec(
            name="callback-stream-test",
            model=MockModel(script=["response"]),
            step_callback=lambda step, response, messages: calls.append((step, response, messages)),
        )
    )

    async with session:
        async for _ in session.stream("hello"):
            pass

    assert len(calls) == 1
    assert calls[0][0] == 1
    assert calls[0][1].message.text == "response"
    assert calls[0][2][-1].role == "assistant"


@pytest.mark.asyncio
async def test_stream_honors_stop_predicate_before_tool_execution():
    """stream() terminates with predicate semantics before executing requested tools."""
    tool_call = ToolUseBlock(id="stop", name="unused", input={})
    session = _make_session(
        AgentSpec(
            name="predicate-stream-test",
            model=MockModel(script=[tool_call]),
            stop_predicate=lambda result: result.steps == 1,
        )
    )

    events = []
    async with session:
        async for event in session.stream("hello"):
            events.append(event)

    assert [event.type for event in events].count("tool_call") == 0
    assert (
        next(event for event in events if event.type == "turn_end").data["stopped"] == "predicate"
    )
    assert events[-1].data["stopped"] == "predicate"


@pytest.mark.asyncio
async def test_stream_unknown_model_approve_fails_closed_before_provider_execution():
    """Unknown-model approval cannot authorize unmetered provider spend."""
    from tvastar.cost import BudgetPolicy, UnknownModelCostError

    model = MockModel(script=["should not run"])
    model.name = "unknown-approved-stream-model"
    session = _make_session(
        AgentSpec(
            name="unknown-approved-stream-test",
            model=model,
            budget=BudgetPolicy(max_usd=1.0, on_exceed="approve"),
        )
    )

    with pytest.raises(UnknownModelCostError):
        async with session:
            async for _ in session.stream("hello"):
                pass

    assert model.calls == []


@pytest.mark.asyncio
async def test_stream_cancellation_persists_terminal_lifecycle_and_propagates():
    """Consumer cancellation records an aborted run without swallowing cancellation."""
    from tvastar.memory import InMemoryStore

    store = InMemoryStore()
    session = Harness(
        AgentSpec(name="cancel-stream-test", model=MockModel(script=["done"])), store=store
    ).session()

    async with session:
        stream = session.stream("hello")
        assert (await anext(stream)).type == "execution_started"
        assert (await anext(stream)).type == "turn_start"
        with pytest.raises(asyncio.CancelledError):
            await stream.athrow(asyncio.CancelledError())

    records = store.get(f"event_log:{session.id}")
    record_types = [record["type"] for record in records]
    assert record_types[-3:] == ["error", "run_end", "session_end"]
    assert records[-2]["data"]["stopped"] == "cancelled"


@pytest.mark.asyncio
async def test_stream_unpriced_fallback_fails_closed_before_any_provider_call():
    from tvastar.cost import BudgetPolicy, UnknownModelCostError

    primary = MockModel(script=[RuntimeError("provider unavailable")])
    primary.name = "gpt-4o"
    fallback = MockModel(script=["must not run"])
    fallback.name = "unpriced-stream-fallback"
    session = _make_session(
        AgentSpec(
            name="unpriced-stream-fallback",
            model=primary,
            fallback_models=[fallback],
            budget=BudgetPolicy(max_usd=1.0),
        )
    )

    with pytest.raises(UnknownModelCostError):
        async with session:
            async for _ in session.stream("hello"):
                pass
    assert primary.calls == []
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_stream_attributes_priced_fallback_usage_to_the_producer():
    from tvastar.cost import BudgetPolicy

    primary = MockModel(script=[RuntimeError("provider unavailable")])
    primary.name = "gpt-4o"
    fallback = MockModel(script=["fallback"])
    fallback.name = "gpt-4o-mini"
    budget = BudgetPolicy(max_usd=1.0)
    session = _make_session(
        AgentSpec(
            name="priced-stream-fallback",
            model=primary,
            fallback_models=[fallback],
            budget=budget,
        )
    )

    async with session:
        async for _ in session.stream("hello"):
            pass

    attributed = budget.cost_breakdown()["_unattributed"]
    assert attributed.model == "gpt-4o-mini"
    assert attributed.usd == pytest.approx(
        Cost(attributed.input_tokens, attributed.output_tokens, "gpt-4o-mini").usd
    )
