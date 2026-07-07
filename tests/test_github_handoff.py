"""Tests for GitHubIssueHandoff channel."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from tvastar.loop import FailureKind, LoopRun, LoopState


class TestGitHubIssueHandoffConstruction:
    def test_missing_httpx_raises_import_error(self, monkeypatch):
        """Construction raises ImportError when httpx is not installed."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        # Need to reload the module to bypass any cached import
        from tvastar.loop.channels.github import GitHubIssueHandoff

        with pytest.raises(ImportError, match="Install tvastar\\[github\\] for GitHub handoff"):
            GitHubIssueHandoff(repo="owner/repo", token="tok")

    def test_token_from_env(self, monkeypatch):
        """Falls back to GITHUB_TOKEN env var when no token provided."""
        # Ensure httpx is "importable" via a fake module
        fake_httpx = ModuleType("httpx")
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")

        from tvastar.loop.channels.github import GitHubIssueHandoff

        h = GitHubIssueHandoff(repo="owner/repo")
        assert h.token == "env-token"

    def test_explicit_token_preferred(self, monkeypatch):
        """Explicit token takes precedence."""
        fake_httpx = ModuleType("httpx")
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")

        from tvastar.loop.channels.github import GitHubIssueHandoff

        h = GitHubIssueHandoff(repo="owner/repo", token="explicit")
        assert h.token == "explicit"


class TestGitHubIssueHandoffEscalate:
    @pytest.fixture
    def run(self):
        return LoopRun(
            run_id="run-123",
            loop_name="ci-sweeper",
            state=LoopState.FAIL,
            iteration=3,
            started_at=1000.0,
            ended_at=1045.5,
            failure_kind=FailureKind.MODEL_ERROR,
            error="Connection timed out",
        )

    @pytest.fixture
    def history(self):
        return [
            LoopRun(
                run_id="run-100",
                loop_name="ci-sweeper",
                state=LoopState.PASS,
                iteration=1,
                started_at=900.0,
                ended_at=910.0,
            ),
            LoopRun(
                run_id="run-101",
                loop_name="ci-sweeper",
                state=LoopState.FAIL,
                iteration=2,
                started_at=950.0,
                ended_at=960.0,
                failure_kind=FailureKind.TIMEOUT,
                error="Budget exceeded",
            ),
        ]

    @pytest.fixture(autouse=True)
    def mock_httpx(self, monkeypatch):
        """Provide a fake httpx module with AsyncClient that captures calls."""
        self._captured = {}

        fake_response = MagicMock()
        fake_response.status_code = 201

        async def fake_post(url, **kwargs):
            self._captured["url"] = str(url)
            self._captured["json"] = kwargs.get("json")
            self._captured["headers"] = kwargs.get("headers")
            return fake_response

        fake_client = MagicMock()
        fake_client.post = fake_post
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)

        fake_httpx = ModuleType("httpx")
        fake_httpx.AsyncClient = MagicMock(return_value=fake_client)
        fake_httpx.Response = MagicMock  # Not used directly

        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        self._fake_response = fake_response
        self._fake_client = fake_client

    def _get_handoff(self, repo="owner/repo", token="tok-123"):
        from tvastar.loop.channels.github import GitHubIssueHandoff
        return GitHubIssueHandoff(repo=repo, token=token)

    async def test_creates_issue_with_correct_title(self, run, history):
        """Issue title matches the required format."""
        h = self._get_handoff()
        await h.escalate(run, history)
        assert self._captured["json"]["title"] == "Loop Handoff: ci-sweeper — model_error"

    async def test_body_contains_required_fields(self, run, history):
        """Issue body includes run_id, iteration, error, duration."""
        h = self._get_handoff()
        await h.escalate(run, history)

        body = self._captured["json"]["body"]
        assert "run-123" in body
        assert "3" in body  # iteration
        assert "Connection timed out" in body
        assert "45.5s" in body  # duration

    async def test_body_includes_history_summary(self, run, history):
        """Issue body includes last 3 runs from history."""
        h = self._get_handoff()
        await h.escalate(run, history)

        body = self._captured["json"]["body"]
        assert "run-100" in body
        assert "run-101" in body
        assert "pass" in body
        assert "timeout" in body

    async def test_auth_header_sent(self, run, history):
        """Authorization header uses Bearer token."""
        h = self._get_handoff(token="my-token")
        await h.escalate(run, history)

        assert self._captured["headers"]["Authorization"] == "Bearer my-token"
        assert self._captured["headers"]["Accept"] == "application/vnd.github+json"

    async def test_raises_on_api_failure(self, run, history):
        """Raises RuntimeError with status code on HTTP >= 400."""
        self._fake_response.status_code = 422
        h = self._get_handoff()
        with pytest.raises(RuntimeError, match="422"):
            await h.escalate(run, history)

    async def test_posts_to_correct_url(self, run, history):
        """POST goes to the right repo issues endpoint."""
        h = self._get_handoff(repo="octo/cat")
        await h.escalate(run, history)
        assert self._captured["url"] == "https://api.github.com/repos/octo/cat/issues"

    async def test_unknown_failure_kind_when_none(self, history):
        """Title uses 'unknown' when failure_kind is None."""
        run = LoopRun(
            run_id="run-999",
            loop_name="test-loop",
            state=LoopState.FAIL,
            iteration=1,
            started_at=100.0,
            ended_at=110.0,
            failure_kind=None,
            error="Something broke",
        )
        h = self._get_handoff()
        await h.escalate(run, history)
        assert self._captured["json"]["title"] == "Loop Handoff: test-loop — unknown"
