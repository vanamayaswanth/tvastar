"""Tests for the tvastar-ci autonomous CI module."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

from tvastar.ci import CIConfig, CIRunResult, CIRunner
from tvastar.ci.github import GitHubClient, GitHubEvent, parse_github_webhook
from tvastar.ci.reporter import format_ci_report, notify_result


# ---------------------------------------------------------------------------
# CIConfig tests
# ---------------------------------------------------------------------------


def test_config_from_file_missing_returns_defaults(tmp_path, monkeypatch):
    """from_file with a non-existent path returns default CIConfig."""
    monkeypatch.chdir(tmp_path)
    config = CIConfig.from_file(str(tmp_path / "nope.json"))
    assert config.test_command == "pytest -q"
    assert config.branch == "main"
    assert config.max_fix_attempts == 3
    assert config.timeout == 300.0


def test_config_save_and_from_file_roundtrip(tmp_path):
    """Config save → from_file round-trip preserves values."""
    path = str(tmp_path / "ci.json")
    original = CIConfig(
        repo_path="/my/repo",
        test_command="make test",
        branch="develop",
        max_fix_attempts=5,
        timeout=120.0,
        auto_pr=False,
        notifications={"slack": "https://hooks.slack.com/xxx"},
        schedule="*/10 * * * *",
        memory_path=".ci/mem.db",
    )
    original.save(path)

    loaded = CIConfig.from_file(path)
    assert loaded.repo_path == "/my/repo"
    assert loaded.test_command == "make test"
    assert loaded.branch == "develop"
    assert loaded.max_fix_attempts == 5
    assert loaded.timeout == 120.0
    assert loaded.auto_pr is False
    assert loaded.notifications == {"slack": "https://hooks.slack.com/xxx"}
    assert loaded.schedule == "*/10 * * * *"
    assert loaded.memory_path == ".ci/mem.db"


def test_config_from_file_invalid_json(tmp_path):
    """from_file with invalid JSON returns defaults."""
    path = tmp_path / "bad.json"
    path.write_text("not json at all {{{", encoding="utf-8")
    config = CIConfig.from_file(str(path))
    assert config.test_command == "pytest -q"


# ---------------------------------------------------------------------------
# CIRunner tests
# ---------------------------------------------------------------------------


async def test_runner_run_no_model_returns_error():
    """CIRunner.run with no model returns an error result."""
    config = CIConfig()
    runner = CIRunner(config)
    result = await runner.run()
    assert result.status == "error"
    assert "No model configured" in result.error


async def test_runner_run_green_with_mock(tmp_path):
    """CIRunner.run returns 'green' when tests already pass."""
    # Create a simple passing test project
    (tmp_path / "test_ok.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")

    config = CIConfig(repo_path=str(tmp_path), test_command="pytest -q")

    from tvastar.model.mock import MockModel

    model = MockModel(script=["Done."])
    runner = CIRunner(config)
    result = await runner.run(model=model)
    assert result.status == "green"
    assert result.fix_attempted is False
    assert result.failures_found == 0


async def test_runner_run_unfixed_with_mock(tmp_path):
    """CIRunner.run returns 'unfixed' when agent can't fix the failure."""
    (tmp_path / "test_fail.py").write_text(
        "def test_fail():\n    assert False\n", encoding="utf-8"
    )

    config = CIConfig(repo_path=str(tmp_path), test_command="pytest -q", timeout=30.0)

    from tvastar.model.mock import MockModel

    model = MockModel(script=["I tried but couldn't fix it."])
    runner = CIRunner(config)
    result = await runner.run(model=model)
    assert result.status == "unfixed"
    assert result.fix_attempted is True
    assert result.fix_succeeded is False


def test_runner_as_loop_returns_loop_instance(tmp_path):
    """CIRunner.as_loop returns a Loop with correct config."""
    from tvastar.loop import Loop
    from tvastar.model.mock import MockModel

    config = CIConfig(repo_path=str(tmp_path), test_command="pytest -q")
    runner = CIRunner(config)
    model = MockModel(script=["Done."])

    loop = runner.as_loop(model)
    assert isinstance(loop, Loop)
    assert loop.name == "tvastar-ci"


# ---------------------------------------------------------------------------
# GitHub webhook parsing tests
# ---------------------------------------------------------------------------


def test_parse_github_webhook_push():
    """parse_github_webhook correctly handles push events."""
    payload = {
        "pusher": {"name": "user1"},
        "ref": "refs/heads/main",
        "after": "abc123",
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "user1"},
    }
    event = parse_github_webhook(payload)
    assert event.action == "push"
    assert event.branch == "main"
    assert event.commit_sha == "abc123"
    assert event.repo == "owner/repo"
    assert event.sender == "user1"
    assert event.raw is payload


