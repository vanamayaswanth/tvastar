"""Unit tests for the @workflow decorator.

Covers Requirement 21 (REQ-WORKFLOW-001) acceptance criteria:
  21.1 .run() creates WorkflowRun with status RUNNING
  21.2 Completion transitions to COMPLETED with output
  21.3 Exception transitions to FAILED with error message
  21.4 RunRegistry persists and retrieves runs by run_id
  21.5 WorkflowContext.init(spec) provides WorkflowHarness
"""

from tvastar import create_agent, workflow
from tvastar.model import MockModel
from tvastar.workflow import (
    RunRegistry,
    RunStatus,
    WorkflowContext,
    WorkflowHarness,
    WorkflowRun,
)
from tvastar.memory.store import InMemoryStore


# ── Helpers ───────────────────────────────────────────────────────────────────


def _agent(script=None):
    return create_agent(
        "test-workflow",
        model=MockModel(script or []),
        instructions="be helpful",
    )


# ── 21.1: .run() creates WorkflowRun with status RUNNING ─────────────────────


async def test_run_creates_workflow_run_with_running_status():
    """During execution the WorkflowRun is persisted with RUNNING status."""
    observed_status = []

    @workflow
    async def check_running(ctx: WorkflowContext) -> dict:
        # Inside the workflow, the run should already be RUNNING in the registry
        run = ctx._registry.get(ctx.run_id)
        observed_status.append(run.status)
        return {"ok": True}

    await check_running.run({"x": 1})
    assert observed_status[0] == RunStatus.RUNNING


async def test_run_returns_workflow_run_with_run_id():
    """The run() method returns a WorkflowRun with a valid run_id."""

    @workflow
    async def simple(ctx: WorkflowContext) -> dict:
        return {}

    run = await simple.run()
    assert isinstance(run, WorkflowRun)
    assert run.run_id.startswith("run_")


async def test_run_accepts_custom_run_id():
    """A caller can supply a custom run_id."""

    @workflow
    async def tagged(ctx: WorkflowContext) -> dict:
        return {"id": ctx.run_id}

    run = await tagged.run(payload=None, run_id="custom-123")
    assert run.run_id == "custom-123"
    assert run.output == {"id": "custom-123"}


async def test_run_records_workflow_name():
    """WorkflowRun carries the workflow name from the decorator."""

    @workflow(name="my-pipeline")
    async def pipeline(ctx: WorkflowContext) -> dict:
        return {}

    run = await pipeline.run()
    assert run.workflow_name == "my-pipeline"


async def test_run_uses_function_name_as_default():
    """Without explicit name, the function name is used."""

    @workflow
    async def auto_named(ctx: WorkflowContext) -> dict:
        return {}

    run = await auto_named.run()
    assert run.workflow_name == "auto_named"


# ── 21.2: Completion transitions to COMPLETED with output ─────────────────────


async def test_successful_completion_sets_completed_status():
    """A workflow that returns normally has status COMPLETED."""

    @workflow
    async def succeed(ctx: WorkflowContext) -> dict:
        return {"result": 42}

    run = await succeed.run()
    assert run.status == RunStatus.COMPLETED


async def test_completion_stores_output():
    """The return value of the workflow function becomes run.output."""

    @workflow
    async def produce(ctx: WorkflowContext) -> dict:
        return {"items": [1, 2, 3]}

    run = await produce.run({"input": "data"})
    assert run.output == {"items": [1, 2, 3]}


async def test_completion_sets_ended_at():
    """Completed runs have ended_at set."""

    @workflow
    async def timed(ctx: WorkflowContext) -> dict:
        return {}

    run = await timed.run()
    assert run.ended_at is not None
    assert run.ended_at >= run.started_at


async def test_completion_has_run_end_event():
    """Completed runs have a run_end event with status=completed."""

    @workflow
    async def evented(ctx: WorkflowContext) -> dict:
        return {"done": True}

    run = await evented.run()
    end_events = [e for e in run.events if e.type == "run_end"]
    assert len(end_events) == 1
    assert end_events[0].data["status"] == "completed"


# ── 21.3: Exception transitions to FAILED with error message ──────────────────


async def test_exception_sets_failed_status():
    """A workflow that raises has status FAILED."""

    @workflow
    async def fail(ctx: WorkflowContext) -> dict:
        raise RuntimeError("something broke")

    run = await fail.run()
    assert run.status == RunStatus.FAILED


async def test_exception_stores_error_message():
    """The error field contains the exception type and message."""

    @workflow
    async def explode(ctx: WorkflowContext) -> dict:
        raise ValueError("bad input: 'xyz'")

    run = await explode.run()
    assert "ValueError" in run.error
    assert "bad input" in run.error


async def test_failed_run_has_no_output():
    """A failed run has output=None."""

    @workflow
    async def no_output(ctx: WorkflowContext) -> dict:
        raise TypeError("wrong type")

    run = await no_output.run()
    assert run.output is None


async def test_failed_run_sets_ended_at():
    """Failed runs still have ended_at set."""

    @workflow
    async def timed_fail(ctx: WorkflowContext) -> dict:
        raise Exception("fail")

    run = await timed_fail.run()
    assert run.ended_at is not None
    assert run.ended_at >= run.started_at


async def test_failed_run_has_run_end_event():
    """Failed runs have a run_end event with status=failed."""

    @workflow
    async def evented_fail(ctx: WorkflowContext) -> dict:
        raise KeyError("missing")

    run = await evented_fail.run()
    end_events = [e for e in run.events if e.type == "run_end"]
    assert len(end_events) == 1
    assert end_events[0].data["status"] == "failed"
    assert "KeyError" in end_events[0].data["error"]


