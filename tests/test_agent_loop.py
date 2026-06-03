from tvastar import Harness, create_agent, default_toolset
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
