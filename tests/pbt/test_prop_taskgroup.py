"""Property-based tests for TaskGroup executor (replaces asyncio.gather).

Property: Non-cycling graphs produce IDENTICAL results under TaskGroup
as they would under the previous gather() implementation. Specifically:
- Every node in the DAG produces a result keyed by its name.
- The result text matches the deterministic model output for that node.
- Concurrency semaphore still limits simultaneous model calls.

**Validates: Requirements 2, 6**
"""

from __future__ import annotations

import asyncio

import hypothesis.strategies as st
from hypothesis import given, settings, assume

from tvastar import Harness, TaskGraph, create_agent
from tvastar.types import Message, ModelResponse, StopReason, TextBlock, Usage


# ---------------------------------------------------------------------------
# Strategy: generate random valid DAGs (same as test_prop_graph.py)
# ---------------------------------------------------------------------------


@st.composite
def st_dag(draw: st.DrawFn):
    """Generate a random valid DAG as {task_name: [dependency_names]}."""
    num_nodes = draw(st.integers(min_value=1, max_value=8))
    layers = [draw(st.integers(min_value=0, max_value=num_nodes - 1)) for _ in range(num_nodes)]
    if 0 not in layers:
        layers[0] = 0

    task_names = [f"t{i}" for i in range(num_nodes)]
    dag: dict[str, list[str]] = {}

    for i, name in enumerate(task_names):
        my_layer = layers[i]
        if my_layer == 0:
            dag[name] = []
        else:
            candidates = [task_names[j] for j in range(num_nodes) if layers[j] < my_layer]
            if not candidates:
                dag[name] = []
            else:
                deps = draw(
                    st.lists(
                        st.sampled_from(candidates),
                        min_size=1,
                        max_size=min(3, len(candidates)),
                        unique=True,
                    )
                )
                dag[name] = deps

    return dag


# ---------------------------------------------------------------------------
# Deterministic model: produces a fixed hash of the task name
# ---------------------------------------------------------------------------


class DeterministicModel:
    """Model that returns a deterministic result based on task name in prompt."""

    name = "deterministic-mock"
    system = "mock"

    def __init__(self):
        self._concurrent = 0
        self._max_concurrent = 0

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: list[str] | None = None,
        thinking_level: str | None = None,
    ) -> ModelResponse:
        self._concurrent += 1
        self._max_concurrent = max(self._max_concurrent, self._concurrent)

        # Extract task name from prompt marker
        user_msg = next((m for m in reversed(messages) if m.role == "user"), None)
        prompt_text = user_msg.text if user_msg else ""
        task_name = "unknown"
        for line in prompt_text.split("\n"):
            if line.startswith("TASK:"):
                task_name = line.split("TASK:")[1].strip()
                break

        # Small yield to allow other tasks to run concurrently
        await asyncio.sleep(0.001)

        self._concurrent -= 1

        # Deterministic output: "result_<task_name>"
        return ModelResponse(
            message=Message("assistant", [TextBlock(text=f"result_{task_name}")]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=10, output_tokens=5),
        )


# ---------------------------------------------------------------------------
# Property: Non-cycling DAG produces identical, deterministic results
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(dag=st_dag())
async def test_taskgroup_produces_identical_results_to_gather(dag: dict[str, list[str]]):
    """Property: Non-cycling graph produces identical results under TaskGroup.

    For any valid DAG (no cycle policies), every task produces its expected
    deterministic result keyed by task name, proving TaskGroup behaves
    identically to the previous asyncio.gather() implementation.

    **Validates: Requirements 2, 6**
    """
    model = DeterministicModel()
    agent = create_agent("taskgroup-test", model=model, instructions="Execute", detect=False)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    for task_name, deps in dag.items():
        graph.task(task_name, f"TASK:{task_name}", depends_on=deps if deps else None)

    result = await graph.run()

    # Every task must produce a result
    assert len(result) == len(dag), (
        f"Expected {len(dag)} results, got {len(result)}"
    )

    # Each result must match the deterministic output for that task
    for task_name in dag:
        assert task_name in result.text, f"Missing result for task {task_name!r}"
        assert result[task_name].text == f"result_{task_name}", (
            f"Task {task_name!r} produced {result[task_name].text!r}, "
            f"expected 'result_{task_name}'"
        )

    # cycle_journals should be empty for non-cycling graphs
    assert result.cycle_journals == {}


# ---------------------------------------------------------------------------
# Property: Concurrency semaphore still gates model calls
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    dag=st_dag(),
    concurrency=st.integers(min_value=1, max_value=4),
)
async def test_taskgroup_respects_concurrency_semaphore(
    dag: dict[str, list[str]], concurrency: int
):
    """Property: Concurrency semaphore limits simultaneous model calls under TaskGroup.

    For any valid DAG with concurrency=N, at no point should more than N
    model calls execute simultaneously.

    **Validates: Requirements 2, 6**
    """
    # Need multiple independent tasks to actually test concurrency
    roots = [name for name, deps in dag.items() if not deps]
    assume(len(roots) >= 2)

    concurrent_counter = {"current": 0, "max": 0}

    class ConcurrencyTrackingModel:
        name = "concurrency-mock"
        system = "mock"

        async def generate(
            self,
            messages: list[Message],
            *,
            system: str | None = None,
            tools: list | None = None,
            max_tokens: int = 4096,
            temperature: float = 1.0,
            stop_sequences: list[str] | None = None,
            thinking_level: str | None = None,
        ) -> ModelResponse:
            concurrent_counter["current"] += 1
            concurrent_counter["max"] = max(
                concurrent_counter["max"], concurrent_counter["current"]
            )
            await asyncio.sleep(0.005)  # hold the slot briefly
            concurrent_counter["current"] -= 1

            return ModelResponse(
                message=Message("assistant", [TextBlock(text="done")]),
                stop_reason=StopReason.END_TURN,
                usage=Usage(input_tokens=10, output_tokens=5),
            )

    model = ConcurrencyTrackingModel()
    agent = create_agent("sem-test", model=model, instructions="Execute", detect=False)
    harness = Harness(agent)

    graph = TaskGraph(harness)
    for task_name, deps in dag.items():
        graph.task(task_name, f"TASK:{task_name}", depends_on=deps if deps else None)

    await graph.run(concurrency=concurrency)

    # The semaphore must have bounded concurrent model calls
    assert concurrent_counter["max"] <= concurrency, (
        f"Max concurrent calls was {concurrent_counter['max']}, "
        f"but concurrency limit is {concurrency}"
    )
