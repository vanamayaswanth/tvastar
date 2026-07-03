"""Planner — goal decomposition with simple and full spec-driven modes.

Simple mode: planner.decompose(goal) → ordered step list
Full mode: planner.plan(goal) → requirements → design → tasks (EARS default)

Usage:
    from tvastar.planning import Planner
    from tvastar.model.mock import MockModel

    planner = Planner(model=MockModel())

    # Simple decomposition
    result = await planner.decompose("Add user authentication")
    print(result.steps)  # ["Step 1: ...", "Step 2: ...", ...]

    # Full spec-driven planning
    plan = await planner.plan("Add user authentication")
    print(plan.requirements)  # [Requirement(...), ...]
    print(plan.design)        # DesignDoc(...)
    print(plan.tasks)         # [Task(...), ...]

    # Feed tasks to TaskGraph
    from tvastar import TaskGraph, Harness
    graph = TaskGraph(harness)
    for task in plan.tasks:
        graph.task(task.id, task.description, depends_on=task.depends_on)
    result = await graph.run()
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .methodology import EARSMethodology, PlanningMethodology
from .types import Decomposition, DesignComponent, DesignDoc, Plan, Requirement, Task


class Planner:
    """Goal decomposition with pluggable methodology.

    Parameters
    ----------
    model:
        The Model instance to use for LLM-based decomposition.
    methodology:
        The planning methodology to use. Defaults to EARSMethodology.
    context:
        Optional context string (e.g., project description, constraints).
    """

    def __init__(
        self,
        model: Any,
        *,
        methodology: Optional[PlanningMethodology] = None,
        context: str = "",
    ) -> None:
        self._model = model
        self._methodology = methodology or EARSMethodology()
        self._context = context

    @property
    def methodology(self) -> PlanningMethodology:
        return self._methodology

    async def decompose(self, goal: str) -> Decomposition:
        """Simple mode: break a goal into ordered steps.

        Fast, single LLM call. Returns a Decomposition with plain-text steps.
        Good for quick tasks where full requirements/design is overkill.
        """
        prompt = self._methodology.decompose_prompt(goal)

        from ..types import Message
        messages = [Message("user", prompt)]

        resp = await self._model.generate(
            messages,
            system="You are a task decomposition expert. Output valid JSON only.",
            tools=None,
            max_tokens=2048,
            temperature=0.3,
        )

        steps = self._parse_steps(resp.message.text)
        return Decomposition(goal=goal, steps=steps)

    async def plan(self, goal: str) -> Plan:
        """Full mode: spec-driven planning (requirements → design → tasks).

        Three sequential LLM calls using the configured methodology.
        Returns a complete Plan with structured requirements, design, and tasks.
        """
        from ..types import Message

        # Phase 1: Requirements
        req_prompt = self._methodology.requirements_prompt(goal, self._context)
        req_resp = await self._model.generate(
            [Message("user", req_prompt)],
            system="You are a requirements analyst. Output valid JSON only.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )
        requirements = self._parse_requirements(req_resp.message.text)
        req_text = json.dumps([{"id": r.id, "title": r.title, "criteria": r.acceptance_criteria} for r in requirements])

        # Phase 2: Design
        design_prompt = self._methodology.design_prompt(goal, req_text)
        design_resp = await self._model.generate(
            [Message("user", design_prompt)],
            system="You are a software architect. Output valid JSON only.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )
        design = self._parse_design(design_resp.message.text)
        design_text = json.dumps({"overview": design.overview, "components": [c.name for c in design.components]})

        # Phase 3: Tasks
        tasks_prompt = self._methodology.tasks_prompt(goal, req_text, design_text)
        tasks_resp = await self._model.generate(
            [Message("user", tasks_prompt)],
            system="You are a project planner. Output valid JSON only.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )
        tasks = self._parse_tasks(tasks_resp.message.text)

        return Plan(
            goal=goal,
            requirements=requirements,
            design=design,
            tasks=tasks,
            methodology=self._methodology.name,
        )

    # --- Parsing helpers ---

    def _parse_steps(self, text: str) -> list[str]:
        """Parse decompose output into a list of step strings."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, list):
                return [str(s) for s in data]
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: split by newlines, filter empty
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        return lines if lines else [text.strip()]

    def _parse_requirements(self, text: str) -> list[Requirement]:
        """Parse requirements JSON into Requirement objects."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, list):
                return [
                    Requirement(
                        id=r.get("id", f"R{i+1}"),
                        title=r.get("title", "Untitled"),
                        user_story=r.get("user_story", ""),
                        acceptance_criteria=r.get("acceptance_criteria", []),
                        priority=r.get("priority", "must"),
                    )
                    for i, r in enumerate(data)
                ]
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: single requirement from the goal
        return [Requirement(id="R1", title="Main requirement", user_story=text[:200], acceptance_criteria=[], priority="must")]

    def _parse_design(self, text: str) -> DesignDoc:
        """Parse design JSON into a DesignDoc."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, dict):
                components = []
                for c in data.get("components", []):
                    if isinstance(c, dict):
                        components.append(DesignComponent(
                            name=c.get("name", ""),
                            description=c.get("description", ""),
                            interfaces=c.get("interfaces", []),
                            dependencies=c.get("dependencies", []),
                        ))
                    elif isinstance(c, str):
                        components.append(DesignComponent(name=c, description=""))
                return DesignDoc(
                    overview=data.get("overview", ""),
                    components=components,
                    data_models=data.get("data_models", []),
                    correctness_properties=data.get("correctness_properties", []),
                )
        except (json.JSONDecodeError, ValueError):
            pass
        return DesignDoc(overview=text[:200], components=[], data_models=[], correctness_properties=[])

    def _parse_tasks(self, text: str) -> list[Task]:
        """Parse tasks JSON into Task objects."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, list):
                return [
                    Task(
                        id=t.get("id", f"T{i+1}"),
                        title=t.get("title", "Untitled"),
                        description=t.get("description", ""),
                        depends_on=t.get("depends_on", []),
                        requirements=t.get("requirements", []),
                        estimated_effort=t.get("estimated_effort", "small"),
                    )
                    for i, t in enumerate(data)
                ]
        except (json.JSONDecodeError, ValueError):
            pass
        return [Task(id="T1", title="Implementation", description=text[:200], depends_on=[], requirements=[])]

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from text that might have markdown fences or preamble."""
        text = text.strip()
        # Try to find JSON array or object
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            start = 1
            end = len(lines)
            for i in range(1, len(lines)):
                if lines[i].strip() == "```":
                    end = i
                    break
            text = "\n".join(lines[start:end])
        # Find first [ or {
        for i, ch in enumerate(text):
            if ch in "[{":
                # Find matching close
                depth = 0
                for j in range(i, len(text)):
                    if text[j] in "[{":
                        depth += 1
                    elif text[j] in "]}":
                        depth -= 1
                        if depth == 0:
                            return text[i:j+1]
                return text[i:]
        return text
