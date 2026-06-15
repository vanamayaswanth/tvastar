"""Pre-built loop patterns — clone, configure, and run in minutes.

Each pattern is a Loop subclass with production-ready defaults:
- Sensible schedule
- Minimal-change, verify-before-commit instructions
- LogHandoff by default (override with your Slack/PagerDuty handler)
- extra_instructions= to extend without replacing the base prompt

Usage::

    from tvastar.loop.patterns import CISweeper
    from tvastar.model.anthropic import AnthropicModel

    loop = CISweeper(
        model=AnthropicModel("claude-sonnet-4-6"),
        schedule="*/15 * * * *",
        handoff=SlackHandoff("#oncall"),
    )
    await loop.start()   # runs forever in background
    # --- or ---
    run = await loop.trigger()   # one shot
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...memory.store import Store
    from ...model.base import Model
    from ...observability import Tracer
    from ..handoff import HandoffPolicy
    from .. import LoopRun

from ...tools import default_toolset
from .. import FailureKind, Loop, LoopConfig

_REJECTION_HISTORY_KEY = "loop:{name}:rejection_history"
_MAX_REJECTION_HISTORY = 5

# ---------------------------------------------------------------------------
# Shared instructions suffix (every pattern ends with this)
# ---------------------------------------------------------------------------

_VERIFY_FOOTER = """
─── Completion Report ───────────────────────────────────────────────────────
Before finishing, state:
1. What you found (facts, not intentions).
2. What you changed (exact files and commands run), or why you made no changes.
3. Finish with exactly one of:
   SUCCESS   — goal fully met, changes committed/pushed if applicable.
   PARTIAL   — goal partly met; describe what remains.
   FAILURE   — goal not met; describe the blocker clearly.

Do NOT claim SUCCESS if tests still fail, if you made no verifiable change,
or if you are uncertain. A clear FAILURE is better than a false SUCCESS.
─────────────────────────────────────────────────────────────────────────────
"""


def _make_agent(name: str, model: "Model", instructions: str, tools: list | None):
    from ...agent import create_agent
    from ...detect import default_detectors

    return create_agent(
        name,
        model=model,
        instructions=instructions,
        tools=tools or default_toolset(),
        detect=default_detectors(),
    )


# ---------------------------------------------------------------------------
# CI Sweeper
# ---------------------------------------------------------------------------

_CI_GOAL = (
    "Check the CI status. If any tests or builds are failing, identify the root "
    "cause, make the minimal fix required, verify locally, then commit and push."
)

_CI_INSTRUCTIONS = (
    """You are a CI sweeper agent. Your job is to keep the build green.

