import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel

from tvastar import Harness, create_agent, default_toolset
from tvastar.compaction import CompactionPolicy
from tvastar.model import MockModel
from tvastar.types import ImageBlock, TextBlock, ToolUseBlock


def make_agent(script):
    return create_agent(
        "test",
        model=MockModel(script),
        instructions="be helpful",
        tools=default_toolset(),
    )


async def test_simple_text_turn():
    agent = make_agent(["Hello there."])
    h = Harness(agent)
    r = await h.run("hi")
    assert r.text == "Hello there."
    assert r.steps == 1
    assert r.stopped == "end_turn"


async def test_tool_loop_writes_and_reads():
    script = [
        ToolUseBlock(name="write_file", input={"path": "a.txt", "content": "data"}),
        ToolUseBlock(name="read_file", input={"path": "a.txt"}),
        "Wrote and read the file.",
    ]
    agent = make_agent(script)
    h = Harness(agent)
    sess = h.session()
    async with sess:
        r = await sess.prompt("do it")
        assert r.steps == 3
        assert sess.sandbox.fs.read("a.txt") == "data"
    assert "Wrote and read" in r.text


async def test_max_steps_guard():
    # Model always asks for a tool -> would loop forever without the guard.
    forever = [ToolUseBlock(name="list_files", input={}) for _ in range(50)]
    agent = create_agent("loopy", model=MockModel(forever), tools=default_toolset(), max_steps=4)
    h = Harness(agent)
    r = await h.run("go")
    assert r.steps == 4
    assert r.stopped == "max_steps"


async def test_tool_error_is_fed_back_not_raised():
    # read a missing file -> tool returns an error string, loop continues
    script = [
        ToolUseBlock(name="read_file", input={"path": "nope.txt"}),
        "Handled the missing file.",
    ]
    agent = make_agent(script)
    h = Harness(agent)
    r = await h.run("read nope")
    assert r.stopped == "end_turn"
    assert "Handled" in r.text


# ── Priority 1: structured output retry ──────────────────────────────────────


class _User(BaseModel):
    name: str
    age: int


async def test_structured_output_succeeds_first_try():
    agent = make_agent(['{"name": "Alice", "age": 30}'])
    r = await Harness(agent).run("get user", result=_User)
    assert isinstance(r.data, _User)
    assert r.data.name == "Alice"
    assert r.data.age == 30


async def test_structured_output_retries_on_bad_json():
    # First response is invalid JSON; second is valid.
    script = ["not json at all", '{"name": "Bob", "age": 25}']
    agent = make_agent(script)
    r = await Harness(agent).run("get user", result=_User)
    assert isinstance(r.data, _User), f"Expected _User, got {type(r.data)}: {r.data!r}"
    assert r.data.name == "Bob"


async def test_structured_output_falls_back_after_max_retries():
    # All responses are garbage — after _STRUCTURED_RETRIES attempts, data is the last raw text.
    script = ["bad", "also bad", "still bad"]
    agent = make_agent(script)
    r = await Harness(agent).run("get user", result=_User)
    # data falls back to the raw final text, not a _User instance
    assert not isinstance(r.data, _User)
    assert isinstance(r.data, str)


# ── Priority 2: context overflow → compact + retry ───────────────────────────


async def test_overflow_triggers_compaction_and_retries():
    overflow = RuntimeError("context_length_exceeded: prompt is too long for this model")
    success = '{"name": "Carol", "age": 40}'

    # Build an agent with compaction enabled so overflow recovery fires.
    summary_model = MockModel(["Summary of earlier messages."])
    policy = CompactionPolicy(
        max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model
    )
    agent = create_agent(
        "overflow-test",
        model=MockModel([overflow, success]),
        instructions="test",
        tools=default_toolset(),
        compaction=policy,
    )
    # Seed enough messages to make compaction possible, then trigger overflow.
    h = Harness(agent)
    sess = h.session()
    async with sess:
        # Pre-populate history so compact_session has something to work with.
        from tvastar.types import Message

        sess.messages += [Message("user", "msg1"), Message("assistant", "reply1")]
        r = await sess.prompt("get user", result=_User)
    assert isinstance(r.data, _User), f"Expected _User after overflow recovery, got {r.data!r}"
    assert r.data.name == "Carol"


