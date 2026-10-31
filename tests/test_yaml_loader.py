"""pytest suite for serving/yaml_loader.py — 30 targeted cases.

Run: pytest tests/test_yaml_loader.py -v
"""
from __future__ import annotations

import os
import tempfile
import textwrap
import warnings

import pytest

from tvastar.errors import TvastarError
from tvastar.serving.yaml_loader import load_agent_from_yaml


def yaml_file(text: str) -> str:
    """Write dedented YAML to a temp file, return path."""
    f = tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8")
    f.write(textwrap.dedent(text))
    f.close()
    return f.name


# ── happy-path ────────────────────────────────────────────────────────────────

def test_minimal():
    f = yaml_file("""
        agent:
          name: minimal
          model:
            provider: mock
          instructions: test
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.name == "minimal"
        assert s.instructions == "test"
    finally:
        os.unlink(f)


def test_named_model_ref():
    f = yaml_file("""
        models:
          fast:
            provider: mock
        agent:
          name: named-model-test
          model: fast
          instructions: hi
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.name == "named-model-test"
    finally:
        os.unlink(f)


def test_fallback_models():
    f = yaml_file("""
        models:
          backup:
            provider: mock
        agent:
          name: fallbacks
          model:
            provider: mock
          instructions: hi
          fallback_models:
            - backup
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.fallback_models and len(s.fallback_models) == 1
    finally:
        os.unlink(f)


def test_virtual_sandbox_all_fields():
    f = yaml_file("""
        agent:
          name: sandbox-test
          model:
            provider: mock
          instructions: hi
          sandbox:
            type: virtual
            allow_python: false
            security:
              network: false
              allowed_commands: [echo]
              denied_mcp_tools: [danger_tool]
            credential_filter:
              enabled: true
              patterns: ['*_KEY']
    """)
    try:
        s = load_agent_from_yaml(f)
        from tvastar.sandbox import VirtualSandbox
        sb = s.sandbox_factory()
        assert isinstance(sb, VirtualSandbox)
        assert sb.allow_python is False
        assert not sb.policy.network
        assert "echo" in sb.policy.allowed_commands
        assert "danger_tool" in sb.policy.denied_mcp_tools
        assert sb.credential_filter is not None
    finally:
        os.unlink(f)


def test_named_sandbox_ref():
    f = yaml_file("""
        sandboxes:
          strict:
            type: virtual
            security:
              network: false
        agent:
          name: named-sandbox
          model:
            provider: mock
          instructions: hi
          sandbox: strict
    """)
    try:
        s = load_agent_from_yaml(f)
        assert not s.sandbox_factory().policy.network
    finally:
        os.unlink(f)


def test_tools_default_shorthand():
    f = yaml_file("""
        agent:
          name: tools-test
          model:
            provider: mock
          instructions: hi
          tools: default
    """)
    try:
        s = load_agent_from_yaml(f)
        assert len(s.tools) > 0
    finally:
        os.unlink(f)


def test_tools_list_with_default():
    f = yaml_file("""
        agent:
          name: tools-list
          model:
            provider: mock
          instructions: hi
          tools:
            - default
    """)
    try:
        s = load_agent_from_yaml(f)
        assert len(s.tools) > 0
    finally:
        os.unlink(f)


def test_readonly_sets_tool_policy():
    f = yaml_file("""
        agent:
          name: readonly-test
          model:
            provider: mock
          instructions: hi
          readonly: true
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.tool_policy is not None
    finally:
        os.unlink(f)


def test_tools_whitelist_sets_policy():
    f = yaml_file("""
        agent:
          name: whitelist-test
          model:
            provider: mock
          instructions: hi
          tools_whitelist: [read_file, grep]
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.tool_policy is not None
    finally:
        os.unlink(f)


def test_inline_skills():
    f = yaml_file("""
        agent:
          name: skills-test
          model:
            provider: mock
          instructions: hi
          skills:
            - name: tip
              description: A helpful tip
              content: Always test first.
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.skills is not None
        assert "tip" in s.skills.names()
    finally:
        os.unlink(f)


def test_instruction_file():
    instr = tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    )
    instr.write("You are a helpful agent from file.")
    instr.close()
    instr_path = instr.name.replace("\\", "/")
    f = yaml_file(f"""
        agent:
          name: instr-file
          model:
            provider: mock
          instruction_file: "{instr_path}"
    """)
    try:
        s = load_agent_from_yaml(f)
        assert "from file" in s.instructions
    finally:
        os.unlink(f)
        os.unlink(instr.name)


def test_subagents_with_named_model():
    f = yaml_file("""
        models:
          fast:
            provider: mock
        agent:
          name: coordinator
          model:
            provider: mock
          instructions: coordinate
          subagents:
            - name: researcher
              description: Does research
              model: fast
              instructions: Research thoroughly.
              max_steps: 10
    """)
    try:
        s = load_agent_from_yaml(f)
        assert "researcher" in s.subagents
        assert s.subagents["researcher"].description == "Does research"
        assert s.subagents["researcher"].max_steps == 10
    finally:
        os.unlink(f)


def test_compaction_all_nine_fields():
    f = yaml_file("""
        agent:
          name: compact-test
          model:
            provider: mock
          instructions: hi
          compaction:
            max_messages: 50
            max_tokens_estimate: 60000
            keep_last: 7
            min_messages: 15
            cooldown: 45.0
            summary_max_tokens: 512
            summary_temperature: 0.1
            summary_model:
              provider: mock
    """)
    try:
        s = load_agent_from_yaml(f)
        c = s.compaction
        assert c.max_messages == 50
        assert c.max_tokens_estimate == 60_000
        assert c.keep_last == 7
        assert c.min_messages == 15
        assert c.cooldown == 45.0
        assert c.summary_max_tokens == 512
        assert c.summary_temperature == 0.1
        assert c.summary_model is not None
    finally:
        os.unlink(f)


def test_budget_all_fields():
    f = yaml_file("""
        agent:
          name: budget-test
          model:
            provider: mock
          instructions: hi
          budget:
            max_usd: 5.00
            on_exceed: stop
            warn_at: 0.7
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.budget.max_usd == 5.0
        assert s.budget.on_exceed == "stop"
        assert s.budget.warn_at == 0.7
    finally:
        os.unlink(f)


def test_governance_phases():
    f = yaml_file("""
        agent:
          name: gov-test
          model:
            provider: mock
          instructions: hi
          governance:
            initial_phase: read
            phases:
              read: [read_file, grep, glob, list_files]
              write: [read_file, write_file, edit_file]
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.governance.current_phase == "read"
        assert "grep" in s.governance.phases["read"]
        assert "write_file" in s.governance.phases["write"]
    finally:
        os.unlink(f)


def test_tool_retry_all_fields():
    f = yaml_file("""
        agent:
          name: retry-test
          model:
            provider: mock
          instructions: hi
          tool_retry:
            max_attempts: 5
            backoff_base: 2.0
            backoff_max: 30.0
            jitter: 0.5
    """)
    try:
        s = load_agent_from_yaml(f)
        r = s.tool_retry
        assert r.max_attempts == 5
        assert r.backoff_base == 2.0
        assert r.backoff_max == 30.0
        assert r.jitter == 0.5
    finally:
        os.unlink(f)


def test_approval_gate():
    f = yaml_file("""
        agent:
          name: gate-test
          model:
            provider: mock
          instructions: hi
          approval_gate:
            backend: cli
            approved_by_default: true
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.approval_gate.backend == "cli"
        assert s.approval_gate.approved_by_default is True
    finally:
        os.unlink(f)


def test_detect_false_empties_detectors():
    f = yaml_file("""
        agent:
          name: nodetect
          model:
            provider: mock
          instructions: hi
          detect: false
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.detectors == []
    finally:
        os.unlink(f)


def test_add_date_add_env_compose():
    f = yaml_file("""
        agent:
          name: ctx-test
          model:
            provider: mock
          instructions: base
          add_date: true
          add_environment_info: true
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.system_prompt_hook is not None
        result = s.system_prompt_hook("base")
        assert "base" in result
        assert any(yr in result for yr in ["2025", "2026", "2027", "date"])
    finally:
        os.unlink(f)


def test_env_expansion():
    os.environ["TVASTAR_TEST_NAME"] = "env-expanded"
    f = yaml_file("""
        agent:
          name: ${TVASTAR_TEST_NAME}
          model:
            provider: mock
          instructions: hi
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.name == "env-expanded"
    finally:
        os.unlink(f)
        del os.environ["TVASTAR_TEST_NAME"]


def test_description_stored_in_metadata():
    f = yaml_file("""
        agent:
          name: desc-test
          description: Fixes CI failures
          model:
            provider: mock
          instructions: hi
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.metadata.get("description") == "Fixes CI failures"
    finally:
        os.unlink(f)


def test_all_scalar_params():
    f = yaml_file("""
        agent:
          name: scalars
          model:
            provider: mock
          instructions: hi
          max_steps: 99
          max_tokens: 2048
          temperature: 0.5
          thinking_level: medium
          max_task_depth: 2
          tool_concurrency: 4
          structured_retries: 5
          memory_cap_mb: 128.0
          scrub_after_run: true
          compress_tool_output: false
          memory_extraction: pattern
    """)
    try:
        s = load_agent_from_yaml(f)
        assert s.max_steps == 99
        assert s.max_tokens == 2048
        assert s.temperature == 0.5
        assert s.thinking_level == "medium"
        assert s.max_task_depth == 2
        assert s.tool_concurrency == 4
        assert s.structured_retries == 5
        assert s.memory_cap_mb == 128.0
        assert s.scrub_after_run is True
        assert s.memory_extraction == "pattern"
    finally:
        os.unlink(f)


def test_named_toolset_ref():
    f = yaml_file("""
        toolsets:
          coding:
            - default
        agent:
          name: named-toolset
          model:
            provider: mock
          instructions: hi
          tools: coding
    """)
    try:
        s = load_agent_from_yaml(f)
        assert len(s.tools) > 0
    finally:
        os.unlink(f)


def test_mcp_toolset_warns_and_skips():
    f = yaml_file("""
        agent:
          name: mcp-test
          model:
            provider: mock
          instructions: hi
          tools:
            - type: mcp
              command: npx
              args: ["-y", "some-server"]
    """)
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            s = load_agent_from_yaml(f)
        assert any("mcp" in str(x.message).lower() for x in w)
        assert s.tools is None or len(s.tools) == 0
    finally:
        os.unlink(f)


# ── error cases ───────────────────────────────────────────────────────────────

def test_error_missing_name():
    f = yaml_file("""
        agent:
          model:
            provider: mock
          instructions: hi
    """)
    try:
        with pytest.raises(TvastarError, match="name"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)


def test_error_missing_model():
    f = yaml_file("""
        agent:
          name: test
          instructions: hi
    """)
    try:
        with pytest.raises(TvastarError, match="model"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)


def test_error_unknown_sandbox_ref():
    f = yaml_file("""
        agent:
          name: test
          model:
            provider: mock
          instructions: hi
          sandbox: does-not-exist
    """)
    try:
        with pytest.raises(TvastarError, match="does-not-exist"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)


def test_error_governance_initial_phase_not_in_phases():
    f = yaml_file("""
        agent:
          name: test
          model:
            provider: mock
          instructions: hi
          governance:
            initial_phase: typo
            phases:
              read: [read_file]
    """)
    try:
        with pytest.raises(TvastarError, match="typo"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)


def test_error_reserved_key_in_metadata():
    f = yaml_file("""
        agent:
          name: test
          model:
            provider: mock
          instructions: hi
          metadata:
            max_steps: 99
    """)
    try:
        with pytest.raises(TvastarError, match="max_steps"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)


def test_error_unknown_provider():
    f = yaml_file("""
        agent:
          name: test
          model:
            provider: fakeai
            name: x
          instructions: hi
    """)
    try:
        with pytest.raises(TvastarError, match="fakeai"):
            load_agent_from_yaml(f)
    finally:
        os.unlink(f)
