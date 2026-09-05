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
from .types import (
    Decomposition,
    DesignComponent,
    DesignDoc,
    Plan,
    PlanDiagnostic,
    Requirement,
    Task,
)


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
        requirements, diagnostic = self._parse_plan_requirements(req_resp.message.text)
        if diagnostic is not None:
            return self._invalid_plan(goal, diagnostic)
        req_text = json.dumps(
            [
                {"id": r.id, "title": r.title, "criteria": r.acceptance_criteria}
                for r in requirements
            ]
        )

        # Phase 2: Design
        design_prompt = self._methodology.design_prompt(goal, req_text)
        design_resp = await self._model.generate(
            [Message("user", design_prompt)],
            system="You are a software architect. Output valid JSON only.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )
        design, diagnostic = self._parse_plan_design(design_resp.message.text)
        if diagnostic is not None:
            return self._invalid_plan(goal, diagnostic)
        design_text = json.dumps(
            {"overview": design.overview, "components": [c.name for c in design.components]}
        )

        # Phase 3: Tasks
        tasks_prompt = self._methodology.tasks_prompt(goal, req_text, design_text)
        tasks_resp = await self._model.generate(
            [Message("user", tasks_prompt)],
            system="You are a project planner. Output valid JSON only.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )
        tasks, diagnostic = self._parse_plan_tasks(tasks_resp.message.text)
        if diagnostic is not None:
            return self._invalid_plan(goal, diagnostic)

        diagnostics = self._validate_plan(requirements, tasks)
        return Plan(
            goal=goal,
            requirements=requirements,
            design=design,
            tasks=tasks,
            methodology=self._methodology.name,
            valid=not diagnostics,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _validate_plan(requirements: list[Requirement], tasks: list[Task]) -> list[PlanDiagnostic]:
        """Return semantic diagnostics before an otherwise parsed plan can execute."""
        diagnostics: list[PlanDiagnostic] = []
        requirement_ids = [requirement.id.strip() for requirement in requirements]
        task_ids = [task.id.strip() for task in tasks]

        if not requirements:
            diagnostics.append(
                PlanDiagnostic(
                    "requirements", "empty_requirements", "at least one requirement is required"
                )
            )
        if not tasks:
            diagnostics.append(
                PlanDiagnostic("tasks", "empty_tasks", "at least one task is required")
            )
        for phase, ids in (("requirements", requirement_ids), ("tasks", task_ids)):
            empty_indexes = [index for index, item_id in enumerate(ids) if not item_id]
            if empty_indexes:
                diagnostics.append(
                    PlanDiagnostic(
                        phase, "empty_id", "ids must be non-empty", {"indexes": empty_indexes}
                    )
                )
            duplicates = sorted({item_id for item_id in ids if item_id and ids.count(item_id) > 1})
            if duplicates:
                diagnostics.append(
                    PlanDiagnostic(phase, "duplicate_id", "ids must be unique", {"ids": duplicates})
                )

        known_requirements = set(requirement_ids)
        known_tasks = set(task_ids)
        for task, task_id in zip(tasks, task_ids):
            unknown_requirements = sorted(set(task.requirements) - known_requirements)
            if unknown_requirements:
                diagnostics.append(
                    PlanDiagnostic(
                        "tasks",
                        "unknown_requirement",
                        f"task {task_id!r} references unknown requirements",
                        {"task": task_id, "requirements": unknown_requirements},
                    )
                )
            unknown_dependencies = sorted(set(task.depends_on) - known_tasks)
            if unknown_dependencies:
                diagnostics.append(
                    PlanDiagnostic(
                        "tasks",
                        "unknown_dependency",
                        f"task {task_id!r} depends on unknown tasks",
                        {"task": task_id, "dependencies": unknown_dependencies},
                    )
                )
            if task_id and task_id in task.depends_on:
                diagnostics.append(
                    PlanDiagnostic(
                        "tasks",
                        "self_dependency",
                        f"task {task_id!r} cannot depend on itself",
                        {"task": task_id},
                    )
                )

        dependencies = {
            task_id: task.depends_on for task, task_id in zip(tasks, task_ids) if task_id
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited or task_id not in dependencies:
                return
            if task_id in visiting:
                diagnostics.append(
                    PlanDiagnostic(
                        "tasks",
                        "dependency_cycle",
                        "task dependencies must not contain a cycle",
                        {"task": task_id},
                    )
                )
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)
        return diagnostics

    def _invalid_plan(self, goal: str, diagnostic: "PlanDiagnostic") -> Plan:
        """Return a non-executable Plan while preserving its public shape."""
        return Plan(
            goal=goal,
            requirements=[],
            design=DesignDoc(overview="", components=[]),
            tasks=[],
            methodology=self._methodology.name,
            valid=False,
            diagnostics=[diagnostic],
        )

    def _parse_plan_requirements(
        self, text: str
    ) -> tuple[list[Requirement], "PlanDiagnostic | None"]:
        data, diagnostic = self._parse_plan_json(text, "requirements", list)
        if diagnostic is not None:
            return [], diagnostic
        requirements: list[Requirement] = []
        for index, item in enumerate(data):
            error = self._object_fields(
                item,
                {"id": str, "title": str, "user_story": str, "acceptance_criteria": list},
                "requirements",
                index,
            )
            if error is not None or not self._is_string_list(item["acceptance_criteria"]):
                return [], error or self._schema_error(
                    "requirements", index, "acceptance_criteria must be a list of strings"
                )
            if "priority" in item and not isinstance(item["priority"], str):
                return [], self._schema_error("requirements", index, "priority must be a string")
            requirements.append(
                Requirement(
                    id=item["id"],
                    title=item["title"],
                    user_story=item["user_story"],
                    acceptance_criteria=item["acceptance_criteria"],
                    priority=item.get("priority", "must"),
                )
            )
        return requirements, None

    def _parse_plan_design(self, text: str) -> tuple[DesignDoc, "PlanDiagnostic | None"]:
        data, diagnostic = self._parse_plan_json(text, "design", dict)
        if diagnostic is not None:
            return DesignDoc(overview="", components=[]), diagnostic
        error = self._object_fields(data, {"overview": str, "components": list}, "design")
        if error is not None:
            return DesignDoc(overview="", components=[]), error
        components: list[DesignComponent] = []
        for index, item in enumerate(data["components"]):
            error = self._object_fields(item, {"name": str, "description": str}, "design", index)
            if error is not None:
                return DesignDoc(overview="", components=[]), error
            for field_name in ("interfaces", "dependencies"):
                if field_name in item and not self._is_string_list(item[field_name]):
                    return DesignDoc(overview="", components=[]), self._schema_error(
                        "design", index, f"{field_name} must be a list of strings"
                    )
            components.append(
                DesignComponent(
                    name=item["name"],
                    description=item["description"],
                    interfaces=item.get("interfaces", []),
                    dependencies=item.get("dependencies", []),
                )
            )
        for field_name in ("data_models", "correctness_properties"):
            if field_name in data and not self._is_string_list(data[field_name]):
                return DesignDoc(overview="", components=[]), self._schema_error(
                    "design", None, f"{field_name} must be a list of strings"
                )
        return DesignDoc(
            overview=data["overview"],
            components=components,
            data_models=data.get("data_models", []),
            correctness_properties=data.get("correctness_properties", []),
        ), None

    def _parse_plan_tasks(self, text: str) -> tuple[list[Task], "PlanDiagnostic | None"]:
        data, diagnostic = self._parse_plan_json(text, "tasks", list)
        if diagnostic is not None:
            return [], diagnostic
        tasks: list[Task] = []
        for index, item in enumerate(data):
            error = self._object_fields(
                item,
                {"id": str, "title": str, "description": str},
                "tasks",
                index,
            )
            if error is not None:
                return [], error
            for field_name in ("depends_on", "requirements"):
                if field_name in item and not self._is_string_list(item[field_name]):
                    return [], self._schema_error(
                        "tasks", index, f"{field_name} must be a list of strings"
                    )
            if "estimated_effort" in item and not isinstance(item["estimated_effort"], str):
                return [], self._schema_error("tasks", index, "estimated_effort must be a string")
            tasks.append(
                Task(
                    id=item["id"],
                    title=item["title"],
                    description=item["description"],
                    depends_on=item.get("depends_on", []),
                    requirements=item.get("requirements", []),
                    estimated_effort=item.get("estimated_effort", "small"),
                )
            )
        return tasks, None

    def _parse_plan_json(
        self, text: str, phase: str, expected_type: type[Any]
    ) -> tuple[Any, "PlanDiagnostic | None"]:
        try:
            data = json.loads(self._extract_json(text))
        except (json.JSONDecodeError, ValueError) as exc:
            return None, self._schema_error(
                phase, None, "output is not valid JSON", "malformed_json", str(exc)
            )
        if not isinstance(data, expected_type):
            return None, self._schema_error(
                phase,
                None,
                f"output must be a {expected_type.__name__}",
                details={"actual_type": type(data).__name__},
            )
        return data, None

    @staticmethod
    def _is_string_list(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    def _object_fields(
        self,
        value: Any,
        fields: dict[str, type[Any]],
        phase: str,
        index: int | None = None,
    ) -> "PlanDiagnostic | None":
        if not isinstance(value, dict):
            return self._schema_error(phase, index, "entry must be an object")
        for field_name, field_type in fields.items():
            if field_name not in value:
                return self._schema_error(phase, index, f"missing required field {field_name!r}")
            if not isinstance(value[field_name], field_type):
                return self._schema_error(
                    phase, index, f"{field_name} must be a {field_type.__name__}"
                )
        return None

    @staticmethod
    def _schema_error(
        phase: str,
        index: int | None,
        message: str,
        code: str = "schema_invalid",
        details: Any = None,
    ) -> "PlanDiagnostic":
        diagnostic_details = {} if details is None else {"error": details}
        if index is not None:
            diagnostic_details["index"] = index
        return PlanDiagnostic(phase=phase, code=code, message=message, details=diagnostic_details)

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
                        id=r.get("id", f"R{i + 1}"),
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
        return [
            Requirement(
                id="R1",
                title="Main requirement",
                user_story=text[:200],
                acceptance_criteria=[],
                priority="must",
            )
        ]

    def _parse_design(self, text: str) -> DesignDoc:
        """Parse design JSON into a DesignDoc."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, dict):
                components = []
                for c in data.get("components", []):
                    if isinstance(c, dict):
                        components.append(
                            DesignComponent(
                                name=c.get("name", ""),
                                description=c.get("description", ""),
                                interfaces=c.get("interfaces", []),
                                dependencies=c.get("dependencies", []),
                            )
                        )
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
        return DesignDoc(
            overview=text[:200], components=[], data_models=[], correctness_properties=[]
        )

    def _parse_tasks(self, text: str) -> list[Task]:
        """Parse tasks JSON into Task objects."""
        try:
            data = json.loads(self._extract_json(text))
            if isinstance(data, list):
                return [
                    Task(
                        id=t.get("id", f"T{i + 1}"),
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
        return [
            Task(
                id="T1",
                title="Implementation",
                description=text[:200],
                depends_on=[],
                requirements=[],
            )
        ]

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
                            return text[i : j + 1]
                return text[i:]
        return text
