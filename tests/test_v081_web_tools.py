"""Tests for tvastar web tools — web_browse + web_search (0.8.1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_response(body: str, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body.encode("utf-8")
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# web_browse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_browse_returns_content():
    from tvastar.tools.builtin import web_browse

    mock_resp = _mock_response("# Hello World\nThis is a page.")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await web_browse.fn("https://example.com")

    assert "Hello World" in result


@pytest.mark.asyncio
async def test_web_browse_uses_jina_reader_url():
    from tvastar.tools.builtin import web_browse

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_response("content")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await web_browse.fn("https://example.com")

    assert captured["url"].startswith("https://r.jina.ai/")
    assert "example.com" in captured["url"]


@pytest.mark.asyncio
async def test_web_browse_truncates_at_max_chars():
    from tvastar.tools.builtin import web_browse

    long_body = "x" * 20000
    mock_resp = _mock_response(long_body)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await web_browse.fn("https://example.com", max_chars=100)

    assert len(result) == 100


@pytest.mark.asyncio
async def test_web_browse_handles_http_error():
    import urllib.error
    from tvastar.tools.builtin import web_browse

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(
            url="https://r.jina.ai/x", code=404, msg="Not Found", hdrs=None, fp=None
        ),
    ):
        result = await web_browse.fn("https://example.com")

    assert "[http 404]" in result


@pytest.mark.asyncio
async def test_web_browse_handles_generic_error():
    from tvastar.tools.builtin import web_browse

    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = await web_browse.fn("https://example.com")

    assert "[error]" in result


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_returns_content():
    from tvastar.tools.builtin import web_search

    mock_resp = _mock_response("## Result 1\nSome search result.")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await web_search.fn("python agent frameworks")

    assert "Result 1" in result


@pytest.mark.asyncio
async def test_web_search_uses_jina_search_url():
    from tvastar.tools.builtin import web_search

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_response("results")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await web_search.fn("best agent harness")

    assert captured["url"].startswith("https://s.jina.ai/")


@pytest.mark.asyncio
async def test_web_search_encodes_query():
    from tvastar.tools.builtin import web_search

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_response("results")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await web_search.fn("hello world test")

    assert " " not in captured["url"]


@pytest.mark.asyncio
async def test_web_search_truncates_at_max_chars():
    from tvastar.tools.builtin import web_search

    mock_resp = _mock_response("y" * 20000)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await web_search.fn("query", max_chars=50)

    assert len(result) == 50


# ---------------------------------------------------------------------------
# web_toolset
# ---------------------------------------------------------------------------


def test_web_toolset_returns_two_tools():
    from tvastar.tools.builtin import web_toolset

    tools = web_toolset()
    names = [t.name for t in tools]
    assert "web_browse" in names
    assert "web_search" in names
    assert len(tools) == 2


# ---------------------------------------------------------------------------
# top-level exports
# ---------------------------------------------------------------------------


def test_top_level_exports():
    from tvastar import web_browse, web_search, web_toolset  # noqa: F401

    assert callable(web_toolset)