async def test_overflow_without_compaction_policy_reraises():
    overflow = RuntimeError("context_length_exceeded: prompt too long")
    agent = create_agent(
        "no-compact",
        model=MockModel([overflow]),
        instructions="test",
        # No compaction policy — overflow must propagate.
    )
    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        await Harness(agent).run("hi")


# ── Priority 3: compaction model override ────────────────────────────────────


async def test_compaction_uses_summary_model():
    cheap_model = MockModel(["Compact summary."])
    main_model = MockModel(["Final answer."])

    policy = CompactionPolicy(min_messages=3, keep_last=1, summary_model=cheap_model)
    agent = create_agent("summary-model-test", model=main_model, instructions="test")

    from tvastar.compaction import compact_session
    from tvastar.types import Message

    h = Harness(agent)
    sess = h.session()
    async with sess:
        sess.messages = [
            Message("user", "turn 1"),
            Message("assistant", "reply 1"),
            Message("user", "turn 2"),
            Message("assistant", "reply 2"),
        ]
        compacted = await compact_session(sess, policy=policy, force=True)

    assert compacted, "compact_session should have run"
    # cheap_model cursor advanced — it (not main_model) did the summarisation.
    assert cheap_model._cursor == 1, "summary_model should have been called for compaction"
    assert main_model._cursor == 0, "main model should not have been used for compaction"


# ── Feature #5: image input ───────────────────────────────────────────────────


async def test_image_block_appended_to_message():
    """ImageBlock passed to prompt() is included as a content block alongside the text."""
    agent = create_agent("img-test", model=MockModel(["Got your image."]), instructions="")
    h = Harness(agent)
    sess = h.session()
    async with sess:
        img = ImageBlock(data="abc123", media_type="image/png", source_type="base64")
        r = await sess.prompt("describe this image", images=[img])
    assert r.text == "Got your image."
    # The first (and only) user message should contain both text and image blocks.
    user_msgs = [m for m in sess.messages if m.role == "user"]
    assert user_msgs, "No user messages found"
    blocks = user_msgs[0].blocks
    types = [type(b).__name__ for b in blocks]
    assert "TextBlock" in types
    assert "ImageBlock" in types


async def test_image_block_without_images_sends_plain_string():
    """When no images are provided, the user message is a plain string (no block list)."""
    agent = create_agent("no-img", model=MockModel(["Plain reply."]), instructions="")
    h = Harness(agent)
    sess = h.session()
    async with sess:
        r = await sess.prompt("no image here")
    assert r.text == "Plain reply."
    user_msgs = [m for m in sess.messages if m.role == "user"]
    assert user_msgs
    # Content should be plain text, not a block list with ImageBlock
    img_blocks = [b for b in user_msgs[0].blocks if isinstance(b, ImageBlock)]
    assert img_blocks == []


async def test_anthropic_image_translation_base64():
    """_to_anthropic_messages() converts an ImageBlock (base64) to the Anthropic wire format."""
    from tvastar.model.anthropic import AnthropicModel
    from tvastar.types import Message

    m = AnthropicModel.__new__(AnthropicModel)
    img = ImageBlock(data="deadbeef", media_type="image/png", source_type="base64")
    msgs = [Message("user", [TextBlock(text="look"), img])]
    out = m._to_anthropic_messages(msgs)
    assert len(out) == 1
    content = out[0]["content"]
    types = [c["type"] for c in content]
    assert "text" in types
    assert "image" in types
    img_block = next(c for c in content if c["type"] == "image")
    assert img_block["source"]["type"] == "base64"
    assert img_block["source"]["data"] == "deadbeef"
    assert img_block["source"]["media_type"] == "image/png"


