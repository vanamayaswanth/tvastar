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

from .cycle_policy import CyclePolicy
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
      "depends_on": ["name_of_upstream_subtask"],
      "cycle_edges": []
    }}
  ]
}}

## Cycle Edges (optional)
When a subtask needs to loop back to an earlier subtask for iterative refinement
(e.g. reviewer sends work back to writer), add a cycle_edges entry:
  "cycle_edges": [
    {{
      "target": "name_of_upstream_subtask_to_loop_back_to",
      "cycle_policy": "allow_ttl",
      "max_iterations": 3
    }}
  ]
Cycle policies:
- "allow_ttl": permit the cycle up to max_iterations re-entries (default 3).
- "forbid" or omitting cycle_edges: no cycle, strict DAG edge.
Only use cycle_edges when the goal explicitly requires iterative review/revision loops.
The target MUST be an upstream subtask that this subtask already depends on (directly or transitively).

Rules:
- Keep names unique, lowercase, underscored (e.g. "competitor_research").
- depends_on may be empty [] for tasks that can run in parallel from the start.
- cycle_edges may be empty [] or omitted entirely for strict DAG tasks.
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
    from contextlib import nullcontext

    instructions = _PLANNER_INSTRUCTIONS.format(max_subtasks=max_subtasks)

    prompt = (
        f"Goal: {goal}\n\n"
        "Decompose this into subtasks. "
        f"Use at most {max_subtasks} subtasks. "
        "Return ONLY the JSON object."
    )

    _tracer = getattr(harness, "tracer", None) or getattr(harness, "_tracer", None)
    _plan_ctx = (
        _tracer.span("topology.plan", max_subtasks=max_subtasks)
        if _tracer is not None
        else nullcontext()
    )
    with _plan_ctx:
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
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw output:\n{raw}") from e

    # Validate: all depends_on names exist
    names = {s["name"] for s in subtasks}
    for s in subtasks:
        for dep in s.get("depends_on", []):
            if dep not in names:
                raise ValueError(f"Subtask {s['name']!r} depends on unknown task {dep!r}")

    # Parse cycle annotations and validate targets are topological ancestors
    cycle_edges: dict[str, list[tuple[str, Any]]] = {}  # source_name -> [(target, policy)]
    for s in subtasks:
        for ce in s.get("cycle_edges", []):
            target = ce.get("target", "")
            if target not in names:
                raise ValueError(
                    f"Subtask {s['name']!r} cycle_edge targets unknown task {target!r}"
                )
            # Validate target is a topological ancestor of source
            if not _is_ancestor(target, s["name"], subtasks):
                raise ValueError(
                    f"Cycle target {target!r} is not a topological ancestor of {s['name']!r}"
                )
            policy_str = ce.get("cycle_policy", "forbid")
            if policy_str == "allow_ttl":
                max_iter = ce.get("max_iterations", 3)
                policy = CyclePolicy.ALLOW_TTL(max_iter)
            elif policy_str == "forbid":
                policy = CyclePolicy.FORBID
            else:
                raise ValueError(
                    f"Unknown cycle_policy {policy_str!r} on subtask {s['name']!r}"
                )
            cycle_edges.setdefault(s["name"], []).append((target, policy))

    # Build TaskGraph
    graph = TaskGraph(harness)
    for s in subtasks:
        # Merge normal depends_on with cycle back-edges
        deps: list[str | tuple[str, Any]] = list(s.get("depends_on", []))
        for target, policy in cycle_edges.get(s["name"], []):
            deps.append((target, policy))
        graph.task(
            s["name"],
            s.get("prompt", s["name"]),
            depends_on=deps,
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


def _is_ancestor(
    candidate: str, source: str, subtasks: list[dict[str, Any]]
) -> bool:
    """Return True if *candidate* is a topological ancestor of *source*.

    An ancestor is reachable by walking the depends_on edges backward from source.
    """
    deps_map: dict[str, list[str]] = {
        s["name"]: s.get("depends_on", []) for s in subtasks
    }
    # BFS from source walking upstream
    visited: set[str] = set()
    frontier = list(deps_map.get(source, []))
    while frontier:
        node = frontier.pop()
        if node == candidate:
            return True
        if node in visited:
            continue
        visited.add(node)
        frontier.extend(deps_map.get(node, []))
    return False
