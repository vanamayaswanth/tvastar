"""The test-fixer — Tvastar's flagship reference application.

Give it a project with a failing test suite; it runs the suite, lets a Tvastar
agent read failures and edit the source, then **re-runs the suite itself** and
reports success based on the *real* exit code — never the model's say-so. That
ground-truth check is the whole point: an agent that fixes tests is only useful
if it can't lie about having fixed them, which is exactly what Tvastar's
silent-failure detection is about.

Used by the `tvastar-fix` CLI and the GitHub Action.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..agent import create_agent
from ..harness import Harness
from ..model.base import Model
from ..sandbox.base import ResourcePolicy, SecurityPolicy
from ..sandbox.local import LocalSandbox
from ..tools import default_toolset

_INSTRUCTIONS = (
    "You are a precise test-fixing agent working in a real code repository.\n"
    "Goal: make the failing test suite pass by fixing the SOURCE code.\n\n"
    "Rules:\n"
    "1. Use `bash` to run the test command and read the failures.\n"
    "2. Use `read_file`, `grep`, and `glob_files` to locate the cause.\n"
    "3. Fix the *implementation*, not the tests — do not weaken or delete tests "
    "to make them pass.\n"
    "4. Re-run the tests to confirm, and iterate until they pass.\n"
    "5. Make the smallest change that fixes the root cause.\n"
)


@dataclass
class FixResult:
    fixed: bool
    already_green: bool
    attempts: int
    test_command: str
    model: str
    before_output: str = ""
    after_output: str = ""
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    findings: list = field(default_factory=list)
    summary: str = ""

    @property
    def status(self) -> str:
        if self.already_green:
            return "already-green"
        return "fixed" if self.fixed else "unfixed"


async def fix_tests(
    project_dir: str | Path = ".",
    *,
    model: Model,
    test_command: str = "pytest -q",
    max_steps: int = 15,
    timeout: float = 180.0,
    network: bool = True,
    max_cpu_seconds: float | None = None,
    max_memory_mb: int | None = None,
) -> FixResult:
    """Run the self-heal-and-verify loop against a project's test suite.

    The agent edits files in ``project_dir`` directly (via a jailed
    LocalSandbox). Success is decided by re-running ``test_command`` ourselves.
    """
    root = Path(project_dir).resolve()
    policy = SecurityPolicy(network=network, timeout_seconds=timeout)
    resources = ResourcePolicy(
        max_cpu_seconds=max_cpu_seconds or timeout,
        max_memory_mb=max_memory_mb,
    )
    sandbox = LocalSandbox(root, policy=policy, resources=resources)
    # Don't let cached .pyc bytecode mask an edit: a fix written in the same
    # second as the baseline run can otherwise be ignored by Python's mtime
    # check, making a real fix look like a failure.
    no_pyc = {"PYTHONDONTWRITEBYTECODE": "1"}

    # 1. Baseline — is anything actually broken?
    baseline = await sandbox.exec(test_command, timeout=timeout, env=no_pyc)
    if baseline.ok:
        return FixResult(
            fixed=True,
            already_green=True,
            attempts=0,
            test_command=test_command,
            model=model.name,
            before_output=baseline.render(),
            after_output=baseline.render(),
            summary="Test suite already passes; nothing to fix.",
        )

    # 2. Let the agent attempt a fix (editing real files in the sandbox).
    agent = create_agent(
        "test-fixer",
        model=model,
        instructions=_INSTRUCTIONS,
        tools=default_toolset(),
        sandbox=lambda: sandbox,
        max_steps=max_steps,
    )
    prompt = (
        f"The test suite fails. Fix the source so `{test_command}` passes.\n\n"
        f"Current output:\n{baseline.render()}"
    )
    run = await Harness(agent).run(prompt)

    # 3. Ground truth — WE re-run the suite. The model's claim is irrelevant.
    _purge_pycache(root)  # ensure the verify run compiles the edited source
    after = await sandbox.exec(test_command, timeout=timeout, env=no_pyc)
    fixed = after.ok

    # 4. What changed (best-effort; only if it's a git repo).
    changed_files, diff = _git_changes(root)

    return FixResult(
        fixed=fixed,
        already_green=False,
        attempts=run.steps,
        test_command=test_command,
        model=model.name,
        before_output=baseline.render(),
        after_output=after.render(),
        changed_files=changed_files,
        diff=diff,
        findings=run.findings,
        summary=run.text.strip(),
    )


def _purge_pycache(root: Path) -> None:
    """Remove cached bytecode so the verify run reflects the edited source."""
    import shutil

    try:
        for pycache in root.rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)
        for pyc in root.rglob("*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _git_changes(root: Path) -> tuple[list[str], str]:
    """Return (changed_files, unified_diff) if root is a git repo, else ([], '')."""
    try:
        names = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if names.returncode != 0:
            return [], ""
        files = [f for f in names.stdout.splitlines() if f.strip()]
        diff = subprocess.run(
            ["git", "-C", str(root), "diff"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return files, diff.stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return [], ""
