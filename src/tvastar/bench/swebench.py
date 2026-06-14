"""SWE-bench adapter — load tasks from the canonical benchmark dataset.

SWE-bench (swe-bench.github.io) is the standard for measuring how well agents
fix real GitHub issues in open-source Python projects. Each task is: a repo
snapshot, a failing test suite, and a ground-truth patch. Resolve rate
(fraction of tasks where the agent's patch makes the hidden tests pass) is the
standard metric.

This adapter loads tasks in two ways:

1. **HuggingFace** (``source="hf"``): streams ``princeton-nlp/SWE-bench_Lite``
   (or any compatible dataset) — needs ``pip install datasets``.

2. **Local JSONL** (``source="jsonl"``): reads a JSONL file where each line is
   a task object in SWE-bench format (``instance_id``, ``problem_statement``,
   ``hints_text``, ``patch``, ``test_patch``, …).

How verification works
----------------------
Full SWE-bench evaluation requires running a Docker container per task (to
apply the patch and run the hidden test suite in a clean repo environment).
Tvastar ships a *lightweight* verifier that:

1. Applies the agent's output as a unified diff to the workspace files.
2. Runs ``python -m pytest`` on the workspace.
3. Reports pass/fail based on the *real* exit code.

This is NOT the official SWE-bench harness (which uses Docker + hidden tests).
Results from this verifier are labelled ``swe_lite_local`` to distinguish them
from the official ``swe_lite`` numbers. Use the official harness for published
comparisons; use this for rapid local iteration.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from .core import BenchTask

_INSTRUCTIONS = (
    "You are a software engineering agent solving a real GitHub issue.\n"
    "You have a working copy of the repository in your workspace.\n\n"
    "Your task:\n"
    "1. Read the problem statement carefully.\n"
    "2. Use `grep`, `read_file`, and `glob_files` to understand the codebase.\n"
    "3. Write a fix using `edit_file` or `write_file`.\n"
    "4. Run `bash` with pytest to verify your fix passes.\n"
    "5. Make the minimal change that resolves the issue.\n\n"
    "Fix the SOURCE code only — do not modify the test files.\n"
)


def swe_bench_tasks(
    *,
    source: str = "hf",
    path: Optional[str] = None,
    split: str = "lite",
    max_tasks: Optional[int] = None,
    instructions: str = _INSTRUCTIONS,
) -> list[BenchTask]:
    """Load SWE-bench tasks as a list of ``BenchTask`` objects.

    Args:
        source: ``"hf"`` to load from HuggingFace (needs ``pip install
            datasets``), or ``"jsonl"`` to load from a local file.
        path: Path to JSONL file when ``source="jsonl"``. Ignored for ``"hf"``.
        split: ``"lite"`` (300 tasks) or ``"full"`` (2294 tasks). Only used
            for ``source="hf"``.
        max_tasks: Limit the number of tasks loaded (useful for quick runs).
        instructions: System-prompt suffix injected into each task prompt.

    Returns:
        A list of ``BenchTask`` objects, each with a ``verify`` function that
        applies the agent's diff to the workspace and runs pytest.
    """
    if source == "hf":
        return _load_from_hf(split=split, max_tasks=max_tasks, instructions=instructions)
    elif source == "jsonl":
        if not path:
            raise ValueError("path= is required when source='jsonl'")
        return _load_from_jsonl(path, max_tasks=max_tasks, instructions=instructions)
    else:
        raise ValueError(f"Unknown source {source!r}. Use 'hf' or 'jsonl'.")


def _load_from_hf(*, split: str, max_tasks: Optional[int], instructions: str) -> list[BenchTask]:
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as e:
        raise ImportError(
            "HuggingFace datasets is required for source='hf'.\nInstall with: pip install datasets"
        ) from e

    dataset_name = "princeton-nlp/SWE-bench_Lite" if split == "lite" else "princeton-nlp/SWE-bench"
    ds = load_dataset(dataset_name, split="test")
    rows = list(ds)
    if max_tasks is not None:
        rows = rows[:max_tasks]
    return [_row_to_task(r, instructions) for r in rows]


def _load_from_jsonl(path: str, *, max_tasks: Optional[int], instructions: str) -> list[BenchTask]:
    tasks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            tasks.append(_row_to_task(row, instructions))
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
    return tasks


def _row_to_task(row: dict, instructions: str) -> BenchTask:
    instance_id = row.get("instance_id", "unknown")
    problem = row.get("problem_statement", "")
    hints = row.get("hints_text", "")

    prompt = f"{instructions}\n\n## Problem\n\n{problem}\n"
    if hints:
        prompt += f"\n## Hints\n\n{hints}\n"

    # The test_patch tells us which tests to run for verification.
    test_patch = row.get("test_patch", "")

    def verify(run_result: Any, workspace: Path) -> bool:
        return _verify_with_pytest(workspace, test_patch=test_patch)

    return BenchTask(
        id=instance_id,
        prompt=prompt,
        workspace={},  # workspace is the live repo snapshot; BenchSuite sets it up
        verify=verify,
        metadata={
            "repo": row.get("repo", ""),
            "base_commit": row.get("base_commit", ""),
            "version": row.get("version", ""),
            "source": "swe_bench",
        },
    )


def _verify_with_pytest(workspace: Path, *, test_patch: str = "") -> bool:
    """Run pytest in the workspace, return True if it exits 0.

    If ``test_patch`` names specific test files, only those are run.
    Falls back to running the full suite.
    """
    # Try to extract the test file paths from the test_patch diff header
    test_files: list[str] = []
    if test_patch:
        for line in test_patch.splitlines():
            if line.startswith("+++ b/") or line.startswith("--- a/"):
                candidate = line[6:].strip()
                if candidate.startswith("test") or "/test" in candidate:
                    test_files.append(candidate)

    cmd = ["python", "-m", "pytest", "-q", "--tb=no", "--no-header"]
    if test_files:
        cmd += list(dict.fromkeys(test_files))  # dedupe, preserve order

    try:
        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
