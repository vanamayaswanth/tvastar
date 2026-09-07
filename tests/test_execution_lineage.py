from __future__ import annotations

import asyncio
import hashlib
from contextlib import aclosing
from dataclasses import FrozenInstanceError

import pytest

from tvastar import Harness, create_agent
from tvastar.execution import (
    ExecutionContext,
    ExecutionQuery,
    ExecutionRecorder,
    execution_key,
    get_execution_context,
    get_execution_lineage,
    new_execution_id,
    outcome_key,
)
from tvastar.memory import FileStore, InMemoryStore, SQLiteStore
from tvastar.model import MockModel
from tvastar.skills import Skill
from tvastar.tools import ToolContext, tool
from tvastar.types import ToolUseBlock


class NoScanStore(InMemoryStore):
    def keys(self, prefix: str = "") -> list[str]:
        raise AssertionError("lineage queries must not scan keys")


class FailingStore(InMemoryStore):
    def set(self, key: str, value) -> None:
        if key.startswith("lineage:"):
            raise OSError("lineage unavailable")
        super().set(key, value)


class OutcomeFailingStore(InMemoryStore):
    def set(self, key: str, value) -> None:
        if ":outcome:" in key:
            raise OSError("outcome unavailable")
        super().set(key, value)


class CountingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.outcome_writes = 0

    def set(self, key: str, value) -> None:
        if ":outcome:" in key:
            self.outcome_writes += 1
        super().set(key, value)


def _agent(script=None, **kwargs):
    return create_agent("lineage-test", model=MockModel(script or ["ok"]), detect=False, **kwargs)


def _envelope(scope: str, execution_id: str, *, spawned_by: str | None = None) -> dict:
    return {
        "schema": "tvastar.execution.envelope",
        "version": 1,
        "scope": scope,
        "execution_id": execution_id,
        "root_execution_id": execution_id,
        "spawned_by": spawned_by,
        "session_ref": f"sha256:{hashlib.sha256(b'session').hexdigest()}",
        "invocation": "prompt",
        "started_at": 1.0,
    }


def _outcome(scope: str, execution_id: str, *, status: str = "completed") -> dict:
    return {
        "schema": "tvastar.execution.outcome",
        "version": 1,
        "scope": scope,
        "execution_id": execution_id,
        "status": status,
        "completed_at": 2.0,
        "duration_ms": 1000.0,
        "attempt_count": 1,
        "attempt_trace": ["attempt_started:1"],
    }


def test_scope_validation_is_fail_fast():
    with pytest.raises(ValueError, match="lineage_scope"):
        Harness(_agent(), lineage_scope="tenant/unsafe")
    assert Harness(_agent(), lineage_scope="tenant.ok-1").lineage_scope == "tenant.ok-1"


def test_mutable_execution_context_is_not_exported_from_root():
    import tvastar

    assert not hasattr(tvastar, "ExecutionContext")
    assert not hasattr(tvastar, "get_execution_context")


async def test_default_off_still_returns_execution_identity():
    store = InMemoryStore()
    result = await Harness(_agent(), store=store, durable=False).run("hello")
    assert result.execution_id.startswith("exec_")
    assert result.root_execution_id == result.execution_id
    assert result.lineage_complete is None
    assert not [key for key in store.keys() if key.startswith("lineage:")]


async def test_prompt_and_skill_each_create_one_execution():
    store = InMemoryStore()
    skill = Skill("review", "", "Review")
    harness = Harness(
        _agent(["prompt", "skill"], skills=[skill]),
        store=store,
        durable=False,
        lineage_scope="tests",
    )
    session = harness.session()
    async with session:
        prompt_result = await session.prompt("one")
        skill_result = await session.skill("review", "two")
    assert prompt_result.execution_id != skill_result.execution_id
    assert prompt_result.lineage_complete is True
    assert skill_result.lineage_complete is True
    assert len(store.keys("lineage:v1:tests:execution:")) == 2
    assert len(store.keys("lineage:v1:tests:outcome:")) == 2


