"""Tests for auto_topology — mocked harness, no real model calls."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tvastar import auto_topology
from tvastar.graph import TaskGraph
from tvastar.profiles import AgentProfile
from tvastar.session import RunResult
from tvastar.types import Usage


def _harness(plan: dict) -> MagicMock:
    result = RunResult(
        text=json.dumps(plan),
        messages=[],
        usage=Usage(),
        steps=1,
        stopped="end_turn",
        findings=[],
        data=None,
    )
    h = MagicMock()
    h.run = AsyncMock(return_value=result)
    return h


_PLAN = {
    "subtasks": [
        {"name": "research",  "role": "Research specialist", "prompt": "Research X", "depends_on": []},
        {"name": "analyse",   "role": "Analysis specialist", "prompt": "Analyse X",  "depends_on": ["research"]},
        {"name": "report",    "role": "Report writer",       "prompt": "Write report", "depends_on": ["analyse"]},
    ]
}


class TestAutoTopology:
    @pytest.mark.asyncio
    async def test_returns_graph_and_profiles(self):
        graph, profiles = await auto_topology("Research and report on X", harness=_harness(_PLAN))
        assert isinstance(graph, TaskGraph)
        assert isinstance(profiles, list)
        assert all(isinstance(p, AgentProfile) for p in profiles)

    @pytest.mark.asyncio
    async def test_profile_names_match_subtasks(self):
        _, profiles = await auto_topology("goal", harness=_harness(_PLAN))
        names = {p.name for p in profiles}
        assert names == {"research", "analyse", "report"}

    @pytest.mark.asyncio
    async def test_profile_descriptions_from_role(self):
        _, profiles = await auto_topology("goal", harness=_harness(_PLAN))
        desc = {p.name: p.description for p in profiles}
        assert desc["research"] == "Research specialist"

    @pytest.mark.asyncio
    async def test_graph_has_correct_task_count(self):
        graph, _ = await auto_topology("goal", harness=_harness(_PLAN))
        assert len(graph._nodes) == 3

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        bad = RunResult(text="not json", messages=[], usage=Usage(),
                        steps=1, stopped="end_turn", findings=[], data=None)
        h = MagicMock(); h.run = AsyncMock(return_value=bad)
        with pytest.raises(ValueError, match="invalid JSON"):
            await auto_topology("goal", harness=h)

    @pytest.mark.asyncio
    async def test_raises_on_unknown_dependency(self):
        bad_plan = {"subtasks": [
            {"name": "a", "role": "r", "prompt": "p", "depends_on": ["nonexistent"]},
        ]}
        with pytest.raises(ValueError, match="unknown task"):
            await auto_topology("goal", harness=_harness(bad_plan))

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        fenced = RunResult(
            text="```json\n" + json.dumps(_PLAN) + "\n```",
            messages=[], usage=Usage(), steps=1, stopped="end_turn", findings=[], data=None,
        )
        h = MagicMock(); h.run = AsyncMock(return_value=fenced)
        graph, profiles = await auto_topology("goal", harness=h)
        assert len(profiles) == 3

    @pytest.mark.asyncio
    async def test_exported_from_tvastar(self):
        from tvastar import auto_topology as at
        assert at is auto_topology
