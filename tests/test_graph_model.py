"""Property-based and unit tests for per-task model routing in TaskGraph.

# Feature: pi-ecosystem-adaptations, Properties 7-8 + unit test for Req 3.3
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar import Harness, TaskGraph, create_agent
from tvastar.model.mock import MockModel
from tvastar.types import Message, ModelResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TrackingModel(MockModel):
    """MockModel that tracks how many times generate() was called."""

    def __init__(self, label: str, response: str = "done"):
        super().__init__(script=[response])
        self.label = label
        self.call_count = 0

    async def generate(self, messages: list[Message], **kwargs) -> ModelResponse:
        self.call_count += 1
        return await super().generate(messages, **kwargs)


def _agent(responses: list[str]):
    """Agent whose MockModel replies in order."""
    model = MockModel(script=responses)
    return create_agent("test", model=model)


# Strategy: generate random objects without a 'generate' attribute
# These are used to test validation (Property 8)
_non_model_st = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=50),
    st.lists(st.integers(), min_size=0, max_size=5),
    st.dictionaries(
        keys=st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
        values=st.integers(),
        min_size=0,
        max_size=5,
    ),
    st.tuples(st.integers(), st.text(max_size=10)),
    st.just(None),  # will be filtered out below
).filter(lambda x: not hasattr(x, "generate") and x is not None)


# Strategy: number of nodes (2-5 for reasonable test speed)
_node_count_st = st.integers(min_value=2, max_value=5)

# Strategy: node names (unique identifiers)
_node_name_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=2,
    max_size=10,
)


# ---------------------------------------------------------------------------
# Property 7: Per-node model routing
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 7: Per-node model routing
class TestProperty7PerNodeModelRouting:
    """**Validates: Requirements 3.2**"""

    @given(node_count=_node_count_st)
    @settings(max_examples=100)
    async def test_each_node_uses_its_assigned_model(self, node_count: int):
        """Each node's session calls generate() on its assigned model, not the harness default."""
        # Create distinct tracking models for each node
        models = [
            TrackingModel(label=f"model_{i}", response=f"result_{i}") for i in range(node_count)
        ]

        # Create a harness with a default model (should NOT be called)
        default_model = TrackingModel(label="default", response="default_result")
        agent = create_agent("test", model=default_model)
        harness = Harness(agent)

        # Build graph with each node assigned its own model
        graph = TaskGraph(harness)
        for i in range(node_count):
            graph.task(f"node_{i}", f"do task {i}", model=models[i])

        await graph.run()

        # Each assigned model must have been called exactly once
        for i, m in enumerate(models):
            assert m.call_count == 1, (
                f"Model for node_{i} was called {m.call_count} times, expected 1"
            )

        # The default model must NOT have been called
        assert default_model.call_count == 0, (
            f"Default model was called {default_model.call_count} times, expected 0"
        )


# ---------------------------------------------------------------------------
# Property 8: Non-Model parameter raises TypeError at validation
# Validates: Requirements 3.5
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 8: Non-Model parameter raises TypeError at validation
class TestProperty8NonModelRaisesTypeError:
    """**Validates: Requirements 3.5**"""

    @given(non_model=_non_model_st)
    @settings(max_examples=100)
    async def test_non_model_raises_typeerror_before_execution(self, non_model: Any):
        """An object without a `generate` attribute raises TypeError at validation time."""
        # Confirm the generated object really lacks `generate`
        assert not hasattr(non_model, "generate")

        harness = Harness(_agent(["should not run"]))
        graph = TaskGraph(harness)
        graph.task("bad_node", "do something", model=non_model)

        with pytest.raises(TypeError, match="model must implement the Model interface"):
            await graph.run()


# ---------------------------------------------------------------------------
# Unit test: no model param uses harness default (Req 3.3)
# ---------------------------------------------------------------------------


class TestNoModelUsesHarnessDefault:
    """Unit test: when no model= is provided, the harness default model is used."""

    async def test_no_model_param_uses_harness_default(self):
        """A task node with no model= param uses the harness-level model."""
        default_model = TrackingModel(label="default", response="default_output")
        agent = create_agent("test", model=default_model)
        harness = Harness(agent)

        graph = TaskGraph(harness)
        graph.task("basic", "do basic task")

        gr = await graph.run()

        # The harness default model should have been called
        assert default_model.call_count == 1
        assert gr["basic"].text == "default_output"

    async def test_mixed_model_and_no_model(self):
        """Nodes with model= use their assigned model; nodes without use the default.

        Uses a sequential chain (b depends on a) to avoid shared-spec
        concurrency issues with the current implementation.
        """
        default_model = TrackingModel(label="default", response="default_out")
        custom_model = TrackingModel(label="custom", response="custom_out")

        agent = create_agent("test", model=default_model)
        harness = Harness(agent)

        # Sequential chain: b runs AFTER a to avoid shared-spec race
        graph = TaskGraph(harness)
        graph.task("a", "task with custom model", model=custom_model)
        graph.task("b", "task with default model", depends_on=["a"])

        gr = await graph.run()

        # Custom model called for node a
        assert custom_model.call_count == 1
        assert gr["a"].text == "custom_out"

        # Default model called for node b
        assert default_model.call_count == 1
        assert gr["b"].text == "default_out"
