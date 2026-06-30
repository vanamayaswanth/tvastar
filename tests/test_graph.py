"""Tests for tvastar.graph — DAG-based parallel task execution (0.8.0).

Validates: Requirements 15.1, 15.2, 15.4, 15.5, 15.6
"""

import asyncio
import time

import pytest
from pydantic import BaseModel

from tvastar import GraphResult, Harness, TaskGraph, create_agent
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
    from tvastar import GraphResult, TaskGraph  # noqa: F401

    assert callable(TaskGraph)


# ===========================================================================
# Additional unit tests for Task 18.1
# Validates: Requirements 15.1, 15.2, 15.4, 15.5, 15.6
# ===========================================================================


# ---------------------------------------------------------------------------
# 15.1: Tasks with no dependencies execute immediately in parallel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_independent_tasks_execute_in_parallel_timing():
    """
    Independent tasks should overlap in execution, not run sequentially.
    We inject a small delay into each task's model call to verify concurrency.
    """
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}

    class SlowModel(MockModel):
        async def generate(self, messages, **kw):
            # Identify which task is running from prompt content
            user_text = next((m.text for m in messages if m.role == "user"), "")
            task_name = "unknown"
            if "task_A" in user_text:
                task_name = "a"
            elif "task_B" in user_text:
                task_name = "b"
            elif "task_C" in user_text:
                task_name = "c"

            start_times[task_name] = time.monotonic()
            await asyncio.sleep(0.1)  # simulate work
            end_times[task_name] = time.monotonic()
            return await super().generate(messages, **kw)

    model = SlowModel(script=["result_A", "result_B", "result_C"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("a", "do task_A")
    graph.task("b", "do task_B")
    graph.task("c", "do task_C")

    wall_start = time.monotonic()
    gr = await graph.run()
    wall_end = time.monotonic()

    # All three tasks completed
    assert set(gr.text.keys()) == {"a", "b", "c"}

    # Wall-clock time should be roughly 1 task duration (0.1s), not 3x (0.3s)
    # Allow some slack for scheduling overhead
    wall_time = wall_end - wall_start
    assert wall_time < 0.25, f"Expected parallel execution but took {wall_time:.3f}s"


@pytest.mark.asyncio
async def test_many_independent_tasks_all_complete():
    """Five independent tasks should all run and return results."""
    responses = [f"result_{i}" for i in range(5)]
    harness = Harness(_agent(responses))
    graph = TaskGraph(harness)
    for i in range(5):
        graph.task(f"t{i}", f"do task {i}")
    gr = await graph.run()
    assert len(gr) == 5
    for i in range(5):
        assert f"t{i}" in gr.text


# ---------------------------------------------------------------------------
# 15.2: Tasks start when all dependencies complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_waits_for_all_dependencies():
    """A task depending on multiple predecessors waits for ALL of them."""
    execution_order: list[str] = []

    class OrderTrackingModel(MockModel):
        async def generate(self, messages, **kw):
            user_text = next((m.text for m in messages if m.role == "user"), "")
            if "dep_a" in user_text:
                execution_order.append("a")
                await asyncio.sleep(0.05)
            elif "dep_b" in user_text:
                execution_order.append("b")
                await asyncio.sleep(0.1)  # b takes longer
            elif "final" in user_text:
                execution_order.append("final")
            return await super().generate(messages, **kw)

    model = OrderTrackingModel(script=["out_a", "out_b", "out_final"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("a", "dep_a work")
    graph.task("b", "dep_b work")
    graph.task("final", "final work", depends_on=["a", "b"])
    gr = await graph.run()

    # final must come after both a and b
    assert "final" in execution_order
    final_idx = execution_order.index("final")
    assert "a" in execution_order[:final_idx]
    assert "b" in execution_order[:final_idx]
    assert set(gr.text.keys()) == {"a", "b", "final"}


@pytest.mark.asyncio
async def test_dependency_chain_strict_order():
    """In A → B → C chain, execution must respect ordering."""
    execution_order: list[str] = []

    class TrackingModel(MockModel):
        async def generate(self, messages, **kw):
            user_text = next((m.text for m in messages if m.role == "user"), "")
            for name in ["step_a", "step_b", "step_c"]:
                if name in user_text:
                    execution_order.append(name)
                    break
            return await super().generate(messages, **kw)

    model = TrackingModel(script=["A", "B", "C"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("a", "step_a")
    graph.task("b", "step_b", depends_on=["a"])
    graph.task("c", "step_c", depends_on=["b"])
    await graph.run()

    assert execution_order == ["step_a", "step_b", "step_c"]


# ---------------------------------------------------------------------------
# 15.4: GraphResult contains results keyed by task name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_result_keyed_by_task_name():
    """GraphResult keys match exactly the task names registered."""
    harness = Harness(_agent(["alpha", "beta", "gamma"]))
    graph = TaskGraph(harness)
    graph.task("alpha_task", "do alpha")
    graph.task("beta_task", "do beta")
    graph.task("gamma_task", "do gamma")
    gr = await graph.run()

    assert set(gr.text.keys()) == {"alpha_task", "beta_task", "gamma_task"}
    assert gr["alpha_task"].text == "alpha"
    assert gr["beta_task"].text == "beta"
    assert gr["gamma_task"].text == "gamma"


def test_graph_result_ok_true_when_all_succeed():
    """GraphResult.ok is True when all tasks succeed with no findings."""
    from tvastar.session import RunResult
    from tvastar.types import Usage

    results = {
        "x": RunResult(text="ok", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[], data=None),
        "y": RunResult(text="ok", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[], data=None),
    }
    gr = GraphResult(results)
    assert gr.ok is True


def test_graph_result_ok_false_when_findings_present():
    """GraphResult.ok is False when tasks have findings."""
    from tvastar.detect import Finding, Severity
    from tvastar.session import RunResult
    from tvastar.types import Usage

    results = {
        "x": RunResult(
            text="ok",
            messages=[],
            usage=Usage(),
            steps=1,
            stopped="end_turn",
            findings=[Finding("test_detector", Severity.WARNING, "something wrong", {})],
            data=None,
        ),
    }
    gr = GraphResult(results, findings={"x": [Finding("test_detector", Severity.WARNING, "something wrong", {})]})
    assert gr.ok is False


def test_graph_result_iteration():
    """GraphResult iteration yields task names."""
    from tvastar.session import RunResult
    from tvastar.types import Usage

    results = {
        "first": RunResult(text="a", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[], data=None),
        "second": RunResult(text="b", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[], data=None),
    }
    gr = GraphResult(results)
    assert set(gr) == {"first", "second"}


def test_graph_result_all_findings_aggregates():
    """GraphResult.all_findings collects findings from all tasks."""
    from tvastar.detect import Finding, Severity
    from tvastar.session import RunResult
    from tvastar.types import Usage

    f1 = Finding("det_a", Severity.ERROR, "err", {})
    f2 = Finding("det_b", Severity.WARNING, "warn", {})
    results = {
        "x": RunResult(text="x", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[f1], data=None),
        "y": RunResult(text="y", messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[f2], data=None),
    }
    gr = GraphResult(results, findings={"x": [f1], "y": [f2]})
    assert len(gr.all_findings) == 2


# ---------------------------------------------------------------------------
# 15.5: Dependency results injection into task prompt context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_results_injected_into_prompt():
    """Downstream task receives upstream results in its prompt."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["upstream-data-123", "downstream-response"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("source", "produce data")
    graph.task("consumer", "consume data", depends_on=["source"])
    await graph.run()

    # The consumer's prompt should contain the upstream result text
    consumer_prompt = [p for p in captured_prompts if "upstream-data-123" in p]
    assert len(consumer_prompt) > 0, "Upstream result was not injected into downstream prompt"
    # Should also contain the task label
    assert "[source result]" in consumer_prompt[0]


@pytest.mark.asyncio
async def test_multiple_dependency_results_injected():
    """When a task depends on multiple predecessors, all results are injected."""
    captured_prompts: list[str] = []

    class CapturingModel(MockModel):
        async def generate(self, messages, **kw):
            for m in messages:
                if m.role == "user":
                    captured_prompts.append(m.text)
            return await super().generate(messages, **kw)

    model = CapturingModel(script=["result_from_a", "result_from_b", "final_result"])
    agent = create_agent("test", model=model)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    graph.task("a", "first task")
    graph.task("b", "second task")
    graph.task("c", "merge results", depends_on=["a", "b"])
    await graph.run()

    # Find the prompt sent to task c
    merge_prompt = [p for p in captured_prompts if "merge results" in p]
    assert len(merge_prompt) > 0
    # Both dependency results should be present
    assert "result_from_a" in merge_prompt[0]
    assert "result_from_b" in merge_prompt[0]


# ---------------------------------------------------------------------------
# 15.6: Structured output parsing for tasks with result=Schema
# ---------------------------------------------------------------------------


class _TaskOutput(BaseModel):
    summary: str
    score: int


@pytest.mark.asyncio
async def test_structured_output_in_graph_task():
    """When a task has result=Schema, the output is parsed into RunResult.data."""
    json_response = '{"summary": "analysis complete", "score": 95}'
    harness = Harness(_agent([json_response]))

    graph = TaskGraph(harness)
    graph.task("analyze", "do analysis", result=_TaskOutput)
    gr = await graph.run()

    result = gr["analyze"]
    assert isinstance(result.data, _TaskOutput)
    assert result.data.summary == "analysis complete"
    assert result.data.score == 95


@pytest.mark.asyncio
async def test_structured_output_with_dependencies():
    """Structured output works correctly when the task also has dependencies."""
    json_response = '{"summary": "combined", "score": 42}'
    harness = Harness(_agent(["dep_output", json_response]))

    graph = TaskGraph(harness)
    graph.task("gather", "gather data")
    graph.task("analyze", "analyze gathered data", depends_on=["gather"], result=_TaskOutput)
    gr = await graph.run()

    assert gr["gather"].text == "dep_output"
    result = gr["analyze"]
    assert isinstance(result.data, _TaskOutput)
    assert result.data.summary == "combined"
    assert result.data.score == 42


@pytest.mark.asyncio
async def test_structured_output_only_on_specified_tasks():
    """Only the task with result=Schema gets structured parsing; others return plain text."""
    json_response = '{"summary": "done", "score": 10}'
    harness = Harness(_agent(["plain text output", json_response]))

    graph = TaskGraph(harness)
    graph.task("plain", "do plain work")
    graph.task("structured", "do structured work", result=_TaskOutput)
    gr = await graph.run()

    # Plain task has no structured data
    assert gr["plain"].data is None
    # Structured task is parsed
    assert isinstance(gr["structured"].data, _TaskOutput)
    assert gr["structured"].data.summary == "done"


# ---------------------------------------------------------------------------
# Cycle detection (additional cases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_cycle_raises():
    """A task depending on itself is a cycle."""
    harness = Harness(_agent([]))
    graph = TaskGraph(harness)
    graph.task("a", "self-loop", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle detected"):
        await graph.run()


@pytest.mark.asyncio
async def test_three_node_cycle_raises():
    """A → B → C → A forms a cycle."""
    harness = Harness(_agent([]))
    graph = TaskGraph(harness)
    graph.task("a", "A", depends_on=["c"])
    graph.task("b", "B", depends_on=["a"])
    graph.task("c", "C", depends_on=["b"])
    with pytest.raises(ValueError, match="Cycle detected"):
        await graph.run()
