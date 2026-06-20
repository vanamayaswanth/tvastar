"""Tvastar — the framework for loop engineering.

    Agent = Model + Harness
    Loop  = Agent + Schedule + Verify + Handoff

Build agents that run once or loops that run forever,
with the same reliable harness underneath.

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
from .assurance import AssurancePolicy, ExecutionReceipt, SLABreached, TrustLog
from .approval import (
    ApprovalDenied,
    ApprovalGate,
    ApprovalRequest,
    ApprovalTimeout,
    require_approval,
    set_default_gate,
)
from .bench import BenchReport, BenchResult, BenchSuite, BenchTask, swe_bench_tasks
from .boundary import looks_like_injection, scan_for_injection, wrap_untrusted
from .compaction import CompactionPolicy, compact_messages, compact_session, should_compact
from .cost import COST_TABLE, BudgetExceeded, BudgetPolicy, Cost, cost_for_model
from .detect import (
    Finding,
    RunContext,
    Severity,
    default_detectors,
    prompt_injection,
    run_detectors,
)
from .dispatch import (
    DispatchEvent,
    DispatchInput,
    cancel_dispatch,
    dispatch,
    dispatch_and_wait,
    list_active_dispatches,
    observe_dispatch,
)
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
from .eval import (
    Case,
    CaseResult,
    EvalReport,
    EvalSuite,
    assert_contains,
    assert_cost_under,
    assert_custom,
    assert_json,
    assert_no_findings,
    assert_not_contains,
    assert_ok,
    assert_pydantic,
    assert_steps_under,
)
from .graph import GraphResult, TaskGraph
from .harness import Harness
from .loop import FailureKind, Loop, LoopConfig, LoopEvent, LoopGeneration, LoopRun, LoopState
from .loop.audit import ReadinessLevel, audit_loop
from .quality import LoopQualityReport, score_run
from .loop.handoff import CallbackHandoff, HandoffPolicy, LogHandoff, MultiHandoff
from .loop.patterns import (
    ChangelogDrafter,
    CISweeper,
    DailyTriage,
    DependencySweeper,
    MakerChecker,
    PostMergeCleanup,
    PRBabysitter,
)
from .masking import GovernancePolicy, MaskContext, ToolPolicy, allow_only, deny, phases
from .mcp import MCPClient, connect_mcp_server
from .memory import FileStore, InMemoryStore, Memory, Store
from .model import MockModel, Model, ModelRetryPolicy
from .observability import (
    ConsoleExporter,
    JSONLExporter,
    OTelExporter,
    Tracer,
)
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
from .profiles import MAX_TASK_DEPTH, AgentProfile, define_agent_profile
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
    tool,
    web_browse,
    web_search,
    web_toolset,
)
from .types import (
    ImageBlock,
    Message,
    ModelResponse,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from .ui import create_ui_app, run_ui
from .workflow import (
    RunEvent,
    RunRegistry,
    RunStatus,
    Workflow,
    WorkflowContext,
    WorkflowHarness,
    WorkflowRun,
    workflow,
)
from .workflow import (
    cli_logs as workflow_logs,
)
from .wrap import WrappedResult, wrap

__version__ = "0.15.1"

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
    "ModelRetryPolicy",
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
    # tool masking + invocation-layer governance
    "MaskContext",
    "ToolPolicy",
    "allow_only",
    "deny",
    "phases",
    "GovernancePolicy",
    "Tracer",
    "ConsoleExporter",
    "JSONLExporter",
    "OTelExporter",
    "ImageBlock",
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
    # loop engineering
    "Loop",
    "LoopConfig",
    "LoopState",
    "LoopRun",
    "LoopEvent",
    "LoopGeneration",
    "FailureKind",
    "HandoffPolicy",
    "LogHandoff",
    "CallbackHandoff",
    "MultiHandoff",
    "CISweeper",
    "PRBabysitter",
    "DailyTriage",
    "DependencySweeper",
    "PostMergeCleanup",
    "ChangelogDrafter",
    "MakerChecker",
    # loop readiness audit
    "ReadinessLevel",
    "audit_loop",
    # loop quality scoring
    "LoopQualityReport",
    "score_run",
    # adapter layer — wrap any external agent loop
    "wrap",
    "WrappedResult",
    # verifiable execution — signed receipts + SLA enforcement
    "AssurancePolicy",
    "ExecutionReceipt",
    "TrustLog",
    "SLABreached",
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