async def test_anthropic_image_translation_url():
    """_to_anthropic_messages() converts an ImageBlock (url) to the Anthropic wire format."""
    from tvastar.model.anthropic import AnthropicModel
    from tvastar.types import Message

    m = AnthropicModel.__new__(AnthropicModel)
    img = ImageBlock(data="https://example.com/pic.jpg", source_type="url")
    msgs = [Message("user", [TextBlock(text="url image"), img])]
    out = m._to_anthropic_messages(msgs)
    content = out[0]["content"]
    img_block = next(c for c in content if c["type"] == "image")
    assert img_block["source"]["type"] == "url"
    assert img_block["source"]["url"] == "https://example.com/pic.jpg"


async def test_openai_image_translation_base64():
    """_to_openai() converts an ImageBlock (base64) to a data: URL image_url part."""
    from tvastar.model.openai import OpenAIModel
    from tvastar.types import Message

    m = OpenAIModel.__new__(OpenAIModel)
    img = ImageBlock(data="deadbeef", media_type="image/jpeg", source_type="base64")
    msgs = [Message("user", [TextBlock(text="look"), img])]
    out = m._to_openai(msgs, system=None)
    assert len(out) == 1
    content = out[0]["content"]
    assert isinstance(content, list)
    img_part = next(p for p in content if p["type"] == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ── Feature #7: workspace skill discovery ─────────────────────────────────────


async def test_skill_library_from_workspace_discovers_skills():
    """from_workspace() loads *.md files from .agents/skills/."""
    from tvastar.skills import SkillLibrary

    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / ".agents" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "summarise.md").write_text(
            "---\nname: summarise\ndescription: Summarise text\n---\nSummarise the following.",
            encoding="utf-8",
        )
        (skills_dir / "translate.md").write_text(
            "---\nname: translate\ndescription: Translate text\n---\nTranslate to English.",
            encoding="utf-8",
        )
        lib = SkillLibrary.from_workspace(cwd=tmpdir)

    assert len(lib) == 2
    assert "summarise" in lib.names()
    assert "translate" in lib.names()
    assert lib.get("summarise").description == "Summarise text"


async def test_skill_library_from_workspace_empty_when_no_dir():
    """from_workspace() returns an empty library if .agents/skills/ doesn't exist."""
    from tvastar.skills import SkillLibrary

    with tempfile.TemporaryDirectory() as tmpdir:
        lib = SkillLibrary.from_workspace(cwd=tmpdir)

    assert len(lib) == 0


# ── Feature #8: observability content_filter ──────────────────────────────────


async def test_content_filter_called_before_export():
    """Tracer.content_filter mutates spans before they reach exporters."""
    from tvastar.observability import Span, Tracer

    received: list[Span] = []

    class CapturingExporter:
        def export(self, span: Span) -> None:
            received.append(span)

    filtered: list[Span] = []

    def redact(span: Span) -> Span:
        filtered.append(span)
        return Span(
            name=span.name,
            span_id=span.span_id,
            parent_id=span.parent_id,
            start=span.start,
            end=span.end,
            attributes={**span.attributes, "redacted": True},
        )

    tracer = Tracer([CapturingExporter()], content_filter=redact)
    with tracer.span("test_op", attributes={"secret": "password"}):
        pass

    assert filtered, "content_filter was not called"
    assert received, "exporter never received a span"
    assert received[0].attributes.get("redacted") is True
    assert "secret" not in received[0].attributes or received[0].attributes.get("redacted")


async def test_content_filter_failure_does_not_break_run():
    """A crashing content_filter must not propagate — the span still reaches exporters."""
    from tvastar.observability import Span, Tracer

    received: list[Span] = []

    class CapturingExporter:
        def export(self, span: Span) -> None:
            received.append(span)

    def bad_filter(_span: Span) -> Span:
        raise RuntimeError("filter exploded")

    tracer = Tracer([CapturingExporter()], content_filter=bad_filter)
    with tracer.span("safe_op") as _span:
        pass

    # The span must still be exported despite the filter crash.
    assert received, "Span was swallowed after filter failure"


