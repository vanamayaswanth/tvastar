"""Tests for the GitHub integration tool (src/tvastar/tools/github.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.errors import ToolError
from tvastar.tools.base import ToolContext
from tvastar.tools.github import (
    _clamp_limit,
    _get_token,
    github_create_issue,
    github_list_prs,
    github_toolset,
)


# ── Factory ───────────────────────────────────────────────────────────────────


def test_github_toolset_returns_six_tools():
    tools = github_toolset()
    assert len(tools) == 6
    names = [t.name for t in tools]
    assert "github_list_prs" in names
    assert "github_get_pr" in names
    assert "github_ci_status" in names
    assert "github_list_issues" in names
    assert "github_create_issue" in names
    assert "github_post_comment" in names


def test_github_toolset_all_want_ctx():
    for t in github_toolset():
        assert t.wants_ctx is True


def test_github_toolset_ctx_not_in_schema():
    for t in github_toolset():
        assert "ctx" not in t.input_schema.get("properties", {})


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_get_token_from_memory():
    ctx = ToolContext(memory={"github_token": "mem-token"})
    assert _get_token(ctx) == "mem-token"


def test_get_token_from_env(monkeypatch):
    ctx = ToolContext()
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert _get_token(ctx) == "env-token"


def test_get_token_memory_priority_over_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    ctx = ToolContext(memory={"github_token": "mem-token"})
    assert _get_token(ctx) == "mem-token"


def test_get_token_raises_tool_error_when_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ctx = ToolContext()
    with pytest.raises(ToolError, match="No GitHub token configured"):
        _get_token(ctx)


# ── Limit clamping ────────────────────────────────────────────────────────────


def test_clamp_limit_default():
    assert _clamp_limit(None) == 30


def test_clamp_limit_within_range():
    assert _clamp_limit(50) == 50


def test_clamp_limit_below_min():
    assert _clamp_limit(0) == 1
    assert _clamp_limit(-5) == 1


def test_clamp_limit_above_max():
    assert _clamp_limit(200) == 100
    assert _clamp_limit(101) == 100


# ── Lazy import ───────────────────────────────────────────────────────────────


def test_httpx_import_error():
    """When httpx is missing, calling a tool raises ImportError with message."""
    from tvastar.tools.github import _get_httpx

    with patch.dict("sys.modules", {"httpx": None}):
        with patch("builtins.__import__", side_effect=ImportError("No module named 'httpx'")):
            with pytest.raises(ImportError, match="Install tvastar\\[github\\] for GitHub tools"):
                _get_httpx()


# ── HTTP error handling ───────────────────────────────────────────────────────


@pytest.fixture
def ctx_with_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    return ToolContext()


@pytest.mark.asyncio
async def test_http_4xx_raises_tool_error(ctx_with_token):
    """HTTP 4xx → ToolError with status code and body."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = '{"message": "Not Found"}'

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("tvastar.tools.github._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        mock_httpx.return_value.TimeoutException = TimeoutError

        with pytest.raises(ToolError, match="GitHub API error 404"):
            await github_list_prs.fn(ctx=ctx_with_token, repo="owner/repo")


@pytest.mark.asyncio
async def test_timeout_raises_tool_error(ctx_with_token):
    """30s timeout → ToolError indicating timeout."""
    import httpx as real_httpx

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=real_httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("tvastar.tools.github._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        mock_httpx.return_value.TimeoutException = real_httpx.TimeoutException

        with pytest.raises(ToolError, match="timed out"):
            await github_list_prs.fn(ctx=ctx_with_token, repo="owner/repo")


# ── Successful responses ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_prs_success(ctx_with_token):
    """github_list_prs returns structured dict with pull_requests key."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"number": 1, "title": "Fix bug"}]

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("tvastar.tools.github._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        mock_httpx.return_value.TimeoutException = TimeoutError

        result = await github_list_prs.fn(ctx=ctx_with_token, repo="owner/repo")
        assert "pull_requests" in result
        assert result["pull_requests"][0]["number"] == 1


@pytest.mark.asyncio
async def test_create_issue_success(ctx_with_token):
    """github_create_issue returns number and html_url."""
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"number": 42, "html_url": "https://github.com/o/r/issues/42"}

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("tvastar.tools.github._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        mock_httpx.return_value.TimeoutException = TimeoutError

        result = await github_create_issue.fn(
            ctx=ctx_with_token, repo="owner/repo", title="Bug", body="Details"
        )
        assert result["number"] == 42
