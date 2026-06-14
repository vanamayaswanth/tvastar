"""Local disk filesystem, jailed to a root directory."""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import SecurityViolation
from .base import FileSystem, GrepMatch, normalize

# Skip noisy / large dirs during traversal — keeps grep/glob fast and lean.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


class LocalFileSystem(FileSystem):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _abs(self, path: str) -> Path:
        rel = normalize(path)
        full = (self.root / rel).resolve()
        # Defense in depth: ensure the resolved path stays under root.
        if full != self.root and self.root not in full.parents:
            raise SecurityViolation(f"Path escapes root: {path!r}")
        return full

    def read(self, path: str) -> str:
        return self._abs(path).read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str) -> None:
        full = self._abs(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def exists(self, path: str) -> bool:
        return self._abs(path).exists()

    def delete(self, path: str) -> None:
        full = self._abs(path)
        if full.is_dir():
            import shutil

            shutil.rmtree(full)
        elif full.exists():
            full.unlink()

    def listdir(self, path: str = ".") -> list[str]:
        base = self._abs(path)
        if not base.exists():
            return []
        return sorted(p.name + ("/" if p.is_dir() else "") for p in base.iterdir())

    def glob(self, pattern: str) -> list[str]:
        out = []
        for p in self.root.glob(pattern):
            if any(part in _SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            if p.is_file():
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)

    def grep(self, pattern: str, *, glob: str = "**/*") -> list[GrepMatch]:
        rx = re.compile(pattern)
        matches: list[GrepMatch] = []
        for rel in self.glob(glob):
            try:
                text = self.read(rel)
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    matches.append(GrepMatch(rel, i, line.rstrip()))
        return matches
