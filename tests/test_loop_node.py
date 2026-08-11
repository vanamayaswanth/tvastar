"""Tests for LoopNode — embedding a Loop as a TaskGraph node.

Validates: Requirement 14
"""

import pytest

from tvastar import Harness, LoopNode, TaskGraph, create_agent
from tvastar.detect.base import Finding, Severity
from tvastar.loop import LoopConfig
from tvastar.model import MockModel


def _always_warn_detector(ctx):
    """Detector that fires 3 WARNINGs — pushes score to 70, failing default quality gate (80)."""
    return [
        Finding(detector="always_warn", severity=Severity.WARNING, message="warn 1"),
        Finding(detector="always_warn", severity=Severity.WARNING, message="warn 2"),
        Finding(detector="always_warn", severity=Severity.WARNING, message="warn 3"),
    ]


def _clean_agent(responses: list[str]):
    """Agent with clean responses that pass quality gate."""
    model = MockModel(script=responses)
    return create_agent("loop-test", model=model, instructions="test agent")


def _failing_agent(responses: list[str]):
    """Agent whose responses always trigger a WARNING (fails quality gate)."""
    model = MockModel(script=responses)
    return create_agent(
        "loop-test-fail",
        model=model,
        instructions="test agent",
        detect=[_always_warn_detector],
    )


# ---------------------------------------------------------------------------
# PASS within iterations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_node_pass_within_iterations():
    """LoopNode returns the passing RunResult when quality gate is met."""
    spec = _clean_agent(["good result"])
    config = LoopConfig(name="writer", goal="Write something good", max_iterations=3)
    loop_node = LoopNode(config, spec)

    result = await loop_node.execute()

    assert result.text == "good result"
    # No WARNING finding from LoopNode (it passed)
    loop_warnings = [f for f in result.findings if f.detector == "LoopNode"]
    assert loop_warnings == []


# ---------------------------------------------------------------------------
# Exhaustion returns last result with WARNING finding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_node_exhaustion_returns_warning():
    """When all iterations fail quality gate, return last result with WARNING."""
    spec = _failing_agent(["attempt 1", "attempt 2", "attempt 3"])
    config = LoopConfig(name="writer", goal="Write something good", max_iterations=3)
    loop_node = LoopNode(config, spec)

    result = await loop_node.execute()

    assert result.text == "attempt 3"
    # Should have the exhaustion WARNING
    loop_warnings = [f for f in result.findings if f.detector == "LoopNode"]
    assert len(loop_warnings) == 1
    assert loop_warnings[0].severity == Severity.WARNING
    assert "exhausted" in loop_warnings[0].message
    assert "3" in loop_warnings[0].message  # max_iterations mentioned


# ---------------------------------------------------------------------------
# Upstream injection works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_node_upstream_injection():
    """LoopNode receives upstream results as context in the prompt."""
    # Use a model that echoes its input so we can verify injection
    model = MockModel(script=[])  # will use echo/ack mode
    spec = create_agent("loop-test", model=model, instructions="test agent")
    config = LoopConfig(name="writer", goal="Write a draft", max_iterations=3)
    loop_node = LoopNode(config, spec)

    upstream = {"research": "The sky is blue", "data": "Temperature is 72F"}
    await loop_node.execute(context=upstream)

    # The model received a prompt containing the upstream context.
    # MockModel stores calls — verify the prompt was built with context.
    assert model.calls  # at least one call was made
    first_call_text = model.calls[0][0].text if model.calls[0] else ""
    assert "research" in first_call_text or "sky is blue" in first_call_text


# ---------------------------------------------------------------------------
# LoopNode in a TaskGraph (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_node_in_task_graph():
    """LoopNode participates in TaskGraph dependency injection."""
    # research task → loop_node (writer) → publish task
    harness_agent = _clean_agent(["research findings", "published ok"])
    harness = Harness(harness_agent)

    writer_spec = _clean_agent(["final draft"])
    writer_config = LoopConfig(name="writer", goal="Write based on research", max_iterations=3)
    writer_node = LoopNode(writer_config, writer_spec)

    graph = TaskGraph(harness)
    graph.task("research", "Find information")
    graph.loop_task("write", writer_node, depends_on=["research"])
    graph.task("publish", "Publish the draft", depends_on=["write"])

    result = await graph.run()

    assert result["research"].text == "research findings"
    assert result["write"].text == "final draft"
    assert result["publish"].text == "published ok"


@pytest.mark.asyncio
async def test_loop_node_graph_exhaustion_still_flows():
    """When LoopNode exhausts, the result (with warning) still flows downstream."""
    harness_agent = _clean_agent(["upstream data", "downstream ok"])
    harness = Harness(harness_agent)

    writer_spec = _failing_agent(["draft 1", "draft 2"])
    writer_config = LoopConfig(name="writer", goal="Write something", max_iterations=2)
    writer_node = LoopNode(writer_config, writer_spec)

    graph = TaskGraph(harness)
    graph.task("research", "Find info")
    graph.loop_task("write", writer_node, depends_on=["research"])
    graph.task("publish", "Publish", depends_on=["write"])

    result = await graph.run()

    # Writer exhausted but result still flows
    assert result["write"].text == "draft 2"
    loop_warnings = [f for f in result["write"].findings if f.detector == "LoopNode"]
    assert len(loop_warnings) == 1
    # Downstream still executed
    assert result["publish"].text == "downstream ok"


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_loop_node_requires_loop_config():
    """LoopNode constructor rejects non-LoopConfig."""
    spec = _clean_agent([])
    with pytest.raises(TypeError, match="LoopConfig"):
        LoopNode("not a config", spec)


def test_loop_task_requires_loop_node():
    """TaskGraph.loop_task rejects non-LoopNode."""
    harness = Harness(_clean_agent([]))
    graph = TaskGraph(harness)
    with pytest.raises(TypeError, match="LoopNode"):
        graph.loop_task("test", "not a loop node")
