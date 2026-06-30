"""Tvastar benchmark runner — standardised evaluation against known task sets.

``BenchSuite`` runs a list of ``BenchTask`` objects through a Tvastar agent and
reports a ``BenchReport``. The SWE-bench-lite adapter (``swe_bench_tasks``)
loads tasks from the canonical HuggingFace dataset or a local JSONL file and
produces a *resolve rate* — the fraction of tasks where the agent's patch makes
the hidden test suite pass.

Why this matters
----------------
``EvalSuite`` measures quality against *your* checks. ``BenchSuite`` measures
quality against *standardised, externally-defined* tasks — the difference
between testing whether your code works and testing whether your agent works on
real-world software engineering problems.

Quick start::

    import asyncio
    from tvastar import create_agent
    from tvastar.bench import BenchSuite, swe_bench_tasks
    from tvastar.model import AnthropicModel

    agent = create_agent("coder", model=AnthropicModel(), tools=default_toolset())
    suite = BenchSuite(agent, concurrency=4)
    suite.add_many(swe_bench_tasks(split="lite", max_tasks=10))
    report = asyncio.run(suite.run())
    report.print()
    print(f"Resolve rate: {report.score:.1%}")
"""

from .core import BenchReport, BenchResult, BenchSuite, BenchTask
from .swebench import swe_bench_tasks
from . import silent_failure

__all__ = [
    "BenchTask",
    "BenchResult",
    "BenchReport",
    "BenchSuite",
    "swe_bench_tasks",
    "silent_failure",
]
