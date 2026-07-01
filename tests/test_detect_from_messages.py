"""Tests for detect_from_messages() convenience function."""

from __future__ import annotations

from tvastar.detect import detect_from_messages
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _asst(*blocks):
    return Message("assistant", list(blocks))


def _user(*blocks):
    return Message("user", list(blocks))


def _use(name="bash", input=None, id="c1"):
    return ToolUseBlock(name=name, input=input or {}, id=id)


def _result(content="ok", is_error=False, tool_use_id="c1"):
    return ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)


class TestDetectFromMessagesEmpty:
    def test_empty_messages_returns_empty(self):
        assert detect_from_messages([]) == []


class TestDetectFromMessagesThrashLoop:
    def test_thrash_loop_fires(self):
        # Same tool called 3x with identical args triggers thrash_loop
        msgs = [
            _asst(_use(name="ls", input={"path": "."}, id="c1")),
            _user(_result(content="file1.py", tool_use_id="c1")),
            _asst(_use(name="ls", input={"path": "."}, id="c2")),
            _user(_result(content="file1.py", tool_use_id="c2")),
            _asst(_use(name="ls", input={"path": "."}, id="c3")),
            _user(_result(content="file1.py", tool_use_id="c3")),
            _asst(TextBlock(text="Here are the files.")),
        ]
        findings = detect_from_messages(msgs)
        assert any(f.detector == "thrash_loop" for f in findings)


class TestDetectFromMessagesUnverifiedCompletion:
    def test_unverified_completion_fires(self):
        msgs = [
            _asst(_use(id="c1")),
            _user(_result(content="FAILED test_x.py - assert 1 == 2\n1 failed", tool_use_id="c1")),
            _asst(TextBlock(text="All tests pass now!")),
        ]
        findings = detect_from_messages(msgs)
        assert any(f.detector == "unverified_completion" for f in findings)


class TestDetectFromMessagesStopped:
    def test_custom_stopped_parameter_respected(self):
        # With stopped="max_steps", step_limit should fire
        msgs = [_asst(TextBlock(text="Partial answer."))]
        findings = detect_from_messages(msgs, stopped="max_steps")
        assert any(f.detector == "step_limit" for f in findings)

    def test_auto_infer_tool_use_stopped(self):
        # Last assistant message has only tool_use (no text) -> infer "tool_use"
        # This means step_limit won't fire even though there's no final text
        msgs = [
            _asst(_use(name="bash", input={"cmd": "ls"}, id="c1")),
        ]
        findings = detect_from_messages(msgs)
        # Should NOT fire empty_answer because stopped is inferred as "tool_use"
        assert not any(f.detector == "empty_answer" for f in findings)

    def test_explicit_stopped_overrides_inference(self):
        # If stopped is explicitly provided as "end_turn", auto-infer still
        # applies (the heuristic overrides when last assistant has no text)
        msgs = [
            _asst(TextBlock(text="Done."), _use(name="bash", input={}, id="c1")),
        ]
        # Last assistant has BOTH text and tool_use -> stopped stays "end_turn"
        findings = detect_from_messages(msgs, stopped="end_turn")
        # No step_limit since stopped="end_turn"
        assert not any(f.detector == "step_limit" for f in findings)


class TestDetectFromMessagesCleanRun:
    def test_clean_run_no_findings(self):
        msgs = [_asst(TextBlock(text="Here is your answer."))]
        findings = detect_from_messages(msgs)
        assert findings == []

    def test_successful_tool_run_no_findings_except_unknown_tool(self):
        # With an empty tool registry, unknown_tool fires for any tool call.
        # This is expected — detect_from_messages can't know which tools were
        # registered. The key point is no *behavioral* failure is flagged.
        msgs = [
            _asst(_use(name="bash", input={"command": "make"}, id="c1")),
            _user(_result(content="build succeeded", tool_use_id="c1")),
            _asst(TextBlock(text="The build is green.")),
        ]
        findings = detect_from_messages(msgs)
        # Only unknown_tool fires (because we have no tool registry)
        detectors_hit = {f.detector for f in findings}
        assert "unverified_completion" not in detectors_hit
        assert "thrash_loop" not in detectors_hit


class TestDetectFromMessagesKnownTools:
    """Tests for the known_tools parameter suppressing unknown_tool findings."""

    def test_known_tools_suppresses_unknown_tool(self):
        # With known_tools provided, unknown_tool should NOT fire
        msgs = [
            _asst(_use(name="bash", input={"command": "make"}, id="c1")),
            _user(_result(content="build succeeded", tool_use_id="c1")),
            _asst(TextBlock(text="The build is green.")),
        ]
        findings = detect_from_messages(msgs, known_tools=["bash"])
        detectors_hit = {f.detector for f in findings}
        assert "unknown_tool" not in detectors_hit

    def test_without_known_tools_unknown_tool_fires(self):
        # Without known_tools, unknown_tool fires (existing behavior)
        msgs = [
            _asst(_use(name="bash", input={"command": "make"}, id="c1")),
            _user(_result(content="build succeeded", tool_use_id="c1")),
            _asst(TextBlock(text="The build is green.")),
        ]
        findings = detect_from_messages(msgs)
        detectors_hit = {f.detector for f in findings}
        assert "unknown_tool" in detectors_hit

    def test_known_tools_partial_match_still_fires_for_unknown(self):
        # known_tools=["bash"] but model also calls "ghost" → unknown_tool fires for ghost
        msgs = [
            _asst(_use(name="bash", input={}, id="c1")),
            _user(_result(content="ok", tool_use_id="c1")),
            _asst(_use(name="ghost", input={}, id="c2")),
            _user(_result(content="ok", tool_use_id="c2")),
            _asst(TextBlock(text="Done.")),
        ]
        findings = detect_from_messages(msgs, known_tools=["bash"])
        unknown_findings = [f for f in findings if f.detector == "unknown_tool"]
        assert len(unknown_findings) == 1
        assert "ghost" in unknown_findings[0].message

    def test_known_tools_empty_list_behaves_like_empty_registry(self):
        # known_tools=[] means nothing is known — unknown_tool should fire
        msgs = [
            _asst(_use(name="bash", input={}, id="c1")),
            _user(_result(content="ok", tool_use_id="c1")),
            _asst(TextBlock(text="Done.")),
        ]
        findings = detect_from_messages(msgs, known_tools=[])
        detectors_hit = {f.detector for f in findings}
        assert "unknown_tool" in detectors_hit
