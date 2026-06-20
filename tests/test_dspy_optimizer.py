"""Tests for DSPyOptimizer — all mocked, no real DSPy or model calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tvastar.loop import LoopRun, LoopState
from tvastar.loop.optimize import DSPyOptimizer


def _run(state: LoopState, error: str | None = None) -> LoopRun:
    return LoopRun(
        run_id=f"run_{state.value}",
        loop_name="test",
        state=state,
        iteration=1,
        started_at=0.0,
        ended_at=1.0,
        error=error,
    )


class TestDSPyOptimizer:
    def test_repr(self):
        opt = DSPyOptimizer("gpt-4o")
        assert "DSPyOptimizer" in repr(opt)
        assert "gpt-4o" in repr(opt)

    def test_import_error_without_dspy(self):
        import sys

        opt = DSPyOptimizer("gpt-4o")
        saved = sys.modules.pop("dspy", None)
        sys.modules["dspy"] = None  # type: ignore
        try:
            with pytest.raises((ImportError, TypeError)):
                opt("instructions", [])
        finally:
            if saved is not None:
                sys.modules["dspy"] = saved
            else:
                sys.modules.pop("dspy", None)

    def test_calls_dspy_chain_of_thought(self):
        opt = DSPyOptimizer("gpt-4o")
        runs = [_run(LoopState.FAIL, error="timeout"), _run(LoopState.PASS)]

        mock_result = MagicMock()
        mock_result.improved_instructions = "Better instructions here."

        mock_cot = MagicMock(return_value=mock_result)
        mock_dspy = MagicMock()
        mock_dspy.ChainOfThought.return_value = mock_cot
        mock_dspy.InputField.return_value = MagicMock()
        mock_dspy.OutputField.return_value = MagicMock()
        mock_dspy.Signature = object  # base class

        with patch.dict("sys.modules", {"dspy": mock_dspy}):
            result = opt("Do something.", runs)

        assert result == "Better instructions here."

    def test_returns_original_when_improved_is_empty(self):
        opt = DSPyOptimizer("gpt-4o")
        runs = [_run(LoopState.FAIL, error="crash")]

        mock_result = MagicMock()
        mock_result.improved_instructions = "   "  # blank

        mock_cot = MagicMock(return_value=mock_result)
        mock_dspy = MagicMock()
        mock_dspy.ChainOfThought.return_value = mock_cot
        mock_dspy.InputField.return_value = MagicMock()
        mock_dspy.OutputField.return_value = MagicMock()
        mock_dspy.Signature = object

        with patch.dict("sys.modules", {"dspy": mock_dspy}):
            result = opt("original instructions", runs)

        assert result == "original instructions"

    def test_loop_config_accepts_optimizer(self):
        from tvastar.loop import LoopConfig

        opt = DSPyOptimizer("gpt-4o")
        cfg = LoopConfig(name="test", goal="do work", optimizer=opt)
        assert cfg.optimizer is opt

    def test_optimizer_takes_precedence_field_exists(self):
        from tvastar.loop import LoopConfig
        import dataclasses

        fields = {f.name for f in dataclasses.fields(LoopConfig)}
        assert "optimizer" in fields
