"""GitHub integration tools for listing PRs, issues, CI status, and posting comments.

Use :func:`github_toolset` to get all GitHub tools as a list.
Requires ``httpx`` — install via ``pip install tvastar[github]``.
"""

from __future__ import annotations

import os
from typing import Optional

from .base import Tool, ToolContext, tool
from ..errors import ToolError


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_httpx():
    """Lazy-import httpx, raising a clear error if missing."""
    try:
        import httpx
    except ImportError:
        raise ImportError("Install tvastar[github] for GitHub tools")
    return httpx


def _get_token(ctx: ToolContext) -> str:
    """Resolve GitHub token: ToolContext.memory['github_token'] → env → error."""
    mem = getattr(ctx, "memory", None)
    if isinstance(mem, dict):
        token = mem.get("github_token")
        if token:
            return token
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    raise ToolError(
        "No GitHub token configured: set GITHUB_TOKEN or provide via ToolContext.memory"
    )


def _clamp_limit(limit: Optional[int]) -> int:
    """Clamp limit to [1, 100], defaulting to 30."""
    if limit is None:
        return 30
    return max(1, min(100, limit))


async def _github_request(
    ctx: ToolContext,
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
) -> dict | list:
    """Make an authenticated GitHub API request. Raises ToolError on failure."""
    httpx = _get_httpx()
    token = _get_token(ctx)

    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, headers=headers, json=json, params=params)
    except httpx.TimeoutException:
        raise ToolError(f"GitHub API request timed out after 30s: {method} {path}")
    except Exception as e:
        raise ToolError(f"GitHub API request failed: {e}")

    if resp.status_code >= 400:
        body = resp.text[:500]
        raise ToolError(f"GitHub API error {resp.status_code}: {body}")

    return resp.json()


# ── Tools ─────────────────────────────────────────────────────────────────────


@tool
async def github_list_prs(
    ctx: ToolContext,
    repo: str,
    state: str = "open",
    limit: Optional[int] = None,
) -> dict:
    """List pull requests for a GitHub repository.

    Args:
        repo: Repository in "owner/repo" format.
        state: Filter by state: open, closed, or all (default: open).
        limit: Max results to return (default 30, max 100).
    """
    effective_limit = _clamp_limit(limit)
    params = {"state": state, "per_page": effective_limit}
    data = await _github_request(ctx, "GET", f"/repos/{repo}/pulls", params=params)
    return {"pull_requests": data[:effective_limit]}


@tool
async def github_get_pr(
    ctx: ToolContext,
    repo: str,
    pr_number: int,
) -> dict:
    """Get details of a specific pull request.

    Args:
        repo: Repository in "owner/repo" format.
        pr_number: Pull request number.
    """
    data = await _github_request(ctx, "GET", f"/repos/{repo}/pulls/{pr_number}")
    return {
        "title": data.get("title"),
        "author": data.get("user", {}).get("login"),
        "branch": data.get("head", {}).get("ref"),
        "base": data.get("base", {}).get("ref"),
        "state": data.get("state"),
        "mergeable": data.get("mergeable"),
        "changed_files": data.get("changed_files"),
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
    }


@tool
async def github_ci_status(
    ctx: ToolContext,
    repo: str,
    ref: str,
) -> dict:
    """Get CI workflow run status for a given ref (branch, tag, or SHA).

    Args:
        repo: Repository in "owner/repo" format.
        ref: Git ref — branch name, tag, or commit SHA.
    """
    data = await _github_request(
        ctx, "GET", f"/repos/{repo}/actions/runs", params={"head_sha": ref, "per_page": 10}
    )
    runs = data.get("workflow_runs", [])
    return {
        "total_count": data.get("total_count", 0),
        "runs": [
            {
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "html_url": r.get("html_url"),
            }
            for r in runs
        ],
    }


@tool
async def github_list_issues(
    ctx: ToolContext,
    repo: str,
    state: str = "open",
    limit: Optional[int] = None,
) -> dict:
    """List issues for a GitHub repository.

    Args:
        repo: Repository in "owner/repo" format.
        state: Filter by state: open, closed, or all (default: open).
        limit: Max results to return (default 30, max 100).
    """
    effective_limit = _clamp_limit(limit)
    params = {"state": state, "per_page": effective_limit}
    data = await _github_request(ctx, "GET", f"/repos/{repo}/issues", params=params)
    return {"issues": data[:effective_limit]}


@tool
async def github_create_issue(
    ctx: ToolContext,
    repo: str,
    title: str,
    body: str = "",
) -> dict:
    """Create a new issue in a GitHub repository.

    Args:
        repo: Repository in "owner/repo" format.
        title: Issue title.
        body: Issue body (markdown).
    """
    data = await _github_request(
        ctx, "POST", f"/repos/{repo}/issues", json={"title": title, "body": body}
    )
    return {"number": data.get("number"), "html_url": data.get("html_url")}


@tool
async def github_post_comment(
    ctx: ToolContext,
    repo: str,
    issue_number: int,
    body: str,
) -> dict:
    """Post a comment on an issue or pull request.

    Args:
        repo: Repository in "owner/repo" format.
        issue_number: Issue or PR number.
        body: Comment body (markdown).
    """
    data = await _github_request(
        ctx, "POST", f"/repos/{repo}/issues/{issue_number}/comments", json={"body": body}
    )
    return {"id": data.get("id"), "html_url": data.get("html_url")}


# ── Factory ───────────────────────────────────────────────────────────────────


def github_toolset() -> list[Tool]:
    """All GitHub tools as a list, ready to register."""
    return [
        github_list_prs,
        github_get_pr,
        github_ci_status,
        github_list_issues,
        github_create_issue,
        github_post_comment,
    ]
