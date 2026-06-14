"""Skills layer: reusable expertise packages loaded on demand.

A Skill is a Markdown file with YAML-ish frontmatter::

    ---
    name: code-reviewer
    description: Review a diff for bugs and style issues
    tools: [read_file, grep]          # optional: restrict tools while active
    ---

    You are a meticulous code reviewer. When invoked:
    1. Read the changed files...

Skills are discovered from one or more directories and loaded lazily — only the
``name`` + ``description`` live in context until a skill is actually invoked,
keeping the prompt lean.
"""

from .loader import Skill, SkillLibrary, parse_skill

__all__ = ["Skill", "SkillLibrary", "parse_skill"]
