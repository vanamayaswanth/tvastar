"""Tests for LTMStore.traverse() — BFS graph traversal.

Covers:
- Linear chain traversal (A→B→C)
- Circular reference (no infinite loop)
- Expired edge skipping
- Property test: never visits same node twice

**Validates: Requirements 8**
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar.contrib.ltm.store import LTMStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_traverse.db")
    with LTMStore(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestTraverseLinearChain:
    """Linear chain: A→B→C should return both edges at increasing depth."""

    def test_linear_chain(self, store: LTMStore):
        store.relate("A", "DEPENDS_ON", "B")
        store.relate("B", "DEPENDS_ON", "C")

        results = store.traverse("A", edge_type="DEPENDS_ON", max_hops=3)

        assert ("A", "DEPENDS_ON", "B", 1) in results
        assert ("B", "DEPENDS_ON", "C", 2) in results
        assert len(results) == 2

    def test_linear_chain_respects_max_hops(self, store: LTMStore):
        store.relate("A", "DEPENDS_ON", "B")
        store.relate("B", "DEPENDS_ON", "C")
        store.relate("C", "DEPENDS_ON", "D")

        results = store.traverse("A", edge_type="DEPENDS_ON", max_hops=2)

        assert ("A", "DEPENDS_ON", "B", 1) in results
        assert ("B", "DEPENDS_ON", "C", 2) in results
        # C→D is at depth 3, beyond max_hops=2
        assert all(t[3] <= 2 for t in results)
        assert len(results) == 2

    def test_no_outgoing_edges(self, store: LTMStore):
        results = store.traverse("lonely_node", edge_type="DEPENDS_ON")
        assert results == []


class TestTraverseCircularReference:
    """Circular graphs must not loop infinitely — visited set prevents re-visits."""

    def test_circular_no_infinite_loop(self, store: LTMStore):
        store.relate("A", "RELATED_TO", "B")
        store.relate("B", "RELATED_TO", "C")
        store.relate("C", "RELATED_TO", "A")  # cycle back

        results = store.traverse("A", edge_type="RELATED_TO", max_hops=10)

        # Should visit A→B, B→C, C→A (but A already visited, so stops)
        assert ("A", "RELATED_TO", "B", 1) in results
        assert ("B", "RELATED_TO", "C", 2) in results
        assert ("C", "RELATED_TO", "A", 3) in results
        assert len(results) == 3

    def test_self_loop(self, store: LTMStore):
        store.relate("X", "RELATED_TO", "X")

        results = store.traverse("X", edge_type="RELATED_TO", max_hops=5)

        # X→X is found, but X won't be re-visited
        assert ("X", "RELATED_TO", "X", 1) in results
        assert len(results) == 1


class TestTraverseExpiredEdges:
    """Expired edges (valid_until IS NOT NULL) are skipped unless include_expired=True."""

    def test_expired_edge_skipped_by_default(self, store: LTMStore):
        store.relate("A", "DEPENDS_ON", "B")
        rel = store.relate("A", "DEPENDS_ON", "C")
        # Expire the A→C edge
        store._conn.execute(
            "UPDATE relationships SET valid_until = ? WHERE id = ?",
            (time.time(), rel.id),
        )
        store._conn.commit()

        results = store.traverse("A", edge_type="DEPENDS_ON")

        targets = [r[2] for r in results]
        assert "B" in targets
        assert "C" not in targets

    def test_expired_edge_included_when_flagged(self, store: LTMStore):
        store.relate("A", "DEPENDS_ON", "B")
        rel = store.relate("A", "DEPENDS_ON", "C")
        store._conn.execute(
            "UPDATE relationships SET valid_until = ? WHERE id = ?",
            (time.time(), rel.id),
        )
        store._conn.commit()

        results = store.traverse("A", edge_type="DEPENDS_ON", include_expired=True)

        targets = [r[2] for r in results]
        assert "B" in targets
        assert "C" in targets


class TestTraverseEdgeTypeFilter:
    """edge_type=None traverses all edge types."""

    def test_all_edge_types(self, store: LTMStore):
        store.relate("A", "DEPENDS_ON", "B")
        store.relate("A", "CAUSED", "C")

        results = store.traverse("A", edge_type=None, max_hops=1)

        assert len(results) == 2
        edge_types = {r[1] for r in results}
        assert "DEPENDS_ON" in edge_types
        assert "CAUSED" in edge_types


# ---------------------------------------------------------------------------
# Property-based test: never visits same node twice
# ---------------------------------------------------------------------------

# Strategy: generate a small graph as a list of (source, target) edges
# using a small alphabet of node names, then traverse and assert no node
# appears as a source more than once in the results.

_NODE_NAMES = st.sampled_from(["n0", "n1", "n2", "n3", "n4", "n5"])
_EDGE = st.tuples(_NODE_NAMES, _NODE_NAMES)
_GRAPH = st.lists(_EDGE, min_size=1, max_size=15)


@given(edges=_GRAPH)
@settings(max_examples=200, deadline=None)
def test_traverse_never_visits_same_node_twice(edges, tmp_path_factory):
    """**Validates: Requirements 8**

    Property: For any graph shape, BFS never visits the same node twice.
    This means no node appears as a source in results at two different depths
    (the visited set prevents re-expansion).
    """
    db_path = str(tmp_path_factory.mktemp("pbt") / "t.db")
    with LTMStore(db_path) as store:
        for src, tgt in edges:
            store.relate(src, "RELATED_TO", tgt)

        start = edges[0][0]
        results = store.traverse(start, edge_type="RELATED_TO", max_hops=6)

        # A node is only expanded once: it should appear as source in results
        # only at a single depth level (the first time BFS reaches it).
        source_depths: dict[str, set[int]] = {}
        for src, _, _, depth in results:
            source_depths.setdefault(src, set()).add(depth)

        for node, depths in source_depths.items():
            assert len(depths) == 1, (
                f"Node {node!r} was expanded at multiple depths {depths}, "
                f"violating the visited-set invariant."
            )
