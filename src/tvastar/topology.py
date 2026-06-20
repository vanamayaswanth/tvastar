"""Auto-topology: generate a TaskGraph from a natural-language goal.

Given a high-level objective, a planner session decomposes it into concrete
subtasks with explicit dependencies. The result is a ready-to-run TaskGraph
and a matching list of AgentProfiles — one per subtask role.

Usage::

    from tvastar import Harness, create_agent, auto_topology
    from tvastar.model import AnthropicModel

    agent = create_agent("coordinator", model=AnthropicModel("claude-sonnet-4-6"),
                         instructions="You are a planning expert.")
    harness = Harness(agent)

    graph, profiles = await auto_topology(
        "Research our top 3 competitors, score their pricing, write a strategy report.",
        harness=harness,
    )
    results = await graph.run()
    print(results["report"].text)

The planner reuses the harness's model — no extra model or config required.
Pass ``max_subtasks`` to cap the decomposition size.
"""

from __future__ import annotations

import json
from typing import Any

from .graph import TaskGraph
from .profiles import AgentProfile

__all__ = ["auto_topology"]

_PLANNER_INSTRUCTIONS = """You are a task decomposition expert.
Given a high-level goal, decompose it into concrete, independently executable subtasks.
Output ONLY a JSON object — no preamble, no markdown fences — matching this schema:
{{
  "subtasks": [
    {{
      "name": "short_snake_case_id",
      "role": "one-sentence specialist role description",
      "prompt": "full task prompt for an AI agent to execute",
      "depends_on": ["name_of_upstream_subtask"]
    }}
  ]
}}
Rules:
- Keep names unique, lowercase, underscored (e.g. "competitor_research").
- depends_on may be empty [] for tasks that can run in parallel from the start.
- A task's prompt should be self-contained — don't assume the agent sees other tasks.
- Upstream results are injected automatically; reference them naturally in the prompt.
- Minimum 2, maximum {max_subtasks} subtasks.
"""


async def auto_topology(
    goal: str,
    *,
    harness: Any,
    max_subtasks: int = 6,
    cancel_after: float = 60.0,
) -> tuple["TaskGraph", list[AgentProfile]]:
    """Decompose *goal* into a TaskGraph and AgentProfile list.

    Args:
        goal:         Natural-language objective.
        harness:      Tvastar Harness whose model plans the decomposition.
        max_subtasks: Cap on the number of subtasks generated.
        cancel_after: Planner timeout in seconds.

    Returns:
        ``(graph, profiles)`` — a configured TaskGraph (not yet run) and a
        list of AgentProfile objects, one per subtask role.

    Raises:
        ValueError:  If the planner returns unparseable JSON or the
                     decomposition violates topological ordering.
        asyncio.TimeoutError: If planning exceeds *cancel_after*.
    """
    import asyncio

    instructions = _PLANNER_INSTRUCTIONS.format(max_subtasks=max_subtasks)

    prompt = (
        f"Goal: {goal}\n\n"
        "Decompose this into subtasks. "
        f"Use at most {max_subtasks} subtasks. "
        "Return ONLY the JSON object."
    )

    result = await asyncio.wait_for(
        harness.run(prompt, system=instructions),
        timeout=cancel_after,
    )

    raw = result.text.strip()
    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        subtasks = data["subtasks"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(
            f"Planner returned invalid JSON: {e}\nRaw output:\n{raw}"
        ) from e

    # Validate: all depends_on names exist
    names = {s["name"] for s in subtasks}
    for s in subtasks:
        for dep in s.get("depends_on", []):
            if dep not in names:
                raise ValueError(
                    f"Subtask {s['name']!r} depends on unknown task {dep!r}"
                )

    # Build TaskGraph
    graph = TaskGraph(harness)
    for s in subtasks:
        graph.task(
            s["name"],
            s.get("prompt", s["name"]),
            depends_on=s.get("depends_on", []),
        )

    # Build one AgentProfile per subtask role
    profiles = [
        AgentProfile(
            name=s["name"],
            description=s.get("role", s["name"]),
        )
        for s in subtasks
    ]

    return graph, profiles
