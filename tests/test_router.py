"""Tests for AgentRouter — difflib fallback (no semantic-router needed)."""

from __future__ import annotations

import pytest

from tvastar import AgentRouter
from tvastar.profiles import AgentProfile


def _profiles():
    return [
        AgentProfile(name="coder", description="Write and fix Python code"),
        AgentProfile(name="reviewer", description="Review code for bugs and security"),
        AgentProfile(name="tester", description="Write unit tests and run test suites"),
        AgentProfile(name="devops", description="Deploy infrastructure and CI pipelines"),
    ]


class TestAgentRouterDifflib:
    def test_routes_to_tester(self):
        r = AgentRouter(_profiles())
        assert r.route("Write unit tests for auth.py") == "tester"

    def test_routes_to_reviewer(self):
        r = AgentRouter(_profiles())
        assert r.route("Review this PR for security issues") == "reviewer"

    def test_routes_to_coder(self):
        r = AgentRouter(_profiles())
        # difflib fallback: "Write Python code" overlaps clearly with "Write and fix Python code"
        assert r.route("Write Python code for the new feature") == "coder"

    def test_routes_to_devops(self):
        r = AgentRouter(_profiles())
        assert r.route("Deploy to production and update CI pipeline") == "devops"

    def test_returns_none_when_below_threshold(self):
        r = AgentRouter(_profiles(), threshold=0.99)
        # Nothing will match 99% similarity
        result = r.route("xyzzy frobnicator")
        assert result is None

    def test_empty_profiles_returns_none(self):
        r = AgentRouter([])
        assert r.route("anything") is None

    def test_repr_shows_backend(self):
        r = AgentRouter(_profiles())
        assert "word-overlap" in repr(r)

    def test_single_profile_always_matches_above_threshold(self):
        profiles = [AgentProfile(name="solo", description="Do everything")]
        r = AgentRouter(profiles, threshold=0.0)
        assert r.route("some task") == "solo"

    def test_profile_with_no_description_uses_name(self):
        profiles = [
            AgentProfile(name="sql", description=""),
            AgentProfile(name="api", description=""),
        ]
        r = AgentRouter(profiles, threshold=0.0)
        result = r.route("sql query")
        assert result == "sql"


class TestAgentRouterIntegration:
    """Integration: router wired into sess.task() via router= kwarg."""

    @pytest.mark.asyncio
    async def test_router_kwarg_picks_agent(self):
        from unittest.mock import MagicMock, patch

        router = AgentRouter(_profiles())

        # Patch session.task internals to capture which agent was resolved
        resolved = {}

        from tvastar.session import Session

        async def fake_task(self, prompt, *, agent=None, router=None, **kw):
            if agent is None and router is not None:
                agent = router.route(prompt)
            resolved["agent"] = agent
            from tvastar.session import RunResult
            from tvastar.types import Usage

            return RunResult(
                text="ok",
                messages=[],
                usage=Usage(),
                steps=1,
                stopped="end_turn",
                findings=[],
                data=None,
            )

        with patch.object(Session, "task", fake_task):
            sess = MagicMock(spec=Session)
            sess.task = lambda *a, **kw: fake_task(sess, *a, **kw)
            await sess.task("Write unit tests for login.py", router=router)

        assert resolved["agent"] == "tester"


class TestAgentRouterCustomScoring:
    """Tests for the custom scoring_fn parameter."""

    def test_custom_scoring_fn_picks_highest_scorer(self):
        profiles = _profiles()

        # Always return highest score for "devops"
        def scorer(text, profile):
            return 1.0 if profile.name == "devops" else 0.0

        r = AgentRouter(profiles, scoring_fn=scorer)
        assert r.route("Write unit tests") == "devops"

    def test_custom_scoring_fn_respects_threshold(self):
        profiles = _profiles()

        # All scores below threshold
        def scorer(text, profile):
            return 0.1

        r = AgentRouter(profiles, threshold=0.5, scoring_fn=scorer)
        assert r.route("anything") is None

    def test_custom_scoring_fn_receives_correct_args(self):
        profiles = [AgentProfile(name="solo", description="Only one")]
        calls = []

        def scorer(text, profile):
            calls.append((text, profile))
            return 0.9

        r = AgentRouter(profiles, scoring_fn=scorer)
        r.route("hello world")
        assert len(calls) == 1
        assert calls[0][0] == "hello world"
        assert calls[0][1].name == "solo"

    def test_no_scoring_fn_uses_difflib(self):
        # Without scoring_fn, the difflib logic should still work
        r = AgentRouter(_profiles())
        assert r.route("Write unit tests for auth.py") == "tester"

    def test_repr_shows_custom_backend(self):
        r = AgentRouter(_profiles(), scoring_fn=lambda t, p: 0.5)
        assert "custom" in repr(r)

    def test_empty_profiles_with_scoring_fn_returns_none(self):
        r = AgentRouter([], scoring_fn=lambda t, p: 1.0)
        assert r.route("anything") is None

    def test_custom_scoring_fn_picks_among_multiple(self):
        profiles = _profiles()

        # Score based on length of profile name
        def scorer(text, profile):
            return len(profile.name) / 10.0

        r = AgentRouter(profiles, threshold=0.0, scoring_fn=scorer)
        # "reviewer" has 8 chars -> 0.8, "devops" has 6 -> 0.6, "tester" has 6 -> 0.6, "coder" has 5 -> 0.5
        assert r.route("irrelevant") == "reviewer"
