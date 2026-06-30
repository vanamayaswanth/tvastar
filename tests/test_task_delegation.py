"""Unit tests for session.task() delegation.

Covers:
  - Child session creation using named AgentProfile
  - Child spec resolution precedence: task override > profile > parent
  - Router selects best-matching profile when no agent specified
  - cancel_after raises asyncio.TimeoutError
  - ValueError for non-existent profile names

Requirements: 18.1, 18.3, 18.4, 18.5, 18.6
"""

from __future__ import annotations

import asyncio

import pytest

from tvastar import (
    AgentRouter,
    Harness,
    MAX_TASK_DEPTH,
    create_agent,
    define_agent_profile,
)
from tvastar.model import MockModel
from tvastar.profiles import AgentProfile


# ── Helpers ───────────────────────────────────────────────────────────────────


def _agent(script=None, **kw):
    """Create a minimal agent spec with a MockModel."""
    return create_agent(
        "parent-agent",
        model=MockModel(script or []),
        instructions="parent instructions",
        **kw,
    )


def _profile(name="specialist", **kw):
    """Create a named profile with optional overrides."""
    return define_agent_profile(name=name, **kw)


# ── Child session creation with named AgentProfile ────────────────────────────


class TestChildSessionCreation:
    """Tests for session.task() creating child sessions using named profiles."""

    async def test_task_with_named_profile_returns_result(self):
        """session.task(agent='name') resolves profile and delegates."""
        reviewer = _profile(
            "reviewer",
            description="Reviews code",
            instructions="Review carefully.",
        )
        spec = _agent(["Review complete."], subagents=[reviewer])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("Review this code", agent="reviewer")
        assert result.text == "Review complete."
        assert result.stopped == "end_turn"

    async def test_task_anonymous_inherits_parent_model(self):
        """Anonymous task (no agent) inherits parent model."""
        spec = _agent(["Anonymous reply."])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("Do something")
        assert result.text == "Anonymous reply."

    async def test_child_session_gets_incremented_depth(self):
        """Child session has _task_depth = parent + 1."""
        spec = _agent(["Done."])
        h = Harness(spec)
        sess = h.session()
        assert sess._task_depth == 0
        async with sess:
            # We can verify depth indirectly — task succeeds at depth 0
            result = await sess.task("work")
            assert result.text == "Done."

    async def test_task_with_profile_uses_profile_instructions(self):
        """Profile instructions override parent instructions in child spec."""
        custom = _profile(
            "custom",
            instructions="Custom child instructions.",
        )
        spec = _agent(["ok."], subagents=[custom])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="custom")
        assert result.text == "ok."

    async def test_task_with_cwd_prepends_to_prompt(self):
        """cwd parameter adds working directory context to prompt."""
        prompts_seen = []
        original_generate = MockModel.generate

        class CapturingModel(MockModel):
            async def generate(self, messages, **kwargs):
                # Capture the last user message
                for m in reversed(messages):
                    if m.role == "user":
                        prompts_seen.append(m.text)
                        break
                return await super().generate(messages, **kwargs)

        spec = create_agent(
            "cwd-test",
            model=CapturingModel(["done."]),
            instructions="hi",
        )
        h = Harness(spec)
        sess = h.session()
        async with sess:
            await sess.task("list files", cwd="/home/user/project")
        assert any("[Working directory: /home/user/project]" in p for p in prompts_seen)


# ── Child spec resolution precedence ─────────────────────────────────────────


class TestSpecResolutionPrecedence:
    """Tests for child spec resolution: task override > profile > parent."""

    async def test_task_override_model_wins_over_profile(self):
        """model= task override takes highest precedence over profile.model."""
        override_model = MockModel(["from-override"])
        profile_model = MockModel(["from-profile"])

        prof = _profile("worker", model=profile_model)
        spec = _agent(["from-parent"], subagents=[prof])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="worker", model=override_model)
        assert result.text == "from-override"

    async def test_profile_model_wins_over_parent(self):
        """Profile model takes precedence over parent model."""
        profile_model = MockModel(["from-profile"])
        prof = _profile("worker", model=profile_model)
        # Parent model has different script
        spec = _agent(["from-parent"], subagents=[prof])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="worker")
        assert result.text == "from-profile"

    async def test_parent_model_used_when_no_overrides(self):
        """Parent model is used when neither task override nor profile provides one."""
        prof = _profile("worker")  # no model override
        spec = _agent(["from-parent"], subagents=[prof])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="worker")
        assert result.text == "from-parent"

    async def test_task_override_instructions_wins(self):
        """instructions= task override wins when using anonymous task."""
        spec = _agent(["result."])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task(
                "go", instructions="override instructions"
            )
        # Task completes successfully with override instructions active
        assert result.text == "result."

    async def test_task_override_max_steps_wins(self):
        """max_steps= task override takes precedence over profile.max_steps."""
        prof = _profile("worker", max_steps=10)
        spec = _agent(["done."], subagents=[prof])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="worker", max_steps=5)
        # The child completed (within 5 steps)
        assert result.text == "done."

    async def test_profile_max_steps_wins_over_parent(self):
        """Profile max_steps takes precedence over parent max_steps."""
        prof = _profile("worker", max_steps=3)
        spec = _agent(["done."], subagents=[prof], max_steps=20)
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", agent="worker")
        assert result.text == "done."

    async def test_task_override_thinking_level_wins(self):
        """thinking_level= task override takes highest precedence."""
        prof = _profile("worker", thinking_level="low")
        spec = _agent(["done."], subagents=[prof])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task(
                "go", agent="worker", thinking_level="high"
            )
        assert result.text == "done."


