from tvastar import (
    FileStore,
    Harness,
    InMemoryStore,
    SkillLibrary,
    create_agent,
    default_toolset,
    parse_skill,
)
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


def test_parse_skill_frontmatter():
    text = """---
name: reviewer
description: Reviews code
tools: [read_file, grep]
---

Do the review carefully.
"""
    skill = parse_skill(text)
    assert skill.name == "reviewer"
    assert skill.description == "Reviews code"
    assert skill.tools == ["read_file", "grep"]
    assert "carefully" in skill.instructions


async def test_skill_scopes_tools_and_runs():
    skill = parse_skill(
        "---\nname: only_list\ndescription: x\ntools: [list_files]\n---\nList things."
    )
    agent = create_agent(
        "t",
        model=MockModel(["catalog ok"]),
        tools=default_toolset(),
        skills=SkillLibrary([skill]),
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        r = await sess.skill("only_list", "list please")
    assert r.stopped == "end_turn"


def test_skill_catalog_in_system_prompt():
    skill = parse_skill("---\nname: s1\ndescription: does s1\n---\nbody")
    agent = create_agent("t", model=MockModel(), skills=SkillLibrary([skill]))
    sysp = agent.build_system_prompt()
    assert "s1: does s1" in sysp


async def test_durable_checkpoint_and_resume():
    store = InMemoryStore()
    script = [
        ToolUseBlock(name="write_file", input={"path": "x.txt", "content": "v1"}),
        "saved",
    ]
    agent = create_agent("t", model=MockModel(script), tools=default_toolset())
    h = Harness(agent, store=store)
    sess = h.session()
    sid = sess.id
    async with sess:
        await sess.prompt("write it")

    # New harness, same store -> resume restores transcript + fs snapshot.
    h2 = Harness(create_agent("t", model=MockModel(), tools=default_toolset()), store=store)
    resumed = h2.resume(sid)
    assert resumed is not None
    assert len(resumed.messages) >= 3
    assert resumed.sandbox.fs.read("x.txt") == "v1"


def test_filestore_roundtrip(tmp_path):
    store = FileStore(tmp_path)
    store.set("k", {"a": 1})
    assert store.get("k") == {"a": 1}
    assert "k" in store.keys()
    store.delete("k")
    assert store.get("k") is None
