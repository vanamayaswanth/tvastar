"""Tvastar — a programmable agent harness framework for Python.

    Agent = Model + Harness

Quick start::

    import asyncio
    from tvastar import create_agent, Harness, default_toolset
    from tvastar.model import MockModel

    agent = create_agent(
        "assistant",
        model=MockModel(),
        instructions="You are a helpful coding agent.",
        tools=default_toolset(),
    )
    harness = Harness(agent)
    print(asyncio.run(harness.run("List the files in the workspace.")))
"""

from __future__ import annotations

from .agent import AgentSpec, create_agent
from .compaction import CompactionPolicy, compact_session, compact_messages, should_compact
from .durable import Checkpointer
from .errors import (
    ModelError,
    SandboxError,
    SecurityViolation,
    SkillError,
    ToolError,
    ToolNotFound,
    TvastarError,
)
from .detect import (
    Finding,
    RunContext,
    Severity,
    default_detectors,
    prompt_injection,
    run_detectors,
)
from .boundary import looks_like_injection, scan_for_injection, wrap_untrusted
from .masking import MaskContext, ToolPolicy, allow_only, deny, phases
from .harness import Harness
from .mcp import MCPClient, connect_mcp_server
from .memory import FileStore, InMemoryStore, Memory, Store
from .model import Model, MockModel
from .observability import (
    ConsoleExporter,
    JSONLExporter,
    OTelExporter,
    Tracer,
)
from .sandbox import (
    AuditEntry,
    CredentialFilter,
    ExecResult,
    LocalSandbox,
    ResourcePolicy,
    Sandbox,
    SecurityPolicy,
    VirtualSandbox,
)
from .session import RunResult, Session
from .skills import Skill, SkillLibrary, parse_skill
from .tools import (
    Tool,
    ToolContext,
    ToolRegistry,
    ToolRetryPolicy,
    default_toolset,
    web_toolset,
    web_browse,
    web_search,
    tool,
)
from .types import (
    Message,
    ModelResponse,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from .workflow import (
    Workflow,
    WorkflowContext,
    WorkflowHarness,
    WorkflowRun,
    RunEvent,
    RunRegistry,
    RunStatus,
    workflow,
    cli_logs as workflow_logs,
)
from .dispatch import (
    DispatchInput,
    DispatchEvent,
    dispatch,
    dispatch_and_wait,
    observe_dispatch,
    cancel_dispatch,
    list_active_dispatches,
)
from .profiles import AgentProfile, define_agent_profile, MAX_TASK_DEPTH
from .eval import (
    EvalSuite,
    Case,
    CaseResult,
    EvalReport,
    assert_contains,
    assert_not_contains,
    assert_ok,
    assert_steps_under,
    assert_json,
    assert_pydantic,
    assert_cost_under,
    assert_custom,
    assert_no_findings,
)
from .cost import Cost, BudgetPolicy, BudgetExceeded, cost_for_model, COST_TABLE
from .approval import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalDenied,
    ApprovalTimeout,
    require_approval,
    set_default_gate,
)
from .bench import BenchSuite, BenchTask, BenchResult, BenchReport, swe_bench_tasks
from .ui import create_ui_app, run_ui
from .graph import TaskGraph, GraphResult
from .outbound import (
    CampaignResult,
    EmailDraft,
    EmailSender,
    Lead,
    ResearchResult,
    ScoredLead,
    SendResult,
    StdoutSender,
    parse_csv,
    parse_leads,
    research_lead,
    run_campaign,
    score_lead,
    write_draft,
)

__version__ = "0.9.0"

__all__ = [
    "create_agent",
    "AgentSpec",
    "Harness",
    "Session",
    "RunResult",
    "workflow",
    "Workflow",
    "WorkflowContext",
    "WorkflowHarness",
    "WorkflowRun",
    "RunEvent",
    "RunRegistry",
    "RunStatus",
    "workflow_logs",
    "Model",
    "MockModel",
    "tool",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolRetryPolicy",
    "default_toolset",
    "web_toolset",
    "web_browse",
    "web_search",
    "Skill",
    "SkillLibrary",
    "parse_skill",
    "Sandbox",
    "VirtualSandbox",
    "LocalSandbox",
    "SecurityPolicy",
    "ResourcePolicy",
    "AuditEntry",
    "CredentialFilter",
    "ExecResult",
    "Store",
    "InMemoryStore",
    "FileStore",
    "Memory",
    "Checkpointer",
    "MCPClient",
    "connect_mcp_server",
    "Finding",
    "Severity",
    "RunContext",
    "default_detectors",
    "run_detectors",
    "prompt_injection",
    # content boundary / injection scan (honest mitigation, not protection)
    "wrap_untrusted",
    "scan_for_injection",
    "looks_like_injection",
    # tool masking
    "MaskContext",
    "ToolPolicy",
    "allow_only",
    "deny",
    "phases",
    "Tracer",
    "ConsoleExporter",
    "JSONLExporter",
    "OTelExporter",
    "Message",
    "ModelResponse",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "StreamEvent",
    "Usage",
    "TvastarError",
    "ModelError",
    "ToolError",
    "ToolNotFound",
    "SkillError",
    "SandboxError",
    "SecurityViolation",
    # compaction
    "CompactionPolicy",
    "compact_session",
    "compact_messages",
    "should_compact",
    # profiles / subagents
    "AgentProfile",
    "define_agent_profile",
    "MAX_TASK_DEPTH",
    # dispatch
    "dispatch",
    "dispatch_and_wait",
    "DispatchInput",
    "DispatchEvent",
    "observe_dispatch",
    "cancel_dispatch",
    "list_active_dispatches",
    # eval
    "EvalSuite",
    "Case",
    "CaseResult",
    "EvalReport",
    "assert_contains",
    "assert_not_contains",
    "assert_ok",
    "assert_steps_under",
    "assert_json",
    "assert_pydantic",
    "assert_cost_under",
    "assert_custom",
    "assert_no_findings",
    # cost tracking
    "Cost",
    "BudgetPolicy",
    "BudgetExceeded",
    "cost_for_model",
    "COST_TABLE",
    # approval gate
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalDenied",
    "ApprovalTimeout",
    "require_approval",
    "set_default_gate",
    # benchmarks
    "BenchSuite",
    "BenchTask",
    "BenchResult",
    "BenchReport",
    "swe_bench_tasks",
    # trace viewer UI
    "create_ui_app",
    "run_ui",
    # DAG-based parallel task execution
    "TaskGraph",
    "GraphResult",
    # outbound sales agent
    "run_campaign",
    "CampaignResult",
    "Lead",
    "parse_csv",
    "parse_leads",
    "ResearchResult",
    "research_lead",
    "ScoredLead",
    "score_lead",
    "EmailDraft",
    "write_draft",
    "EmailSender",
    "SendResult",
    "StdoutSender",
]
