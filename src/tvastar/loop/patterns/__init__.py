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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model.base import Model
    from ..handoff import HandoffPolicy

from ...tools import default_toolset
from .. import Loop, LoopConfig

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


__all__ = [
    "CISweeper",
    "PRBabysitter",
    "DailyTriage",
    "DependencySweeper",
    "PostMergeCleanup",
    "ChangelogDrafter",
]
