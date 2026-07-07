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
from .assurance import (
    AssurancePolicy,
    ExecutionReceipt,
    RetentionPolicy,
    SLABreached,
    SanitizationPolicy,
    TokenVault,
    TrustLog,
)
from .approval import (
    ApprovalDenied,
    ApprovalGate,
    ApprovalRequest,
    ApprovalTimeout,
    ModelVerifier,
    require_approval,
    set_default_gate,
)
from .bench import BenchReport, BenchResult, BenchSuite, BenchTask, swe_bench_tasks
from .boundary import (
    InjectionScanResult,
    RedactionResult,
    looks_like_injection,
    redact_messages,
    register_injection_pattern,
    scan_for_injection,
    scan_messages_for_injection,
    wrap_untrusted,
)
from .compaction import CompactionPolicy, compact_messages, compact_session, should_compact
from .compressor import ToolOutputCompressor
from .cost import COST_TABLE, BudgetExceeded, BudgetPolicy, Cost, cost_for_model, register_model_cost
from .detect import (
    Finding,
    RunContext,
    Severity,
    default_detectors,
    detect_from_messages,
    prompt_injection,
    run_detectors,
)
from .dispatch import (
    DispatchEvent,
    DispatchInput,
    DispatchPool,
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
from .topology import auto_topology
from .harness import Harness
from .loop import FailureKind, Loop, LoopConfig, LoopEvent, LoopGeneration, LoopRun, LoopState
from .loop.audit import ReadinessLevel, audit_loop
from .quality import LoopQualityReport, score_pipeline, score_run
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
from .memory import FileStore, InMemoryStore, Memory, SQLiteStore, Store
from .model import MockModel, Model, ModelRetryPolicy
from .observability import (
    ConsoleExporter,
    JSONLExporter,
    OTelExporter,
    Tracer,
)
from .event_exporter import TvastarEventExporter
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
from .router import AgentPruner, AgentRouter
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
from .session import RunResult, Session, StructuredOutputError, register_overflow_phrase
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
from .tools.latchkey import latchkey_curl
from .types import (
    Detector,
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
    FileCheckpoint,
    RunEvent,
    RunRegistry,
    RunStatus,
    Workflow,
    WorkflowCheckpoint,
    WorkflowContext,
    WorkflowHarness,
    WorkflowRun,
    workflow,
)
from .workflow import (
    cli_logs as workflow_logs,
)
from .wrap import WrappedResult, wrap

# Fleet engineering layer — lazy-imported to avoid loading fleet submodules on
# plain `import tvastar`.  Symbols are resolved on first attribute access.
_FLEET_SYMBOLS = {
    "Fleet",
    "FleetConfig",
    "FleetBudgetConfig",
    "FleetRegistry",
    "FleetGateway",
    "SharedStateStore",
    "EventBus",
    "FleetBudget",
    "FleetObserver",
    "FleetDefaults",
    "FleetError",
}

_LAZY_MODULES = {"fleet": ".fleet"}


def __getattr__(name: str):
    if name in _LAZY_MODULES:
        import importlib

        mod = importlib.import_module(_LAZY_MODULES[name], __name__)
        globals()[name] = mod
        return mod
    if name in _FLEET_SYMBOLS:
        import importlib

        fleet_mod = importlib.import_module(".fleet", __name__)
        # Cache all fleet symbols at once to avoid repeated imports
        for sym in _FLEET_SYMBOLS:
            globals()[sym] = getattr(fleet_mod, sym)
        return globals()[name]
    raise AttributeError(f"module 'tvastar' has no attribute {name!r}")

__version__ = "0.22.0"

__all__ = [
    "create_agent",
    "AgentSpec",
    "Harness",
    "Session",
    "RunResult",
    "workflow",
    "Workflow",
    "WorkflowCheckpoint",
    "FileCheckpoint",
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
    "SQLiteStore",
    "Memory",
    "Checkpointer",
    "MCPClient",
    "connect_mcp_server",
    "Finding",
    "Severity",
    "RunContext",
    "default_detectors",
    "detect_from_messages",
    "run_detectors",
    "prompt_injection",
    # content boundary / injection scan (honest mitigation, not protection)
    "wrap_untrusted",
    "scan_for_injection",
    "scan_messages_for_injection",
    "InjectionScanResult",
    "looks_like_injection",
    "register_injection_pattern",
    "redact_messages",
    "RedactionResult",
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
    "TvastarEventExporter",
    "Detector",
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
    "StructuredOutputError",
    # overflow phrase registration
    "register_overflow_phrase",
    # compaction
    "CompactionPolicy",
    "compact_session",
    "compact_messages",
    "should_compact",
    # tool output compression
    "ToolOutputCompressor",
    # latchkey authenticated request tool
    "latchkey_curl",
    # profiles / subagents
    "AgentProfile",
    "define_agent_profile",
    "MAX_TASK_DEPTH",
    "AgentRouter",
    "AgentPruner",
    # dispatch
    "dispatch",
    "dispatch_and_wait",
    "DispatchInput",
    "DispatchEvent",
    "DispatchPool",
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
    "register_model_cost",
    "COST_TABLE",
    # approval gate
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalDenied",
    "ApprovalTimeout",
    "ModelVerifier",
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
    # DAG-based parallel task execution + auto-topology
    "TaskGraph",
    "GraphResult",
    "auto_topology",
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
    "score_pipeline",
    # adapter layer — wrap any external agent loop
    "wrap",
    "WrappedResult",
    # verifiable execution — signed receipts + SLA enforcement
    "AssurancePolicy",
    "ExecutionReceipt",
    "RetentionPolicy",
    "SanitizationPolicy",
    "TokenVault",
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
    # Fleet engineering
    "Fleet",
    "FleetConfig",
    "FleetBudgetConfig",
    "FleetRegistry",
    "FleetGateway",
    "SharedStateStore",
    "EventBus",
    "FleetBudget",
    "FleetObserver",
    "FleetDefaults",
    "FleetError",
]
