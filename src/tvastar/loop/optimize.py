"""DSPy-powered instruction optimizer for Loop self-improvement.

Replaces the free-form meta_model one-shot rewrite with a structured
DSPy ChainOfThought pipeline. Historical PASS runs are used as few-shot
examples; recent FAIL runs supply the failure evidence.

Usage::

    from tvastar.loop import LoopConfig
    from tvastar.loop.optimize import DSPyOptimizer

    config = LoopConfig(
        name="billing",
        goal="Process invoices without errors.",
        optimizer=DSPyOptimizer("gpt-4o"),   # replaces meta_model
    )

Requires: pip install tvastar[dspy]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import LoopRun

__all__ = ["DSPyOptimizer"]


class DSPyOptimizer:
    """Systematic instruction optimizer backed by DSPy ChainOfThought.

    Collects failure evidence from recent FAIL runs, uses PASS runs as
    few-shot demonstrations, and asks a DSPy predictor to rewrite the
    agent instructions to prevent the observed failures.

    Args:
        model:      DSPy model string (e.g. ``"gpt-4o"``, ``"anthropic/claude-sonnet-4-6"``).
        max_demos:  Maximum PASS examples to use as few-shot context.
        max_fails:  Maximum FAIL runs to include in the failure evidence.
        **lm_kwargs: Extra kwargs forwarded to ``dspy.LM``.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        max_demos: int = 3,
        max_fails: int = 5,
        **lm_kwargs: Any,
    ) -> None:
        self._model = model
        self._max_demos = max_demos
        self._max_fails = max_fails
        self._lm_kwargs = lm_kwargs

    def __call__(self, instructions: str, runs: list[LoopRun]) -> str:
        """Return improved instructions given current instructions and run history.

        This method is the optimizer callable interface expected by
        ``LoopConfig.optimizer``.

        Args:
            instructions: Current agent instructions.
            runs:         Recent LoopRun history (any state mix).

        Returns:
            Improved instructions as a plain string.
        """
        try:
            import dspy  # type: ignore
        except ImportError as e:
            raise ImportError(
                "DSPy is not installed. Run: pip install tvastar[dspy]"
            ) from e

        from . import LoopState

        lm = dspy.LM(self._model, **self._lm_kwargs)
        dspy.configure(lm=lm)

        fails = [r for r in runs if r.state == LoopState.FAIL][-self._max_fails:]
        passes = [r for r in runs if r.state == LoopState.PASS][-self._max_demos:]

        failure_lines = []
        for r in fails:
            parts = [f"[{r.run_id}]"]
            if r.error:
                parts.append(f"error={r.error!r}")
            for f in (r.findings or [])[:3]:
                msg = getattr(f, "message", str(f))
                parts.append(msg)
            failure_lines.append(" ".join(parts))
        failure_evidence = "\n".join(failure_lines) or "No specific failure details."

        class _Signature(dspy.Signature):  # type: ignore
            """Rewrite agent instructions to prevent failures while preserving what works."""

            current_instructions: str = dspy.InputField()
            failure_evidence: str = dspy.InputField(
                desc="Recent failures to eliminate"
            )
            improved_instructions: str = dspy.OutputField(
                desc="Complete improved instructions — only the instructions, no commentary or quotes"
            )

        predictor = dspy.ChainOfThought(_Signature)

        if passes:
            predictor.demos = [
                dspy.Example(
                    current_instructions=instructions,
                    failure_evidence="[no failures — these instructions worked]",
                    improved_instructions=instructions,
                ).with_inputs("current_instructions", "failure_evidence")
                for _ in passes[:self._max_demos]
            ]

        result = predictor(
            current_instructions=instructions,
            failure_evidence=failure_evidence,
        )
        improved = (result.improved_instructions or "").strip()
        return improved if improved else instructions  # never return empty

    def __repr__(self) -> str:
        return f"DSPyOptimizer(model={self._model!r}, max_demos={self._max_demos})"
