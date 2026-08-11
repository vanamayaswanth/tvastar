"""Tests for tvastar.contrib.ltm.graphify — NetworkX bridge."""

from __future__ import annotations

import sys
import time
from unittest.mock import patch

import pytest

from tvastar.contrib.ltm.store import LTMStore


@pytest.fixture
def store(tmp_path):
    """Fresh LTMStore with some relationships."""
    db = tmp_path / "test.db"
    s = LTMStore(str(db))
    # Create a small graph: A -> B -> C, A -> C
    s.relate("A", "DEPENDS_ON", "B")
    s.relate("B", "DEPENDS_ON", "C")
    s.relate("A", "RELATED_TO", "C")
    return s


class TestToNetworkx:
    def test_correct_node_and_edge_counts(self, store):
        """to_networkx returns DiGraph with correct node/edge counts."""
        from tvastar.contrib.ltm.graphify import to_networkx

        G = to_networkx(store)
        assert G.number_of_nodes() == 3  # A, B, C
        assert G.number_of_edges() == 3  # A->B, B->C, A->C

    def test_include_expired_false_skips_expired(self, store):
        """Expired relationships are excluded by default."""
        from tvastar.contrib.ltm.graphify import to_networkx

        # Expire one relationship (A -> B)
        now = time.time()
        store._conn.execute(
            "UPDATE relationships SET valid_until = ? WHERE source_key = 'A' AND target_key = 'B'",
            (now,),
        )
        store._conn.commit()

        G = to_networkx(store, include_expired=False)
        assert G.number_of_edges() == 2  # B->C, A->C remain active

    def test_include_expired_true_includes_all(self, store):
        """include_expired=True includes expired edges."""
        from tvastar.contrib.ltm.graphify import to_networkx

        now = time.time()
        store._conn.execute(
            "UPDATE relationships SET valid_until = ? WHERE source_key = 'A' AND target_key = 'B'",
            (now,),
        )
        store._conn.commit()

        G = to_networkx(store, include_expired=True)
        assert G.number_of_edges() == 3  # all three edges

    def test_edge_attributes(self, store):
        """Edges carry edge_type, valid_from, valid_until, confidence attributes."""
        from tvastar.contrib.ltm.graphify import to_networkx

        G = to_networkx(store)
        edge_data = G.get_edge_data("A", "B")
        assert edge_data["edge_type"] == "DEPENDS_ON"
        assert edge_data["confidence"] == 1.0
        assert edge_data["valid_until"] is None
        assert isinstance(edge_data["valid_from"], float)

    def test_empty_store(self, tmp_path):
        """Empty relationships table produces empty DiGraph."""
        from tvastar.contrib.ltm.graphify import to_networkx

        db = tmp_path / "empty.db"
        s = LTMStore(str(db))
        G = to_networkx(s)
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0


class TestImportError:
    def test_raises_import_error_when_networkx_missing(self, store):
        """Raises ImportError with install instructions when networkx is absent."""
        import importlib

        import tvastar.contrib.ltm.graphify as graphify_mod

        # Patch the import mechanism to simulate networkx not being installed
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("No module named 'networkx'")
            return original_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"networkx": None}):
            # Reload module so the try-import runs fresh
            with patch("builtins.__import__", side_effect=mock_import):
                importlib.reload(graphify_mod)
                with pytest.raises(ImportError, match="pip install tvastar\\[graph\\]"):
                    graphify_mod.to_networkx(store)

        # Reload to restore normal state
        importlib.reload(graphify_mod)
