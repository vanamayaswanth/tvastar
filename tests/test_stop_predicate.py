"""Tests for stop_predicate in the Session loop.

Validates Requirements 15.2, 15.3, 15.4:
- When stop_predicate returns True, loop ends with stopped="predicate"
- When stop_predicate is not configured or returns False, loop continues normally
- When stop_predicate raises an exception, loop continues (treat as False)
"""

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel


class TestStopPredicateReturnsTrue:
    """When stop_predicate returns True, loop terminates with stopped='predicate'."""

    @pytest.mark.asyncio
    async def test_immediate_stop(self):
        """Predicate returning True on first step stops immediately."""
        model = MockModel(["Hello, world!"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            stop_predicate=lambda result: True,
        )
        h = Harness(agent)
        async with h.session() as sess:
            result = await sess.prompt("hi")

        assert result.stopped == "predicate"
        assert result.steps == 1

    @pytest.mark.asyncio
    async def test_stop_on_second_step(self):
        """Predicate returning True on step 2 lets step 1 run tools."""
        call_count = 0

        def stop_on_second(result):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        from tvastar.tools.base import tool as tool_decorator
        from tvastar.types import ToolUseBlock

        @tool_decorator
        def noop() -> str:
            """A no-op tool."""
            return "ok"

        model = MockModel(
            [
                ToolUseBlock(name="noop", input={}, id="t1"),
                "Final response",
            ]
        )
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            tools=[noop],
            stop_predicate=stop_on_second,
        )
        h = Harness(agent)
        async with h.session() as sess:
            result = await sess.prompt("hi")

        assert result.stopped == "predicate"
        assert result.steps == 2


class TestStopPredicateReturnsFalse:
    """When stop_predicate returns False, loop continues normally."""

    @pytest.mark.asyncio
    async def test_false_continues_to_end_turn(self):
        """Predicate returning False lets loop finish with end_turn."""
        model = MockModel(["done"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            stop_predicate=lambda result: False,
        )
        h = Harness(agent)
        async with h.session() as sess:
            result = await sess.prompt("hi")

        assert result.stopped == "end_turn"


class TestStopPredicateNone:
    """When stop_predicate is not configured, loop continues normally."""

    @pytest.mark.asyncio
    async def test_none_continues_to_end_turn(self):
        """No predicate configured: normal end_turn."""
        model = MockModel(["done"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
        )
        h = Harness(agent)
        async with h.session() as sess:
            result = await sess.prompt("hi")

        assert result.stopped == "end_turn"


class TestStopPredicateException:
    """When stop_predicate raises, loop continues (treat as False)."""

    @pytest.mark.asyncio
    async def test_exception_swallowed_loop_continues(self):
        """Predicate that raises does not break the run."""

        def bad_predicate(result):
            raise ValueError("predicate exploded")

        model = MockModel(["done"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            stop_predicate=bad_predicate,
        )
        h = Harness(agent)
        async with h.session() as sess:
            with pytest.warns(UserWarning, match="stop_predicate raised"):
                result = await sess.prompt("hi")

        assert result.stopped == "end_turn"
        assert result.text == "done"

    @pytest.mark.asyncio
    async def test_exception_emits_warning(self):
        """Predicate exception emits a UserWarning."""

        def exploding_predicate(result):
            raise RuntimeError("boom")

        model = MockModel(["response"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            stop_predicate=exploding_predicate,
        )
        h = Harness(agent)
        async with h.session() as sess:
            with pytest.warns(UserWarning, match="stop_predicate raised"):
                await sess.prompt("hi")


class TestStopPredicateReceivesRunResult:
    """The predicate receives a RunResult-in-progress with current state."""

    @pytest.mark.asyncio
    async def test_receives_partial_result(self):
        """Predicate receives a RunResult with text, usage, steps."""
        received = []

        def capture_predicate(result):
            received.append(result)
            return True  # stop immediately to inspect

        model = MockModel(["captured text"])
        agent = create_agent(
            "stop-test",
            model=model,
            instructions="be helpful",
            stop_predicate=capture_predicate,
        )
        h = Harness(agent)
        async with h.session() as sess:
            result = await sess.prompt("hi")

        assert len(received) == 1
        partial = received[0]
        assert partial.text == "captured text"
        assert partial.steps == 1
        assert partial.stopped == "in_progress"
        assert partial.usage is not None
