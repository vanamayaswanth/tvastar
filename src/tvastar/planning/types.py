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


@dataclass
class Plan:
    """Complete plan output from full spec-driven planning."""
    goal: str
    requirements: list[Requirement]
    design: DesignDoc
    tasks: list[Task]
    methodology: str  # name of the methodology used

    @property
    def task_graph(self) -> dict[str, list[str]]:
        """Return tasks as a dependency graph: {task_id: [dependency_ids]}."""
        return {t.id: t.depends_on for t in self.tasks}

    async def execute(self, harness: Any) -> Any:
        """Execute this plan's tasks via TaskGraph.

        Convenience method that feeds all tasks into a TaskGraph with
        their dependency relationships and runs them.

        Parameters
        ----------
        harness: A Harness instance to run tasks against.

        Returns
        -------
        GraphResult from the task execution.
        """
        from tvastar.graph import TaskGraph

        graph = TaskGraph(harness)
        for task in self.tasks:
            graph.task(task.id, task.description, depends_on=task.depends_on)
        return await graph.run()


@dataclass
class Decomposition:
    """Simple decomposition output — just an ordered task list."""
    goal: str
    steps: list[str]  # ordered plain-text steps
    context: dict[str, Any] = field(default_factory=dict)
