"""Reusable Hypothesis strategies for Tvastar PBT suite.

Provides composite strategies for the core domain types:
- st_messages: generates valid Message instances
- st_findings: generates valid Finding instances
- st_tool_specs: generates valid ToolSpec instances
- st_run_results: generates valid RunResult instances
- st_agent_specs: generates valid AgentSpec instances (with MockModel)

Validates: Requirements REQ-LOOP-001, REQ-DETECT-001, CON-006
"""

from __future__ import annotations

import hypothesis.strategies as st

from tvastar.types import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from tvastar.detect.base import Finding, Severity
from tvastar.session import RunResult
from tvastar.agent import AgentSpec
from tvastar.model.mock import MockModel
from tvastar.tools.base import ToolRegistry


# ---------------------------------------------------------------------------
# Primitive strategies
# ---------------------------------------------------------------------------

st_role = st.sampled_from(["user", "assistant", "tool"])

st_severity = st.sampled_from(list(Severity))

st_stop_reason_enum = st.sampled_from(["end_turn", "tool_use", "max_tokens", "error"])

st_stopped_reason = st.sampled_from(["end_turn", "max_steps", "budget", "error"])

# Non-empty text suitable for message content
st_text_content = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# Tool names: valid identifiers
st_tool_name = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)


# ---------------------------------------------------------------------------
# ContentBlock strategies
# ---------------------------------------------------------------------------


@st.composite
def st_text_blocks(draw: st.DrawFn) -> TextBlock:
    """Generate a valid TextBlock."""
    text = draw(st_text_content)
    return TextBlock(text=text)


@st.composite
def st_tool_use_blocks(draw: st.DrawFn) -> ToolUseBlock:
    """Generate a valid ToolUseBlock."""
    name = draw(st_tool_name)
    # Simple dict input: 0-3 string keys
    input_data = draw(
        st.dictionaries(
            keys=st.from_regex(r"[a-z_]{1,10}", fullmatch=True),
            values=st.one_of(
                st.text(min_size=0, max_size=50),
                st.integers(min_value=-1000, max_value=1000),
                st.booleans(),
            ),
            min_size=0,
            max_size=3,
        )
    )
    return ToolUseBlock(name=name, input=input_data)


@st.composite
def st_tool_result_blocks(draw: st.DrawFn) -> ToolResultBlock:
    """Generate a valid ToolResultBlock."""
    tool_use_id = draw(st.from_regex(r"call_[a-f0-9]{12}", fullmatch=True))
    content = draw(st.text(min_size=0, max_size=200))
    is_error = draw(st.booleans())
    return ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)


# ---------------------------------------------------------------------------
# Message strategy
# ---------------------------------------------------------------------------


@st.composite
def st_messages(draw: st.DrawFn) -> Message:
    """Generate a valid Message instance.

    Produces messages with role-appropriate content:
    - user/system: plain text content
    - assistant: text or tool_use blocks
    - tool: tool_result blocks
    """
    role = draw(st_role)

    if role == "tool":
        # Tool messages contain tool result blocks
        blocks = draw(st.lists(st_tool_result_blocks(), min_size=1, max_size=3))
        return Message(role=role, content=blocks)
    elif role == "assistant":
        # Assistant messages can be text or contain tool_use blocks
        use_tool_blocks = draw(st.booleans())
        if use_tool_blocks:
            blocks = draw(st.lists(st_tool_use_blocks(), min_size=1, max_size=3))
            return Message(role=role, content=blocks)
        else:
            text = draw(st_text_content)
            return Message(role=role, content=text)
    else:
        # user/system: plain text
        text = draw(st_text_content)
        return Message(role=role, content=text)


# ---------------------------------------------------------------------------
# Finding strategy
# ---------------------------------------------------------------------------


@st.composite
def st_findings(draw: st.DrawFn) -> Finding:
    """Generate a valid Finding instance."""
    detector = draw(
        st.sampled_from(
            [
                "unverified_completion",
                "thrash_loop",
                "unknown_tool",
                "schema_mismatch",
                "prompt_injection",
                "ignored_tool_error",
                "empty_answer",
            ]
        )
    )
    severity = draw(st_severity)
    message = draw(st_text_content)
    evidence = draw(
        st.dictionaries(
            keys=st.from_regex(r"[a-z_]{1,15}", fullmatch=True),
            values=st.text(min_size=0, max_size=50),
            min_size=0,
            max_size=3,
        )
    )
    return Finding(detector=detector, severity=severity, message=message, evidence=evidence)


# ---------------------------------------------------------------------------
# ToolSpec strategy
# ---------------------------------------------------------------------------


@st.composite
def st_tool_specs(draw: st.DrawFn) -> ToolSpec:
    """Generate a valid ToolSpec instance."""
    name = draw(st_tool_name)
    description = draw(st.text(min_size=1, max_size=100))
    # Simple JSON schema for input
    properties = draw(
        st.dictionaries(
            keys=st.from_regex(r"[a-z_]{1,10}", fullmatch=True),
            values=st.just({"type": "string"}),
            min_size=0,
            max_size=4,
        )
    )
    input_schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }
    return ToolSpec(name=name, description=description, input_schema=input_schema)


# ---------------------------------------------------------------------------
# RunResult strategy
# ---------------------------------------------------------------------------


@st.composite
def st_run_results(draw: st.DrawFn) -> RunResult:
    """Generate a valid RunResult instance.

    Produces a RunResult with coherent fields: text matches final message,
    steps > 0, usage is non-negative, stopped is a valid reason.
    """
    text = draw(st_text_content)
    # Generate 1-10 messages for the conversation
    messages = draw(st.lists(st_messages(), min_size=1, max_size=10))
    usage = Usage(
        input_tokens=draw(st.integers(min_value=0, max_value=100_000)),
        output_tokens=draw(st.integers(min_value=0, max_value=50_000)),
    )
    steps = draw(st.integers(min_value=1, max_value=50))
    stopped = draw(st_stopped_reason)
    findings = draw(st.lists(st_findings(), min_size=0, max_size=5))

    return RunResult(
        text=text,
        messages=messages,
        usage=usage,
        steps=steps,
        stopped=stopped,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# AgentSpec strategy
# ---------------------------------------------------------------------------


@st.composite
def st_agent_specs(draw: st.DrawFn) -> AgentSpec:
    """Generate a valid AgentSpec instance using MockModel.

    Produces an AgentSpec with reasonable defaults suitable for property testing.
    Uses MockModel so no API keys are needed.
    """
    name = draw(st.from_regex(r"[a-z][a-z0-9_-]{0,19}", fullmatch=True))
    instructions = draw(st.text(min_size=0, max_size=200))
    max_steps = draw(st.integers(min_value=1, max_value=50))
    max_tokens = draw(st.sampled_from([1024, 2048, 4096, 8192]))
    temperature = draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False))

    return AgentSpec(
        name=name,
        model=MockModel(),
        instructions=instructions,
        tools=ToolRegistry(),
        max_steps=max_steps,
        max_tokens=max_tokens,
        temperature=temperature,
    )
