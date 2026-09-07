"""Release benchmark for payload-free execution lineage.

The normal smoke run measures in-memory recording overhead and serialized record
size. Pass ``--sqlite-records 100000`` for the release-scale direct-query gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

from tvastar.execution import (
    ExecutionContext,
    ExecutionQuery,
    ExecutionRecorder,
    execution_key,
    new_execution_id,
    outcome_key,
)
from tvastar.memory import InMemoryStore, SQLiteStore


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _context(scope: str, parent: ExecutionContext | None = None) -> ExecutionContext:
    execution_id = new_execution_id()
    return ExecutionContext(
        execution_id=execution_id,
        root_execution_id=parent.root_execution_id if parent else execution_id,
        scope=scope,
        spawned_by=parent.execution_id if parent else None,
        attempt_count=1,
        attempt_trace=["attempt_started:1"],
    )


def _recording_gate(iterations: int) -> None:
    baseline_ns: list[float] = []
    enabled_ns: list[float] = []
    store = InMemoryStore()
    recorder = ExecutionRecorder(store, "benchmark")
    last: ExecutionContext | None = None

    for _ in range(iterations):
        started = time.perf_counter_ns()
        _context("benchmark")
        baseline_ns.append(float(time.perf_counter_ns() - started))

        started = time.perf_counter_ns()
        last = _context("benchmark")
        assert recorder.start(last, session_id="benchmark", invocation="prompt")
        assert recorder.finish(last, status="completed")
        enabled_ns.append(float(time.perf_counter_ns() - started))

    assert last is not None
    baseline_p95_ms = _p95(baseline_ns) / 1_000_000
    enabled_p95_ms = _p95(enabled_ns) / 1_000_000
    added_p95_ms = max(0.0, enabled_p95_ms - baseline_p95_ms)
    allowed_ms = max(5.0, baseline_p95_ms * 0.01)
    envelope_size = len(
        json.dumps(store.get(execution_key("benchmark", last.execution_id))).encode("utf-8")
    )
    outcome_size = len(
        json.dumps(store.get(outcome_key("benchmark", last.execution_id))).encode("utf-8")
    )

    print(
        f"recording: baseline p95={baseline_p95_ms:.3f}ms, "
        f"enabled p95={enabled_p95_ms:.3f}ms, added={added_p95_ms:.3f}ms "
        f"(limit={allowed_ms:.3f}ms)"
    )
    print(f"record sizes: envelope={envelope_size}B, outcome={outcome_size}B (limit=2048B each)")
    if added_p95_ms > allowed_ms:
        raise SystemExit("lineage recording overhead gate failed")
    if envelope_size >= 2048 or outcome_size >= 2048:
        raise SystemExit("lineage record-size gate failed")


def _sqlite_gate(record_count: int, query_samples: int) -> None:
    with tempfile.TemporaryDirectory(prefix="tvastar-lineage-bench-") as tmp:
        store = SQLiteStore(Path(tmp) / "lineage.db")
        contexts: list[ExecutionContext] = []
        parent: ExecutionContext | None = None
        session_ref = f"sha256:{hashlib.sha256(b'benchmark').hexdigest()}"
        rows: list[tuple[str, str]] = []

        def flush() -> None:
            if not rows:
                return
            # ponytail: benchmark setup bypasses per-record commits; production
            # queries still use SQLiteStore. Upgrade to a Store bulk API if one exists.
            with store._lock:
                store._conn.executemany(
                    "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", rows
                )
                store._conn.commit()
            rows.clear()

        for index in range(record_count):
            if index % 20 == 0:
                parent = None
            context = _context("benchmark", parent)
            envelope = {
                "schema": "tvastar.execution.envelope",
                "version": 1,
                "scope": "benchmark",
                "execution_id": context.execution_id,
                "root_execution_id": context.root_execution_id,
                "spawned_by": context.spawned_by,
                "session_ref": session_ref,
                "invocation": "prompt",
                "started_at": context.started_at,
            }
            outcome = {
                "schema": "tvastar.execution.outcome",
                "version": 1,
                "scope": "benchmark",
                "execution_id": context.execution_id,
                "status": "completed",
                "completed_at": context.started_at,
                "duration_ms": 0.0,
                "attempt_count": context.attempt_count,
                "attempt_trace": context.attempt_trace,
            }
            rows.extend(
                (
                    (execution_key("benchmark", context.execution_id), json.dumps(envelope)),
                    (outcome_key("benchmark", context.execution_id), json.dumps(outcome)),
                )
            )
            if len(rows) >= 2_000:
                flush()
            contexts.append(context)
            parent = context
        flush()

        query = ExecutionQuery(store, "benchmark")
        get_ns: list[float] = []
        ancestors_ns: list[float] = []
        for index in range(query_samples):
            context = contexts[(index * len(contexts)) // query_samples]
            started = time.perf_counter_ns()
            assert query.get(context.execution_id).complete
            get_ns.append(float(time.perf_counter_ns() - started))

            position = min(
                len(contexts) - 1,
                ((index + 1) * len(contexts)) // query_samples - 1,
            )
            leaf = contexts[min(len(contexts) - 1, position + (19 - position % 20))]
            started = time.perf_counter_ns()
            assert query.ancestors(leaf.execution_id).complete
            ancestors_ns.append(float(time.perf_counter_ns() - started))

        get_p95_ms = _p95(get_ns) / 1_000_000
        ancestors_p95_ms = _p95(ancestors_ns) / 1_000_000
        print(
            f"sqlite {record_count} records: get p95={get_p95_ms:.3f}ms, "
            f"depth-20 ancestors p95={ancestors_p95_ms:.3f}ms (limit=2000ms each)"
        )
        store._conn.close()
        if get_p95_ms >= 2000 or ancestors_p95_ms >= 2000:
            raise SystemExit("SQLite lineage query gate failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--sqlite-records", type=int, default=0)
    parser.add_argument("--query-samples", type=int, default=100)
    args = parser.parse_args()
    if args.iterations < 1 or args.sqlite_records < 0 or args.query_samples < 1:
        parser.error("iteration counts must be positive (sqlite records may be zero)")

    _recording_gate(args.iterations)
    if args.sqlite_records:
        _sqlite_gate(args.sqlite_records, min(args.query_samples, args.sqlite_records))


if __name__ == "__main__":
    main()
