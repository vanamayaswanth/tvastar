"""Property-based and unit tests for TaskGraph resume journal.

# Feature: pi-ecosystem-adaptations, Properties 16-19 + unit tests for Req 6.5, 6.8
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar import Harness, TaskGraph, create_agent
from tvastar.memory.store import InMemoryStore, Store
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


class FailingStore(Store):
    """A Store that raises on every operation."""

    def __init__(self, error: Exception | None = None):
        self._error = error or RuntimeError("store failure")

    def get(self, key: str) -> Any:
        raise self._error

    def set(self, key: str, value: Any) -> None:
        raise self._error

    def delete(self, key: str) -> None:
        raise self._error

    def keys(self, prefix: str = "") -> list[str]:
        raise self._error


class CorruptStore(Store):
    """A Store that returns corrupt (non-string) values for pre-populated keys."""

    def __init__(self, corrupt_data: dict[str, Any]):
        self._data = corrupt_data

    def get(self, key: str) -> Any:
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._data if k.startswith(prefix))


def _make_harness(model: MockModel) -> Harness:
    """Create a Harness with the given model."""
    agent = create_agent("test", model=model)
    return Harness(agent)


# Strategies
_graph_run_id_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=20,
)

_node_name_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=2,
    max_size=10,
)

_node_result_text_st = st.text(min_size=1, max_size=200)

_node_count_st = st.integers(min_value=1, max_value=5)


# ---------------------------------------------------------------------------
# Property 16: Journal write key format
# Validates: Requirements 6.1
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 16: Journal write key format
class TestProperty16JournalWriteKeyFormat:
    """**Validates: Requirements 6.1**"""

    @given(
        graph_run_id=_graph_run_id_st,
        node_names=st.lists(
            _node_name_st,
            min_size=1,
            max_size=4,
            unique=True,
        ),
        responses=st.lists(_node_result_text_st, min_size=4, max_size=4),
    )
    @settings(max_examples=100)
    async def test_journal_write_stores_at_correct_key(
        self,
        graph_run_id: str,
        node_names: list[str],
        responses: list[str],
    ):
        """Completed node stored at "{graph_run_id}:{node_name}" with value = RunResult.text."""
        # Create a model that responds with known text for each node
        model = MockModel(script=[responses[i % len(responses)] for i in range(len(node_names))])
        harness = _make_harness(model)

        journal = InMemoryStore()
        graph = TaskGraph(harness)
        for name in node_names:
            graph.task(name, f"do {name}")

        await graph.run(graph_run_id=graph_run_id, journal=journal)

        # Verify each node's result is stored at the correct key
        for name in node_names:
            key = f"{graph_run_id}:{name}"
            stored = journal.get(key)
            assert stored is not None, f"No journal entry for key {key!r}"
            assert isinstance(stored, str), f"Journal value for {key!r} is not a string"


# ---------------------------------------------------------------------------
# Property 17: Resume skips journaled nodes
# Validates: Requirements 6.2, 6.3
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 17: Resume skips journaled nodes
class TestProperty17ResumeSkipsJournaledNodes:
    """**Validates: Requirements 6.2, 6.3**"""

    @given(
        graph_run_id=_graph_run_id_st,
        node_names=st.lists(
            _node_name_st,
            min_size=2,
            max_size=4,
            unique=True,
        ),
        cached_texts=st.lists(_node_result_text_st, min_size=4, max_size=4),
    )
    @settings(max_examples=100)
    async def test_resume_skips_nodes_with_valid_journal_entries(
        self,
        graph_run_id: str,
        node_names: list[str],
        cached_texts: list[str],
    ):
        """Nodes with valid journal entries don't call model.generate()."""
        # Pre-populate journal for the first half of nodes
        journal = InMemoryStore()
        cached_nodes = node_names[: len(node_names) // 2]
        uncached_nodes = node_names[len(node_names) // 2 :]

        for i, name in enumerate(cached_nodes):
            journal.set(f"{graph_run_id}:{name}", cached_texts[i % len(cached_texts)])

        # Create a tracking model — should only be called for uncached nodes
        model = TrackingModel(label="main", response="fresh_result")
        # Need enough scripted responses for uncached nodes
        model._script = ["fresh_result"] * len(uncached_nodes)
        model._cursor = 0
        harness = _make_harness(model)

        graph = TaskGraph(harness)
        for name in node_names:
            graph.task(name, f"do {name}")

        result = await graph.run(resume=True, graph_run_id=graph_run_id, journal=journal)

        # Model should only be called for uncached nodes
        assert model.call_count == len(uncached_nodes), (
            f"Expected {len(uncached_nodes)} calls, got {model.call_count}"
        )

        # Cached nodes should have their stored text as result
        for i, name in enumerate(cached_nodes):
            assert result[name].text == cached_texts[i % len(cached_texts)]


# ---------------------------------------------------------------------------
# Property 18: Journal fault tolerance
# Validates: Requirements 6.6
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 18: Journal fault tolerance
class TestProperty18JournalFaultTolerance:
    """**Validates: Requirements 6.6**"""

    @given(
        graph_run_id=_graph_run_id_st,
        node_names=st.lists(
            _node_name_st,
            min_size=1,
            max_size=4,
            unique=True,
        ),
        error_msg=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    async def test_store_exceptions_dont_propagate(
        self,
        graph_run_id: str,
        node_names: list[str],
        error_msg: str,
    ):
        """Store exceptions don't propagate; graph completes."""
        failing_journal = FailingStore(RuntimeError(error_msg))

        model = MockModel(script=["done"] * len(node_names))
        harness = _make_harness(model)

        graph = TaskGraph(harness)
        for name in node_names:
            graph.task(name, f"do {name}")

        # Should complete without raising, despite the journal failing
        result = await graph.run(graph_run_id=graph_run_id, journal=failing_journal)

        # All nodes should have completed
        assert len(result) == len(node_names)
        for name in node_names:
            assert result[name].text == "done"


# ---------------------------------------------------------------------------
# Property 19: Corrupt journal entry causes re-execution
# Validates: Requirements 6.7
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 19: Corrupt journal entry causes re-execution
class TestProperty19CorruptJournalEntryCausesReExecution:
    """**Validates: Requirements 6.7**"""

    @given(
        graph_run_id=_graph_run_id_st,
        node_names=st.lists(
            _node_name_st,
            min_size=1,
            max_size=4,
            unique=True,
        ),
        corrupt_values=st.lists(
            st.one_of(
                st.none(),
                st.integers(),
                st.lists(st.integers(), min_size=0, max_size=3),
                st.dictionaries(
                    keys=st.text(min_size=1, max_size=5),
                    values=st.integers(),
                    min_size=0,
                    max_size=3,
                ),
            ),
            min_size=4,
            max_size=4,
        ),
    )
    @settings(max_examples=100)
    async def test_corrupt_entries_cause_re_execution(
        self,
        graph_run_id: str,
        node_names: list[str],
        corrupt_values: list[Any],
    ):
        """None or non-string entries cause node re-execution."""
        # Pre-populate journal with corrupt (non-string) values
        corrupt_data: dict[str, Any] = {}
        for i, name in enumerate(node_names):
            corrupt_data[f"{graph_run_id}:{name}"] = corrupt_values[i % len(corrupt_values)]

        journal = CorruptStore(corrupt_data)

        model = TrackingModel(label="main", response="re-executed")
        model._script = ["re-executed"] * len(node_names)
        model._cursor = 0
        harness = _make_harness(model)

        graph = TaskGraph(harness)
        for name in node_names:
            graph.task(name, f"do {name}")

        result = await graph.run(resume=True, graph_run_id=graph_run_id, journal=journal)

        # All nodes should have been re-executed because entries are corrupt
        assert model.call_count == len(node_names), (
            f"Expected {len(node_names)} calls (all re-executed), got {model.call_count}"
        )

        # All should have the fresh result
        for name in node_names:
            assert result[name].text == "re-executed"


# ---------------------------------------------------------------------------
# Unit tests: run without resume=True executes all nodes (Req 6.5)
# ---------------------------------------------------------------------------


class TestNoResumeExecutesAllNodes:
    """Unit test: run without resume=True executes all nodes regardless of journal."""

    async def test_without_resume_all_nodes_execute(self):
        """Without resume=True, all nodes execute even if journal has cached entries."""
        journal = InMemoryStore()
        graph_run_id = "test-run-1"

        # Pre-populate journal with entries
        journal.set(f"{graph_run_id}:node_a", "cached_a")
        journal.set(f"{graph_run_id}:node_b", "cached_b")

        model = TrackingModel(label="main", response="fresh")
        model._script = ["fresh_a", "fresh_b"]
        model._cursor = 0
        harness = _make_harness(model)

        graph = TaskGraph(harness)
        graph.task("node_a", "do a")
        graph.task("node_b", "do b")

        # Run WITHOUT resume=True — all nodes should execute
        result = await graph.run(graph_run_id=graph_run_id, journal=journal)

        # Both nodes should have been executed (not skipped)
        assert model.call_count == 2
        assert result["node_a"].text == "fresh_a"
        assert result["node_b"].text == "fresh_b"


# ---------------------------------------------------------------------------
# Unit tests: stale entries for absent nodes ignored (Req 6.8)
# ---------------------------------------------------------------------------


class TestStaleEntriesIgnored:
    """Unit test: journal entries for non-existent nodes are ignored."""

    async def test_stale_journal_entries_for_absent_nodes_ignored(self):
        """Journal with entries for non-existent nodes doesn't cause errors."""
        journal = InMemoryStore()
        graph_run_id = "test-run-2"

        # Populate journal with entries for nodes NOT in the current graph
        journal.set(f"{graph_run_id}:old_node_x", "stale_result_x")
        journal.set(f"{graph_run_id}:old_node_y", "stale_result_y")
        # Also add a valid entry for a node that IS in the graph
        journal.set(f"{graph_run_id}:current_a", "cached_a")

        model = TrackingModel(label="main", response="fresh")
        model._script = ["fresh_b"]
        model._cursor = 0
        harness = _make_harness(model)

        graph = TaskGraph(harness)
        graph.task("current_a", "do a")
        graph.task("current_b", "do b")

        result = await graph.run(resume=True, graph_run_id=graph_run_id, journal=journal)

        # current_a should be loaded from cache, current_b should execute
        assert result["current_a"].text == "cached_a"
        assert result["current_b"].text == "fresh_b"
        # Only current_b should have called model
        assert model.call_count == 1
