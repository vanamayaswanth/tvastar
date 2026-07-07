"""GitHub Issue handoff channel — create a GitHub issue with full handoff context."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..handoff import HandoffPolicy

if TYPE_CHECKING:
    from .. import LoopRun


@dataclass
class GitHubIssueHandoff(HandoffPolicy):
    """Create a GitHub issue with structured handoff context.

    Requires ``httpx`` — install via ``pip install tvastar[github]``.
    """

    repo: str  # "owner/repo"
    token: str | None = None

    def __post_init__(self) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise ImportError("Install tvastar[github] for GitHub handoff")
        if not self.token:
            self.token = os.environ.get("GITHUB_TOKEN")

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        import httpx

        failure = run.failure_kind.value if run.failure_kind else "unknown"
        title = f"Loop Handoff: {run.loop_name} — {failure}"

        body_lines = [
            f"**Run ID:** {run.run_id}",
            f"**Iteration:** {run.iteration}",
            f"**Error:** {run.error}",
            f"**Duration:** {run.duration}s"
            if run.duration is not None
            else "**Duration:** unknown",
            "",
            "## Last 3 Runs",
            "",
        ]
        for prev in history[-3:]:
            fk = prev.failure_kind.value if prev.failure_kind else "None"
            dur = f"{prev.duration}s" if prev.duration is not None else "unknown"
            body_lines.append(
                f"- `{prev.run_id}` | state={prev.state.value} | failure_kind={fk} | duration={dur}"
            )

        body = "\n".join(body_lines)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{self.repo}/issues",
                json={"title": title, "body": body},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub API error: {resp.status_code}")
