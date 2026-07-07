"""GitHub integration — parse webhooks, create PRs, post status comments.

Uses stdlib urllib only (zero deps). For full GitHub API features,
users can pass their own token.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class GitHubEvent:
    """Parsed GitHub webhook event."""

    action: str  # push, pull_request, check_run, etc.
    repo: str  # owner/repo
    branch: str
    commit_sha: str
    sender: str
    raw: dict


def parse_github_webhook(payload: dict) -> GitHubEvent:
    """Parse a GitHub webhook payload into a GitHubEvent."""
    # Determine action type
    if "pusher" in payload:
        action = "push"
        branch = payload.get("ref", "").replace("refs/heads/", "")
        commit_sha = payload.get("after", "")
    elif "pull_request" in payload:
        action = f"pull_request.{payload.get('action', 'opened')}"
        branch = payload["pull_request"].get("head", {}).get("ref", "")
        commit_sha = payload["pull_request"].get("head", {}).get("sha", "")
    elif "check_run" in payload:
        action = f"check_run.{payload.get('action', 'completed')}"
        branch = ""
        commit_sha = payload.get("check_run", {}).get("head_sha", "")
    else:
        action = payload.get("action", "unknown")
        branch = ""
        commit_sha = ""

    repo = payload.get("repository", {}).get("full_name", "")
    sender = payload.get("sender", {}).get("login", "")

    return GitHubEvent(
        action=action,
        repo=repo,
        branch=branch,
        commit_sha=commit_sha,
        sender=sender,
        raw=payload,
    )


class GitHubClient:
    """Minimal GitHub API client using stdlib urllib.

    Supports: creating PRs, posting comments, setting commit status.
    """

    def __init__(self, token: str, *, api_url: str = "https://api.github.com") -> None:
        self._token = token
        self._api_url = api_url

    def create_pr(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> dict:
        """Create a pull request. Returns the PR data dict."""
        return self._post(
            f"/repos/{repo}/pulls",
            {
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )

    def comment_on_pr(self, repo: str, pr_number: int, body: str) -> dict:
        """Post a comment on a PR."""
        return self._post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            {
                "body": body,
            },
        )

    def set_commit_status(
        self,
        repo: str,
        sha: str,
        *,
        state: str,  # pending | success | failure | error
        description: str = "",
        context: str = "tvastar-ci",
    ) -> dict:
        """Set a commit status check."""
        return self._post(
            f"/repos/{repo}/statuses/{sha}",
            {
                "state": state,
                "description": description[:140],
                "context": context,
            },
        )

    def _post(self, path: str, data: dict) -> dict:
        """Make an authenticated POST to the GitHub API."""
        url = f"{self._api_url}{path}"
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"token {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github.v3+json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"GitHub API error {e.code}: {body}") from e
