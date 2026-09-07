from __future__ import annotations

import hashlib
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar.execution import (
    ExecutionContext,
    ExecutionRecorder,
    execution_key,
    get_execution_lineage,
    new_execution_id,
    validate_lineage_scope,
)
from tvastar.memory import InMemoryStore


_SCOPE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"


@given(st.text(alphabet=_SCOPE_CHARS, min_size=1, max_size=128))
def test_valid_scope_round_trips(scope):
    assert validate_lineage_scope(scope) == scope


@given(st.integers(min_value=1, max_value=20))
@settings(max_examples=20)
def test_ancestor_traversal_is_bounded_and_ordered(length):
    store = InMemoryStore()
    scope = "property"
    ids = [new_execution_id() for _ in range(length)]
    for index, execution_id in enumerate(ids):
        parent = ids[index + 1] if index + 1 < len(ids) else None
        store.set(
            execution_key(scope, execution_id),
            {
                "schema": "tvastar.execution.envelope",
                "version": 1,
                "scope": scope,
                "execution_id": execution_id,
                "root_execution_id": ids[-1],
                "spawned_by": parent,
                "session_ref": f"sha256:{hashlib.sha256(b'session').hexdigest()}",
                "invocation": "prompt",
                "started_at": float(index),
            },
        )
    lineage = get_execution_lineage(store, scope=scope, execution_id=ids[0])
    assert [record.execution_id for record in lineage.executions] == ids
    assert len(lineage.executions) <= 20


@given(st.sampled_from(["completed", "failed", "cancelled", "timed_out"]))
def test_create_once_records_are_immutable(status):
    store = InMemoryStore()
    scope = "property"
    execution_id = new_execution_id()
    context = ExecutionContext(execution_id, execution_id, scope, attempt_count=1)
    recorder = ExecutionRecorder(store, scope)
    assert recorder.start(context, session_id="session", invocation="prompt")
    assert recorder.start(context, session_id="session", invocation="prompt")
    assert not recorder.start(context, session_id="other", invocation="skill")
    assert recorder.finish(context, status=status)
    assert recorder.finish(context, status=status)
    conflicting_status = "failed" if status != "failed" else "completed"
    assert not recorder.finish(context, status=conflicting_status)
    lineage = get_execution_lineage(store, scope=scope, execution_id=execution_id)
    assert lineage.outcomes[execution_id].status == status


@given(st.sampled_from(["completed", "failed", "cancelled", "timed_out"]))
def test_create_once_accepts_byte_equivalent_replay(status):
    class ByteReadbackStore(InMemoryStore):
        def get(self, key):
            value = super().get(key)
            return None if value is None else json.dumps(value, sort_keys=True).encode()

    store = ByteReadbackStore()
    scope = "property"
    execution_id = new_execution_id()
    context = ExecutionContext(execution_id, execution_id, scope, attempt_count=1)
    recorder = ExecutionRecorder(store, scope)
    assert recorder.start(context, session_id="session", invocation="prompt")
    assert recorder.start(context, session_id="session", invocation="prompt")
    assert recorder.finish(context, status=status)
    assert recorder.finish(context, status=status)
