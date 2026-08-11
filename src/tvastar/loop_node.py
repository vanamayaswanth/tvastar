"""tvastar.loop_node — LoopNode: embed a Loop as a TaskGraph node.

Extracted from graph.py for single-responsibility: LoopNode owns
its own iteration/quality-gate logic independent of DAG execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import AgentSpec
    from .loop import LoopConfig
    from .session import RunResult


class LoopNode:
    """Embed a Loop as a TaskGraph node.

    Wraps a LoopConfig + AgentSpec and runs iterations inline during graph
    execution, respecting max_iterations and quality_gate. On PASS, returns
    the passing RunResult. On exhaustion, returns the last result with a
    WARNING finding.

    Participates in dependency injection identically to a regular _TaskNode:
    upstream results are injected as context for the loop's prompt.
    """

    def __init__(self, config: "LoopConfig", spec: "AgentSpec") -> None:
        from .loop import LoopConfig

        if not isinstance(config, LoopConfig):
            raise TypeError("LoopNode requires a LoopConfig instance")
        self.config = config
        self.spec = spec

    async def execute(self, context: dict[str, str] | None = None) -> "RunResult":
        """Run the loop iterations and return the final RunResult.

        Parameters
        ----------
        context:
            Dict of upstream dependency results (name → text) injected into
            the loop's prompt as additional context.
        """
        from .detect.base import Finding, Severity
        from .harness import Harness
        from .memory.store import InMemoryStore

        store = InMemoryStore()
        harness = Harness(self.spec, store=store)

        last_result: "RunResult | None" = None
        quality_gate = self.config.quality_gate

        for iteration in range(1, self.config.max_iterations + 1):
            prompt = self._build_prompt(context, iteration)

            sess = harness.session()
            try:
                async with sess:
                    result = await sess.prompt(prompt)
            finally:
                harness._release(sess.id)

            last_result = result

            # Check quality gate — same logic as Loop._run_iteration_inner
            has_warnings = bool(result.warnings)
            has_findings = any(f.severity in ("ERROR", "WARNING") for f in result.findings)
            failed = (not result.ok) or has_warnings or has_findings

            if failed and result.stopped == "end_turn" and result.quality.score >= quality_gate:
                failed = False

            if not failed:
                return result

        # Exhaustion: return last result with WARNING finding
        assert last_result is not None  # max_iterations >= 1 guaranteed by LoopConfig
        warning = Finding(
            detector="LoopNode",
            severity=Severity.WARNING,
            message=(
                f"Loop '{self.config.name}' exhausted {self.config.max_iterations} "
                f"iterations without passing quality gate ({quality_gate})"
            ),
        )
        last_result.findings = list(last_result.findings) + [warning]
        return last_result

    def _build_prompt(self, context: dict[str, str] | None, iteration: int) -> str:
        """Build prompt for a loop iteration, injecting upstream context."""
        parts = [f"Goal: {self.config.goal}"]
        if context:
            ctx_str = "; ".join(f"{k}={v}" for k, v in context.items())
            parts.append(f"Context: {ctx_str}")
        if iteration > 1:
            parts.append(f"This is attempt {iteration} of {self.config.max_iterations}.")
        return "\n".join(parts)
