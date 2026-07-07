"""Handoff channels — concrete HandoffPolicy implementations for external services."""

from .email import EmailHandoff
from .github import GitHubIssueHandoff
from .pagerduty import PagerDutyHandoff
from .slack import SlackHandoff
from .webhook import WebhookHandoff

__all__ = [
    "SlackHandoff",
    "GitHubIssueHandoff",
    "PagerDutyHandoff",
    "WebhookHandoff",
    "EmailHandoff",
]
