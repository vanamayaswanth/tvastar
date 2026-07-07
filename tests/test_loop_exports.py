"""Verify all production-hardening modules are importable from their documented paths.

Validates Requirements 1.2, 2.2, 3.3, 6.3 — that optional extras are declared
and all public classes/functions are accessible at documented import paths.
"""


def test_loop_registry_importable():
    from tvastar.loop.registry import LoopRegistry

    assert LoopRegistry is not None


def test_structured_logger_importable():
    from tvastar.loop.logger import StructuredLogger

    assert StructuredLogger is not None


def test_metrics_collector_importable():
    from tvastar.loop.metrics import MetricsCollector

    assert MetricsCollector is not None


def test_install_signal_handlers_importable():
    from tvastar.loop.shutdown import install_signal_handlers

    assert install_signal_handlers is not None


def test_channels_all_importable():
    from tvastar.loop.channels import (
        EmailHandoff,
        GitHubIssueHandoff,
        PagerDutyHandoff,
        SlackHandoff,
        WebhookHandoff,
    )

    assert SlackHandoff is not None
    assert GitHubIssueHandoff is not None
    assert PagerDutyHandoff is not None
    assert WebhookHandoff is not None
    assert EmailHandoff is not None


def test_channels_init_all_list():
    import tvastar.loop.channels as ch

    expected = {
        "SlackHandoff",
        "GitHubIssueHandoff",
        "PagerDutyHandoff",
        "WebhookHandoff",
        "EmailHandoff",
    }
    assert expected.issubset(set(ch.__all__))
