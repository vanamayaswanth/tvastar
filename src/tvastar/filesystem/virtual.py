"""In-memory filesystem for ephemeral / sandboxed runs.

Backed by a plain dict — minimal memory footprint, trivially snapshottable for
durable execution, and isolated per session.
"""

from __future__ import annotations

import fnmatch
import re

from .base import FileSystem, GrepMatch, normalize


class VirtualFileSystem(FileSystem):
    def __init__(self, files: dict[str, str] | None = None):
        self._files: dict[str, str] = {}
        for k, v in (files or {}).items():
            self._files[normalize(k)] = v

    def read(self, path: str) -> str:
        p = normalize(path)
        if p not in self._files:
            raise FileNotFoundError(path)
        return self._files[p]

    def write(self, path: str, content: str) -> None:
        self._files[normalize(path)] = content

    def exists(self, path: str) -> bool:
        return normalize(path) in self._files

    def delete(self, path: str) -> None:
        self._files.pop(normalize(path), None)

    def listdir(self, path: str = ".") -> list[str]:
        prefix = normalize(path)
        prefix = (prefix + "/") if prefix else ""
        seen: set[str] = set()
        for f in self._files:
            if not f.startswith(prefix):
                continue
            rest = f[len(prefix) :]
            head, slash, _ = rest.partition("/")
            seen.add(head + ("/" if slash else ""))
        return sorted(seen)

    def glob(self, pattern: str) -> list[str]:
        # fnmatch doesn't grok ** across path separators; use _glob_match.
        return sorted(p for p in self._files if _glob_match(pattern, p))

    def grep(self, pattern: str, *, glob: str = "**/*") -> list[GrepMatch]:
        rx = re.compile(pattern)
        out: list[GrepMatch] = []
        for path in self.glob(glob):
            for i, line in enumerate(self._files[path].splitlines(), start=1):
                if rx.search(line):
                    out.append(GrepMatch(path, i, line.rstrip()))
        return out

    def snapshot(self) -> dict[str, str]:
        return dict(self._files)

    def restore(self, snap: dict[str, str]) -> None:
        self._files = dict(snap)


def _glob_match(pattern: str, path: str) -> bool:
    """Glob with ``**`` matching any number of path segments."""
    if "**" in pattern:
        prefix, _, suffix = pattern.partition("**")
        prefix = prefix.rstrip("/")
        suffix = suffix.lstrip("/")
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            return False
        if not suffix:
            return True
        tail = path.split("/")[-1]
        return fnmatch.fnmatch(path, "*" + suffix) or fnmatch.fnmatch(tail, suffix)
    return fnmatch.fnmatch(path, pattern)
