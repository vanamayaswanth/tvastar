"""Tests for the HTTP integration tool (src/tvastar/tools/http.py)."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tvastar.errors import ToolError
from tvastar.tools.http import (
    _check_ssrf,
    _is_private_ip,
    http_request,
    http_toolset,
)


# ── Factory ───────────────────────────────────────────────────────────────────


def test_http_toolset_returns_one_tool():
    tools = http_toolset()
    assert len(tools) == 1
    assert tools[0].name == "http_request"


def test_http_request_schema_has_required_params():
    t = http_toolset()[0]
    props = t.input_schema["properties"]
    assert "method" in props
    assert "url" in props
    assert "headers" in props
    assert "body" in props
    assert "timeout" in props
    assert "allow_private" in props
    assert set(t.input_schema["required"]) == {"method", "url"}


def test_http_request_does_not_want_ctx():
    t = http_toolset()[0]
    assert t.wants_ctx is False


# ── SSRF Protection ──────────────────────────────────────────────────────────


def test_is_private_ip_10_range():
    assert _is_private_ip("10.0.0.1") is True
    assert _is_private_ip("10.255.255.255") is True


def test_is_private_ip_172_range():
    assert _is_private_ip("172.16.0.1") is True
    assert _is_private_ip("172.31.255.255") is True
    assert _is_private_ip("172.32.0.1") is False


def test_is_private_ip_192_168_range():
    assert _is_private_ip("192.168.0.1") is True
    assert _is_private_ip("192.169.0.1") is False


def test_is_private_ip_loopback():
    assert _is_private_ip("127.0.0.1") is True
    assert _is_private_ip("127.255.255.255") is True


def test_is_private_ip_link_local():
    assert _is_private_ip("169.254.1.1") is True


def test_is_private_ip_public():
    assert _is_private_ip("8.8.8.8") is False
    assert _is_private_ip("1.1.1.1") is False


def test_check_ssrf_blocks_localhost():
    with pytest.raises(ToolError, match="private/reserved IP"):
        _check_ssrf("http://127.0.0.1/secret")


def test_check_ssrf_blocks_private_dns(monkeypatch):
    """When hostname resolves to a private IP, block it."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("10.0.0.5", 80))],
    )
    with pytest.raises(ToolError, match="private/reserved IP"):
        _check_ssrf("http://evil.example.com/steal")


def test_check_ssrf_allows_public_ip(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )
    # Should not raise
    _check_ssrf("http://example.com/")


def test_check_ssrf_dns_failure():
    """When DNS fails, raise ToolError."""
    import socket

    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ToolError, match="DNS resolution failed"):
            _check_ssrf("http://nonexistent.invalid/")


def test_check_ssrf_no_hostname():
    with pytest.raises(ToolError, match="no hostname"):
        _check_ssrf("http:///path")


# ── Lazy import ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_httpx_import_error_when_missing():
    """When httpx is not installed, raise ImportError with helpful message."""
    with patch.dict(sys.modules, {"httpx": None}):
        with patch("builtins.__import__", side_effect=ImportError("No module named 'httpx'")):
            with pytest.raises(ImportError, match="Install tvastar\\[http\\] for HTTP tools"):
                await http_request.fn(method="GET", url="http://example.com")


# ── Successful requests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_get_success(monkeypatch):
    """Successful GET returns structured dict with status_code, headers, body."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = "<html>OK</html>"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(method="GET", url="http://example.com")

    assert result["status_code"] == 200
    assert result["headers"]["content-type"] == "text/html"
    assert result["body"] == "<html>OK</html>"


@pytest.mark.asyncio
async def test_http_post_passes_body(monkeypatch):
    """POST passes body content to httpx."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.headers = {}
    mock_resp.text = '{"id": 1}'

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(
            method="POST", url="https://api.example.com/data", body='{"key":"val"}'
        )

    assert result["status_code"] == 201
    mock_client.request.assert_called_once_with(
        "POST", "https://api.example.com/data", headers=None, content='{"key":"val"}'
    )


@pytest.mark.asyncio
async def test_http_4xx_still_returns_response(monkeypatch):
    """Even 4xx/5xx HTTP responses are returned (not raised as errors)."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {}
    mock_resp.text = "Not Found"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(method="GET", url="http://example.com/missing")

    assert result["status_code"] == 404
    assert result["body"] == "Not Found"


# ── Error handling ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_raises_tool_error(monkeypatch):
    """Timeout → ToolError with URL and timeout duration."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ToolError, match="timed out after 30"):
            await http_request.fn(method="GET", url="http://example.com/slow")


@pytest.mark.asyncio
async def test_connect_error_raises_tool_error(monkeypatch):
    """Connection refused → ToolError with description."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ToolError, match="Network error"):
            await http_request.fn(method="GET", url="http://example.com/down")


@pytest.mark.asyncio
async def test_invalid_method_raises_tool_error(monkeypatch):
    with pytest.raises(ToolError, match="Unsupported HTTP method"):
        await http_request.fn(method="TRACE", url="http://example.com")


@pytest.mark.asyncio
async def test_invalid_url_scheme_raises_tool_error():
    with pytest.raises(ToolError, match="must start with http"):
        await http_request.fn(method="GET", url="ftp://example.com/file")


# ── Body truncation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_body_truncation_at_5mb(monkeypatch):
    """Response bodies exceeding 5 MB get truncated with indicator."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    big_body = "x" * (5 * 1024 * 1024 + 100)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = big_body

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(method="GET", url="http://example.com/big")

    assert result["body"].endswith("[truncated: response exceeded 5 MB]")
    # The truncated body (before indicator) should be at most 5 MB of content
    assert len(result["body"]) < len(big_body)


@pytest.mark.asyncio
async def test_body_under_5mb_not_truncated(monkeypatch):
    """Response bodies under 5 MB are returned as-is."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 80))],
    )

    small_body = "hello world"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = small_body

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(method="GET", url="http://example.com/small")

    assert result["body"] == "hello world"
    assert "[truncated" not in result["body"]


# ── allow_private bypass ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_private_skips_ssrf_check(monkeypatch):
    """When allow_private=True, private IPs are allowed."""
    # Don't mock getaddrinfo — let it resolve 127.0.0.1 naturally
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "internal"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await http_request.fn(
            method="GET", url="http://127.0.0.1/internal", allow_private=True
        )

    assert result["status_code"] == 200
    assert result["body"] == "internal"