# ── Feature #9: xhigh thinking (OpenAI side) ─────────────────────────────────


async def test_openai_xhigh_capped_at_high():
    """xhigh thinking_level maps to reasoning_effort='high' for OpenAI (no native xhigh)."""
    from tvastar.model.openai import OpenAIModel

    captured: dict = {}

    class StubClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    captured.update(kwargs)
                    # Minimal stub response
                    from unittest.mock import MagicMock

                    resp = MagicMock()
                    resp.choices[0].finish_reason = "stop"
                    resp.choices[0].message.content = "done"
                    resp.choices[0].message.tool_calls = []
                    resp.usage.prompt_tokens = 10
                    resp.usage.completion_tokens = 5
                    return resp

    m = OpenAIModel(model="o1", client=StubClient())

    from tvastar.types import Message

    await m.generate([Message("user", "hi")], thinking_level="xhigh")
    assert captured.get("reasoning_effort") == "high", (
        f"Expected 'high', got {captured.get('reasoning_effort')!r}"
    )


# ── Fix 1+2: TaskGraph deadlock + session pollution ───────────────────────────


async def test_taskgraph_upstream_failure_does_not_deadlock():
    """If task A fails, task B (which depends on A) must not hang."""
    from tvastar import TaskGraph

    fail_agent = create_agent(
        "fail-agent",
        model=MockModel([RuntimeError("task A exploded")]),
        instructions="",
    )
    h = Harness(fail_agent)
    g = TaskGraph(h)
    g.task("a", "do task a")
    g.task("b", "do task b", depends_on=["a"])

    with pytest.raises(RuntimeError, match="task A exploded|Task 'a' failed"):
        await g.run()


async def test_taskgraph_rerun_gets_fresh_sessions():
    """Re-running a graph must start with empty sessions (no history contamination)."""
    from tvastar import TaskGraph

    prompts_seen: list[str] = []

    class SpyModel(MockModel):
        async def generate(self, messages, **kw):
            prompts_seen.append(messages[-1].text if messages else "")
            return await super().generate(messages, **kw)

    agent = create_agent("spy", model=SpyModel(["run1", "run2", "run3", "run4"]), instructions="")
    h = Harness(agent)

    g1 = TaskGraph(h)
    g1.task("t", "first run prompt")
    await g1.run()

    history_after_run1 = len(prompts_seen)

    g2 = TaskGraph(h)
    g2.task("t", "second run prompt")
    await g2.run()

    # The second run should only have seen its own prompt, not the first run's messages
    assert prompts_seen[history_after_run1] == "second run prompt"


# ── Fix 3: Harness session registry memory leak ───────────────────────────────


async def test_harness_run_releases_anonymous_session():
    """harness.run() (no session_id) must remove the session from the registry afterward."""
    agent = create_agent("mem-test", model=MockModel(["done"]), instructions="")
    h = Harness(agent)
    assert len(h._sessions) == 0
    await h.run("hello")
    assert len(h._sessions) == 0, "Anonymous session was not released after run()"


async def test_harness_named_run_keeps_session():
    """harness.run(session_id=...) must keep the session in the registry for reuse."""
    agent = create_agent("named-sess", model=MockModel(["a", "b"]), instructions="")
    h = Harness(agent)
    await h.run("first", session_id="my-thread")
    assert "my-thread" in h._sessions, "Named session should remain in registry"


async def test_harness_fan_out_releases_sessions():
    """fan_out() must release each session from the registry after it completes."""
    agent = create_agent("fan-mem", model=MockModel(["a", "b", "c"]), instructions="")
    h = Harness(agent)
    await h.fan_out(["p1", "p2", "p3"])
    assert len(h._sessions) == 0, "fan_out sessions were not released"


# ── Fix 4: context overflow auto-recovery without explicit CompactionPolicy ───