def test_parse_github_webhook_pull_request():
    """parse_github_webhook correctly handles pull_request events."""
    payload = {
        "action": "opened",
        "pull_request": {
            "head": {"ref": "feature-branch", "sha": "def456"},
        },
        "repository": {"full_name": "org/project"},
        "sender": {"login": "contributor"},
    }
    event = parse_github_webhook(payload)
    assert event.action == "pull_request.opened"
    assert event.branch == "feature-branch"
    assert event.commit_sha == "def456"
    assert event.repo == "org/project"
    assert event.sender == "contributor"


def test_parse_github_webhook_check_run():
    """parse_github_webhook correctly handles check_run events."""
    payload = {
        "action": "completed",
        "check_run": {"head_sha": "sha789"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "github-actions"},
    }
    event = parse_github_webhook(payload)
    assert event.action == "check_run.completed"
    assert event.commit_sha == "sha789"
    assert event.branch == ""


def test_parse_github_webhook_unknown():
    """parse_github_webhook handles unknown event types gracefully."""
    payload = {
        "action": "custom_action",
        "repository": {"full_name": "x/y"},
        "sender": {"login": "bot"},
    }
    event = parse_github_webhook(payload)
    assert event.action == "custom_action"
    assert event.branch == ""
    assert event.commit_sha == ""


# ---------------------------------------------------------------------------
# Reporter tests
# ---------------------------------------------------------------------------


def test_format_ci_report_green():
    """format_ci_report for green status produces expected output."""
    result = CIRunResult(status="green", test_command="pytest -q", duration_seconds=1.5)
    report = format_ci_report(result)
    assert "GREEN" in report
    assert "pytest -q" in report
    assert "1.5s" in report


def test_format_ci_report_fixed():
    """format_ci_report for fixed status shows changed files."""
    result = CIRunResult(
        status="fixed",
        test_command="pytest -q",
        duration_seconds=45.2,
        fix_attempted=True,
        fix_succeeded=True,
        changed_files=["src/app.py", "src/utils.py"],
    )
    report = format_ci_report(result)
    assert "FIXED" in report
    assert "succeeded" in report
    assert "src/app.py" in report


def test_format_ci_report_unfixed():
    """format_ci_report for unfixed status shows failure info."""
    result = CIRunResult(
        status="unfixed",
        test_command="make test",
        duration_seconds=120.0,
        fix_attempted=True,
        fix_succeeded=False,
    )
    report = format_ci_report(result)
    assert "UNFIXED" in report
    assert "failed" in report


def test_format_ci_report_error():
    """format_ci_report for error status shows error message."""
    result = CIRunResult(
        status="error",
        test_command="pytest",
        duration_seconds=0.1,
        error="Connection timeout",
    )
    report = format_ci_report(result)
    assert "ERROR" in report
    assert "Connection timeout" in report


def test_format_ci_report_with_repo():
    """format_ci_report includes repo name when provided."""
    result = CIRunResult(status="green", test_command="pytest", duration_seconds=1.0)
    report = format_ci_report(result, repo="owner/repo")
    assert "owner/repo" in report


def test_format_ci_report_with_pr_url():
    """format_ci_report includes PR URL when present."""
    result = CIRunResult(
        status="fixed",
        test_command="pytest",
        duration_seconds=30.0,
        fix_attempted=True,
        fix_succeeded=True,
        pr_url="https://github.com/org/repo/pull/42",
    )
    report = format_ci_report(result)
    assert "https://github.com/org/repo/pull/42" in report


def test_notify_result_skips_green():
    """notify_result does nothing for green status (no noise)."""
    result = CIRunResult(status="green", test_command="pytest", duration_seconds=1.0)
    # Should not raise even with invalid webhook URLs
    notify_result(result, config={"slack": "http://invalid"})


# ---------------------------------------------------------------------------
# GitHubClient tests
# ---------------------------------------------------------------------------


def test_github_client_post_constructs_correct_request():
    """GitHubClient._post builds the right URL, headers, and payload."""
    client = GitHubClient("test-token", api_url="https://api.example.com")

    captured = {}

    def mock_urlopen(req, **kwargs):
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["headers"] = dict(req.headers)
        captured["data"] = json.loads(req.data.decode("utf-8"))

        # Return a mock response
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        resp.read.return_value = json.dumps({"id": 1}).encode("utf-8")
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = client._post("/repos/owner/repo/pulls", {"title": "Fix tests"})

    assert captured["url"] == "https://api.example.com/repos/owner/repo/pulls"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "token test-token"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["data"] == {"title": "Fix tests"}
    assert result == {"id": 1}
