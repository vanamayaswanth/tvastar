"""Built-in tools that operate through the session's sandbox + filesystem.

These are the core capabilities a coding agent needs: read, write, edit, list,
glob, grep, and bash. Each requests ``ctx`` so it binds to whatever
sandbox/filesystem the session is running with — virtual, local, or a remote
container — without changing the tool code.

Use :func:`default_toolset` to get them all as a list.
Use :func:`web_toolset` to get the internet-access tools (browse + search).
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .base import Tool, ToolContext, tool


@tool
async def bash(ctx: ToolContext, command: str, timeout: Optional[float] = None) -> str:
    """Run a shell command in the sandbox and return its combined output.

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in seconds.
    """
    if ctx.sandbox is None:
        raise RuntimeError("bash tool requires a sandbox")
    result = await ctx.sandbox.exec(command, timeout=timeout)
    return result.render()


@tool
def read_file(ctx: ToolContext, path: str) -> str:
    """Read a file from the workspace.

    Args:
        path: Path relative to the workspace root.
    """
    fs = _fs(ctx)
    if not fs.exists(path):
        return f"[error] file not found: {path}"
    content = fs.read(path)
    # Number lines for easy reference, like Claude Code.
    lines = content.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i:>{width}}\t{ln}" for i, ln in enumerate(lines, 1)) or "[empty file]"


@tool
def write_file(ctx: ToolContext, path: str, content: str) -> str:
    """Create or overwrite a file in the workspace.

    Args:
        path: Path relative to the workspace root.
        content: Full file contents to write.
    """
    fs = _fs(ctx)
    fs.write(path, content)
    return f"wrote {len(content)} bytes to {path}"


@tool
def edit_file(ctx: ToolContext, path: str, old: str, new: str) -> str:
    """Replace the first exact occurrence of ``old`` with ``new`` in a file.

    Args:
        path: Path relative to the workspace root.
        old: Exact text to find (must be unique enough to match once).
        new: Replacement text.
    """
    fs = _fs(ctx)
    if not fs.exists(path):
        return f"[error] file not found: {path}"
    content = fs.read(path)
    count = content.count(old)
    if count == 0:
        return f"[error] text not found in {path}"
    if count > 1:
        return f"[error] text is ambiguous ({count} matches) in {path}; add context"
    fs.write(path, content.replace(old, new, 1))
    return f"edited {path}"


@tool
def list_files(ctx: ToolContext, path: str = ".") -> str:
    """List directory entries in the workspace.

    Args:
        path: Directory path relative to the workspace root.
    """
    entries = _fs(ctx).listdir(path)
    return "\n".join(entries) if entries else "[empty]"


@tool
def glob_files(ctx: ToolContext, pattern: str) -> str:
    """Find files by glob pattern (supports ``**``).

    Args:
        pattern: Glob like ``**/*.py`` or ``src/*.md``.
    """
    matches = _fs(ctx).glob(pattern)
    return "\n".join(matches) if matches else "[no matches]"


@tool
def grep(ctx: ToolContext, pattern: str, glob: str = "**/*") -> str:
    """Regex-search file contents across the workspace.

    Args:
        pattern: Regular expression to search for.
        glob: Restrict to files matching this glob (default all).
    """
    matches = _fs(ctx).grep(pattern, glob=glob)
    if not matches:
        return "[no matches]"
    return "\n".join(f"{m.path}:{m.line_no}: {m.line}" for m in matches[:200])


def _fs(ctx: ToolContext):
    fs = ctx.filesystem or (ctx.sandbox.fs if ctx.sandbox else None)
    if fs is None:
        raise RuntimeError("filesystem tool requires a sandbox or filesystem")
    return fs


def default_toolset() -> list[Tool]:
    """All built-in tools as a list, ready to register."""
    return [bash, read_file, write_file, edit_file, list_files, glob_files, grep]


# ---------------------------------------------------------------------------
# Web tools — require internet access, no extra dependencies (stdlib urllib)
# ---------------------------------------------------------------------------

_JINA_READER = "https://r.jina.ai/"
_JINA_SEARCH = "https://s.jina.ai/"
_TIMEOUT = 20


def _http_get(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"[http {e.code}] {e.reason}"
    except Exception as e:
        return f"[error] {e}"


@tool
async def web_browse(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its content as clean markdown.

    Uses Jina AI Reader (r.jina.ai) — no API key required.

    Args:
        url: Full URL to fetch (include https://).
        max_chars: Truncate response to this many characters (default 8000).
    """
    target = _JINA_READER + url
    headers = {
        "Accept": "text/markdown",
        "X-Retain-Images": "none",
        "X-Timeout": str(_TIMEOUT - 2),
    }
    text = await asyncio.to_thread(_http_get, target, headers)
    return text[:max_chars] if len(text) > max_chars else text


@tool
async def web_search(query: str, max_chars: int = 8000) -> str:
    """Search the web and return top results as clean text.

    Uses Jina AI Search (s.jina.ai) — no API key required.

    Args:
        query: Search query in plain English.
        max_chars: Truncate response to this many characters (default 8000).
    """
    target = _JINA_SEARCH + urllib.parse.quote(query, safe="")
    headers = {
        "Accept": "text/markdown",
        "X-Retain-Images": "none",
    }
    text = await asyncio.to_thread(_http_get, target, headers)
    return text[:max_chars] if len(text) > max_chars else text


def web_toolset() -> list[Tool]:
    """Internet-access tools: web_browse + web_search (no API key, no extra deps)."""
    return [web_browse, web_search]
