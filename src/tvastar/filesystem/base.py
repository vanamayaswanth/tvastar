"""Filesystem abstraction — read/write/grep/glob over a root.

Two implementations live alongside this base:
* :class:`LocalFileSystem` — real disk, jailed to a root directory.
* :class:`VirtualFileSystem` — in-memory, for sandboxed/ephemeral runs.

All paths are treated as relative to the root and normalized so they can never
escape it (no ``..`` traversal) — a baseline security guarantee.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class GrepMatch:
    path: str
    line_no: int
    line: str


class FileSystem(abc.ABC):
    @abc.abstractmethod
    def read(self, path: str) -> str: ...

    @abc.abstractmethod
    def write(self, path: str, content: str) -> None: ...

    @abc.abstractmethod
    def exists(self, path: str) -> bool: ...

    @abc.abstractmethod
    def delete(self, path: str) -> None: ...

    @abc.abstractmethod
    def glob(self, pattern: str) -> list[str]:
        """Return paths matching a glob pattern (e.g. ``**/*.py``)."""

    @abc.abstractmethod
    def grep(self, pattern: str, *, glob: str = "**/*") -> list[GrepMatch]:
        """Regex search file contents across matching files."""

    @abc.abstractmethod
    def listdir(self, path: str = ".") -> list[str]: ...


def normalize(path: str) -> str:
    """Normalize to a safe relative posix path; raise on traversal."""
    import posixpath

    p = path.replace("\\", "/").strip()
    p = p.lstrip("/")
    norm = posixpath.normpath(p)
    if norm == ".":
        return ""
    if norm.startswith("..") or "/../" in f"/{norm}/":
        from ..errors import SecurityViolation

        raise SecurityViolation(f"Path escapes sandbox root: {path!r}")
    return norm
