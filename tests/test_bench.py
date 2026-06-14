"""Tests for 0.6.0: BenchSuite, BenchTask, BenchReport, swe_bench_tasks."""

import json

import pytest

from tvastar import BenchSuite, BenchTask, create_agent
from tvastar.bench.swebench import swe_bench_tasks
from tvastar.model import MockModel


def _agent(script=None):
    return create_agent("bench-agent", model=MockModel(script or []))


# ── BenchTask / BenchResult basics ───────────────────────────────────────────


async def test_bench_suite_resolved_when_verify_returns_true():
    def always_pass(run_result, workspace):
        return True

    suite = BenchSuite(_agent(["done"]), concurrency=1)
    suite.add(BenchTask(id="t1", prompt="fix it", verify=always_pass))
    report = await suite.run()
    assert report.total == 1
    assert report.resolved == 1
    assert report.score == 1.0


async def test_bench_suite_not_resolved_when_verify_returns_false():
    def always_fail(run_result, workspace):
        return False

    suite = BenchSuite(_agent(["done"]), concurrency=1)
    suite.add(BenchTask(id="t1", prompt="fix it", verify=always_fail))
    report = await suite.run()
    assert report.resolved == 0
    assert report.score == 0.0


async def test_bench_suite_fallback_verify_uses_run_ok():
    # No verify= → uses run_result.ok (MockModel ends cleanly → ok=True)
    suite = BenchSuite(_agent(["done"]), concurrency=1)
    suite.add(BenchTask(id="t1", prompt="hi"))
    report = await suite.run()
    assert report.resolved == 1


async def test_bench_suite_workspace_files_written(tmp_path):
    written: list[bool] = []

    def check_files(run_result, workspace):
        written.append((workspace / "src/foo.py").exists())
        return True

    suite = BenchSuite(_agent(["done"]), concurrency=1)
    suite.add(
        BenchTask(
            id="t1",
            prompt="check files",
            workspace={"src/foo.py": "x = 1"},
            verify=check_files,
        )
    )
    await suite.run()
    assert written == [True]


async def test_bench_suite_multiple_tasks_concurrent():
    tasks = [BenchTask(id=f"t{i}", prompt=f"task {i}", verify=lambda r, w: True) for i in range(5)]
    suite = BenchSuite(_agent(), concurrency=3)
    suite.add_many(tasks)
    report = await suite.run()
    assert report.total == 5
    assert report.resolved == 5


async def test_bench_suite_captures_run_error():
    # Model with no script echoes prompt — that's fine; let's make verify blow up
    def bad_verify(run_result, workspace):
        raise RuntimeError("verifier exploded")

    suite = BenchSuite(_agent(["done"]), concurrency=1)
    suite.add(BenchTask(id="t1", prompt="go", verify=bad_verify))
    report = await suite.run()
    assert report.results[0].resolved is False
    assert "verifier exploded" in (report.results[0].error or "")


# ── BenchReport helpers ───────────────────────────────────────────────────────


async def test_bench_report_to_dict():
    suite = BenchSuite(_agent(["hi"]), concurrency=1)
    suite.name = "my-suite"
    suite.add(BenchTask(id="x", prompt="p", verify=lambda r, w: True))
    report = await suite.run()
    d = report.to_dict()
    assert d["suite"] == "my-suite"
    assert d["score"] == 1.0
    assert d["results"][0]["id"] == "x"


async def test_bench_report_print_does_not_raise(capsys):
    suite = BenchSuite(_agent(), concurrency=1)
    suite.add(BenchTask(id="t1", prompt="hi", verify=lambda r, w: False))
    report = await suite.run()
    report.print()
    out = capsys.readouterr().out
    assert "0/1" in out


# ── swe_bench_tasks JSONL loader ──────────────────────────────────────────────


def test_swe_bench_tasks_from_jsonl(tmp_path):
    rows = [
        {
            "instance_id": "repo__proj-1",
            "problem_statement": "Fix the off-by-one error",
            "hints_text": "Look at src/calc.py line 42",
            "test_patch": "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n",
            "repo": "repo/proj",
            "base_commit": "abc123",
        },
        {
            "instance_id": "repo__proj-2",
            "problem_statement": "Null pointer in parser",
            "hints_text": "",
            "test_patch": "",
        },
    ]
    jsonl = tmp_path / "tasks.jsonl"
    jsonl.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    tasks = swe_bench_tasks(source="jsonl", path=str(jsonl))
    assert len(tasks) == 2
    assert tasks[0].id == "repo__proj-1"
    assert "Fix the off-by-one" in tasks[0].prompt
    assert "Look at src/calc.py" in tasks[0].prompt
    assert tasks[1].id == "repo__proj-2"


def test_swe_bench_tasks_jsonl_max_tasks(tmp_path):
    rows = [
        {"instance_id": f"t{i}", "problem_statement": f"p{i}", "hints_text": ""} for i in range(10)
    ]
    jsonl = tmp_path / "tasks.jsonl"
    jsonl.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    tasks = swe_bench_tasks(source="jsonl", path=str(jsonl), max_tasks=3)
    assert len(tasks) == 3


def test_swe_bench_tasks_unknown_source():
    with pytest.raises(ValueError, match="Unknown source"):
        swe_bench_tasks(source="bogus")


def test_swe_bench_tasks_jsonl_requires_path():
    with pytest.raises(ValueError, match="path="):
        swe_bench_tasks(source="jsonl")
