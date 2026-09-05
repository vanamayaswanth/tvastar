"""Planning data types — requirements, design, tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Requirement:
    """A single requirement in EARS format."""

    id: str
    title: str
    user_story: str  # "As a X, I want Y, so that Z"
    acceptance_criteria: list[str]  # EARS: WHEN/WHERE/WHILE/IF/THEN/THE SHALL
    priority: str = "must"  # must | should | could


@dataclass
class DesignComponent:
    """A component in the technical design."""

    name: str
    description: str
    interfaces: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class DesignDoc:
    """Technical design output."""

    overview: str
    components: list[DesignComponent]
    data_models: list[str] = field(default_factory=list)
    correctness_properties: list[str] = field(default_factory=list)


@dataclass
class Task:
    """A single implementation task."""

    id: str
    title: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)  # requirement IDs this addresses
    estimated_effort: str = "small"  # small | medium | large


@dataclass(frozen=True)
class PlanDiagnostic:
    """A machine-readable reason a plan could not be safely executed."""

    phase: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """Complete plan output from full spec-driven planning."""

    goal: str
    requirements: list[Requirement]
    design: DesignDoc
    tasks: list[Task]
    methodology: str  # name of the methodology used
    valid: bool = True
    diagnostics: list[PlanDiagnostic] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether this plan passed the planner's structured-output validation."""
        return self.valid

    @property
    def task_graph(self) -> dict[str, list[str]]:
        """Return tasks as a dependency graph: {task_id: [dependency_ids]}."""
        return {t.id: t.depends_on for t in self.tasks}

    async def execute(
        self,
        harness: Any,
        *,
        resume: bool = False,
        graph_run_id: str | None = None,
        journal: Any = None,
        verified_resume: bool = False,
    ) -> Any:
        """Execute this plan's tasks via TaskGraph.

        Invalid plans fail before creating graph tasks. Resume options are passed
        through unchanged so callers may opt into verified graph journals.
        """
        if not self.valid:
            details = "; ".join(f"{d.phase}: {d.message}" for d in self.diagnostics)
            raise ValueError(f"Cannot execute invalid plan{': ' + details if details else ''}")

        from tvastar.graph import TaskGraph

        graph = TaskGraph(harness)
        for task in self.tasks:
            graph.task(task.id, task.description, depends_on=task.depends_on)
        return await graph.run(
            resume=resume,
            graph_run_id=graph_run_id,
            journal=journal,
            verified_resume=verified_resume,
        )


@dataclass
class Decomposition:
    """Simple decomposition output — just an ordered task list."""

    goal: str
    steps: list[str]  # ordered plain-text steps
    context: dict[str, Any] = field(default_factory=dict)
