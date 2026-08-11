"""Tests for edge_condition support in TaskGraph.

Validates: Requirement 12
"""

import pytest

from tvastar import GraphResult, Harness, TaskGraph, create_agent
from tvastar.model import MockModel


def _agent(responses: list[str]):
    """Agent whose MockModel replies in order."""
    return create_agent("test", model=MockModel(script=responses))


# ---------------------------------------------------------------------------
# edge_condition True → result injected normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edge_condition_true_injects_result():
    """When edge_condition returns True, upstream result is injected into downstream prompt."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["APPROVED content", "final output"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("reviewer", "review the doc")
    graph.task(
        "publish",
        "publish the doc",
        depends_on=["reviewer"],
        edge_conditions={"reviewer": lambda r: "APPROVED" in r.text},
    )
    gr = await graph.run()

    assert "publish" in gr.results
    # The publish prompt should contain the reviewer result (condition was True)
    publish_prompt = [p for p in captured_prompts if "publish the doc" in p]
    assert len(publish_prompt) > 0
    assert "APPROVED content" in publish_prompt[0]
    assert gr.skipped == []


# ---------------------------------------------------------------------------
# edge_condition False → dependency satisfied but result NOT injected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edge_condition_false_satisfies_but_no_injection():
    """When edge_condition returns False but other deps are unconditional, task runs without that dep's result."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["REJECTED content", "other data", "final output"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("reviewer", "review the doc")
    graph.task("data", "fetch data")
    graph.task(
        "publish",
        "publish the doc",
        depends_on=["reviewer", "data"],
        edge_conditions={"reviewer": lambda r: "APPROVED" in r.text},
    )
    gr = await graph.run()

    # Task ran (not skipped) because "data" has no condition (unconditional)
    assert "publish" in gr.results
    assert gr.skipped == []

    # The publish prompt should contain data's result but NOT reviewer's result
    publish_prompt = [p for p in captured_prompts if "publish the doc" in p]
    assert len(publish_prompt) > 0
    assert "other data" in publish_prompt[0]
    assert "REJECTED content" not in publish_prompt[0]


# ---------------------------------------------------------------------------
# ALL deps return False → task skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_conditions_false_skips_task():
    """When ALL deps have edge_conditions and ALL return False, task is skipped."""
    harness = Harness(_agent(["bad review", "bad analysis"]))

    graph = TaskGraph(harness)
    graph.task("reviewer", "review")
    graph.task("analyst", "analyze")
    graph.task(
        "publish",
        "publish",
        depends_on=["reviewer", "analyst"],
        edge_conditions={
            "reviewer": lambda r: "APPROVED" in r.text,
            "analyst": lambda r: "PASS" in r.text,
        },
    )
    gr = await graph.run()

    # publish should be skipped — not in results
    assert "publish" not in gr.results
    assert "publish" in gr.skipped


# ---------------------------------------------------------------------------
# No edge_condition → current behavior preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_edge_condition_preserves_current_behavior():
    """Tasks without edge_conditions behave identically to before."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["upstream-data", "downstream-result"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("source", "produce data")
    graph.task("consumer", "consume data", depends_on=["source"])
    gr = await graph.run()

    assert "consumer" in gr.results
    assert gr.skipped == []
    # Result was injected (default behavior)
    consumer_prompt = [p for p in captured_prompts if "consume data" in p]
    assert "upstream-data" in consumer_prompt[0]


# ---------------------------------------------------------------------------
# GraphResult.skipped field exists and defaults empty
# ---------------------------------------------------------------------------


def test_graph_result_skipped_field_default():
    """GraphResult has a skipped field defaulting to empty list."""
    gr = GraphResult({})
    assert gr.skipped == []


# ---------------------------------------------------------------------------
# Mixed: some conditions True, some False — task still runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_conditions_task_runs_with_partial_injection():
    """When some conditions are True and some False, task runs with only True deps injected."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["APPROVED review", "FAIL analysis", "output"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("reviewer", "review")
    graph.task("analyst", "analyze")
    graph.task(
        "publish",
        "publish now",
        depends_on=["reviewer", "analyst"],
        edge_conditions={
            "reviewer": lambda r: "APPROVED" in r.text,
            "analyst": lambda r: "PASS" in r.text,
        },
    )
    gr = await graph.run()

    # Task runs (not all False)
    assert "publish" in gr.results
    assert gr.skipped == []

    # Only reviewer's result injected (condition True), not analyst's (condition False)
    publish_prompt = [p for p in captured_prompts if "publish now" in p]
    assert len(publish_prompt) > 0
    assert "APPROVED review" in publish_prompt[0]
    assert "FAIL analysis" not in publish_prompt[0]
