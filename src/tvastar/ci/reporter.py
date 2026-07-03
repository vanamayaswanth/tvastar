"""CI result reporting — notify humans about fix outcomes.

Uses the fleet alerting handlers (SlackAlertHandler, WebhookAlertHandler)
for actual delivery. This module formats CI-specific messages.
"""
from __future__ import annotations

from .runner import CIRunResult


def format_ci_report(result: CIRunResult, *, repo: str = "") -> str:
    """Format a CIRunResult as a human-readable report."""
    emoji = {"green": "\u2705", "fixed": "\U0001f527", "unfixed": "\u274c", "error": "\U0001f4a5"}.get(
        result.status, "\u2753"
    )

    lines = [f"{emoji} **CI Status: {result.status.upper()}**"]
    if repo:
        lines.append(f"Repository: {repo}")
    lines.append(f"Test command: `{result.test_command}`")
    lines.append(f"Duration: {result.duration_seconds:.1f}s")

    if result.fix_attempted:
        fix_status = "\u2705 succeeded" if result.fix_succeeded else "\u274c failed"
        lines.append(f"Fix attempted: {fix_status}")
    if result.changed_files:
        lines.append(f"Changed files: {', '.join(result.changed_files[:5])}")
    if result.error:
        lines.append(f"Error: {result.error[:200]}")
    if result.pr_url:
        lines.append(f"Fix PR: {result.pr_url}")

    return "\n".join(lines)


def notify_result(result: CIRunResult, *, config: dict[str, str], repo: str = "") -> None:
    """Send notifications about a CI result to configured channels.

    Parameters
    ----------
    result: The CI run result to report.
    config: Notification config dict (e.g., {"slack": webhook_url, "webhook": url}).
    repo: Repository name for context.
    """
    # Only notify on interesting events (not green — that's normal)
    if result.status == "green":
        return

    message = format_ci_report(result, repo=repo)

    if "slack" in config:
        _notify_slack(config["slack"], message)
    if "webhook" in config:
        _notify_webhook(config["webhook"], result, repo)


def _notify_slack(webhook_url: str, message: str) -> None:
    """Send to Slack via incoming webhook."""
    import json
    import urllib.request

    try:
        data = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # notification failure must never break CI


def _notify_webhook(url: str, result: CIRunResult, repo: str) -> None:
    """Send structured JSON to a generic webhook endpoint."""
    import json
    import urllib.request
    from dataclasses import asdict

    try:
        payload = {
            "event": "ci_result",
            "repo": repo,
            "result": asdict(result),
        }
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
