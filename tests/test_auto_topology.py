"""Tests for auto_topology — mocked harness, no real model calls."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tvastar import auto_topology
from tvastar.cycle_policy import CyclePolicy
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
        {
            "name": "research",
            "role": "Research specialist",
            "prompt": "Research X",
            "depends_on": [],
        },
        {
            "name": "analyse",
            "role": "Analysis specialist",
            "prompt": "Analyse X",
            "depends_on": ["research"],
        },
        {
            "name": "report",
            "role": "Report writer",
            "prompt": "Write report",
            "depends_on": ["analyse"],
        },
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
        bad = RunResult(
            text="not json",
            messages=[],
            usage=Usage(),
            steps=1,
            stopped="end_turn",
            findings=[],
            data=None,
        )
        h = MagicMock()
        h.run = AsyncMock(return_value=bad)
        with pytest.raises(ValueError, match="invalid JSON"):
            await auto_topology("goal", harness=h)

    @pytest.mark.asyncio
    async def test_raises_on_unknown_dependency(self):
        bad_plan = {
            "subtasks": [
                {"name": "a", "role": "r", "prompt": "p", "depends_on": ["nonexistent"]},
            ]
        }
        with pytest.raises(ValueError, match="unknown task"):
            await auto_topology("goal", harness=_harness(bad_plan))

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        fenced = RunResult(
            text="```json\n" + json.dumps(_PLAN) + "\n```",
            messages=[],
            usage=Usage(),
            steps=1,
            stopped="end_turn",
            findings=[],
            data=None,
        )
        h = MagicMock()
        h.run = AsyncMock(return_value=fenced)
        graph, profiles = await auto_topology("goal", harness=h)
        assert len(profiles) == 3

    @pytest.mark.asyncio
    async def test_exported_from_tvastar(self):
        from tvastar import auto_topology as at

        assert at is auto_topology


# ── Task 17 Tests: Cycle-annotated auto_topology ──────────────────────────────

_CYCLE_PLAN = {
    "subtasks": [
        {
            "name": "research",
            "role": "Researcher",
            "prompt": "Research topic",
            "depends_on": [],
        },
        {
            "name": "write",
            "role": "Writer",
            "prompt": "Write draft based on research",
            "depends_on": ["research"],
        },
        {
            "name": "review",
            "role": "Reviewer",
            "prompt": "Review the draft and loop back to writer if needed",
            "depends_on": ["write"],
            "cycle_edges": [
                {
                    "target": "write",
                    "cycle_policy": "allow_ttl",
                    "max_iterations": 3,
                }
            ],
        },
    ]
}

_INVALID_CYCLE_TARGET_PLAN = {
    "subtasks": [
        {
            "name": "research",
            "role": "Researcher",
            "prompt": "Research",
            "depends_on": [],
        },
        {
            "name": "write",
            "role": "Writer",
            "prompt": "Write",
            "depends_on": ["research"],
        },
        {
            "name": "review",
            "role": "Reviewer",
            "prompt": "Review",
            "depends_on": ["write"],
            "cycle_edges": [
                {
                    "target": "research",
                    "cycle_policy": "allow_ttl",
                    "max_iterations": 2,
                }
            ],
        },
    ]
}


class TestAutoTopologyCycleAnnotations:
    """Validates: Requirements 16 — auto_topology emits cycle-annotated edges."""

    @pytest.mark.asyncio
    async def test_review_cycle_goal_produces_allow_ttl_edge(self):
        """A plan with cycle_edges configures ALLOW_TTL on the back-edge."""
        graph, _ = await auto_topology("goal", harness=_harness(_CYCLE_PLAN))

        # The review node should have a cycle policy targeting write
        review_node = graph._nodes["review"]
        assert "write" in review_node.cycle_policies
        policy = review_node.cycle_policies["write"]
        assert isinstance(policy, CyclePolicy.ALLOW_TTL)
        assert policy.max_iterations == 3

    @pytest.mark.asyncio
    async def test_no_cycle_goal_produces_strict_dag(self):
        """A plan with no cycle_edges produces a strict DAG (no cycle policies)."""
        graph, _ = await auto_topology("goal", harness=_harness(_PLAN))

        for node in graph._nodes.values():
            assert node.cycle_policies == {}, (
                f"Node {node.name!r} should have no cycle policies in a strict DAG"
            )

    @pytest.mark.asyncio
    async def test_invalid_cycle_target_raises_valueerror(self):
        """Cycle target that is not a direct ancestor raises ValueError."""
        # review depends_on=[write], write depends_on=[research]
        # research is an ancestor of review (transitive), so this should pass.
        # Let's create a plan where the target is NOT an ancestor at all.
        bad_plan = {
            "subtasks": [
                {
                    "name": "alpha",
                    "role": "Alpha",
                    "prompt": "Do alpha",
                    "depends_on": [],
                },
                {
                    "name": "beta",
                    "role": "Beta",
                    "prompt": "Do beta",
                    "depends_on": [],
                },
                {
                    "name": "gamma",
                    "role": "Gamma",
                    "prompt": "Do gamma",
                    "depends_on": ["alpha"],
                    "cycle_edges": [
                        {
                            "target": "beta",
                            "cycle_policy": "allow_ttl",
                            "max_iterations": 2,
                        }
                    ],
                },
            ]
        }
        with pytest.raises(ValueError, match="not a topological ancestor"):
            await auto_topology("goal", harness=_harness(bad_plan))

    @pytest.mark.asyncio
    async def test_cycle_edge_with_valid_transitive_ancestor(self):
        """Cycle target that is a transitive ancestor (not just direct dep) is allowed."""
        # review -> write -> research: research IS an ancestor of review
        plan_with_transitive = {
            "subtasks": [
                {
                    "name": "research",
                    "role": "R",
                    "prompt": "Research",
                    "depends_on": [],
                },
                {
                    "name": "write",
                    "role": "W",
                    "prompt": "Write",
                    "depends_on": ["research"],
                },
                {
                    "name": "review",
                    "role": "Rev",
                    "prompt": "Review",
                    "depends_on": ["write"],
                    "cycle_edges": [
                        {
                            "target": "research",
                            "cycle_policy": "allow_ttl",
                            "max_iterations": 2,
                        }
                    ],
                },
            ]
        }
        graph, _ = await auto_topology("goal", harness=_harness(plan_with_transitive))
        review_node = graph._nodes["review"]
        assert "research" in review_node.cycle_policies
