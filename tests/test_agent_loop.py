import pytest
from pydantic import BaseModel

from tvastar import Harness, create_agent, default_toolset
from tvastar.compaction import CompactionPolicy
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


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
    policy = CompactionPolicy(max_messages=2, min_messages=2, keep_last=1, summary_model=summary_model)
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