async def test_overflow_with_enough_history_auto_recovers():
    """Overflow self-heals via default compaction when session has enough messages."""
    from tvastar.types import Message as Msg

    overflow = RuntimeError("context_length_exceeded: prompt too long")
    summary_model = MockModel(["Compact summary."])
    main_model = MockModel([overflow, "recovered reply"])

    # Inject a cheap summary_model via a custom policy so we don't need the real model
    from tvastar.compaction import CompactionPolicy

    agent = create_agent(
        "auto-recover",
        model=main_model,
        instructions="",
        compaction=CompactionPolicy(keep_last=1, min_messages=2, summary_model=summary_model),
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        # Pre-populate enough history that compaction can trim it
        sess.messages += [Msg("user", "old1"), Msg("assistant", "old2")]
        r = await sess.prompt("new prompt")

    assert r.text == "recovered reply"
    assert r.stopped == "end_turn"


# ── Fix 5: structured-output fallback emits a Finding ────────────────────────


async def test_structured_output_fallback_produces_warning_finding():
    """When structured output exhausts all retries, run.ok is False (Warning Finding added)."""
    from tvastar.detect import Severity

    script = ["bad", "also bad", "still bad"]
    agent = make_agent(script)
    r = await Harness(agent).run("get user", result=_User)

    assert isinstance(r.data, str), "data should fall back to raw text"
    assert not r.ok, "run.ok must be False when structured output falls back"
    fallback_findings = [f for f in r.findings if f.detector == "structured_output_fallback"]
    assert fallback_findings, "structured_output_fallback Finding not raised"
    assert fallback_findings[0].severity == Severity.WARNING


# ── Fix 6: AnthropicModel retry policy ───────────────────────────────────────


async def test_anthropic_model_retries_on_transient_error():
    """AnthropicModel retries when the exception looks like a rate-limit."""
    from unittest.mock import AsyncMock, MagicMock

    from tvastar.model.anthropic import AnthropicModel
    from tvastar.model.base import ModelRetryPolicy

    call_count = 0

    async def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("rate limit exceeded 429")
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.content = [MagicMock(type="text", text="retried successfully")]
        resp.usage.input_tokens = 5
        resp.usage.output_tokens = 3
        return resp

    stub_client = MagicMock()
    stub_client.messages.create = AsyncMock(side_effect=fake_create)
    stub_client.beta.messages.create = AsyncMock(side_effect=fake_create)

    m = AnthropicModel(
        client=stub_client,
        retry=ModelRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0),
    )
    from tvastar.types import Message

    result = await m.generate([Message("user", "hi")])
    assert result.message.text == "retried successfully"
    assert call_count == 3


async def test_anthropic_model_raises_after_max_attempts():
    """AnthropicModel gives up and raises ModelError after exhausting all attempts."""
    from unittest.mock import AsyncMock, MagicMock

    from tvastar.errors import ModelError
    from tvastar.model.anthropic import AnthropicModel
    from tvastar.model.base import ModelRetryPolicy

    stub_client = MagicMock()
    stub_client.messages.create = AsyncMock(side_effect=Exception("rate limit exceeded 429"))
    stub_client.beta.messages.create = AsyncMock(side_effect=Exception("rate limit exceeded 429"))

    m = AnthropicModel(
        client=stub_client,
        retry=ModelRetryPolicy(max_attempts=2, backoff_base=0.0, jitter=0.0),
    )
    from tvastar.types import Message

    with pytest.raises(ModelError):
        await m.generate([Message("user", "hi")])


# ---------------------------------------------------------------------------
# Feature: Dynamic Capability Governance
# ---------------------------------------------------------------------------


