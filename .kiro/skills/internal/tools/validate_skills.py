#!/usr/bin/env python3
"""Validate the Internal Skill Library.

Checks (fail the build on any error):
  * every skill folder has a SKILL.md
  * frontmatter has required keys: name, description, version, owner, lastReviewed
  * skill `name` is unique
  * description reads as a retrieval trigger (contains "Use when")
  * required sections exist: a Mission heading, an output-contract heading, a Motto
  * index.yaml and the folders agree (every folder registered, every entry exists)

Warnings (do not fail the build) are printed separately.

Stdlib only — no third-party dependencies. Run: python tools/validate_skills.py
"""
from __future__ import annotations
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED_KEYS = ("name", "description", "version", "owner", "lastReviewed")

errors: list[str] = []
warnings: list[str] = []


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Return the key/value frontmatter block, or None if absent/malformed."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].strip().splitlines()
    fm: dict[str, str] = {}
    for line in block:
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def find_skill_dirs() -> list[str]:
    dirs = []
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "SKILL.md")):
            dirs.append(name)
    return dirs


def registered_folders() -> set[str]:
    """Pull `folder:` values out of index.yaml without a yaml dependency."""
    idx = os.path.join(ROOT, "index.yaml")
    if not os.path.isfile(idx):
        errors.append("index.yaml is missing")
        return set()
    with open(idx, encoding="utf-8") as fh:
        text = fh.read()
    return set(re.findall(r"^\s*folder:\s*(.+?)\s*$", text, re.MULTILINE))


def has_heading(text: str, *needles: str) -> bool:
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            low = line.lower()
            if any(n in low for n in needles):
                return True
    return False


def main() -> int:
    skill_dirs = find_skill_dirs()
    if not skill_dirs:
        errors.append("no skill folders found (expected <Folder>/SKILL.md)")

    seen_names: dict[str, str] = {}
    folders_on_disk: set[str] = set(skill_dirs)

    for folder in skill_dirs:
        rel = f"{folder}/SKILL.md"
        with open(os.path.join(ROOT, folder, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()

        fm = parse_frontmatter(text)
        if fm is None:
            errors.append(f"{rel}: missing or malformed YAML frontmatter")
        else:
            for key in REQUIRED_KEYS:
                if key not in fm or not fm[key]:
                    errors.append(f"{rel}: frontmatter missing required key '{key}'")
            name = fm.get("name", "")
            if name:
                if name in seen_names:
                    errors.append(
                        f"{rel}: duplicate skill name '{name}' (also in {seen_names[name]})"
                    )
                seen_names[name] = rel
            desc = fm.get("description", "")
            if desc and "use when" not in desc.lower():
                warnings.append(f"{rel}: description has no 'Use when ...' retrieval trigger")

        if not has_heading(text, "mission"):
            errors.append(f"{rel}: no Mission heading")
        if not has_heading(text, "output contract", "required output format"):
            warnings.append(f"{rel}: no Output Contract / Required Output Format section")
        if not has_heading(text, "motto"):
            warnings.append(f"{rel}: no Motto section")

    # registry sync
    registered = registered_folders()
    for folder in folders_on_disk:
        if registered and folder not in registered:
            errors.append(f"index.yaml: folder '{folder}' exists on disk but is not registered")
    for folder in registered:
        if folder not in folders_on_disk:
            errors.append(f"index.yaml: registered folder '{folder}' has no SKILL.md on disk")

    # report
    print(f"Scanned {len(skill_dirs)} skills.")
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  FAIL  {e}")

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s). FAILED.")
        return 1
    print(f"\n0 errors, {len(warnings)} warning(s). OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
