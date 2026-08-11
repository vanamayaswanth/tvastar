"""Tests for EdgeType enum and relate() validation.

Validates: Requirements 9
"""

from __future__ import annotations

import pytest

from tvastar.contrib.ltm.store import EdgeType, LTMStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_edge_type.db")
    with LTMStore(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# EdgeType enum basics
# ---------------------------------------------------------------------------


class TestEdgeTypeEnum:
    def test_all_ten_members_exist(self):
        expected = {
            "SUPERSEDES",
            "DEPENDS_ON",
            "DECIDED_BY",
            "CAUSED",
            "CONTRADICTS",
            "RELATED_TO",
            "PART_OF",
            "DERIVED_FROM",
            "INVALIDATES",
            "SUPPORTS",
        }
        assert {e.value for e in EdgeType} == expected

    def test_is_str_subclass(self):
        """EdgeType members are strings — can be used directly as str."""
        assert isinstance(EdgeType.SUPERSEDES, str)
        assert EdgeType.CAUSED == "CAUSED"

    def test_case_insensitive_construction(self):
        """EdgeType('supersedes') resolves to EdgeType.SUPERSEDES."""
        assert EdgeType("supersedes") is EdgeType.SUPERSEDES
        assert EdgeType("Depends_On") is EdgeType.DEPENDS_ON
        assert EdgeType("CAUSED") is EdgeType.CAUSED

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            EdgeType("NOT_A_TYPE")


# ---------------------------------------------------------------------------
# relate() validation
# ---------------------------------------------------------------------------


class TestRelateValidation:
    ALL_TYPES = [
        "SUPERSEDES",
        "DEPENDS_ON",
        "DECIDED_BY",
        "CAUSED",
        "CONTRADICTS",
        "RELATED_TO",
        "PART_OF",
        "DERIVED_FROM",
        "INVALIDATES",
        "SUPPORTS",
    ]

    @pytest.mark.parametrize("edge_type", ALL_TYPES)
    def test_all_valid_types_accepted(self, store: LTMStore, edge_type: str):
        """All 10 EdgeType values are accepted by relate()."""
        rel = store.relate("a", edge_type, "b")
        assert rel.edge_type == edge_type

    @pytest.mark.parametrize(
        "variant",
        ["supersedes", "Supersedes", "SUPERSEDES", "sUpErSeDeS"],
    )
    def test_case_insensitive_matching(self, store: LTMStore, variant: str):
        """relate() accepts any casing and normalizes to uppercase."""
        rel = store.relate("a", variant, "b")
        assert rel.edge_type == "SUPERSEDES"

    def test_invalid_type_raises_valueerror(self, store: LTMStore):
        """Invalid edge_type raises ValueError listing valid types."""
        with pytest.raises(ValueError, match="Unknown edge_type") as exc_info:
            store.relate("a", "MADE_UP_TYPE", "b")
        # The error message lists valid types
        msg = str(exc_info.value)
        assert "SUPERSEDES" in msg
        assert "DEPENDS_ON" in msg

    def test_invalid_type_error_contains_all_valid(self, store: LTMStore):
        """ValueError message lists all 10 valid types."""
        with pytest.raises(ValueError) as exc_info:
            store.relate("a", "bogus", "b")
        msg = str(exc_info.value)
        for t in self.ALL_TYPES:
            assert t in msg


# ---------------------------------------------------------------------------
# Import path
# ---------------------------------------------------------------------------


class TestImportability:
    def test_importable_from_tvastar_contrib_ltm(self):
        """EdgeType is importable from the tvastar.contrib.ltm package."""
        from tvastar.contrib.ltm import EdgeType as ET

        assert ET is EdgeType
        assert ET.SUPERSEDES.value == "SUPERSEDES"
