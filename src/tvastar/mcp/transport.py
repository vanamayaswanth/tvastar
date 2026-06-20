"""MCP transports — how Tvastar talks JSON-RPC 2.0 to an MCP server.

Two transports, both dependency-free:

* :class:`StdioTransport` — spawns a **local** MCP server as a subprocess and
  exchanges newline-delimited JSON over stdin/stdout.
* :class:`StreamableHttpTransport` — talks to a **remote** MCP server over HTTP,
  handling both a plain JSON response and a ``text/event-stream`` (SSE) reply.

A transport's only job is request/notify framing + correlation. The protocol
handshake (initialize, tools/list, tools/call) lives in :mod:`tvastar.mcp.client`.
"""

from __future__ import annotations

import abc
import asyncio
import json
import urllib.request
from typing import Any, Optional

from ..errors import TvastarError


class MCPError(TvastarError):
    """An MCP transport or protocol error."""


class Transport(abc.ABC):
    """Bidirectional JSON-RPC channel to an MCP server."""

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def request(self, method: str, params: Optional[dict] = None) -> Any:
        """Send a request and await its result (raises MCPError on error)."""

    @abc.abstractmethod
    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        """Send a fire-and-forget notification (no id, no response)."""

    @abc.abstractmethod
    async def close(self) -> None: ...


# --------------------------------------------------------------------------
# stdio transport (local servers)
# --------------------------------------------------------------------------


class StdioTransport(Transport):
    def __init__(
        self,
        command: str,
        args: Optional[list[str]] = None,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.timeout = timeout
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0

    async def start(self) -> None:
        import os

        full_env = {**os.environ, **(self.env or {})} if self.env else None
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
            cwd=self.cwd,
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # ignore non-JSON log noise on stdout
                self._dispatch(msg)
        except asyncio.CancelledError:
            pass
        finally:
            # Fail any in-flight requests so callers don't hang on a dead server.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MCPError("MCP server closed the connection"))
            self._pending.clear()

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        if mid is None or mid not in self._pending:
            return  # server-initiated request/notification — not handled yet
        fut = self._pending.pop(mid)
        if fut.done():
            return
        if "error" in msg:
            err = msg["error"]
            fut.set_exception(MCPError(f"{err.get('message')} (code {err.get('code')})"))
        else:
            fut.set_result(msg.get("result"))

    async def request(self, method: str, params: Optional[dict] = None) -> Any:
        if not self._proc or not self._proc.stdin:
            raise MCPError("transport not started")
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        payload = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError as e:
            self._pending.pop(mid, None)
            raise MCPError(f"MCP request '{method}' timed out") from e

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("transport not started")
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass


# --------------------------------------------------------------------------
# streamable HTTP transport (remote servers)
# --------------------------------------------------------------------------


class StreamableHttpTransport(Transport):
    """Remote MCP over HTTP. Uses stdlib urllib (run off the event loop) so the
    core stays dependency-free. Handles JSON and SSE (text/event-stream) replies.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._next_id = 0
        self._session_id: Optional[str] = None

    async def start(self) -> None:
        return None

    async def request(self, method: str, params: Optional[dict] = None) -> Any:
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        result = await asyncio.to_thread(self._post, payload, True)
        if result is None:
            raise MCPError(f"no response for '{method}'")
        if "error" in result:
            err = result["error"]
            raise MCPError(f"{err.get('message')} (code {err.get('code')})")
        return result.get("result")

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await asyncio.to_thread(self._post, payload, False)

    def _post(self, payload: dict, expect_response: bool) -> Optional[dict]:
        if not self.url.startswith(("http://", "https://")):
            raise MCPError(
                f"Only http/https URLs are allowed for MCP transport, got: {self.url[:40]!r}"
            )
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                if not expect_response:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8")
        except Exception as e:  # pragma: no cover - network
            raise MCPError(f"HTTP MCP request failed: {e}") from e
        if "text/event-stream" in ctype:
            return _parse_sse_for_response(raw, payload["id"])
        return json.loads(raw) if raw.strip() else None

    async def close(self) -> None:
        return None


def _parse_sse_for_response(raw: str, want_id: int) -> Optional[dict]:
    """Pull the JSON-RPC response with the matching id out of an SSE stream."""
    for block in raw.split("\n\n"):
        data_lines = [
            ln[len("data:") :].strip() for ln in block.splitlines() if ln.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            msg = json.loads("".join(data_lines))
        except json.JSONDecodeError:
            continue
        if msg.get("id") == want_id:
            return msg
    return None
