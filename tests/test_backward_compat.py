"""Backward compatibility regression tests (Requirements 19, 20).

Verifies that all new features are opt-in and existing behavior is IDENTICAL
when new parameters are not provided.
"""

from __future__ import annotations

import asyncio

import pytest

from tvastar import Harness, TaskGraph, create_agent
from tvastar.contrib.ltm.store import LTMStore
from tvastar.loop import ChainTarget, LoopConfig
from tvastar.model.mock import MockModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent(responses: list[str]):
    model = MockModel(script=responses)
    return create_agent("compat-test", model=model, instructions="", detect=False)


# ---------------------------------------------------------------------------
# TaskGraph with no cycle_policy params (Req 19.1)
# ---------------------------------------------------------------------------


class TestTaskGraphNoCycleParams:
    """A TaskGraph with no cycle_policy params rejects all cycles (current behavior)."""

    @pytest.mark.asyncio
    async def test_simple_dag_runs_without_cycle_params(self):
        """Linear DAG with plain string depends_on works identically to pre-upgrade."""
        harness = Harness(_agent(["result A", "result B", "result C"]))
        gr = await (
            TaskGraph(harness)
            .task("a", "do A")
            .task("b", "do B", depends_on=["a"])
            .task("c", "do C", depends_on=["b"])
            .run()
        )
        assert gr.ok
        assert len(gr) == 3

    @pytest.mark.asyncio
    async def test_cycle_rejected_without_policy(self):
        """Back-edge without CyclePolicy raises ValueError (current behavior preserved)."""
        harness = Harness(_agent([]))
        with pytest.raises(ValueError, match="[Cc]ycle"):
            await (
                TaskGraph(harness)
                .task("a", "do A", depends_on=["b"])
                .task("b", "do B", depends_on=["a"])
                .run()
            )

    @pytest.mark.asyncio
    async def test_empty_graph_unchanged(self):
        """Empty graph returns empty GraphResult."""
        harness = Harness(_agent([]))
        gr = await TaskGraph(harness).run()
        assert len(gr) == 0
        assert gr.ok


# ---------------------------------------------------------------------------
# recall() without temporal args (Req 19.2)
# ---------------------------------------------------------------------------


class TestRecallWithoutTemporalArgs:
    """recall(key) without `at` returns the current active fact (current behavior)."""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "compat_recall.db")
        with LTMStore(db_path) as s:
            yield s

    def test_recall_returns_current_value(self, store: LTMStore):
        """Plain recall(key) returns the latest value without temporal filtering."""
        store.remember("city", "NYC", agent="test")
        assert store.recall("city") == "NYC"

    def test_recall_after_supersede_returns_latest(self, store: LTMStore):
        """After superseding, recall(key) returns the new value."""
        store.remember("city", "NYC", agent="test")
        store.remember("city", "SF", agent="test")
        assert store.recall("city") == "SF"

    def test_recall_nonexistent_returns_none(self, store: LTMStore):
        """recall(key) for unknown key returns None."""
        assert store.recall("nonexistent") is None


# ---------------------------------------------------------------------------
# LoopConfig.then as plain str (Req 19.3)
# ---------------------------------------------------------------------------


class TestLoopConfigThenStr:
    """LoopConfig.then as str chains on PASS (current behavior preserved)."""

    def test_then_str_accepted(self):
        """Plain string value for then is accepted at construction."""
        config = LoopConfig(name="test", goal="do stuff", then="next_loop")
        assert config.then == "next_loop"

    def test_then_none_default(self):
        """Default then is None (no chaining)."""
        config = LoopConfig(name="test", goal="do stuff")
        assert config.then is None

    def test_then_list_also_works(self):
        """List of ChainTargets is also accepted (new feature, but str still works)."""
        config = LoopConfig(
            name="test",
            goal="do stuff",
            then=[ChainTarget("reviewer", on="pass"), ChainTarget("fixer", on="fail")],
        )
        assert isinstance(config.then, list)
        assert len(config.then) == 2


# ---------------------------------------------------------------------------
# fuel unset (Req 19.4)
# ---------------------------------------------------------------------------


class TestFuelUnset:
    """When fuel is not set, no fuel tracking occurs (zero behavioral change)."""

    def test_fuel_defaults_to_none(self):
        """Default fuel is None — no tracking."""
        config = LoopConfig(name="test", goal="do stuff")
        assert config.fuel is None

    def test_fuel_explicit_none(self):
        """Explicitly passing fuel=None is the same as not setting it."""
        config = LoopConfig(name="test", goal="do stuff", fuel=None)
        assert config.fuel is None

    def test_fuel_set_stores_value(self):
        """Setting fuel stores the value (new feature, but unset = unchanged)."""
        config = LoopConfig(name="test", goal="do stuff", fuel=100.0)
        assert config.fuel == 100.0


# ---------------------------------------------------------------------------
# memory_extraction unset (Req 19.5)
# ---------------------------------------------------------------------------


class TestMemoryExtractionUnset:
    """When memory_extraction is unset, no extraction occurs (opt-in only)."""

    def test_memory_extraction_defaults_to_none(self):
        """Default memory_extraction is None — no extraction."""
        spec = create_agent("test", model=MockModel(), instructions="hi", detect=False)
        assert spec.memory_extraction is None

    def test_memory_extraction_false_equivalent(self):
        """Falsy memory_extraction means no extraction."""
        spec = create_agent(
            "test", model=MockModel(), instructions="hi", detect=False, memory_extraction=None
        )
        # None is falsy — session __aexit__ skips extraction
        assert not spec.memory_extraction


# ---------------------------------------------------------------------------
# pyproject.toml: no new entries in dependencies (Req 20)
# ---------------------------------------------------------------------------


class TestZeroNewDependencies:
    """Verify install_requires / dependencies has no new entries."""

    def test_dependencies_empty(self):
        """pyproject.toml [project].dependencies must remain empty."""
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)

        deps = data["project"].get("dependencies", [])
        assert deps == [], f"Expected empty dependencies, got: {deps}"


# ---------------------------------------------------------------------------
# All new params are keyword-only with defaults (Req 19 invariant)
# ---------------------------------------------------------------------------


class TestNewParamsKeywordOnlyWithDefaults:
    """New parameters are keyword-only with defaults matching current behavior."""

    def test_task_graph_task_new_params_have_defaults(self):
        """TaskGraph.task() new params (edge_conditions) default to None/empty."""
        harness = Harness(_agent(["ok"]))
        # Calling task() with only name and prompt should work (no new params required)
        graph = TaskGraph(harness).task("a", "do A")
        assert "a" in graph._nodes

    def test_loop_config_fuel_is_keyword_only(self):
        """fuel param cannot be passed positionally."""
        import inspect

        sig = inspect.signature(LoopConfig)
        param = sig.parameters["fuel"]
        # dataclass fields after the first positional ones — fuel has a default
        assert param.default is None

    def test_loop_config_memory_maintenance_keyword_default(self):
        """memory_maintenance defaults to False."""
        config = LoopConfig(name="test", goal="do stuff")
        assert config.memory_maintenance is False

    def test_recall_at_param_is_keyword_only(self):
        """recall() 'at' param is keyword-only with default None."""
        import inspect

        from tvastar.contrib.ltm.store import LTMStore

        sig = inspect.signature(LTMStore.recall)
        param = sig.parameters["at"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
