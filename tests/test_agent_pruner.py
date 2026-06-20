"""Tests for AgentPruner — dynamic agent dropout based on quality scores."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tvastar import AgentPruner
from tvastar.profiles import AgentProfile


def _profiles():
    return [
        AgentProfile(name="coder",    description="Write Python code"),
        AgentProfile(name="reviewer", description="Review code"),
        AgentProfile(name="tester",   description="Write tests"),
    ]


def _result(score: float):
    """Fake RunResult that produces the given quality score."""
    mock = MagicMock()
    report = MagicMock()
    report.score = score
    with patch("tvastar.quality.score_run", return_value=report):
        return mock, report


class TestAgentPruner:
    def test_all_active_with_no_runs(self):
        pruner = AgentPruner(threshold=60.0)
        assert pruner.active(_profiles()) == _profiles()

    def test_update_records_score(self):
        pruner = AgentPruner(threshold=60.0)
        report = MagicMock()
        report.score = 80.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("coder", MagicMock())
        assert pruner.avg_score("coder") == pytest.approx(80.0)

    def test_high_score_profile_kept(self):
        pruner = AgentPruner(threshold=60.0)
        report = MagicMock()
        report.score = 90.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("coder", MagicMock())
        active = pruner.active(_profiles())
        assert any(p.name == "coder" for p in active)

    def test_low_score_profile_pruned(self):
        pruner = AgentPruner(threshold=60.0)
        report = MagicMock()
        report.score = 30.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("coder", MagicMock())
        active = pruner.active(_profiles())
        assert not any(p.name == "coder" for p in active)

    def test_unseen_profiles_always_active(self):
        pruner = AgentPruner(threshold=60.0)
        report = MagicMock()
        report.score = 10.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("coder", MagicMock())
        # reviewer and tester have no runs — should stay in
        active = {p.name for p in pruner.active(_profiles())}
        assert "reviewer" in active
        assert "tester" in active

    def test_min_runs_prevents_early_pruning(self):
        pruner = AgentPruner(threshold=60.0, min_runs=3)
        report = MagicMock()
        report.score = 10.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("coder", MagicMock())  # only 1 run, min_runs=3
        assert not pruner.should_prune("coder")

    def test_rolling_average_used(self):
        pruner = AgentPruner(threshold=60.0)
        for score in [90.0, 90.0, 10.0]:  # avg = 63.3 → above threshold
            r = MagicMock()
            r.score = score
            with patch("tvastar.quality.score_run", return_value=r):
                pruner.update("coder", MagicMock())
        assert not pruner.should_prune("coder")

    def test_pruned_returns_dropped_profiles(self):
        pruner = AgentPruner(threshold=60.0)
        report = MagicMock()
        report.score = 20.0
        with patch("tvastar.quality.score_run", return_value=report):
            pruner.update("reviewer", MagicMock())
        dropped = {p.name for p in pruner.pruned(_profiles())}
        assert dropped == {"reviewer"}

    def test_avg_score_none_for_unseen(self):
        pruner = AgentPruner()
        assert pruner.avg_score("ghost") is None

    def test_repr(self):
        pruner = AgentPruner(threshold=70.0)
        assert "AgentPruner" in repr(pruner)
        assert "70.0" in repr(pruner)

    def test_exported_from_tvastar(self):
        from tvastar import AgentPruner as AP
        assert AP is AgentPruner
