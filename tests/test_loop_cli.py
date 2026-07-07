"""Tests for tvastar loop CLI commands."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from tvastar.loop.cli import cmd_history, cmd_reset, cmd_trigger


def _make_suspended_loop():
    """Create a mock loop in SUSPENDED state."""
    from tvastar.loop import LoopConfig, LoopState

    mock = MagicMock()
    mock.name = "test"
    mock.state = LoopState.SUSPENDED
    mock.config = LoopConfig(name="test", goal="do work")
    mock.config.circuit_breaker_limit = 5
    return mock


class TestCmdTrigger:
    """Tests for the trigger subcommand."""

    def test_trigger_not_found(self, capsys):
        """Triggering a non-existent loop file returns 1."""
        result = cmd_trigger("nonexistent_file.py:loop")
        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    @patch("tvastar.loop.cli._load_loop")
    def test_trigger_suspended_loop(self, mock_load, capsys):
        """Triggering a SUSPENDED loop prints error and returns 1."""
        mock_load.return_value = _make_suspended_loop()

        result = cmd_trigger("any_file.py:loop")
        assert result == 1
        captured = capsys.readouterr()
        assert "SUSPENDED" in captured.err


class TestCmdHistory:
    """Tests for the history subcommand."""

    def test_history_not_found(self, capsys):
        """History for a non-existent loop file returns 1."""
        result = cmd_history("nonexistent_file.py:loop")
        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    @patch("tvastar.loop.cli._load_loop")
    def test_history_no_runs(self, mock_load, capsys):
        """History with no runs shows a friendly message."""
        mock_loop = MagicMock()
        mock_loop.name = "test-agent"
        mock_loop.history.return_value = []
        mock_load.return_value = mock_loop

        result = cmd_history("any:loop", limit=10)
        assert result == 0
        captured = capsys.readouterr()
        assert "No runs recorded" in captured.out


class TestCmdReset:
    """Tests for the reset subcommand."""

    def test_reset_not_found(self, capsys):
        """Resetting a non-existent loop file returns 1."""
        result = cmd_reset("nonexistent_file.py:loop")
        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    @patch("tvastar.loop.cli._load_loop")
    def test_reset_non_suspended_loop(self, mock_load, capsys):
        """Resetting a non-SUSPENDED loop returns 1."""
        from tvastar.loop import LoopState

        mock_loop = MagicMock()
        mock_loop.name = "test"
        mock_loop.state = LoopState.IDLE
        mock_load.return_value = mock_loop

        result = cmd_reset("any:loop")
        assert result == 1
        captured = capsys.readouterr()
        assert "not SUSPENDED" in captured.err

    @patch("tvastar.loop.cli._load_loop")
    def test_reset_suspended_loop_succeeds(self, mock_load, capsys):
        """Resetting a SUSPENDED loop calls reset() and returns 0."""
        mock_loop = _make_suspended_loop()
        mock_load.return_value = mock_loop

        result = cmd_reset("any:loop")
        assert result == 0
        mock_loop.reset.assert_called_once()
        captured = capsys.readouterr()
        assert "reset to IDLE" in captured.out