async def test_structured_retries_stay_in_one_execution():
    store = InMemoryStore()
    harness = Harness(
        _agent(["bad", '{"ok": true}']),
        store=store,
        durable=False,
        lineage_scope="retry",
    )
    result = await harness.run("json", result=dict)
    outcome = store.get(outcome_key("retry", result.execution_id))
    assert len(store.keys("lineage:v1:retry:execution:")) == 1
    assert outcome["attempt_count"] == 2
    assert outcome["attempt_trace"] == [
        "attempt_started:1",
        "parse_failed:1",
        "attempt_started:2",
    ]


async def test_recording_failure_never_changes_agent_result():
    result = await Harness(
        _agent(["still works"]),
        store=FailingStore(),
        durable=False,
        lineage_scope="fail",
    ).run("hello")
    assert result.text == "still works"
    assert result.lineage_complete is False


async def test_observability_adds_payload_free_correlation_fields():
    from tvastar.observability import Tracer

    class Capture:
        def __init__(self):
            self.spans = []

        def export(self, span):
            self.spans.append(span)

    capture = Capture()
    result = await Harness(
        _agent(["answer"]),
        durable=False,
        lineage_scope="trace",
        tracer=Tracer([capture]),
    ).run("secret prompt payload")
    correlated = [span for span in capture.spans if "execution_id" in span.attributes]
    assert correlated
    assert all(span.attributes["execution_id"] == result.execution_id for span in correlated)
    assert all(span.attributes["lineage_scope"] == "trace" for span in correlated)
    assert "secret prompt payload" not in repr([span.attributes for span in correlated])


async def test_context_isolated_across_concurrent_prompts():
    seen: list[tuple[str, str]] = []

    class ContextModel(MockModel):
        async def generate(self, messages, **kwargs):
            before = get_execution_context()
            assert before is not None
            with pytest.raises(FrozenInstanceError):
                before.execution_id = "exec_mutation_must_fail"
            await asyncio.sleep(0)
            after = get_execution_context()
            assert after is not None
            seen.append((before.execution_id, after.execution_id))
            return await super().generate(messages, **kwargs)

    harness = Harness(
        create_agent("concurrent", model=ContextModel(), detect=False),
        durable=False,
        lineage_scope="concurrent",
    )
    first, second = await asyncio.gather(
        harness.session("one").prompt("one"),
        harness.session("two").prompt("two"),
    )
    assert first.execution_id != second.execution_id
    assert all(before == after for before, after in seen)
    assert get_execution_context() is None


async def test_context_isolated_across_concurrent_stream_yields():
    """Property: each stream sees only its ID internally and consumers see none."""
    seen: list[tuple[str, str]] = []

    class ContextModel(MockModel):
        async def generate(self, messages, **kwargs):
            before = get_execution_context()
            assert before is not None
            with pytest.raises(FrozenInstanceError):
                before.execution_id = "exec_mutation_must_fail"
            await asyncio.sleep(0)
            after = get_execution_context()
            assert after is not None
            seen.append((before.execution_id, after.execution_id))
            return await super().generate(messages, **kwargs)

    harness = Harness(
        create_agent("concurrent-stream", model=ContextModel(), detect=False),
        durable=False,
        lineage_scope="concurrent-stream",
    )

    async def consume(name: str) -> str:
        execution_id = ""
        async for event in harness.session(name).stream(name):
            assert get_execution_context() is None
            if event.type == "execution_started":
                execution_id = event.data["execution_id"]
        return execution_id

    first, second = await asyncio.gather(consume("one"), consume("two"))
    assert first != second
    assert {before for before, _ in seen} == {first, second}
    assert all(before == after for before, after in seen)
    assert get_execution_context() is None