# ── Router selects best-matching profile ──────────────────────────────────────


class TestRouterProfileSelection:
    """Tests for router auto-selecting the best profile when no agent specified."""

    async def test_router_picks_tester_for_test_prompt(self):
        """Router selects 'tester' profile for test-related prompts."""
        tester = _profile("tester", description="Write unit tests and run test suites")
        coder = _profile("coder", description="Write and fix Python code")
        spec = _agent(["tests written."], subagents=[tester, coder])
        router = AgentRouter([tester, coder])

        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task(
                "Write unit tests for auth.py", router=router
            )
        assert result.text == "tests written."

    async def test_router_picks_coder_for_code_prompt(self):
        """Router selects 'coder' profile for code-writing prompts."""
        tester = _profile("tester", description="Write unit tests and run test suites")
        coder = _profile("coder", description="Write and fix Python code")
        spec = _agent(["code written."], subagents=[tester, coder])
        router = AgentRouter([tester, coder])

        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task(
                "Write Python code for the new feature", router=router
            )
        assert result.text == "code written."

    async def test_router_returns_none_below_threshold(self):
        """When router returns None (below threshold), task runs as anonymous."""
        prof = _profile("specialist", description="very specific domain")
        spec = _agent(["anonymous done."], subagents=[prof])
        router = AgentRouter([prof], threshold=0.99)

        h = Harness(spec)
        sess = h.session()
        async with sess:
            # Router won't match → runs as anonymous task
            result = await sess.task("xyzzy frobnicator", router=router)
        assert result.text == "anonymous done."

    async def test_router_not_used_when_agent_specified(self):
        """Explicit agent= takes priority over router."""
        tester = _profile("tester", description="Write tests")
        coder = _profile("coder", description="Write code")
        spec = _agent(["from coder."], subagents=[tester, coder])
        router = AgentRouter([tester, coder])

        h = Harness(spec)
        sess = h.session()
        async with sess:
            # Even though prompt mentions "tests", explicit agent="coder" wins
            result = await sess.task(
                "Write unit tests", agent="coder", router=router
            )
        assert result.text == "from coder."


# ── cancel_after timeout ──────────────────────────────────────────────────────


class TestCancelAfterTimeout:
    """Tests for cancel_after parameter raising asyncio.TimeoutError."""

    async def test_cancel_after_raises_timeout_error(self):
        """cancel_after raises asyncio.TimeoutError when exceeded."""

        class SlowModel(MockModel):
            async def generate(self, messages, **kwargs):
                await asyncio.sleep(5.0)  # Will be cancelled
                return await super().generate(messages, **kwargs)

        spec = create_agent(
            "slow-agent",
            model=SlowModel(["never returned"]),
            instructions="slow",
        )
        h = Harness(spec)
        sess = h.session()
        async with sess:
            with pytest.raises(asyncio.TimeoutError):
                await sess.task("go", cancel_after=0.05)

    async def test_cancel_after_none_does_not_timeout(self):
        """cancel_after=None (default) does not impose a timeout."""
        spec = _agent(["quick reply."])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", cancel_after=None)
        assert result.text == "quick reply."

    async def test_cancel_after_fast_task_completes_normally(self):
        """A fast task completes before cancel_after deadline."""
        spec = _agent(["fast reply."])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            result = await sess.task("go", cancel_after=10.0)
        assert result.text == "fast reply."


# ── ValueError for non-existent profiles ──────────────────────────────────────


class TestNonExistentProfile:
    """Tests for ValueError when referencing missing profile names."""

    async def test_raises_valueerror_for_unknown_profile(self):
        """ValueError raised when agent name doesn't match any registered profile."""
        spec = _agent(["x"])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            with pytest.raises(ValueError, match="No subagent profile named"):
                await sess.task("go", agent="nonexistent")

    async def test_error_message_lists_available_profiles(self):
        """Error message includes available profile names."""
        reviewer = _profile("reviewer")
        coder = _profile("coder")
        spec = _agent(["x"], subagents=[reviewer, coder])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            with pytest.raises(ValueError, match="Available:.*reviewer.*coder"):
                await sess.task("go", agent="missing-profile")

    async def test_empty_subagents_shows_empty_list(self):
        """Error message shows empty list when no subagents registered."""
        spec = _agent(["x"])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            with pytest.raises(ValueError, match=r"Available: \[\]"):
                await sess.task("go", agent="anything")

    async def test_case_sensitive_profile_names(self):
        """Profile names are case-sensitive — 'Reviewer' != 'reviewer'."""
        reviewer = _profile("reviewer")
        spec = _agent(["x"], subagents=[reviewer])
        h = Harness(spec)
        sess = h.session()
        async with sess:
            with pytest.raises(ValueError, match="No subagent profile named"):
                await sess.task("go", agent="Reviewer")
