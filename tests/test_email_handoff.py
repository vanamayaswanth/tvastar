"""Tests for EmailHandoff channel."""

from unittest.mock import MagicMock, patch

import pytest

from tvastar.loop import FailureKind, LoopRun, LoopState
from tvastar.loop.channels.email import EmailHandoff


def _make_run(**overrides) -> LoopRun:
    defaults = dict(
        run_id="run-abc-123",
        loop_name="test-loop",
        state=LoopState.FAIL,
        iteration=3,
        started_at=1000.0,
        ended_at=1042.5,
        failure_kind=FailureKind.TIMEOUT,
        error="Agent timed out after 30s",
    )
    defaults.update(overrides)
    return LoopRun(**defaults)


@pytest.fixture
def handoff():
    return EmailHandoff(
        recipients=["ops@example.com"],
        sender="loop@example.com",
        smtp_host="mail.example.com",
        smtp_port=587,
    )


@pytest.mark.asyncio
async def test_escalate_builds_correct_subject(handoff):
    run = _make_run()
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        await handoff.escalate(run, [])

    # Check the sendmail was called
    instance.sendmail.assert_called_once()
    raw_msg = instance.sendmail.call_args[0][2]
    # Subject contains em dash which gets RFC2047-encoded; check body for loop name
    assert "test-loop" in raw_msg
    assert "timeout" in raw_msg  # failure_kind.value in subject/body


@pytest.mark.asyncio
async def test_escalate_body_contains_required_fields(handoff):
    run = _make_run()
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        await handoff.escalate(run, [])

    raw_msg = instance.sendmail.call_args[0][2]
    assert "run-abc-123" in raw_msg
    assert "test-loop" in raw_msg
    assert "Iteration: 3" in raw_msg
    assert "42.5s" in raw_msg
    assert "Agent timed out after 30s" in raw_msg
    assert "ACTION REQUIRED" in raw_msg


@pytest.mark.asyncio
async def test_escalate_unknown_duration_when_ended_at_none(handoff):
    run = _make_run(ended_at=None)
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        await handoff.escalate(run, [])

    raw_msg = instance.sendmail.call_args[0][2]
    assert "Duration: unknown" in raw_msg


@pytest.mark.asyncio
async def test_escalate_unknown_failure_kind_when_none(handoff):
    run = _make_run(failure_kind=None)
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        await handoff.escalate(run, [])

    raw_msg = instance.sendmail.call_args[0][2]
    assert "unknown" in raw_msg


@pytest.mark.asyncio
async def test_escalate_uses_tls_when_configured():
    handoff = EmailHandoff(
        recipients=["ops@example.com"],
        sender="loop@example.com",
        smtp_host="mail.example.com",
        smtp_port=465,
        use_tls=True,
    )
    run = _make_run()
    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
        instance = MagicMock()
        mock_smtp_ssl.return_value = instance
        await handoff.escalate(run, [])

    mock_smtp_ssl.assert_called_once_with("mail.example.com", 465)
    instance.sendmail.assert_called_once()


@pytest.mark.asyncio
async def test_escalate_propagates_smtp_error(handoff):
    import smtplib

    run = _make_run()
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        instance.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"rejected")})

        with pytest.raises(smtplib.SMTPRecipientsRefused):
            await handoff.escalate(run, [])


@pytest.mark.asyncio
async def test_escalate_logs_in_when_credentials_provided():
    handoff = EmailHandoff(
        recipients=["ops@example.com"],
        sender="loop@example.com",
        smtp_host="mail.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_pass="pass",
    )
    run = _make_run()
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value = instance
        await handoff.escalate(run, [])

    instance.login.assert_called_once_with("user", "pass")