async def test_task_inherits_root_scope_and_spawned_by():
    store = NoScanStore()

    @tool
    async def delegate(ctx: ToolContext) -> str:
        """Delegate work."""
        child = await ctx.session.task("child")
        return child.execution_id

    model = MockModel(
        [
            ToolUseBlock(name="delegate", input={}, id="call_delegate"),
            "child done",
            "parent done",
        ]
    )
    harness = Harness(
        create_agent("delegate", model=model, tools=[delegate], detect=False),
        store=store,
        durable=False,
        lineage_scope="tasks",
    )
    parent = await harness.run("delegate")
    execution_keys = list(store._data)
    child_id = next(
        value["execution_id"]
        for key, value in store._data.items()
        if ":execution:" in key and value["execution_id"] != parent.execution_id
    )
    lineage = get_execution_lineage(store, scope="tasks", execution_id=child_id)
    assert [record.execution_id for record in lineage.executions] == [
        child_id,
        parent.execution_id,
    ]
    assert lineage.executions[0].spawned_by == parent.execution_id
    assert lineage.executions[0].root_execution_id == parent.execution_id
    assert execution_keys


async def test_nested_harness_starts_independent_root_while_task_propagates():
    store = InMemoryStore()
    inner_results = []
    inner_harness = Harness(
        _agent(["inner done"]),
        store=store,
        durable=False,
        lineage_scope="inner",
    )

    @tool
    async def invoke_other_harness(ctx: ToolContext) -> str:
        """Invoke an unrelated harness."""
        result = await inner_harness.run("inner")
        inner_results.append(result)
        return result.text

    outer = await Harness(
        _agent(
            [
                ToolUseBlock(name="invoke_other_harness", input={}, id="call_inner"),
                "outer done",
            ],
            tools=[invoke_other_harness],
        ),
        store=store,
        durable=False,
        lineage_scope="outer",
    ).run("outer")

    assert len(inner_results) == 1
    inner = inner_results[0]
    assert inner.root_execution_id == inner.execution_id
    assert inner.lineage_complete is True
    inner_envelope = ExecutionQuery(store, "inner").get(inner.execution_id).envelope
    assert inner_envelope is not None
    assert inner_envelope.spawned_by is None
    assert inner_envelope.scope == "inner"
    assert outer.root_execution_id == outer.execution_id


async def test_stream_is_lazy_and_aclose_records_cancelled():
    store = InMemoryStore()
    session = Harness(
        _agent(["done"]), store=store, durable=False, lineage_scope="streams"
    ).session()
    stream = session.stream("hello")
    assert store.keys() == []
    started = await anext(stream)
    assert started.type == "execution_started"
    assert "lineage_scope" not in started.data
    assert get_execution_context() is None
    await stream.aclose()
    outcome = store.get(outcome_key("streams", started.data["execution_id"]))
    assert outcome["status"] == "cancelled"


async def test_stream_finalizes_before_result_and_close_does_not_overwrite():
    store = InMemoryStore()
    session = Harness(
        _agent(["done"]), store=store, durable=False, lineage_scope="streams"
    ).session()
    stream = session.stream("hello")
    result_event = None
    async for event in stream:
        assert get_execution_context() is None
        if event.type == "result":
            result_event = event
            break
    assert result_event is not None
    execution_id = result_event.data["execution_id"]
    assert result_event.data["lineage_complete"] is True
    assert store.get(outcome_key("streams", execution_id))["status"] == "completed"
    await stream.aclose()
    assert store.get(outcome_key("streams", execution_id))["status"] == "completed"


async def test_stream_timeout_records_timed_out():
    store = InMemoryStore()
    session = Harness(
        _agent([TimeoutError("slow")]),
        store=store,
        durable=False,
        lineage_scope="streams",
    ).session()
    execution_id = None
    with pytest.raises(TimeoutError):
        async with aclosing(session.stream("hello")) as stream:
            async for event in stream:
                if event.type == "execution_started":
                    execution_id = event.data["execution_id"]
    assert store.get(outcome_key("streams", execution_id))["status"] == "timed_out"


