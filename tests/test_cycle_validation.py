"""Tests for _validate() CyclePolicy handling on back-edges.

Validates: Requirement 2

Tests:
- ALLOW_TTL on a back-edge passes validation (no ValueError)
- ALLOW_PREDICATE on a back-edge passes validation (no ValueError)
- FORBID on a back-edge still raises ValueError
- Unset/default on a back-edge raises ValueError (same as FORBID)
- _cycle_edges dict is populated correctly for allowed back-edges
"""

import pytest

from tvastar import CyclePolicy, Harness, TaskGraph, create_agent
from tvastar.model import MockModel


def _make_graph():
    agent = create_agent("t", model=MockModel(script=["ok"]))
    return TaskGraph(Harness(agent))


# ---------------------------------------------------------------------------
# ALLOW_TTL passes validation
# ---------------------------------------------------------------------------


def test_allow_ttl_passes_validation():
    """A cycle with ALLOW_TTL on the back-edge does not raise.

    Pattern: write depends on review (back-edge with policy),
    review depends on write (forward edge).
    """
    g = _make_graph()
    g.task("write", "write", depends_on=[("review", CyclePolicy.ALLOW_TTL(3))])
    g.task("review", "review", depends_on=["write"])
    # Should not raise regardless of DFS visit order
    g._validate()


def test_allow_predicate_passes_validation():
    """A cycle with ALLOW_PREDICATE on the back-edge does not raise."""
    g = _make_graph()
    pred = lambda r: "RETRY" in r.text
    g.task("write", "write", depends_on=[("review", CyclePolicy.ALLOW_PREDICATE(pred))])
    g.task("review", "review", depends_on=["write"])
    g._validate()


# ---------------------------------------------------------------------------
# FORBID still raises
# ---------------------------------------------------------------------------


def test_forbid_raises_on_back_edge():
    """A cycle with explicit FORBID raises ValueError."""
    g = _make_graph()
    g.task("a", "A", depends_on=[("b", CyclePolicy.FORBID)])
    g.task("b", "B", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle detected"):
        g._validate()


# ---------------------------------------------------------------------------
# Unset/default raises (same as FORBID)
# ---------------------------------------------------------------------------


def test_unset_policy_raises_on_back_edge():
    """A back-edge with no cycle_policy (unset) defaults to FORBID."""
    g = _make_graph()
    g.task("a", "A", depends_on=["b"])
    g.task("b", "B", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle detected"):
        g._validate()


def test_self_cycle_unset_raises():
    """Self-cycle with no policy raises."""
    g = _make_graph()
    g.task("a", "A", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle detected"):
        g._validate()


# ---------------------------------------------------------------------------
# _cycle_edges populated correctly
# ---------------------------------------------------------------------------


def test_cycle_edges_populated_for_allow_ttl():
    """_cycle_edges registers the back-edge with its policy."""
    g = _make_graph()
    policy = CyclePolicy.ALLOW_TTL(5)
    g.task("write", "write", depends_on=[("review", policy)])
    g.task("review", "review", depends_on=["write"])
    g._validate()
    assert len(g._cycle_edges) == 1
    edge_key = list(g._cycle_edges.keys())[0]
    assert g._cycle_edges[edge_key] == policy


def test_cycle_edges_populated_for_allow_predicate():
    """_cycle_edges registers ALLOW_PREDICATE back-edge."""
    g = _make_graph()
    pred = lambda r: True
    policy = CyclePolicy.ALLOW_PREDICATE(pred)
    g.task("write", "write", depends_on=[("review", policy)])
    g.task("review", "review", depends_on=["write"])
    g._validate()
    assert len(g._cycle_edges) == 1
    edge_key = list(g._cycle_edges.keys())[0]
    assert g._cycle_edges[edge_key] == policy


def test_cycle_edges_empty_for_dag():
    """A valid DAG with no cycles has empty _cycle_edges."""
    g = _make_graph()
    g.task("a", "A")
    g.task("b", "B", depends_on=["a"])
    g.task("c", "C", depends_on=["b"])
    g._validate()
    assert g._cycle_edges == {}


# ---------------------------------------------------------------------------
# Backward compat: non-cycling graphs unchanged
# ---------------------------------------------------------------------------


def test_dag_validates_identically():
    """A graph with no cycle_policy params validates exactly as before."""
    g = _make_graph()
    g.task("a", "A")
    g.task("b", "B", depends_on=["a"])
    g.task("c", "C", depends_on=["a"])
    g.task("d", "D", depends_on=["b", "c"])
    g._validate()
    assert g._cycle_edges == {}