async def test_governance_hard_blocks_disallowed_tool():
    """Tool call that violates the current phase returns an error result (no gate)."""
    from tvastar.masking import GovernancePolicy
    from tvastar.tools.base import tool as tool_decorator
    from tvastar.types import ToolUseBlock

    @tool_decorator
    async def safe_tool() -> str:
        return "safe"

    @tool_decorator
    async def dangerous_tool() -> str:
        return "DANGER"

    gov = GovernancePolicy(
        phases={"read": {"safe_tool"}},
        current_phase="read",
    )
    agent = create_agent(
        "gov-test",
        model=MockModel(
            [
                ToolUseBlock(name="dangerous_tool", input={}, id="tu_1"),  # blocked
                "done",
            ]
        ),
        tools=[safe_tool, dangerous_tool],
        governance=gov,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("do something")
    assert r.text == "done"
    tool_results = [
        b for m in r.messages for b in m.blocks if hasattr(b, "is_error") and b.is_error
    ]
    assert any("governance" in b.content for b in tool_results)


async def test_governance_allows_permitted_tool():
    """Tool call allowed by the current phase executes normally."""
    from tvastar.masking import GovernancePolicy
    from tvastar.tools.base import tool as tool_decorator
    from tvastar.types import ToolUseBlock

    @tool_decorator
    async def allowed_tool() -> str:
        return "allowed-result"

    gov = GovernancePolicy(
        phases={"read": {"allowed_tool"}},
        current_phase="read",
    )
    agent = create_agent(
        "gov-allow",
        model=MockModel(
            [
                ToolUseBlock(name="allowed_tool", input={}, id="tu_2"),
                "finished",
            ]
        ),
        tools=[allowed_tool],
        governance=gov,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("go")
    assert r.text == "finished"
    tool_results = [b for m in r.messages for b in m.blocks if hasattr(b, "is_error")]
    assert not any(b.is_error for b in tool_results)


async def test_governance_set_phase_transition():
    """set_phase() updates current_phase; unknown phase raises ValueError."""
    from tvastar.masking import GovernancePolicy

    gov = GovernancePolicy(
        phases={"read": {"grep"}, "write": {"grep", "bash"}},
        current_phase="read",
    )
    assert gov.is_allowed("bash") is False
    gov.set_phase("write")
    assert gov.current_phase == "write"
    assert gov.is_allowed("bash") is True

    with pytest.raises(ValueError, match="Unknown governance phase"):
        gov.set_phase("nonexistent")


async def test_governance_star_allows_all():
    """A phase with '*' in its allow-set permits any tool."""
    from tvastar.masking import GovernancePolicy

    gov = GovernancePolicy(
        phases={"admin": {"*"}},
        current_phase="admin",
    )
    assert gov.is_allowed("bash") is True
    assert gov.is_allowed("anything") is True


async def test_governance_with_approval_gate_denied():
    """When a gate denies the elevation, the tool call returns a governance error."""
    from unittest.mock import AsyncMock

    from tvastar.approval import ApprovalDenied, ApprovalGate
    from tvastar.masking import GovernancePolicy
    from tvastar.tools.base import tool as tool_decorator

    @tool_decorator
    async def restricted_tool() -> str:
        return "secret"

    gate = ApprovalGate(backend="event")
    gate.request = AsyncMock(side_effect=ApprovalDenied("user denied"))

    gov = GovernancePolicy(
        phases={"locked": set()},
        current_phase="locked",
        approval_gate=gate,
    )
    agent = create_agent(
        "gated",
        model=MockModel(
            [
                ToolUseBlock(name="restricted_tool", input={}, id="tu_3"),
                "gave up",
            ]
        ),
        tools=[restricted_tool],
        governance=gov,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("try it")
    assert r.text == "gave up"
    tool_results = [
        b for m in r.messages for b in m.blocks if hasattr(b, "is_error") and b.is_error
    ]
    assert any("governance" in b.content for b in tool_results)


# ---------------------------------------------------------------------------
# Feature: Transactional Snapshot Sandboxing
# ---------------------------------------------------------------------------


async def test_virtual_sandbox_snapshot_restore():
    """VirtualSandbox.snapshot()/restore() round-trip preserves filesystem state."""
    from tvastar.sandbox.virtual import VirtualSandbox

    sb = VirtualSandbox({"a.txt": "original"})
    snap = sb.snapshot()

    sb.fs.write("a.txt", "modified")
    sb.fs.write("b.txt", "new file")
    assert sb.fs.read("a.txt") == "modified"

    sb.restore(snap)
    assert sb.fs.read("a.txt") == "original"
    assert not sb.fs.exists("b.txt")


async def test_local_sandbox_snapshot_restore():
    """LocalSandbox.snapshot()/restore() round-trip preserves filesystem state."""
    import tempfile
    from pathlib import Path as _P

    from tvastar.sandbox.local import LocalSandbox

    with tempfile.TemporaryDirectory() as tmpdir:
        sb = LocalSandbox(root=tmpdir)
        _P(tmpdir, "a.txt").write_bytes(b"original")
        snap = sb.snapshot()
        assert snap == {"a.txt": b"original"}

        _P(tmpdir, "a.txt").write_bytes(b"modified")
        _P(tmpdir, "b.txt").write_bytes(b"new")

        sb.restore(snap)
        assert _P(tmpdir, "a.txt").read_bytes() == b"original"
        assert not _P(tmpdir, "b.txt").exists()


async def test_harness_transaction_rolls_back_on_exception():
    """harness.transaction() restores the sandbox filesystem when the block raises."""
    from tvastar.sandbox.virtual import VirtualSandbox

    sb = VirtualSandbox({"seed.txt": "seed"})
    agent = create_agent("tx-test", model=MockModel(["ok"]), sandbox=lambda: sb, detect=False)
    h = Harness(agent)

    async with h.session() as sess:
        await sess.start()
        sess.sandbox.fs.write("seed.txt", "seed")  # ensure known state

        with pytest.raises(RuntimeError, match="boom"):
            async with h.transaction(sess):
                sess.sandbox.fs.write("seed.txt", "changed")
                sess.sandbox.fs.write("new.txt", "added")
                raise RuntimeError("boom")

        # Filesystem rolled back
        assert sess.sandbox.fs.read("seed.txt") == "seed"
        assert not sess.sandbox.fs.exists("new.txt")


async def test_harness_transaction_commits_on_success():
    """harness.transaction() keeps changes when the block completes without error."""
    from tvastar.sandbox.virtual import VirtualSandbox

    sb = VirtualSandbox({"x.txt": "before"})
    agent = create_agent("tx-ok", model=MockModel(["ok"]), sandbox=lambda: sb, detect=False)
    h = Harness(agent)

    async with h.session() as sess:
        await sess.start()
        async with h.transaction(sess):
            sess.sandbox.fs.write("x.txt", "after")
            sess.sandbox.fs.write("y.txt", "new")

        assert sess.sandbox.fs.read("x.txt") == "after"
        assert sess.sandbox.fs.read("y.txt") == "new"


async def test_system_prompt_hook_injects_into_prompt():
    """system_prompt_hook receives the composed prompt and its return value is used."""
    received: list[str] = []

    def hook(prompt: str) -> str:
        received.append(prompt)
        return prompt + "\n\n[LTM context injected]"

    agent = create_agent(
        "hook-test",
        model=MockModel(["ok"]),
        instructions="base instructions",
        system_prompt_hook=hook,
        detect=False,
    )
    h = Harness(agent)
    await h.run("hi")
    assert len(received) >= 1
    assert "base instructions" in received[0]


async def test_system_prompt_hook_crash_is_swallowed():
    """A crashing hook does not break the session — base prompt is used instead."""

    def bad_hook(_prompt: str) -> str:
        raise RuntimeError("hook exploded")

    agent = create_agent(
        "hook-crash",
        model=MockModel(["safe"]),
        instructions="safe base",
        system_prompt_hook=bad_hook,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("hi")
    assert r.text == "safe"


async def test_harness_transaction_no_snapshot_sandbox_passes_through():
    """transaction() yields normally for sandboxes that don't support snapshot."""
    from tvastar.sandbox.local import LocalSandbox

    agent = create_agent("tx-local", model=MockModel(["ok"]), sandbox=LocalSandbox, detect=False)
    h = Harness(agent)
    sess = h.session()
    await sess.start()
    # Should not raise even though LocalSandbox.snapshot() raises NotImplementedError
    async with h.transaction(sess):
        pass
    await sess.close()