def test_query_uses_direct_keys_and_reports_missing_corrupt_unknown_cross_scope_and_cycle():
    store = NoScanStore()
    scope = "query"

    missing = get_execution_lineage(store, scope=scope, execution_id=new_execution_id())
    assert [diagnostic.code for diagnostic in missing.diagnostics] == ["missing"]

    corrupt_id = new_execution_id()
    store.set(execution_key(scope, corrupt_id), "bad")
    assert (
        get_execution_lineage(store, scope=scope, execution_id=corrupt_id).diagnostics[0].code
        == "corrupt"
    )

    unknown_id = new_execution_id()
    unknown = _envelope(scope, unknown_id)
    unknown["version"] = 2
    store.set(execution_key(scope, unknown_id), unknown)
    assert (
        get_execution_lineage(store, scope=scope, execution_id=unknown_id).diagnostics[0].code
        == "unknown"
    )

    unknown_schema_id = new_execution_id()
    unknown_schema = _envelope(scope, unknown_schema_id)
    unknown_schema["schema"] = "tvastar.execution.other"
    store.set(execution_key(scope, unknown_schema_id), unknown_schema)
    assert ExecutionQuery(store, scope).get(unknown_schema_id).diagnostics[0].code == "unknown"

    unknown_outcome_id = new_execution_id()
    store.set(execution_key(scope, unknown_outcome_id), _envelope(scope, unknown_outcome_id))
    unknown_outcome = _outcome(scope, unknown_outcome_id)
    unknown_outcome["schema"] = "tvastar.execution.other"
    store.set(outcome_key(scope, unknown_outcome_id), unknown_outcome)
    assert ExecutionQuery(store, scope).get(unknown_outcome_id).diagnostics[0].code == "unknown"

    cross_id = new_execution_id()
    store.set(execution_key(scope, cross_id), _envelope("other", cross_id))
    assert (
        get_execution_lineage(store, scope=scope, execution_id=cross_id).diagnostics[0].code
        == "cross_scope"
    )

    first, second = new_execution_id(), new_execution_id()
    store.set(execution_key(scope, first), _envelope(scope, first, spawned_by=second))
    store.set(execution_key(scope, second), _envelope(scope, second, spawned_by=first))
    cycle = get_execution_lineage(store, scope=scope, execution_id=first)
    assert "cycle" in [diagnostic.code for diagnostic in cycle.diagnostics]


async def test_conversation_compaction_does_not_change_lineage():
    store = InMemoryStore()
    harness = Harness(
        _agent(["done"]),
        store=store,
        compaction_threshold=1,
        lineage_scope="compact",
    )
    result = await harness.run("compact")
    assert len(store.get(f"event_log:{result.conversation_id}")) == 1
    lineage = get_execution_lineage(store, scope="compact", execution_id=result.execution_id)
    assert lineage.executions[0].execution_id == result.execution_id
    assert lineage.outcomes[result.execution_id].status == "completed"


@pytest.mark.parametrize("backend", ["file", "sqlite"])
def test_lineage_survives_store_restart(tmp_path, backend):
    scope = "restart"
    execution_id = new_execution_id()
    if backend == "file":
        path = tmp_path / "state"
        first = FileStore(path)
    else:
        path = tmp_path / "state.db"
        first = SQLiteStore(path)
    recorder = ExecutionRecorder(first, scope)
    from tvastar.execution import ExecutionContext

    context = ExecutionContext(execution_id, execution_id, scope, attempt_count=1)
    assert recorder.start(context, session_id="session", invocation="prompt")
    assert recorder.finish(context, status="completed")
    if backend == "sqlite":
        first._conn.close()
    second = FileStore(path) if backend == "file" else SQLiteStore(path)
    lineage = get_execution_lineage(second, scope=scope, execution_id=execution_id)
    assert lineage.executions[0].execution_id == execution_id
    assert lineage.outcomes[execution_id].status == "completed"
    if backend == "sqlite":
        second._conn.close()


async def test_envelope_uses_pseudonymous_session_ref_and_exact_schemas():
    raw_session_id = "caller-session-secret"
    store = InMemoryStore()
    result = await Harness(
        _agent(["done"]), store=store, durable=False, lineage_scope="privacy"
    ).run("hello", session_id=raw_session_id)

    envelope = store.get(execution_key("privacy", result.execution_id))
    outcome = store.get(outcome_key("privacy", result.execution_id))
    assert envelope["schema"] == "tvastar.execution.envelope"
    assert outcome["schema"] == "tvastar.execution.outcome"
    assert envelope["session_ref"] == (
        f"sha256:{hashlib.sha256(raw_session_id.encode('utf-8')).hexdigest()}"
    )
    assert "session_id" not in envelope
    assert raw_session_id not in repr(envelope)
    assert outcome["duration_ms"] >= 0


