"""Backward compatibility verification (Task 8.1).

Confirms the public API surface is unchanged:
- `from tvastar import create_agent, Harness` succeeds
- `Harness(spec)` creates with durable=True by default (InMemoryStore)
- `Harness(spec, durable=False)` creates without writer
- `harness.run("hello")` returns a RunResult
- `Session.prompt()`, `Session.task()`, `Session.skill()` signatures intact

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
"""

import pytest

from tvastar import create_agent, Harness
from tvastar.memory.store import InMemoryStore
from tvastar.session import RunResult, Session
from tvastar.types import Message, ModelResponse, StopReason, Usage


class _EchoModel:
    """Minimal model that echoes back for backward compat tests."""

    name = "echo"

    async def generate(self, messages, **kw):
        return ModelResponse(
            message=Message("assistant", "echo"),
            usage=Usage(input_tokens=1, output_tokens=1),
            stop_reason=StopReason.END_TURN,
        )


class TestImportPaths:
    def test_core_imports_valid(self):
        """from tvastar import create_agent, Harness succeeds."""
        from tvastar import create_agent, Harness

        assert callable(create_agent)
        assert callable(Harness)


class TestHarnessDefaults:
    def test_harness_defaults_to_durable(self):
        """Harness(spec) creates with durable=True and InMemoryStore."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec)
        assert h._durable is True
        assert isinstance(h.store, InMemoryStore)

    def test_harness_durable_false_disables_writer(self):
        """Harness(spec, durable=False) → session._writer stays None."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec, durable=False)
        assert h._durable is False
        s = h.session(name="no-dur")
        # Writer is None before start (always) and after start (because not durable)
        assert s._writer is None

    @pytest.mark.asyncio
    async def test_harness_durable_false_writer_none_after_start(self):
        """After start(), writer remains None when durable=False."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec, durable=False)
        s = h.session(name="nd2")
        async with s:
            assert s._writer is None


class TestRunResult:
    @pytest.mark.asyncio
    async def test_harness_run_returns_run_result(self):
        """harness.run('hello') returns a RunResult."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec)
        result = await h.run("hello")
        assert isinstance(result, RunResult)
        assert result.text == "echo"

    @pytest.mark.asyncio
    async def test_run_result_has_expected_fields(self):
        """RunResult exposes text, messages, usage, steps, stopped."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec)
        result = await h.run("hello")
        assert hasattr(result, "text")
        assert hasattr(result, "messages")
        assert hasattr(result, "usage")
        assert hasattr(result, "steps")
        assert hasattr(result, "stopped")


class TestSessionSignatures:
    @pytest.mark.asyncio
    async def test_session_prompt_signature(self):
        """Session.prompt(text) works with positional text arg."""
        spec = create_agent("compat", model=_EchoModel(), instructions="hi")
        h = Harness(spec)
        s = h.session(name="sig-test")
        async with s:
            result = await s.prompt("hello")
        assert isinstance(result, RunResult)

    def test_session_task_is_callable(self):
        """Session.task exists and is a coroutine function."""
        import inspect

        assert inspect.iscoroutinefunction(Session.task)

    def test_session_skill_is_callable(self):
        """Session.skill exists and is a coroutine function."""
        import inspect

        assert inspect.iscoroutinefunction(Session.skill)