Rules:
- Run the test suite first. Read the exact error messages before writing any code.
- Identify root cause. Do not guess — trace the failure to its source.
- Make the smallest correct fix. Do not refactor, rename, or clean up unrelated code.
- Re-run only the failing tests to verify your fix works.
- Commit with: "fix: <one-line description of what you fixed>"
- Do NOT push if tests still fail after your fix. Report FAILURE instead.
- Do NOT fix passing tests. Do NOT change test assertions to make tests pass.
"""
    + _VERIFY_FOOTER
)


class CISweeper(Loop):
    """Monitors CI, fixes red builds, escalates if unfixable.

    Default schedule: every 15 minutes.
    Default retries: 3 attempts with exponential backoff (30s → 60s → 120s).
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "*/15 * * * *",
        max_iterations: int = 3,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _CI_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("ci-sweeper", model, instructions, tools)
        config = LoopConfig(
            name="ci-sweeper",
            goal=_CI_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# PR Babysitter
# ---------------------------------------------------------------------------

_PR_GOAL = (
    "Check all open pull requests. Resolve merge conflicts where safe to do so, "
    "flag stale PRs with no activity in 3+ days, and report overall PR health."
)

_PR_INSTRUCTIONS = (
    """You are a PR babysitter agent. Your job is to keep open PRs moving.

Rules:
- List all open PRs and their status (CI, conflicts, last-activity, reviews).
- For PRs with merge conflicts: resolve ONLY if the conflict is trivial (formatting,
  lock files, auto-generated files). Do NOT touch logic conflicts — flag them instead.
- Flag as stale: no activity (commit, comment, review) in 3+ days.
- Do NOT merge PRs under any circumstance.
- Do NOT close PRs.
- Do NOT force-push to any branch.
- Produce a summary table: PR | CI | Conflicts | Stale | Action Taken.
"""
    + _VERIFY_FOOTER
)


class PRBabysitter(Loop):
    """Watches open PRs, resolves trivial conflicts, flags stale reviews.

    Default schedule: every 30 minutes.
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "*/30 * * * *",
        max_iterations: int = 2,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _PR_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("pr-babysitter", model, instructions, tools)
        config = LoopConfig(
            name="pr-babysitter",
            goal=_PR_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# Daily Triage
# ---------------------------------------------------------------------------

_TRIAGE_GOAL = (
    "Review issues created or updated in the last 24 hours. "
    "Classify severity, identify duplicates, and produce a triage report."
)

_TRIAGE_INSTRUCTIONS = (
    """You are a daily triage agent.
Your job is to process new issues every morning.


Rules:
- Find issues created or updated in the last 24 hours.
- For each issue classify: severity (critical/high/medium/low), component, is-duplicate.
- Do NOT close issues.
- Do NOT assign issues — report your recommended assignee but take no action.
- Do NOT add labels unless you have confirmed the label exists in the project.
- Output a triage table: Issue # | Title | Severity | Component | Duplicate of | Recommended Action.
"""
    + _VERIFY_FOOTER
)


class DailyTriage(Loop):
    """Categorises and prioritises new issues every morning at 9am UTC.

    Default schedule: 9am UTC daily.
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "0 9 * * *",
        max_iterations: int = 2,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _TRIAGE_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("daily-triage", model, instructions, tools)
        config = LoopConfig(
            name="daily-triage",
            goal=_TRIAGE_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# Dependency Sweeper
# ---------------------------------------------------------------------------

_DEP_GOAL = (
    "Check for outdated dependencies. Update patch-level versions only, "
    "run the test suite, and commit if all tests pass."
)

_DEP_INSTRUCTIONS = (
    """You are a dependency sweeper agent.
Your job is to keep dependencies current safely.

Rules:
- Check for outdated packages (pip list --outdated / uv lock --check / npm outdated).
- Update PATCH versions only (x.y.Z → x.y.Z+n). NEVER update major or minor versions.
- After updating, run the FULL test suite.
- If all tests pass: commit the updated lock file with "chore: bump patch dependencies".
- If any test fails: revert ALL updates, report exactly which package broke which test.
- NEVER update: cryptography, auth libs, or any package flagged as a security dependency
  without a human reviewing it first. Flag these in your report.
- NEVER update more than 10 packages in one run. If there are more, update the oldest 10.
"""
    + _VERIFY_FOOTER
)


class DependencySweeper(Loop):
    """Keeps patch dependencies current; runs tests before committing.

    Default schedule: 3am UTC daily.
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "0 3 * * *",
        max_iterations: int = 2,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _DEP_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("dependency-sweeper", model, instructions, tools)
        config = LoopConfig(
            name="dependency-sweeper",
            goal=_DEP_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# Post-Merge Cleanup
# ---------------------------------------------------------------------------

_CLEANUP_GOAL = (
    "After a merge to main, perform safe cleanup: report TODO/FIXME comments "
    "introduced in the merge, and check for stale references."
)

_CLEANUP_INSTRUCTIONS = (
    """You are a post-merge cleanup agent.
Your job is to tidy up after merges land.

Rules:
- Identify what was merged (last merge commit, diff summary).
- Find TODO/FIXME comments introduced in the merged diff — LIST them, do NOT delete them.
- Check if any documentation or config references old names from the pre-merge code.
- Check for dead imports or unused variables introduced by the merge.
- Report findings. Make NO code changes unless they are single-line, trivially safe
  (e.g. remove an import that no longer exists).
- Do NOT delete files. Do NOT remove code that might be intentionally kept. When in
  doubt, report instead of act.
"""
    + _VERIFY_FOOTER
)


class PostMergeCleanup(Loop):
    """Reports cleanup opportunities after merges land on main.

    Default schedule: every 30 minutes.
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "*/30 * * * *",
        max_iterations: int = 2,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _CLEANUP_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("post-merge-cleanup", model, instructions, tools)
        config = LoopConfig(
            name="post-merge-cleanup",
            goal=_CLEANUP_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# Changelog Drafter
# ---------------------------------------------------------------------------

_CHANGELOG_GOAL = (
    "Read commits since the last release tag and draft a CHANGELOG entry. "
    "Write to CHANGELOG.md but do NOT commit — leave it for human review."
)

_CHANGELOG_INSTRUCTIONS = (
    """You are a changelog drafter agent.
Your job is to produce clear release notes.

Rules:
- Find the most recent release tag: git tag --sort=-version:refname | head -1
- List commits since that tag: git log <tag>..HEAD --oneline
- Group by type: ### Added | ### Fixed | ### Changed | ### Performance | ### Docs | ### Chores
- Write a new section at the TOP of CHANGELOG.md:
    ## [Unreleased] — YYYY-MM-DD
- Use precise language: "Add X", "Fix Y", "Update Z". Avoid vague words like "improve".
- Omit merge commits and version-bump commits.
- Do NOT commit. Do NOT push. The human must review and edit before release.
- If there are no commits since the last tag, write a note and exit with SUCCESS.
"""
    + _VERIFY_FOOTER
)


class ChangelogDrafter(Loop):
    """Drafts CHANGELOG entries from commit history every Monday at 9am UTC.

    Default schedule: Monday 9am UTC.
    """

    def __init__(
        self,
        model: "Model",
        *,
        schedule: str = "0 9 * * 1",
        max_iterations: int = 2,
        handoff: "HandoffPolicy | None" = None,
        tools: list | None = None,
        extra_instructions: str = "",
    ) -> None:
        from ..handoff import LogHandoff

        instructions = _CHANGELOG_INSTRUCTIONS
        if extra_instructions:
            instructions += f"\n\nProject-specific notes:\n{extra_instructions}"

        spec = _make_agent("changelog-drafter", model, instructions, tools)
        config = LoopConfig(
            name="changelog-drafter",
            goal=_CHANGELOG_GOAL,
            schedule=schedule,
            max_iterations=max_iterations,
            handoff=handoff or LogHandoff(),
        )
        super().__init__(spec, config)


# ---------------------------------------------------------------------------
# Maker / Checker
# ---------------------------------------------------------------------------

_MAKER_INSTRUCTIONS = """You are the Maker. Your job is to accomplish the goal using available tools.

Rules:
- Read the goal carefully. If there is feedback from a previous Checker review,
  address every point raised before proceeding.
- Use tools to do real work: read files, write code, run tests.
- After completing your work, summarise:
  1. What you did (concrete actions taken).
  2. Why it satisfies the goal.
  3. How a Checker can verify it (files changed, commands to run, etc.).
"""

_CHECKER_INSTRUCTIONS = """You are the Checker. Your job is to independently verify the Maker's work.

Rules:
- You are adversarial. Try to find flaws. Do not rubber-stamp the work.
- Read the goal. Read the Maker's output. Run verification commands (tests,
  linters, reads) as needed. Do NOT modify any files — you are reviewing only.
- Check: does the work actually satisfy the goal? Are there bugs or gaps?
  Did tests pass? Are there edge cases the Maker missed?
- If you have any doubt, issue REJECTED with specific feedback.

End your response with exactly one of:
  APPROVED — the goal is fully met, no issues found.
  REJECTED — specific description of what is wrong or missing.

A clear REJECTED with actionable feedback is more valuable than a false APPROVED.
"""

_CHECKER_PROMPT_TEMPLATE = """\
Goal: {goal}

─── Maker's Output ───────────────────────────────────────────────────────────
{maker_output}
─────────────────────────────────────────────────────────────────────────────

Review the Maker's work. Verify it achieves the goal above.
Remember: end with APPROVED or REJECTED (with specific feedback).
"""


class MakerChecker(Loop):
    """Two-agent verification: Maker proposes, Checker independently verifies.

    Each round = one Maker run + one Checker run.
    Only APPROVED from the Checker declares PASS.
    REJECTED feeds back to the Maker for the next round.

    Werner failure modes handled:
    - Checker can't APPROVE after max_rounds → HANDOFF (not silent hang)
    - Maker times out → TIMEOUT → standard retry path
    - Checker errors → MODEL_ERROR counted against round limit (not swallowed)
    - Thrash loop (Maker ↔ Checker cycle same mistake) → thrash_loop detector fires
    - Neither APPROVED nor REJECTED in checker output → treated as REJECTED (fail safe)

    Args:
        maker_model: Model used by the Maker agent.
        checker_model: Model used by the Checker agent (can differ from maker).
        goal: Plain-language description of what should be made and verified.
        name: Loop name for checkpointing and status display.
        schedule: Cron expression or @manual (default: @manual).
        max_rounds: Maker+Checker cycles allowed before HANDOFF (default: 3).
        handoff: Escalation policy on exhausted rounds (default: LogHandoff).
        cancel_after: Per-run timeout in seconds (strongly recommended).
        maker_tools: Tool list for the Maker (default: default_toolset()).
        checker_tools: Tool list for the Checker (default: default_toolset()).
        extra_maker_instructions: Appended to Maker system prompt.
        extra_checker_instructions: Appended to Checker system prompt.
        store: Persistence store for checkpointing (default: InMemoryStore).
        tracer: Observability tracer.
    """

    def __init__(
        self,
        maker_model: "Model",
        checker_model: "Model",
        goal: str,
        *,
        name: str = "maker-checker",
        schedule: str = "@manual",
        max_rounds: int = 3,
        handoff: "HandoffPolicy | None" = None,
        cancel_after: float | None = None,
        maker_tools: list | None = None,
        checker_tools: list | None = None,
        extra_maker_instructions: str = "",
        extra_checker_instructions: str = "",
        store: "Store | None" = None,
        tracer: "Tracer | None" = None,
    ) -> None:
        from ...agent import create_agent
        from ...detect import default_detectors
        from ...harness import Harness
        from ..handoff import LogHandoff

        # Build Maker agent
        maker_instr = _MAKER_INSTRUCTIONS
        if extra_maker_instructions:
            maker_instr += f"\n\nProject-specific notes:\n{extra_maker_instructions}"

        maker_spec = create_agent(
            f"{name}-maker",
            model=maker_model,
            instructions=maker_instr,
            tools=maker_tools or default_toolset(),
            detect=default_detectors(),
        )

        # Build Checker agent — same tools so it can run tests, but instructions say no writes
        checker_instr = _CHECKER_INSTRUCTIONS
        if extra_checker_instructions:
            checker_instr += f"\n\nProject-specific notes:\n{extra_checker_instructions}"

        checker_spec = create_agent(
            f"{name}-checker",
            model=checker_model,
            instructions=checker_instr,
            tools=checker_tools or default_toolset(),
            detect=default_detectors(),
        )

        config = LoopConfig(
            name=name,
            goal=goal,
            schedule=schedule,
            max_iterations=max_rounds,
            cancel_after=cancel_after,
            retry_backoff_base=0.0,  # no sleep between rounds — feedback is immediate
            handoff=handoff or LogHandoff(),
        )

        # Call Loop.__init__ with the Maker spec (sets self._harness to Maker)
        super().__init__(maker_spec, config, store=store, tracer=tracer)

        # Checker harness — separate session per round so it can't reuse Maker context
        self._checker_harness = Harness(checker_spec, store=store, tracer=tracer)

    # ------------------------------------------------------------------
    # Override: two-phase run (Maker → Checker)
    # ------------------------------------------------------------------

    async def _run_iteration(self, run: "LoopRun", context: dict) -> None:
        from .. import LoopState

        # ── Phase 1: Maker ────────────────────────────────────────────────
        async with self._lock:
            self._set(run, LoopState.RUNNING)

        maker_prompt = self._build_maker_prompt(context)

        try:
            if self._config.cancel_after:
                maker_result = await asyncio.wait_for(
                    self._harness.run(maker_prompt),
                    timeout=self._config.cancel_after,
                )
            else:
                maker_result = await self._harness.run(maker_prompt)
        except asyncio.TimeoutError:
            run.error = f"Maker timed out after {self._config.cancel_after}s"
            run.failure_kind = FailureKind.TIMEOUT
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            return
        except Exception as exc:
            run.error = f"Maker error: {exc}"
            run.failure_kind = FailureKind.MODEL_ERROR
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            return

        # ── Phase 2: Checker ─────────────────────────────────────────────
        async with self._lock:
            self._set(run, LoopState.VERIFYING)

        checker_prompt = _CHECKER_PROMPT_TEMPLATE.format(
            goal=self._config.goal,
            maker_output=maker_result.text,
        )

        try:
            if self._config.cancel_after:
                checker_result = await asyncio.wait_for(
                    self._checker_harness.run(checker_prompt),
                    timeout=self._config.cancel_after,
                )
            else:
                checker_result = await self._checker_harness.run(checker_prompt)
        except asyncio.TimeoutError:
            run.error = f"Checker timed out after {self._config.cancel_after}s"
            run.failure_kind = FailureKind.TIMEOUT
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            return
        except Exception as exc:
            run.error = f"Checker error: {exc}"
            run.failure_kind = FailureKind.MODEL_ERROR
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            return

        # ── Outcome: parse APPROVED / REJECTED ───────────────────────────
        # Store Maker metadata (the meaningful work result)
        run.result_text = maker_result.text
        run.result_steps = maker_result.steps
        run.result_stopped = maker_result.stopped
        # Merge findings from both agents
        run.findings = list(maker_result.findings) + list(checker_result.findings)

        checker_text = checker_result.text.upper()
        approved = "APPROVED" in checker_text
        # Explicit REJECTED or no verdict → fail safe
        rejected = "REJECTED" in checker_text or not approved

        async with self._lock:
            if approved and not rejected:
                self._set(run, LoopState.PASS)
                self._iteration = 0
                self._consecutive_failures = 0
                from .. import _CIRCUIT_BREAKER_KEY

                self._store.set(_CIRCUIT_BREAKER_KEY.format(name=self.name), "0")
                return

            # REJECTED — persist to cross-run history, store in context for next Maker round
            self._append_rejection(checker_result.text)
            run.context["checker_feedback"] = checker_result.text
            run.context["maker_output"] = maker_result.text
            run.failure_kind = FailureKind.LOGIC_ERROR
            if run.findings and any(
                getattr(f.severity, "value", f.severity) in ("ERROR", "WARNING")
                for f in run.findings
            ):
                run.failure_kind = FailureKind.DETECTION
            self._set(run, LoopState.FAIL)
            await self._handle_fail(run)

    def _build_maker_prompt(self, context: dict) -> str:
        """Build the Maker's prompt, including Checker feedback and cross-run rejection history."""
        parts = [f"Goal: {self._config.goal}"]

        # Cross-run rejection history — teach the Maker from past sessions
        past_rejections = self._load_rejection_history()
        if past_rejections:
            parts.append(
                f"\n─── Cross-Run Rejection History (Last {len(past_rejections)}) ───────────────\n"
                + "\n".join(f"• {r}" for r in past_rejections)
                + "\n─────────────────────────────────────────────────────────────────────────────\n"
                "Do not repeat these mistakes."
            )

        feedback = context.get("checker_feedback")
        if feedback:
            parts.append(
                f"\n─── Checker Feedback (Round {self._iteration}) ─────────────────────────────\n"
                f"{feedback}\n"
                "─────────────────────────────────────────────────────────────────────────────\n"
                "Address every point above before marking your work complete."
            )
        elif self._iteration > 1:
            parts.append(f"This is attempt {self._iteration}. Previous attempt was rejected.")

        return "\n".join(parts)

    def _append_rejection(self, feedback: str) -> None:
        """Persist a checker REJECTED verdict to the cross-run rejection history."""
        try:
            import json

            key = _REJECTION_HISTORY_KEY.format(name=self.name)
            raw = self._store.get(key)
            history: list[str] = json.loads(raw) if raw else []
            truncated = feedback[:500] + ("…" if len(feedback) > 500 else "")
            history.append(truncated)
            if len(history) > _MAX_REJECTION_HISTORY:
                history = history[-_MAX_REJECTION_HISTORY:]
            self._store.set(key, json.dumps(history))
        except Exception:
            pass  # history write failure must never crash the loop

    def _load_rejection_history(self) -> list[str]:
        """Load cross-run rejection verdicts from the store."""
        try:
            import json

            raw = self._store.get(_REJECTION_HISTORY_KEY.format(name=self.name))
            return json.loads(raw) if raw else []
        except Exception:
            return []

    # Hide base _build_prompt — MakerChecker uses _build_maker_prompt instead
    def _build_prompt(self, context: dict) -> str:
        return self._build_maker_prompt(context)


__all__ = [
    "CISweeper",
    "PRBabysitter",
    "DailyTriage",
    "DependencySweeper",
    "PostMergeCleanup",
    "ChangelogDrafter",
    "MakerChecker",
]
