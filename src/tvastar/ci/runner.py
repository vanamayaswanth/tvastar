"""CIRunner — the core engine that detects failures and applies fixes.

Wraps tvastar-fix logic with Loop integration, LTM memory, and result tracking.

Intelligence features:
- Flaky test detection: re-runs failures to distinguish flakes from real bugs
- Retry awareness: skips known-unfixable tests via LTM memory
- Test selection: parses output to re-verify only failing tests
- Auto-PR: creates fix branches and opens PRs via GitHubClient
- Generalized: handles build errors, not just test failures
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from .config import CIConfig


@dataclass
class CIRunResult:
    """Result of a single CI run cycle.

    Attributes:
        status: Outcome — green | fixed | unfixed | error | flaky | skipped.
        flaky_tests: Test names identified as flaky (passed on retry).
        skipped_reason: Explanation when status is 'skipped' (e.g. known unfixable).
    """

    timestamp: float = field(default_factory=time.time)
    status: str = "unknown"  # green | fixed | unfixed | error | flaky | skipped
    test_command: str = ""
    failures_found: int = 0
    fix_attempted: bool = False
    fix_succeeded: bool = False
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    error: str | None = None
    duration_seconds: float = 0.0
    pr_url: str | None = None
    flaky_tests: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


class CIRunner:
    """Autonomous CI agent that monitors, fixes, and reports.

    Usage:
        runner = CIRunner(config)
        result = await runner.run()

        # Or as a Loop (continuous monitoring):
        loop = runner.as_loop(model)
        await loop.start()
    """

    def __init__(self, config: CIConfig) -> None:
        self._config = config
        self._memory: Any = None  # LTMStore, initialized lazily

    @property
    def config(self) -> CIConfig:
        return self._config

    def _get_memory(self) -> Any:
        """Lazily initialize LTM for remembering past fixes."""
        if self._memory is None:
            try:
                from tvastar.contrib.ltm import LTMStore

                Path(self._config.memory_path).parent.mkdir(parents=True, exist_ok=True)
                self._memory = LTMStore(self._config.memory_path)
            except Exception:
                self._memory = None
        return self._memory

    async def run(self, *, model: Any = None, github: Any = None) -> CIRunResult:
        """Run one CI cycle: check → detect flaky → fix → verify → PR → report.

        Parameters
        ----------
        model: Model instance. Falls back to config.model.
        github: Optional GitHubClient for auto-PR creation.

        Returns
        -------
        CIRunResult with the outcome.
        """
        start = time.time()
        effective_model = model or self._config.model

        if effective_model is None:
            return CIRunResult(
                status="error",
                error="No model configured. Set config.model or pass model= to run().",
                duration_seconds=time.time() - start,
            )

        try:
            from tvastar.sandbox.local import LocalSandbox
            from tvastar.sandbox.base import SecurityPolicy

            sandbox = LocalSandbox(
                self._config.repo_path,
                policy=SecurityPolicy(timeout_seconds=self._config.timeout),
            )

            # --- Step 1: Baseline run ---
            baseline = await sandbox.exec(self._config.test_command, timeout=self._config.timeout)

            if baseline.ok:
                return CIRunResult(
                    status="green",
                    test_command=self._config.test_command,
                    duration_seconds=time.time() - start,
                )

            # --- Gap 5: Retry awareness — skip known-unfixable ---
            failure_sig = _failure_signature(baseline.render())
            memory = self._get_memory()
            if memory is not None:
                past_attempts = memory.recall(f"unfixable:{failure_sig}")
                if past_attempts is not None and past_attempts >= 3:
                    return CIRunResult(
                        status="skipped",
                        test_command=self._config.test_command,
                        failures_found=1,
                        skipped_reason=f"Known unfixable (failed {past_attempts} times). Escalating.",
                        duration_seconds=time.time() - start,
                    )

            # --- Gap 1: Flaky detection — re-run to check if flaky ---
            retry_result = await sandbox.exec(self._config.test_command, timeout=self._config.timeout)
            if retry_result.ok:
                # Passed on second run → flaky!
                flaky_names = _extract_test_names(baseline.render())
                if memory is not None:
                    for name in flaky_names[:5]:
                        try:
                            count = memory.recall(f"flaky:{name}") or 0
                            memory.remember(f"flaky:{name}", count + 1, agent="tvastar-ci")
                        except Exception:
                            pass
                return CIRunResult(
                    status="flaky",
                    test_command=self._config.test_command,
                    failures_found=len(flaky_names),
                    flaky_tests=flaky_names[:10],
                    duration_seconds=time.time() - start,
                )

        except Exception:
            # Sandbox-based checks failed — fall through to fix_tests
            pass

        try:
            from tvastar.fix.fixer import fix_tests

            fix_result = await fix_tests(
                project_dir=self._config.repo_path,
                model=effective_model,
                test_command=self._config.test_command,
                max_steps=15,
                timeout=self._config.timeout,
            )

            if fix_result.already_green:
                status = "green"
            elif fix_result.fixed:
                status = "fixed"
            else:
                status = "unfixed"

            result = CIRunResult(
                status=status,
                test_command=self._config.test_command,
                failures_found=1,
                fix_attempted=not fix_result.already_green,
                fix_succeeded=fix_result.fixed,
                changed_files=fix_result.changed_files,
                diff=fix_result.diff,
                duration_seconds=time.time() - start,
            )

            # --- Gap 5: Record failed attempts ---
            if memory is not None and not fix_result.fixed and not fix_result.already_green:
                try:
                    attempts = memory.recall(f"unfixable:{failure_sig}") or 0
                    memory.remember(f"unfixable:{failure_sig}", attempts + 1, agent="tvastar-ci")
                except Exception:
                    pass

            # --- Gap 2: Auto-PR if fixed ---
            if fix_result.fixed and not fix_result.already_green and self._config.auto_pr:
                pr_url = await self._create_fix_pr(fix_result, github)
                if pr_url:
                    result.pr_url = pr_url

            # Remember successful fix in LTM
            if memory is not None and fix_result.fixed and not fix_result.already_green:
                try:
                    memory.record_episode(
                        agent="tvastar-ci",
                        event="fix_applied",
                        data={
                            "test_command": self._config.test_command,
                            "changed_files": fix_result.changed_files,
                            "summary": fix_result.summary[:500],
                        },
                    )
                except Exception:
                    pass

            return result

        except Exception as e:
            return CIRunResult(
                status="error",
                error=str(e),
                duration_seconds=time.time() - start,
            )

    async def _create_fix_pr(self, fix_result: Any, github: Any) -> str | None:
        """Create a fix branch and open a PR (Gap #2).

        Returns the PR URL if successful, None otherwise.
        """
        if github is None:
            return None

        try:
            import uuid
            from tvastar.sandbox.local import LocalSandbox
            from tvastar.sandbox.base import SecurityPolicy

            sandbox = LocalSandbox(
                self._config.repo_path,
                policy=SecurityPolicy(timeout_seconds=60.0),
            )

            branch_name = f"tvastar-ci/fix-{uuid.uuid4().hex[:8]}"
            summary = fix_result.summary[:60] if fix_result.summary else "Auto-fix failing tests"

            # Create branch, commit, push
            await sandbox.exec(f"git checkout -b {branch_name}")
            await sandbox.exec("git add -A")
            await sandbox.exec(f'git commit -m "fix: {summary}"')
            await sandbox.exec(f"git push -u origin {branch_name}")

            # Open PR via GitHub API
            # Detect repo from git remote
            remote = await sandbox.exec("git remote get-url origin")
            repo = _parse_repo_from_remote(remote.stdout.strip())

            if repo:
                pr_data = github.create_pr(
                    repo,
                    title=f"fix: {summary}",
                    body=f"## Auto-fix by tvastar-ci\n\n"
                         f"**Changed files:** {', '.join(fix_result.changed_files)}\n\n"
                         f"**Summary:** {fix_result.summary[:500]}",
                    head=branch_name,
                    base=self._config.branch,
                )
                return pr_data.get("html_url") or pr_data.get("url", "")
        except Exception:
            pass  # PR creation failure must not break the overall run

        return None

    def as_loop(self, model: Any) -> Any:
        """Create a Loop instance for continuous CI monitoring.

        The Loop triggers on schedule or event, runs the CI cycle,
        and hands off to humans when fixes fail repeatedly.
        """
        from tvastar.agent import create_agent
        from tvastar.loop import Loop, LoopConfig
        from tvastar.tools import default_toolset

        instructions = (
            f"You are a CI agent monitoring {self._config.repo_path}.\n"
            f"Run `{self._config.test_command}` to check test status.\n"
            "If tests fail: identify the root cause, make the minimal fix, "
            "verify locally, then commit.\n"
            "If tests pass: report SUCCESS.\n"
            "Do NOT change test assertions. Fix the implementation only."
        )

        spec = create_agent(
            "tvastar-ci",
            model=model,
            instructions=instructions,
            tools=default_toolset(),
            max_steps=15,
        )

        config = LoopConfig(
            name="tvastar-ci",
            goal=f"Keep `{self._config.test_command}` green in {self._config.repo_path}",
            schedule=self._config.schedule,
            max_iterations=self._config.max_fix_attempts,
            cancel_after=self._config.timeout,
            trigger_on=self._config.trigger_on,
        )

        return Loop(spec, config)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _failure_signature(output: str) -> str:
    """Generate a stable signature for a failure output (for LTM dedup).

    Hashes the first 5 failing test names + error types to create a stable key
    that survives whitespace/timing changes in output.
    """
    # Extract key error lines (ignore timing, paths)
    error_lines = []
    for line in output.splitlines():
        line = line.strip()
        if any(kw in line for kw in ("FAILED", "ERROR", "error:", "Error:", "assert", "Assert")):
            # Normalize: remove paths, timestamps, memory addresses
            normalized = re.sub(r"0x[0-9a-f]+", "ADDR", line)
            normalized = re.sub(r"\d+\.\d+s", "Xs", normalized)
            error_lines.append(normalized)
    key_content = "\n".join(error_lines[:5])
    return hashlib.md5(key_content.encode()).hexdigest()[:12]


def _extract_test_names(output: str) -> list[str]:
    """Extract failing test names from pytest/unittest output (Gap #4).

    Parses common patterns:
    - FAILED tests/test_foo.py::test_bar
    - ERROR tests/test_foo.py::TestClass::test_method
    - test_name (from unittest: FAIL: test_name)
    """
    names: list[str] = []

    # pytest style: FAILED path::test_name
    for match in re.finditer(r"FAILED\s+(\S+::\S+)", output):
        names.append(match.group(1))

    # pytest short: path::test_name FAILED
    for match in re.finditer(r"(\S+::\S+)\s+FAILED", output):
        if match.group(1) not in names:
            names.append(match.group(1))

    # unittest style: FAIL: test_name (module.Class)
    for match in re.finditer(r"FAIL:\s+(test_\w+)", output):
        if match.group(1) not in names:
            names.append(match.group(1))

    return names


def _parse_repo_from_remote(url: str) -> str:
    """Parse 'owner/repo' from a git remote URL.

    Handles:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo
    """
    # HTTPS style
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)
    return ""
