"""Smoke test to verify PBT infrastructure is correctly set up.

This validates that Hypothesis strategies generate valid instances and
that the settings profile is correctly loaded.
"""

from hypothesis import given, settings

from tests.pbt.strategies import (
    st_agent_specs,
    st_findings,
    st_messages,
    st_run_results,
    st_tool_specs,
)
from tvastar.agent import AgentSpec
from tvastar.detect.base import Finding, Severity
from tvastar.session import RunResult
from tvastar.types import Message, ToolSpec


@given(msg=st_messages())
def test_st_messages_produces_valid_message(msg: Message):
    """st_messages always produces a valid Message instance."""
    assert isinstance(msg, Message)
    assert msg.role in ("user", "assistant", "tool", "system")
    assert msg.content is not None


@given(finding=st_findings())
def test_st_findings_produces_valid_finding(finding: Finding):
    """st_findings always produces a valid Finding instance."""
    assert isinstance(finding, Finding)
    assert isinstance(finding.severity, Severity)
    assert len(finding.detector) > 0
    assert len(finding.message) > 0


@given(spec=st_tool_specs())
def test_st_tool_specs_produces_valid_tool_spec(spec: ToolSpec):
    """st_tool_specs always produces a valid ToolSpec instance."""
    assert isinstance(spec, ToolSpec)
    assert len(spec.name) > 0
    assert len(spec.description) > 0
    assert "type" in spec.input_schema
    assert spec.input_schema["type"] == "object"


@given(result=st_run_results())
def test_st_run_results_produces_valid_run_result(result: RunResult):
    """st_run_results always produces a valid RunResult instance."""
    assert isinstance(result, RunResult)
    assert result.steps >= 1
    assert result.usage.input_tokens >= 0
    assert result.usage.output_tokens >= 0
    assert result.stopped in ("end_turn", "max_steps", "budget", "error")


@given(spec=st_agent_specs())
def test_st_agent_specs_produces_valid_agent_spec(spec: AgentSpec):
    """st_agent_specs always produces a valid AgentSpec instance."""
    assert isinstance(spec, AgentSpec)
    assert spec.max_steps >= 1
    assert spec.max_tokens >= 1024
    assert 0.0 <= spec.temperature <= 2.0
    assert spec.model.name == "mock"
