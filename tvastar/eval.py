"""
tvastar.eval — lightweight eval framework for agent quality measurement.

Usage:
    from tvastar import EvalSuite, Case
    from tvastar.eval import assert_contains, assert_ok, assert_steps_under

    suite = EvalSuite(agent)
    suite.add(Case(
        name="writes python",
        prompt="Write a function that reverses a string",
        checks=[assert_contains("def"), assert_contains("return"), assert_ok()],
    ))
    report = asyncio.run(suite.run())
    print(f"Score: {report.score:.0%}  ({report.passed}/{report.total})")
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "Case",
    "CaseResult",
    "EvalReport",
    "EvalSuite",
    # built-in checks
    "assert_contains",
    "assert_not_contains",
    "assert_ok",
    "assert_steps_under",
    "assert_json",
    "assert_pydantic",
    "assert_cost_under",
    "assert_custom",
]

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A check is a callable that receives a RunResult and returns:
#   True        → passed
#   False       → failed (generic message)
#   str         → failed with that message
Check = Callable[[Any], "bool | str"]


@dataclass
class Case:
    """One eval test case."""

    prompt: str
    checks: list[Check] = field(default_factory=list)
    name: str | None = None
    agent: str | None = None  # named AgentProfile to use
    cancel_after: float | None = None  # timeout in seconds
    metadata: dict = field(default_factory=dict)


@dataclass
class CaseResult:
    """Result of running a single Case."""

    case: Case
    result: Any  # RunResult
    passed: bool
    failures: list[str]
    duration: float  # seconds

    @property
    def name(self) -> str:
        return self.case.name or self.case.prompt[:60]


@dataclass
class EvalReport:
    """Aggregate result from running an EvalSuite."""

    case_results: list[CaseResult]
    duration: float

    @property
    def total(self) -> int:
        return len(self.case_results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.case_results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def score(self) -> float:
        if not self.total:
            return 0.0
        return self.passed / self.total

    def print(self) -> None:
        """Pretty-print the report to stdout."""
        print(f"\n{'=' * 60}")
        print(f"Eval Report  —  {self.passed}/{self.total} passed  ({self.score:.0%})")
        print(f"Duration: {self.duration:.1f}s")
        print(f"{'=' * 60}")
        for cr in self.case_results:
            status = "✓" if cr.passed else "✗"
            print(f"  {status}  {cr.name}  ({cr.duration:.1f}s)")
            for failure in cr.failures:
                print(f"       → {failure}")
        print(f"{'=' * 60}\n")

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "passed": self.passed,
            "failed": self.failed,
            "total": self.total,
            "duration": self.duration,
            "cases": [
                {
                    "name": cr.name,
                    "passed": cr.passed,
                    "failures": cr.failures,
                    "duration": cr.duration,
                    "steps": getattr(cr.result, "steps", None),
                }
                for cr in self.case_results
            ],
        }


# ---------------------------------------------------------------------------
# EvalSuite
# ---------------------------------------------------------------------------


class EvalSuite:
    """
    Run a set of Cases against an agent and collect results.

    Args:
        agent_or_harness: An AgentSpec or Harness instance.
        concurrency: Max number of cases to run in parallel (default 4).

    Example::

        suite = EvalSuite(agent, concurrency=8)
        suite.add(Case("Say hello", checks=[assert_contains("hello")]))
        report = asyncio.run(suite.run())
        report.print()
    """

    def __init__(self, agent_or_harness: Any, *, concurrency: int = 4) -> None:
        self._agent_or_harness = agent_or_harness
        self._concurrency = concurrency
        self._cases: list[Case] = []

    def add(self, case: Case) -> "EvalSuite":
        """Add a test case. Returns self for chaining."""
        self._cases.append(case)
        return self

    def add_many(self, cases: list[Case]) -> "EvalSuite":
        for c in cases:
            self._cases.append(c)
        return self

    async def run(self) -> EvalReport:
        """Run all cases concurrently and return an EvalReport."""
        from tvastar.harness import Harness
        from tvastar.agent import AgentSpec

        if isinstance(self._agent_or_harness, AgentSpec):
            harness = Harness(self._agent_or_harness)
        else:
            harness = self._agent_or_harness

        sem = asyncio.Semaphore(self._concurrency)
        start = time.monotonic()

        async def run_case(case: Case) -> CaseResult:
            async with sem:
                t0 = time.monotonic()
                try:
                    kwargs: dict = {}
                    if case.cancel_after:
                        kwargs["cancel_after"] = case.cancel_after
                    run_result = await harness.run(case.prompt, **kwargs)
                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    return CaseResult(
                        case=case,
                        result=None,
                        passed=False,
                        failures=[f"Run raised exception: {exc}"],
                        duration=elapsed,
                    )

                elapsed = time.monotonic() - t0
                failures: list[str] = []

                for check in case.checks:
                    try:
                        outcome = check(run_result)
                        if outcome is True:
                            pass
                        elif outcome is False:
                            failures.append(f"Check {check.__name__} failed")
                        elif isinstance(outcome, str):
                            failures.append(outcome)
                    except Exception as exc:
                        failures.append(f"Check raised exception: {exc}")

                return CaseResult(
                    case=case,
                    result=run_result,
                    passed=len(failures) == 0,
                    failures=failures,
                    duration=elapsed,
                )

        case_results = await asyncio.gather(*[run_case(c) for c in self._cases])
        total = time.monotonic() - start
        return EvalReport(case_results=list(case_results), duration=total)


# ---------------------------------------------------------------------------
# Built-in checks
# ---------------------------------------------------------------------------


def assert_contains(text: str) -> Check:
    """Pass if the result text contains `text` (case-sensitive)."""

    def check(result: Any) -> bool | str:
        output = result.text if result is not None else ""
        if text in output:
            return True
        preview = output[:120].replace("\n", " ")
        return f"Expected output to contain {text!r}. Got: {preview!r}"

    check.__name__ = f"assert_contains({text!r})"
    return check


def assert_not_contains(text: str) -> Check:
    """Pass if the result text does NOT contain `text`."""

    def check(result: Any) -> bool | str:
        output = result.text if result is not None else ""
        if text not in output:
            return True
        return f"Expected output NOT to contain {text!r}"

    check.__name__ = f"assert_not_contains({text!r})"
    return check


def assert_ok() -> Check:
    """Pass if the run completed cleanly (stop_reason == end_turn, no warnings)."""

    def check(result: Any) -> bool | str:
        if result is None:
            return "Run failed with an exception"
        if getattr(result, "ok", False):
            return True
        stopped = getattr(result, "stopped", "unknown")
        warnings = getattr(result, "warnings", [])
        if warnings:
            msgs = "; ".join(w.message for w in warnings)
            return f"Run had warnings: {msgs}"
        return f"Run stopped with reason: {stopped!r}"

    check.__name__ = "assert_ok()"
    return check


def assert_steps_under(n: int) -> Check:
    """Pass if the run completed in fewer than `n` steps."""

    def check(result: Any) -> bool | str:
        if result is None:
            return "Run failed"
        steps = getattr(result, "steps", 0)
        if steps < n:
            return True
        return f"Expected fewer than {n} steps, got {steps}"

    check.__name__ = f"assert_steps_under({n})"
    return check


def assert_json() -> Check:
    """Pass if the result text is valid JSON."""

    def check(result: Any) -> bool | str:
        output = result.text if result is not None else ""
        # Try to find the JSON block in the output
        text = output.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError as e:
            return f"Output is not valid JSON: {e}"

    check.__name__ = "assert_json()"
    return check


def assert_pydantic(model_class: Any) -> Check:
    """Pass if the result text parses into the given Pydantic model."""

    def check(result: Any) -> bool | str:
        output = result.text if result is not None else ""
        text = output.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        try:
            data = json.loads(text)
            model_class.model_validate(data)
            return True
        except Exception as e:
            return f"Output did not parse as {model_class.__name__}: {e}"

    check.__name__ = f"assert_pydantic({model_class.__name__})"
    return check


def assert_cost_under(max_usd: float) -> Check:
    """Pass if the run cost less than `max_usd` dollars."""

    def check(result: Any) -> bool | str:
        if result is None:
            return "Run failed"
        cost = getattr(result, "cost", None)
        if cost is None:
            return True  # no cost tracking — skip
        usd = getattr(cost, "usd", 0.0)
        if usd < max_usd:
            return True
        return f"Run cost ${usd:.4f}, expected under ${max_usd:.4f}"

    check.__name__ = f"assert_cost_under({max_usd})"
    return check


def assert_custom(fn: Callable[[Any], bool], message: str = "Custom check failed") -> Check:
    """Pass if `fn(result)` returns True."""

    def check(result: Any) -> bool | str:
        try:
            if fn(result):
                return True
            return message
        except Exception as exc:
            return f"{message}: {exc}"

    check.__name__ = f"assert_custom({fn.__name__})"
    return check
