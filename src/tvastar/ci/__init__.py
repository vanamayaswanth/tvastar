"""tvastar-ci — autonomous CI agent that monitors, fixes, and reports.

Usage:
    from tvastar.ci import CIRunner, CIConfig

    config = CIConfig(test_command="pytest -q", repo_path=".")
    runner = CIRunner(config)
    result = await runner.run(model=my_model)

    # Or as a Loop:
    loop = runner.as_loop(model)
    await loop.start()
"""
from __future__ import annotations

from .config import CIConfig
from .github import GitHubClient, GitHubEvent, parse_github_webhook
from .reporter import format_ci_report, notify_result
from .runner import CIRunner, CIRunResult

__all__ = [
    "CIConfig",
    "CIRunner",
    "CIRunResult",
    "GitHubClient",
    "GitHubEvent",
    "parse_github_webhook",
    "format_ci_report",
    "notify_result",
]
