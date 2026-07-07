"""Planning methodologies — pluggable approaches to requirements/design/tasks.

The default is EARS (Easy Approach to Requirements Syntax):
- WHEN <trigger>, THE <system> SHALL <response>
- WHERE <state>, THE <system> SHALL <response>
- WHILE <condition>, THE <system> SHALL <response>
- IF <condition>, THEN THE <system> SHALL <response>

Users can plug in their own methodology by implementing the Protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PlanningMethodology(Protocol):
    """Protocol for pluggable planning methodologies."""

    @property
    def name(self) -> str:
        """Human-readable methodology name."""
        ...

    def requirements_prompt(self, goal: str, context: str) -> str:
        """Generate a prompt that produces structured requirements from a goal."""
        ...

    def design_prompt(self, goal: str, requirements: str) -> str:
        """Generate a prompt that produces a technical design from requirements."""
        ...

    def tasks_prompt(self, goal: str, requirements: str, design: str) -> str:
        """Generate a prompt that produces an ordered task list from requirements + design."""
        ...

    def decompose_prompt(self, goal: str) -> str:
        """Generate a prompt for simple decomposition (no full spec)."""
        ...


class EARSMethodology:
    """EARS-based methodology — default planning approach.

    Uses Easy Approach to Requirements Syntax (EARS) for requirements,
    component-based design, and dependency-aware task decomposition.
    """

    @property
    def name(self) -> str:
        return "ears"

    def requirements_prompt(self, goal: str, context: str) -> str:
        return f"""Analyze this goal and produce structured requirements.

GOAL: {goal}

CONTEXT: {context}

For each requirement, produce:
- ID (R1, R2, ...)
- Title (short descriptive name)
- User Story: "As a <role>, I want <feature>, so that <benefit>"
- Acceptance Criteria using EARS syntax:
  - WHEN <trigger>, THE <system> SHALL <response>
  - WHERE <state>, THE <system> SHALL <response>
  - IF <condition>, THEN THE <system> SHALL <response>
- Priority: must | should | could

Output as JSON array of requirements.
Format: [{{"id": "R1", "title": "...", "user_story": "...", "acceptance_criteria": ["WHEN..."], "priority": "must"}}]"""

    def design_prompt(self, goal: str, requirements: str) -> str:
        return f"""Create a technical design for this goal based on the requirements.

GOAL: {goal}

REQUIREMENTS:
{requirements}

Produce:
1. Overview (2-3 sentences describing the approach)
2. Components (name, description, interfaces, dependencies)
3. Data models (key data structures)
4. Correctness properties (invariants that must hold)

Output as JSON:
{{"overview": "...", "components": [...], "data_models": [...], "correctness_properties": [...]}}"""

    def tasks_prompt(self, goal: str, requirements: str, design: str) -> str:
        return f"""Create an ordered implementation task list.

GOAL: {goal}

REQUIREMENTS:
{requirements}

DESIGN:
{design}

For each task, produce:
- ID (T1, T2, ...)
- Title
- Description (what to implement)
- depends_on (list of task IDs that must complete first)
- requirements (list of requirement IDs this addresses)
- estimated_effort: small | medium | large

Order tasks so dependencies come first.
Output as JSON array: [{{"id": "T1", "title": "...", "description": "...", "depends_on": [], "requirements": ["R1"], "estimated_effort": "small"}}]"""

    def decompose_prompt(self, goal: str) -> str:
        return f"""Break this goal into 3-8 ordered implementation steps.
Keep each step concrete and actionable (one clear action per step).

GOAL: {goal}

Output as JSON array of strings: ["Step 1: ...", "Step 2: ...", ...]"""


class AgileMethodology:
    """Agile/Scrum-style methodology — epics → stories → tasks."""

    @property
    def name(self) -> str:
        return "agile"

    def requirements_prompt(self, goal: str, context: str) -> str:
        return f"""Break this goal into user stories (Agile format).

GOAL: {goal}
CONTEXT: {context}

For each story:
- ID (US1, US2, ...)
- Title
- User Story: "As a <role>, I want <feature>, so that <benefit>"
- Acceptance Criteria (Given/When/Then format)
- Priority: must | should | could

Output as JSON array."""

    def design_prompt(self, goal: str, requirements: str) -> str:
        return f"""Design the system architecture.

GOAL: {goal}
USER STORIES:
{requirements}

Produce: overview, components, data models, key decisions.
Output as JSON."""

    def tasks_prompt(self, goal: str, requirements: str, design: str) -> str:
        return f"""Create sprint-ready tasks from the stories and design.

GOAL: {goal}
STORIES: {requirements}
DESIGN: {design}

For each task: id, title, description, depends_on, story_ids, effort (1-8 points).
Output as JSON array."""

    def decompose_prompt(self, goal: str) -> str:
        return f"""Break this into 3-8 actionable sprint tasks.
GOAL: {goal}
Output as JSON array of strings."""
