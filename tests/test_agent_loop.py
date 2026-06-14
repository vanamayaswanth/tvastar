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

    m = OpenAIModel.__new__(OpenAIModel)
    m.name = "o1"
    m._model = "o1"
    m._client = StubClient()

    from tvastar.types import Message

    await m.generate([Message("user", "hi")], thinking_level="xhigh")
    assert captured.get("reasoning_effort") == "high", (
        f"Expected 'high', got {captured.get('reasoning_effort')!r}"
    )
