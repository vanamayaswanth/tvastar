"""Tests for CyclePolicy enum/dataclass and task() normalization.

Validates: Requirement 2
"""

import pytest

from tvastar import CyclePolicy, Harness, TaskGraph, create_agent
from tvastar.model import MockModel


# ---------------------------------------------------------------------------
# Construction of each CyclePolicy variant
# ---------------------------------------------------------------------------


def test_forbid_is_singleton():
    """FORBID is a singleton sentinel."""
    assert CyclePolicy.FORBID is CyclePolicy.FORBID
    assert CyclePolicy._Forbid() is CyclePolicy.FORBID


def test_allow_ttl_construction():
    """ALLOW_TTL wraps a positive max_iterations."""
    policy = CyclePolicy.ALLOW_TTL(3)
    assert policy.max_iterations == 3


def test_allow_ttl_rejects_zero():
    with pytest.raises(ValueError, match="max_iterations must be >= 1"):
        CyclePolicy.ALLOW_TTL(0)


def test_allow_ttl_rejects_negative():
    with pytest.raises(ValueError, match="max_iterations must be >= 1"):
        CyclePolicy.ALLOW_TTL(-1)


def test_allow_predicate_construction():
    """ALLOW_PREDICATE wraps a callable."""

    def fn(r):
        return True

    policy = CyclePolicy.ALLOW_PREDICATE(fn)
    assert policy.predicate is fn


# ---------------------------------------------------------------------------
# Equality comparison between policies
# ---------------------------------------------------------------------------


def test_forbid_equality():
    assert CyclePolicy.FORBID == CyclePolicy.FORBID
    assert CyclePolicy.FORBID != CyclePolicy.ALLOW_TTL(1)


def test_allow_ttl_equality():
    assert CyclePolicy.ALLOW_TTL(3) == CyclePolicy.ALLOW_TTL(3)
    assert CyclePolicy.ALLOW_TTL(3) != CyclePolicy.ALLOW_TTL(5)


def test_allow_predicate_equality():
    def fn(r):
        return True

    def other_fn(r):
        return True

    assert CyclePolicy.ALLOW_PREDICATE(fn) == CyclePolicy.ALLOW_PREDICATE(fn)
    # Different callables are not equal
    assert CyclePolicy.ALLOW_PREDICATE(fn) != CyclePolicy.ALLOW_PREDICATE(other_fn)


def test_cross_variant_inequality():
    def fn(r):
        return True

    assert CyclePolicy.FORBID != CyclePolicy.ALLOW_TTL(1)
    assert CyclePolicy.FORBID != CyclePolicy.ALLOW_PREDICATE(fn)
    assert CyclePolicy.ALLOW_TTL(1) != CyclePolicy.ALLOW_PREDICATE(fn)


# ---------------------------------------------------------------------------
# Normalization of plain string vs tuple format in task()
# ---------------------------------------------------------------------------


def _make_graph():
    agent = create_agent("t", model=MockModel(script=["ok"]))
    return TaskGraph(Harness(agent))


def test_plain_string_dependency():
    """Plain string deps normalize to depends_on list with no cycle policy."""
    g = _make_graph()
    g.task("a", "do A")
    g.task("b", "do B", depends_on=["a"])
    node = g._nodes["b"]
    assert node.depends_on == ["a"]
    assert node.cycle_policies == {}


def test_tuple_dependency_with_policy():
    """Tuple (name, policy) normalizes: name goes to depends_on, policy stored."""
    g = _make_graph()
    g.task("writer", "write")
    g.task("reviewer", "review", depends_on=[("writer", CyclePolicy.ALLOW_TTL(3))])
    node = g._nodes["reviewer"]
    assert node.depends_on == ["writer"]
    assert node.cycle_policies == {"writer": CyclePolicy.ALLOW_TTL(3)}


def test_mixed_dependencies():
    """Mixed plain strings and tuples both normalize correctly."""
    g = _make_graph()
    g.task("a", "task a")
    g.task("b", "task b")

    def pred(r):
        return "done" in r.text

    g.task("c", "task c", depends_on=["a", ("b", CyclePolicy.ALLOW_PREDICATE(pred))])
    node = g._nodes["c"]
    assert node.depends_on == ["a", "b"]
    assert "a" not in node.cycle_policies
    assert node.cycle_policies["b"] == CyclePolicy.ALLOW_PREDICATE(pred)


def test_invalid_dependency_format_raises():
    """Non-string, non-tuple dep raises ValueError."""
    g = _make_graph()
    g.task("a", "do A")
    with pytest.raises(ValueError, match="must be str or"):
        g.task("b", "do B", depends_on=[123])  # type: ignore[list-item]


def test_no_depends_on_defaults_empty():
    """No depends_on arg gives empty lists."""
    g = _make_graph()
    g.task("a", "do A")
    node = g._nodes["a"]
    assert node.depends_on == []
    assert node.cycle_policies == {}


# ---------------------------------------------------------------------------
# Repr sanity
# ---------------------------------------------------------------------------


def test_repr_forbid():
    assert "FORBID" in repr(CyclePolicy.FORBID)


def test_repr_allow_ttl():
    assert "ALLOW_TTL(3)" in repr(CyclePolicy.ALLOW_TTL(3))


def test_repr_allow_predicate():
    assert "ALLOW_PREDICATE" in repr(CyclePolicy.ALLOW_PREDICATE(lambda r: True))
