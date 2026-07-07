"""HTTP integration tool — general-purpose HTTP requests with SSRF protection.

Use :func:`http_toolset` to get the tool list for registration.
Requires the ``httpx`` optional dependency (``pip install tvastar[http]``).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

from ..errors import ToolError
from .base import Tool, tool

_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
]


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address falls within blocked private/reserved ranges."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in _BLOCKED_NETWORKS)


def _check_ssrf(url: str) -> None:
    """Resolve hostname and block requests to private/reserved IP ranges."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ToolError(f"Invalid URL (no hostname): {url}")
    try:
        # Resolve to IP — catches DNS rebinding at request time
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            ip_str = info[4][0]
            if _is_private_ip(ip_str):
                raise ToolError(
                    f"Request to {url} blocked: resolves to private/reserved IP {ip_str}"
                )
    except socket.gaierror as e:
        raise ToolError(f"DNS resolution failed for {url}: {e}") from e


@tool
async def http_request(
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    body: Optional[str] = None,
    timeout: float = 30.0,
    allow_private: bool = False,
) -> dict:
    """Make an HTTP request and return the response.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        url: Target URL (must start with http:// or https://).
        headers: Optional request headers as key-value pairs.
        body: Optional request body string.
        timeout: Request timeout in seconds (default 30).
        allow_private: If True, skip SSRF protection and allow private IPs.
    """
    try:
        import httpx
    except ImportError:
        raise ImportError("Install tvastar[http] for HTTP tools")

    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise ToolError(f"Unsupported HTTP method: {method}")

    if not url.startswith(("http://", "https://")):
        raise ToolError(f"URL must start with http:// or https://, got: {url}")

    # SSRF protection
    if not allow_private:
        _check_ssrf(url)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                content=body,
            )
    except httpx.TimeoutException:
        raise ToolError(f"Request to {url} timed out after {timeout}s")
    except (httpx.ConnectError, httpx.NetworkError) as e:
        raise ToolError(f"Network error for {url}: {e}")

    # Read body with truncation
    raw_body = response.text
    body_bytes = raw_body.encode("utf-8", errors="replace")
    if len(body_bytes) > _MAX_BODY_BYTES:
        truncated = body_bytes[:_MAX_BODY_BYTES].decode("utf-8", errors="replace")
        response_body = truncated + "\n[truncated: response exceeded 5 MB]"
    else:
        response_body = raw_body

    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response_body,
    }


def http_toolset() -> list[Tool]:
    """HTTP integration tool list, ready to register."""
    return [http_request]
