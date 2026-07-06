"""Latchkey authenticated request tool.

Delegates HTTP requests to the external ``latchkey`` CLI, which manages
credentials outside agent context. Secrets never appear in prompts or tool
arguments — they stay in the credential manager.

This tool is opt-in only (not in ``default_toolset()``) because it requires
the user to have the ``latchkey`` binary installed on PATH.
"""

from __future__ import annotations

import subprocess

from .base import tool


@tool
def latchkey_curl(
    url: str,
    *,
    method: str = "GET",
    headers: list[str] | None = None,
    body: str | None = None,
) -> str:
    """Execute an authenticated HTTP request via the latchkey CLI.

    Args:
        url: The target URL for the request.
        method: HTTP method (GET, POST, PUT, DELETE, etc.).
        headers: Optional list of headers in "Key: Value" format.
        body: Optional request body string.
    """
    cmd = ["latchkey", "curl", "-X", method]
    for h in headers or []:
        cmd.extend(["-H", h])
    if body:
        cmd.extend(["-d", body])
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "[error] latchkey is not installed or not on PATH"
    except subprocess.TimeoutExpired as exc:
        # Kill the process if it's still running
        if exc.cmd and hasattr(exc, "stderr"):
            pass  # subprocess.run already handles cleanup
        return "[error] request timed out after 30s"

    if result.returncode != 0:
        if result.stderr.strip():
            return result.stderr
        return f"[error] latchkey exited with code {result.returncode}"

    return result.stdout
