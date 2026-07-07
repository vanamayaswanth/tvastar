"""Tests for StructuredLogger — task 5.1."""

import json
import sys
from io import StringIO

from tvastar.loop import LoopEvent, LoopState
from tvastar.loop.logger import StructuredLogger


def _make_event(
    state=LoopState.RUNNING,
    data=None,
    loop_name="test-loop",
    run_id="run_abc123",
) -> LoopEvent:
    return LoopEvent(
        loop_name=loop_name,
        run_id=run_id,
        state=state,
        at=1705312200.0,  # 2024-01-15T10:30:00Z
        data=data or {},
    )


class TestStructuredLoggerStderr:
    def test_writes_json_line_to_stderr(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(sys, "stderr", buf)

        logger = StructuredLogger()
        event = _make_event(
            state=LoopState.VERIFYING,
            data={"from_state": "running", "iteration": 2},
        )
        logger(event)

        line = buf.getvalue()
        entry = json.loads(line)
        assert entry["timestamp"] == "2024-01-15T09:50:00+00:00"
        assert entry["loop_name"] == "test-loop"
        assert entry["run_id"] == "run_abc123"
        assert entry["from_state"] == "running"
        assert entry["to_state"] == "verifying"
        assert entry["iteration"] == 2
        assert entry["metadata"] == {}

    def test_metadata_excludes_internal_keys(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(sys, "stderr", buf)

        logger = StructuredLogger()
        event = _make_event(
            data={"from_state": "idle", "iteration": 1, "trigger": "webhook", "source": "github"},
        )
        logger(event)

        entry = json.loads(buf.getvalue())
        assert entry["metadata"] == {"trigger": "webhook", "source": "github"}

    def test_defaults_when_from_state_and_iteration_missing(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(sys, "stderr", buf)

        logger = StructuredLogger()
        event = _make_event(data={})
        logger(event)

        entry = json.loads(buf.getvalue())
        assert entry["from_state"] == ""
        assert entry["iteration"] == 0


class TestStructuredLoggerFile:
    def test_writes_to_file_in_append_mode(self, tmp_path):
        log_file = str(tmp_path / "loop.log")

        logger = StructuredLogger(output=log_file)
        logger(_make_event(state=LoopState.TRIGGERED, data={"from_state": "idle", "iteration": 1}))
        logger(_make_event(state=LoopState.RUNNING, data={"from_state": "triggered", "iteration": 1}))

        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["to_state"] == "triggered"
        second = json.loads(lines[1])
        assert second["to_state"] == "running"

    def test_appends_to_existing_file(self, tmp_path):
        log_file = str(tmp_path / "loop.log")
        # Pre-write something
        with open(log_file, "w") as f:
            f.write('{"existing": true}\n')

        logger = StructuredLogger(output=log_file)
        logger(_make_event())

        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) == 2
        assert json.loads(lines[0]) == {"existing": True}

    def test_flush_after_each_write(self, tmp_path):
        log_file = str(tmp_path / "loop.log")
        logger = StructuredLogger(output=log_file)
        logger(_make_event())

        # Read immediately — should be flushed
        with open(log_file) as f:
            content = f.read()
        assert content.strip() != ""


class TestStructuredLoggerOnEvent:
    def test_installable_via_on_event(self, monkeypatch):
        """StructuredLogger is callable and works as an on_event listener."""
        buf = StringIO()
        monkeypatch.setattr(sys, "stderr", buf)

        logger = StructuredLogger()
        # Simulate what loop.on_event(logger) does — calls logger(event)
        event = _make_event(state=LoopState.PASS, data={"from_state": "verifying", "iteration": 3})
        logger(event)

        entry = json.loads(buf.getvalue())
        assert entry["to_state"] == "pass"
        assert entry["from_state"] == "verifying"