# ── 21.4: RunRegistry persists and retrieves runs by run_id ───────────────────


async def test_registry_persists_completed_run():
    """Completed runs are retrievable from the registry by run_id."""
    registry = RunRegistry(store=InMemoryStore())

    @workflow(registry=registry)
    async def stored(ctx: WorkflowContext) -> dict:
        return {"persisted": True}

    run = await stored.run()
    retrieved = registry.get(run.run_id)
    assert retrieved is not None
    assert retrieved.run_id == run.run_id
    assert retrieved.status == RunStatus.COMPLETED
    assert retrieved.output == {"persisted": True}


async def test_registry_persists_failed_run():
    """Failed runs are also persisted to the registry."""
    registry = RunRegistry(store=InMemoryStore())

    @workflow(registry=registry)
    async def stored_fail(ctx: WorkflowContext) -> dict:
        raise RuntimeError("oops")

    run = await stored_fail.run()
    retrieved = registry.get(run.run_id)
    assert retrieved is not None
    assert retrieved.status == RunStatus.FAILED
    assert "RuntimeError" in retrieved.error


async def test_registry_get_returns_none_for_unknown():
    """Getting a non-existent run_id returns None."""
    registry = RunRegistry(store=InMemoryStore())
    assert registry.get("nonexistent-id") is None


async def test_registry_list_runs_returns_all():
    """list_runs returns all runs for a workflow."""
    registry = RunRegistry(store=InMemoryStore())

    @workflow(registry=registry)
    async def multi(ctx: WorkflowContext) -> dict:
        return {"n": ctx.payload}

    await multi.run(1)
    await multi.run(2)
    await multi.run(3)

    runs = registry.list_runs(workflow_name="multi")
    assert len(runs) == 3


async def test_registry_list_runs_filters_by_workflow_name():
    """list_runs can filter by workflow name."""
    registry = RunRegistry(store=InMemoryStore())

    @workflow(name="alpha", registry=registry)
    async def alpha(ctx: WorkflowContext) -> dict:
        return {}

    @workflow(name="beta", registry=registry)
    async def beta(ctx: WorkflowContext) -> dict:
        return {}

    await alpha.run()
    await alpha.run()
    await beta.run()

    alpha_runs = registry.list_runs(workflow_name="alpha")
    beta_runs = registry.list_runs(workflow_name="beta")
    assert len(alpha_runs) == 2
    assert len(beta_runs) == 1


async def test_registry_save_and_get_round_trip():
    """Manual save/get on the registry works correctly."""
    registry = RunRegistry(store=InMemoryStore())
    run = WorkflowRun(
        run_id="test-run-42",
        workflow_name="manual",
        status=RunStatus.COMPLETED,
        payload={"key": "value"},
        output={"result": "done"},
    )
    registry.save(run)

    retrieved = registry.get("test-run-42")
    assert retrieved is not None
    assert retrieved.workflow_name == "manual"
    assert retrieved.payload == {"key": "value"}
    assert retrieved.output == {"result": "done"}


# ── 21.5: WorkflowContext.init(spec) provides WorkflowHarness ─────────────────


async def test_context_init_returns_workflow_harness():
    """ctx.init(spec) returns a WorkflowHarness instance."""
    received_harness = []

    @workflow
    async def use_harness(ctx: WorkflowContext) -> dict:
        spec = _agent(["Hello from workflow."])
        harness = await ctx.init(spec)
        received_harness.append(harness)
        return {}

    await use_harness.run()
    assert len(received_harness) == 1
    assert isinstance(received_harness[0], WorkflowHarness)


async def test_workflow_harness_provides_session():
    """WorkflowHarness.session() opens a session that can prompt the agent."""

    @workflow
    async def agent_workflow(ctx: WorkflowContext) -> dict:
        spec = _agent(["Agent says hi."])
        harness = await ctx.init(spec)
        sess = await harness.session()
        result = await sess.prompt("hello")
        return {"reply": result.text}

    run = await agent_workflow.run()
    assert run.status == RunStatus.COMPLETED
    assert run.output["reply"] == "Agent says hi."


async def test_workflow_harness_session_reuses_same_name():
    """Calling session() with the same name returns the cached session."""

    @workflow
    async def reuse_session(ctx: WorkflowContext) -> dict:
        spec = _agent(["First.", "Second."])
        harness = await ctx.init(spec)
        s1 = await harness.session("main")
        s2 = await harness.session("main")
        return {"same": s1 is s2}

    run = await reuse_session.run()
    assert run.output["same"] is True


async def test_workflow_context_payload_accessible():
    """The context.payload is the input passed to run()."""

    @workflow
    async def read_payload(ctx: WorkflowContext) -> dict:
        return {"received": ctx.payload}

    run = await read_payload.run({"message": "hello"})
    assert run.output["received"] == {"message": "hello"}


async def test_workflow_context_logging():
    """WorkflowContext.log produces events in the run."""

    @workflow
    async def with_logging(ctx: WorkflowContext) -> dict:
        ctx.log.info("starting work")
        ctx.log.warn("something odd")
        return {}

    run = await with_logging.run()
    log_events = [e for e in run.events if e.type == "log"]
    assert len(log_events) == 2
    assert log_events[0].data["level"] == "info"
    assert log_events[0].data["message"] == "starting work"
    assert log_events[1].data["level"] == "warn"
