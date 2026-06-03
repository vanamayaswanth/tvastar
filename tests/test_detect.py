"""Tests for the silent-failure detection layer."""

from tvastar import Harness, create_agent, default_toolset, tool
from tvastar.detect import (
    RunContext,
    Severity,
    default_detectors,
    schema_mismatch,
    thrash_loop,
    unverified_completion,
    validate,
)
from tvastar.model import MockModel
from tvastar.tools.base import ToolRegistry
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


# ---- the mini JSON-schema validator ------------------------------------


def test_validator_accepts_valid():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "required": ["a"],
    }
    assert validate({"a": 1, "b": "x"}, schema) == []


def test_validator_flags_missing_required_and_wrong_type():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
    }
    assert validate({}, schema)  # missing required
    assert validate({"a": "not-int"}, schema)  # wrong type


def test_validator_bool_is_not_integer():
    # In Python bool subclasses int; the validator must not accept True as int.
    assert validate(True, {"type": "integer"})
    assert validate(1, {"type": "integer"}) == []


def test_validator_enum():
    assert validate("z", {"enum": ["x", "y"]})
    assert validate("x", {"enum": ["x", "y"]}) == []


# ---- helpers to build a RunContext -------------------------------------


def _ctx(messages, tools=None, stopped="end_turn", final=""):
    return RunContext(
        messages=messages,
        tools=tools or ToolRegistry(),
        stopped=stopped,
        final_text=final or (messages[-1].text if messages else ""),
    )


# ---- individual detectors ----------------------------------------------


def test_schema_mismatch_detector():
    @tool
    def add(a: int, b: int) -> int:
        "Add."
        return a + b

    reg = ToolRegistry([add])
    msgs = [Message("assistant", [ToolUseBlock(name="add", input={"a": 1})])]  # missing b
    findings = schema_mismatch(_ctx(msgs, reg))
    assert findings and findings[0].severity == Severity.ERROR
    assert "add" in findings[0].message


def test_thrash_loop_detector():
    same = [Message("assistant", [ToolUseBlock(name="ls", input={"path": "."})]) for _ in range(3)]
    findings = thrash_loop(_ctx(same))
    assert findings and findings[0].detector == "thrash_loop"


def test_unverified_completion_detector():
    # Model claims success; last tool result shows a failing test run.
    msgs = [
        Message("assistant", [ToolUseBlock(id="c1", name="bash", input={})]),
        Message("user", [ToolResultBlock(tool_use_id="c1", content="1 failed in 0.1s")]),
        Message("assistant", [TextBlock(text="All tests pass now — done!")]),
    ]
    findings = unverified_completion(_ctx(msgs, final="All tests pass now — done!"))
    assert findings and findings[0].severity == Severity.ERROR


def test_clean_run_has_no_findings():
    msgs = [Message("assistant", [TextBlock(text="Here is your answer.")])]
    findings = []
    for det in default_detectors():
        findings += det(_ctx(msgs, final="Here is your answer."))
    assert findings == []


# ---- integration through the agent loop --------------------------------


async def test_loop_attaches_findings_unverified_completion():
    # bash returns a failing-test string; model then claims success.
    script = [
        ToolUseBlock(name="bash", input={"command": "pytest"}),
        "All tests pass — fixed it!",
    ]

    # Inject a fake sandbox by using a custom tool that returns a failure string.
    @tool
    async def bash(command: str) -> str:
        "Run."
        return "FAILED test_x.py::test_y - assert 1 == 2\n1 failed in 0.02s"

    agent = create_agent("t", model=MockModel(script), tools=[bash])
    result = await Harness(agent).run("fix the tests")
    detectors_hit = {f.detector for f in result.findings}
    assert "unverified_completion" in detectors_hit
    assert not result.ok  # the claim is contradicted -> not ok


async def test_detection_can_be_disabled():
    @tool
    async def bash(command: str) -> str:
        "Run."
        return "1 failed"

    agent = create_agent(
        "t",
        model=MockModel([ToolUseBlock(name="bash", input={"command": "x"}), "done"]),
        tools=[bash],
        detect=False,
    )
    result = await Harness(agent).run("go")
    assert result.findings == []


async def test_max_steps_finding():
    forever = [ToolUseBlock(name="list_files", input={}) for _ in range(20)]
    agent = create_agent("t", model=MockModel(forever), tools=default_toolset(), max_steps=3)
    result = await Harness(agent).run("go")
    assert any(f.detector == "step_limit" for f in result.findings)
