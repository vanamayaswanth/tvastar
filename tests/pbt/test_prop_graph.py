"""Property-based tests for DAG task execution.

Property 30: DAG topological execution
- For any TaskGraph, no task SHALL begin before all its dependencies have
  completed. Independent tasks SHALL be eligible for concurrent execution.

**Validates: Requirements 15.1, 15.2**
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

import hypothesis.strategies as st
from hypothesis import given, settings, assume

from tvastar import Harness, TaskGraph, create_agent
from tvastar.model.mock import MockModel
from tvastar.types import Message, ModelResponse, StopReason, TextBlock, Usage


# ---------------------------------------------------------------------------
# Hypothesis strategy: random valid DAGs (acyclic graphs)
# ---------------------------------------------------------------------------


@st.composite
def st_dag(draw: st.DrawFn):
    """Generate a random valid DAG as a dict of {task_name: [dependency_names]}.

    Strategy: assign each node an integer layer (topological level). A node can
    only depend on nodes in strictly lower layers, guaranteeing acyclicity.
    """
    num_nodes = draw(st.integers(min_value=2, max_value=8))
    # Each node gets a layer from 0..num_nodes-1; layer 0 nodes have no deps
    layers = [draw(st.integers(min_value=0, max_value=num_nodes - 1)) for _ in range(num_nodes)]

    # Ensure at least one root (layer 0) node
    if 0 not in layers:
        layers[0] = 0

    task_names = [f"t{i}" for i in range(num_nodes)]
    dag: dict[str, list[str]] = {}

    for i, name in enumerate(task_names):
        my_layer = layers[i]
        if my_layer == 0:
            dag[name] = []
        else:
            # Potential dependencies: nodes in strictly lower layers
            candidates = [task_names[j] for j in range(num_nodes) if layers[j] < my_layer]
            if not candidates:
                dag[name] = []
            else:
                # Draw a non-empty subset of candidates as dependencies
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
# Custom Model that tracks execution timestamps
# ---------------------------------------------------------------------------


class TimingModel:
    """A model that records start/end timestamps per task for ordering verification."""

    name = "timing-mock"
    system = "mock"

    def __init__(self, execution_log: dict[str, dict[str, float]], delay: float = 0.01):
        self._log = execution_log
        self._delay = delay
        self._call_count = 0

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
        # Extract the task name from the prompt text
        # The prompt will contain the task name as we set it
        user_msg = next((m for m in reversed(messages) if m.role == "user"), None)
        prompt_text = user_msg.text if user_msg else ""

        # Find the task name from our marker format "TASK:<name>"
        task_name = None
        for line in prompt_text.split("\n"):
            if line.startswith("TASK:"):
                task_name = line.split("TASK:")[1].strip()
                break

        if task_name:
            self._log[task_name] = {"start": time.monotonic()}
            # Small delay to allow timing differentiation
            await asyncio.sleep(self._delay)
            self._log[task_name]["end"] = time.monotonic()

        return ModelResponse(
            message=Message("assistant", [TextBlock(text=f"done:{task_name}")]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=10, output_tokens=5),
        )


# ---------------------------------------------------------------------------
# Property 30: DAG topological execution
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(dag=st_dag())
async def test_dag_topological_execution_order(dag: dict[str, list[str]]):
    """Property 30: DAG topological execution — dependency ordering.

    For any TaskGraph, no task SHALL begin before all its dependencies
    have completed.

    We generate random valid DAGs, execute them with a timing-instrumented
    model, and verify that for every task T with dependencies D1, D2, ...,
    T's start time is >= max(Di.end_time) for all Di.

    **Validates: Requirements 15.1, 15.2**
    """
    execution_log: dict[str, dict[str, float]] = {}
    model = TimingModel(execution_log, delay=0.01)

    agent = create_agent(
        "dag-topo-test",
        model=model,
        instructions="Execute tasks",
        detect=False,
    )
    harness = Harness(agent)

    graph = TaskGraph(harness)
    for task_name, deps in dag.items():
        graph.task(task_name, f"TASK:{task_name}", depends_on=deps if deps else None)

    await graph.run()

    # Verify: no task starts before all its dependencies have completed
    for task_name, deps in dag.items():
        assert task_name in execution_log, (
            f"Task {task_name!r} was not executed"
        )
        task_start = execution_log[task_name]["start"]

        for dep in deps:
            assert dep in execution_log, (
                f"Dependency {dep!r} of task {task_name!r} was not executed"
            )
            dep_end = execution_log[dep]["end"]
            assert task_start >= dep_end, (
                f"Task {task_name!r} started at {task_start:.6f} but dependency "
                f"{dep!r} ended at {dep_end:.6f} — topological order violated!"
            )


@settings(max_examples=50, deadline=None)
@given(dag=st_dag())
async def test_dag_independent_tasks_concurrent_eligibility(dag: dict[str, list[str]]):
    """Property 30: DAG topological execution — concurrency eligibility.

    Independent tasks (those with no dependency relationship between them)
    SHALL be eligible for concurrent execution. We verify this by checking
    that tasks in the same topological layer can overlap in execution time.

    For graphs with multiple root tasks (no dependencies), their execution
    windows should overlap, demonstrating concurrent scheduling.

    **Validates: Requirements 15.1, 15.2**
    """
    # Find root tasks (no dependencies) — these should run concurrently
    roots = [name for name, deps in dag.items() if not deps]
    assume(len(roots) >= 2)  # Need at least 2 independent tasks to test concurrency

    execution_log: dict[str, dict[str, float]] = {}
    # Use a slightly longer delay to make concurrency measurable
    model = TimingModel(execution_log, delay=0.05)

    agent = create_agent(
        "dag-concurrent-test",
        model=model,
        instructions="Execute tasks",
        detect=False,
    )
    harness = Harness(agent)

    graph = TaskGraph(harness)
    for task_name, deps in dag.items():
        graph.task(task_name, f"TASK:{task_name}", depends_on=deps if deps else None)

    await graph.run()

    # Verify all root tasks executed
    for root in roots:
        assert root in execution_log, f"Root task {root!r} was not executed"

    # Verify that root tasks have overlapping execution windows (concurrent eligibility).
    # If they ran sequentially, the last one's start would be >= first one's end * (n-1).
    # With concurrency, at least two should overlap.
    root_starts = [(name, execution_log[name]["start"]) for name in roots]
    root_starts.sort(key=lambda x: x[1])

    # Check that at least the first two roots started before the first root finished
    first_root_name = root_starts[0][0]
    first_root_end = execution_log[first_root_name]["end"]
    second_root_start = root_starts[1][1]

    # The second root task should have started before the first root task finished,
    # proving they were scheduled concurrently
    assert second_root_start < first_root_end, (
        f"Independent root tasks were not scheduled concurrently: "
        f"{first_root_name!r} ended at {first_root_end:.6f} but "
        f"{root_starts[1][0]!r} started at {second_root_start:.6f}. "
        f"Expected overlap for concurrent eligibility."
    )