async def test_outcome_only_write_failure_marks_result_and_view_incomplete():
    store = OutcomeFailingStore()
    result = await Harness(
        _agent(["done"]), store=store, durable=False, lineage_scope="partial"
    ).run("hello")

    assert result.lineage_complete is False
    view = ExecutionQuery(store, "partial").get(result.execution_id)
    assert view.envelope is not None
    assert view.outcome is None
    assert view.complete is False
    assert [(item.code, item.detail) for item in view.diagnostics] == [("missing", "outcome")]


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (RuntimeError("failed"), "failed"),
        (TimeoutError("slow"), "timed_out"),
        (asyncio.CancelledError(), "cancelled"),
    ],
)
async def test_prompt_exceptions_persist_terminal_outcomes(exc, status):
    store = InMemoryStore()
    harness = Harness(_agent([exc]), store=store, durable=False, lineage_scope="prompt-errors")

    with pytest.raises(type(exc)):
        await harness.run("hello")

    outcomes = [value for key, value in store._data.items() if ":outcome:" in key]
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == status
    assert outcomes[0]["duration_ms"] >= 0


def test_execution_query_get_is_direct_and_ancestors_is_bounded():
    store = NoScanStore()
    scope = "query-api"
    child, parent, root = new_execution_id(), new_execution_id(), new_execution_id()
    for execution_id, spawned_by in ((child, parent), (parent, root), (root, None)):
        store.set(
            execution_key(scope, execution_id),
            _envelope(scope, execution_id, spawned_by=spawned_by),
        )
        store.set(outcome_key(scope, execution_id), _outcome(scope, execution_id))

    query = ExecutionQuery(store, scope)
    view = query.get(child)
    assert view.complete is True
    assert view.envelope.execution_id == child

    depth_limited = query.ancestors(child, max_depth=1)
    assert [item.execution_id for item in depth_limited.executions] == [child]
    assert depth_limited.complete is False
    assert depth_limited.diagnostics[-1].code == "max_depth"

    visited_limited = query.ancestors(child, max_visited=1)
    assert [item.execution_id for item in visited_limited.executions] == [child]
    assert visited_limited.diagnostics[-1].code == "max_visited"

    complete = query.ancestors(child)
    assert [item.execution_id for item in complete.executions] == [child, parent, root]
    assert complete.complete is True


def test_duration_uses_monotonic_runtime_when_wall_clock_moves_backward(monkeypatch):
    import tvastar.execution as execution

    store = InMemoryStore()
    execution_id = new_execution_id()
    context = ExecutionContext(
        execution_id,
        execution_id,
        "timing",
        started_at=100.0,
        started_monotonic=10.0,
    )
    recorder = ExecutionRecorder(store, "timing")
    assert recorder.start(context, session_id="session", invocation="prompt")
    monkeypatch.setattr(execution.time, "time", lambda: 50.0)
    monkeypatch.setattr(execution.time, "monotonic", lambda: 10.25)

    assert recorder.finish(context, status="completed")
    outcome = store.get(outcome_key("timing", execution_id))
    assert outcome["completed_at"] == 50.0
    assert outcome["duration_ms"] == pytest.approx(250.0)


async def test_stream_athrow_and_followup_close_write_one_failed_outcome():
    store = CountingStore()
    session = Harness(
        _agent(["done"]), store=store, durable=False, lineage_scope="stream-throw"
    ).session()
    stream = session.stream("hello")
    started = await anext(stream)
    assert (await anext(stream)).type == "turn_start"

    with pytest.raises(RuntimeError, match="consumer stopped"):
        await stream.athrow(RuntimeError("consumer stopped"))
    await stream.aclose()

    outcome = store.get(outcome_key("stream-throw", started.data["execution_id"]))
    assert outcome["status"] == "failed"
    assert store.outcome_writes == 1
