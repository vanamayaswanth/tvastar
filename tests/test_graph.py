"""Tests for tvastar.graph — DAG-based parallel task execution (0.8.0)."""

import pytest

from tvastar import Harness, TaskGraph, GraphResult, create_agent
from tvastar.model import MockModel


def _agent(responses: list[str]):
    """Agent whose MockModel replies in order."""
    model = MockModel(script=responses)
    return create_agent("test", model=model)


# ---------------------------------------------------------------------------
# GraphResult basics
# ---------------------------------------------------------------------------


def test_graph_result_indexing():
    from tvastar.session import RunResult
    from tvastar.types import Usage

    rr = RunResult(
        text="hello",
        messages=[],
        usage=Usage(),
        steps=1,
        stopped="end_turn",
        findings=[],
        data=None,
    )
    gr = GraphResult({"a": rr})
    assert gr["a"].text == "hello"
    assert gr.text == {"a": "hello"}
    assert len(gr) == 1
    assert gr.ok  # no warnings


# ---------------------------------------------------------------------------
# Empty graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_graph():
    harness = Harness(_agent([]))
    gr = await TaskGraph(harness).run()
    assert len(gr) == 0
    assert gr.ok


# ---------------------------------------------------------------------------
# Single task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_task():
    harness = Harness(_agent(["result A"]))
    graph = TaskGraph(harness)
    graph.task("a", "do A")
    gr = await graph.run()
    assert gr["a"].text == "result A"


# ---------------------------------------------------------------------------
# Independent tasks run concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_independent_tasks_run_concurrently():
    """Two tasks with no deps should both complete and return results."""
    model = MockModel(script=["r1", "r2"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("a", "do A")
    graph.task("b", "do B")

    gr = await graph.run()

    assert "a" in gr.text
    assert "b" in gr.text


# ---------------------------------------------------------------------------
# Linear chain: A → B → C
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_chain():
    harness = Harness(_agent(["out-A", "out-B", "out-C"]))
    graph = TaskGraph(harness)
    graph.task("a", "step A")
    graph.task("b", "step B", depends_on=["a"])
    graph.task("c", "step C", depends_on=["b"])
    gr = await graph.run()
    assert set(gr.text.keys()) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Diamond: A → B, A → C, B+C → D
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diamond():
    harness = Harness(_agent(["A", "B", "C", "D"]))
    graph = TaskGraph(harness)
    graph.task("a", "root")
    graph.task("b", "left", depends_on=["a"])
    graph.task("c", "right", depends_on=["a"])
    graph.task("d", "sink", depends_on=["b", "c"])
    gr = await graph.run()
    assert set(gr.text.keys()) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# Result injection: dep output appears in downstream prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_results_true():
    """Downstream task prompt should contain upstream result text."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            # capture the user message content
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["upstream-output", "downstream-output"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("up", "do upstream")
    graph.task("down", "do downstream", depends_on=["up"])
    await graph.run()

    # The downstream prompt should contain the upstream result
    downstream_prompt = next((p for p in captured_prompts if "upstream-output" in p), None)
    assert downstream_prompt is not None, "upstream result not injected into downstream prompt"


@pytest.mark.asyncio
async def test_inject_results_false():
    """With inject_results=False, downstream prompt is unchanged."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["upstream-output", "downstream-output"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("up", "do upstream")
    graph.task("down", "do downstream", depends_on=["up"])
    await graph.run(inject_results=False)

    # upstream output must NOT appear in any captured prompt
    assert not any("upstream-output" in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# Validation: duplicate task name
# ---------------------------------------------------------------------------


def test_duplicate_task_name_raises():
    harness = Harness(_agent([]))
    graph = TaskGraph(harness)
    graph.task("a", "first")
    with pytest.raises(ValueError, match="Duplicate task name"):
        graph.task("a", "second")


# ---------------------------------------------------------------------------
# Validation: unknown dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_dep_raises():
    harness = Harness(_agent([]))
    graph = TaskGraph(harness)
    graph.task("a", "do A", depends_on=["nonexistent"])
    with pytest.raises(ValueError, match="unknown task"):
        await graph.run()


# ---------------------------------------------------------------------------
# Validation: cycle detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_raises():
    harness = Harness(_agent([]))
    graph = TaskGraph(harness)
    graph.task("a", "A", depends_on=["b"])
    graph.task("b", "B", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle detected"):
        await graph.run()


# ---------------------------------------------------------------------------
# Fluent chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fluent_chaining():
    harness = Harness(_agent(["r1", "r2"]))
    gr = await TaskGraph(harness).task("a", "step A").task("b", "step B", depends_on=["a"]).run()
    assert set(gr.text.keys()) == {"a", "b"}


# ---------------------------------------------------------------------------
# Top-level export
# ---------------------------------------------------------------------------


def test_top_level_exports():
    from tvastar import TaskGraph, GraphResult  # noqa: F401

    assert callable(TaskGraph)
