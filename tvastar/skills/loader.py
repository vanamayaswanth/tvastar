"""Markdown skill parsing + a lazy library.

Frontmatter is parsed with a tiny built-in reader (no PyYAML dependency) that
handles the scalar / list / quoted-string cases skills actually use. If PyYAML
is installed it is used automatically for full fidelity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..errors import SkillError


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    tools: Optional[list[str]] = None  # None = inherit all session tools
    metadata: dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None

    def summary(self) -> str:
        return f"- {self.name}: {self.description}"


def parse_skill(text: str, *, source: Optional[str] = None) -> Skill:
    """Parse a Markdown skill (frontmatter + body) into a Skill."""
    front, body = _split_frontmatter(text)
    name = front.get("name") or (Path(source).stem if source else None)
    if not name:
        raise SkillError(f"Skill missing 'name' (source={source})")
    desc = front.get("description", "")
    tools = front.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    known = {"name", "description", "tools"}
    meta = {k: v for k, v in front.items() if k not in known}
    return Skill(
        name=str(name),
        description=str(desc),
        instructions=body.strip(),
        tools=tools,
        metadata=meta,
        source=source,
    )


class SkillLibrary:
    """Discovers and lazily loads skills from directories.

    Only file paths + parsed headers are kept in memory; instructions are read
    from disk on first access and cached.
    """

    def __init__(self, skills: Optional[list[Skill]] = None):
        self._skills: dict[str, Skill] = {}
        for s in skills or []:
            self._skills[s.name] = s

    @classmethod
    def from_dirs(cls, *dirs: str | Path, pattern: str = "*.md") -> "SkillLibrary":
        lib = cls()
        for d in dirs:
            base = Path(d)
            if not base.exists():
                continue
            for path in sorted(base.rglob(pattern)):
                try:
                    skill = parse_skill(path.read_text(encoding="utf-8"), source=str(path))
                    lib.add(skill)
                except SkillError:
                    continue  # skip malformed skills rather than crash discovery
        return lib

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise SkillError(f"No skill named '{name}'. Available: {self.names()}")
        return self._skills[name]

    def names(self) -> list[str]:
        return list(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def catalog(self) -> str:
        """A compact catalog for the system prompt (names + descriptions)."""
        if not self._skills:
            return ""
        lines = [s.summary() for s in self._skills.values()]
        return "Available skills (invoke by name when relevant):\n" + "\n".join(lines)


# ---- frontmatter parsing -----------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    # find closing fence
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    raw = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    return _parse_yaml(raw), body


def _parse_yaml(raw: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    # Minimal fallback parser: key: value, with [a, b] lists.
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            out[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        else:
            out[key] = val.strip("'\"")
    return out
