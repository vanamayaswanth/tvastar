"""Tests for the planning module — goal decomposition and spec-driven planning."""
from __future__ import annotations

import json

from tvastar.model.mock import MockModel
from tvastar.planning import (
    AgileMethodology,
    Decomposition,
    DesignDoc,
    EARSMethodology,
    Plan,
    Planner,
    PlanningMethodology,
    Task,
)


# --- Fixtures ---


SAMPLE_STEPS = json.dumps([
    "Step 1: Set up project structure",
    "Step 2: Implement core logic",
    "Step 3: Add tests",
])

SAMPLE_REQUIREMENTS = json.dumps([
    {
        "id": "R1",
        "title": "User login",
        "user_story": "As a user, I want to log in, so that I can access my account",
        "acceptance_criteria": [
            "WHEN a user submits valid credentials, THE system SHALL grant access",
            "IF credentials are invalid, THEN THE system SHALL reject the attempt",
        ],
        "priority": "must",
    },
    {
        "id": "R2",
        "title": "Session management",
        "user_story": "As a user, I want my session maintained, so that I stay logged in",
        "acceptance_criteria": [
            "WHILE a session is active, THE system SHALL refresh the token",
        ],
        "priority": "should",
    },
])

SAMPLE_DESIGN = json.dumps({
    "overview": "Token-based auth with JWT and session store.",
    "components": [
        {
            "name": "AuthService",
            "description": "Handles login/logout",
            "interfaces": ["login()", "logout()"],
            "dependencies": ["TokenStore"],
        },
        {
            "name": "TokenStore",
            "description": "Manages JWT tokens",
            "interfaces": ["create()", "validate()"],
            "dependencies": [],
        },
    ],
    "data_models": ["User", "Session", "Token"],
    "correctness_properties": [
        "A valid token always maps to an active session",
        "Expired tokens are never accepted",
    ],
})

SAMPLE_TASKS = json.dumps([
    {
        "id": "T1",
        "title": "Set up auth module",
        "description": "Create the auth service skeleton",
        "depends_on": [],
        "requirements": ["R1"],
        "estimated_effort": "small",
    },
    {
        "id": "T2",
        "title": "Implement token store",
        "description": "Build JWT creation and validation",
        "depends_on": ["T1"],
        "requirements": ["R1", "R2"],
        "estimated_effort": "medium",
    },
    {
        "id": "T3",
        "title": "Add session refresh",
        "description": "Implement token refresh logic",
        "depends_on": ["T2"],
        "requirements": ["R2"],
        "estimated_effort": "small",
    },
])


# --- Test decompose (simple mode) ---


async def test_decompose_returns_steps():
    """decompose() returns a Decomposition with parsed steps from JSON."""
    model = MockModel(script=[SAMPLE_STEPS])
    planner = Planner(model=model)

    result = await planner.decompose("Add user authentication")

    assert isinstance(result, Decomposition)
    assert result.goal == "Add user authentication"
    assert len(result.steps) == 3
    assert "Set up project structure" in result.steps[0]
    assert "Implement core logic" in result.steps[1]
    assert "Add tests" in result.steps[2]


async def test_decompose_single_model_call():
    """decompose() makes exactly one model call."""
    model = MockModel(script=[SAMPLE_STEPS])
    planner = Planner(model=model)

    await planner.decompose("Build a REST API")

    assert len(model.calls) == 1


# --- Test plan (full mode) ---


async def test_plan_returns_full_plan():
    """plan() returns a Plan with requirements, design, and tasks."""
    model = MockModel(script=[SAMPLE_REQUIREMENTS, SAMPLE_DESIGN, SAMPLE_TASKS])
    planner = Planner(model=model)

    plan = await planner.plan("Add user authentication")

    assert isinstance(plan, Plan)
    assert plan.goal == "Add user authentication"
    assert plan.methodology == "ears"

    # Requirements
    assert len(plan.requirements) == 2
    assert plan.requirements[0].id == "R1"
    assert plan.requirements[0].title == "User login"
    assert len(plan.requirements[0].acceptance_criteria) == 2
    assert plan.requirements[1].priority == "should"

    # Design
    assert "Token-based auth" in plan.design.overview
    assert len(plan.design.components) == 2
    assert plan.design.components[0].name == "AuthService"
    assert "TokenStore" in plan.design.components[0].dependencies
    assert len(plan.design.data_models) == 3
    assert len(plan.design.correctness_properties) == 2

    # Tasks
    assert len(plan.tasks) == 3
    assert plan.tasks[0].id == "T1"
    assert plan.tasks[1].depends_on == ["T1"]
    assert plan.tasks[2].estimated_effort == "small"


async def test_plan_three_model_calls():
    """plan() makes exactly three model calls (requirements, design, tasks)."""
    model = MockModel(script=[SAMPLE_REQUIREMENTS, SAMPLE_DESIGN, SAMPLE_TASKS])
    planner = Planner(model=model)

    await planner.plan("Build a dashboard")

    assert len(model.calls) == 3


# --- Test parsing helpers ---


async def test_parse_steps_clean_json():
    """_parse_steps handles clean JSON array."""
    planner = Planner(model=MockModel())
    steps = planner._parse_steps('["Do A", "Do B", "Do C"]')
    assert steps == ["Do A", "Do B", "Do C"]


async def test_parse_steps_markdown_fenced():
    """_parse_steps handles markdown-fenced JSON."""
    planner = Planner(model=MockModel())
    text = '```json\n["Step 1: Init", "Step 2: Build"]\n```'
    steps = planner._parse_steps(text)
    assert len(steps) == 2
    assert "Init" in steps[0]


async def test_parse_steps_plain_text_fallback():
    """_parse_steps falls back to line splitting for non-JSON text."""
    planner = Planner(model=MockModel())
    text = "First do this\nThen do that\nFinally wrap up"
    steps = planner._parse_steps(text)
    assert len(steps) == 3
    assert steps[0] == "First do this"


async def test_parse_requirements_valid_json():
    """_parse_requirements parses valid JSON into Requirement objects."""
    planner = Planner(model=MockModel())
    reqs = planner._parse_requirements(SAMPLE_REQUIREMENTS)
    assert len(reqs) == 2
    assert reqs[0].id == "R1"
    assert reqs[0].user_story.startswith("As a user")
    assert reqs[1].priority == "should"


async def test_parse_requirements_fallback():
    """_parse_requirements returns a fallback Requirement for invalid input."""
    planner = Planner(model=MockModel())
    reqs = planner._parse_requirements("This is not JSON at all.")
    assert len(reqs) == 1
    assert reqs[0].id == "R1"
    assert reqs[0].title == "Main requirement"


# --- Test _extract_json ---


async def test_extract_json_fenced_code_block():
    """_extract_json strips markdown fences."""
    text = '```json\n[{"id": "R1"}]\n```'
    result = Planner._extract_json(text)
    assert result == '[{"id": "R1"}]'


async def test_extract_json_with_preamble():
    """_extract_json skips text before JSON."""
    text = 'Here are the results:\n[{"step": "one"}]'
    result = Planner._extract_json(text)
    parsed = json.loads(result)
    assert parsed == [{"step": "one"}]


async def test_extract_json_nested_brackets():
    """_extract_json handles nested brackets correctly."""
    obj = {"a": [1, 2], "b": {"c": 3}}
    text = f"Result: {json.dumps(obj)}"
    result = Planner._extract_json(text)
    assert json.loads(result) == obj


# --- Test EARS methodology prompts ---


async def test_ears_methodology_name():
    """EARSMethodology reports correct name."""
    m = EARSMethodology()
    assert m.name == "ears"


async def test_ears_requirements_prompt_contains_ears_keywords():
    """EARS requirements prompt includes EARS-specific syntax markers."""
    m = EARSMethodology()
    prompt = m.requirements_prompt("Build auth", "web app")
    assert "WHEN" in prompt
    assert "SHALL" in prompt
    assert "IF" in prompt
    assert "Build auth" in prompt
    assert "web app" in prompt


async def test_ears_design_prompt_contains_structure():
    """EARS design prompt asks for components and correctness properties."""
    m = EARSMethodology()
    prompt = m.design_prompt("Build auth", "[requirements here]")
    assert "Components" in prompt or "components" in prompt
    assert "Correctness" in prompt or "correctness" in prompt


async def test_ears_decompose_prompt():
    """EARS decompose prompt includes the goal and step count guidance."""
    m = EARSMethodology()
    prompt = m.decompose_prompt("Implement caching")
    assert "Implement caching" in prompt
    assert "3-8" in prompt


# --- Test Agile methodology prompts ---


async def test_agile_methodology_name():
    """AgileMethodology reports correct name."""
    m = AgileMethodology()
    assert m.name == "agile"


async def test_agile_requirements_prompt_contains_agile_keywords():
    """Agile requirements prompt includes Agile-specific syntax."""
    m = AgileMethodology()
    prompt = m.requirements_prompt("Build dashboard", "analytics")
    assert "user stories" in prompt.lower() or "User Story" in prompt
    assert "Given" in prompt or "When" in prompt or "Then" in prompt
    assert "Build dashboard" in prompt


async def test_agile_tasks_prompt_contains_sprint_keywords():
    """Agile tasks prompt mentions sprint-ready tasks."""
    m = AgileMethodology()
    prompt = m.tasks_prompt("Build API", "[stories]", "[design]")
    assert "sprint" in prompt.lower()


# --- Test Plan.task_graph property ---


async def test_plan_task_graph():
    """Plan.task_graph returns correct dependency dict."""
    plan = Plan(
        goal="test",
        requirements=[],
        design=DesignDoc(overview="", components=[]),
        tasks=[
            Task(id="T1", title="First", description="", depends_on=[]),
            Task(id="T2", title="Second", description="", depends_on=["T1"]),
            Task(id="T3", title="Third", description="", depends_on=["T1", "T2"]),
        ],
        methodology="ears",
    )

    graph = plan.task_graph
    assert graph == {
        "T1": [],
        "T2": ["T1"],
        "T3": ["T1", "T2"],
    }


# --- Test custom methodology ---


class MinimalMethodology:
    """A minimal custom methodology for testing pluggability."""

    @property
    def name(self) -> str:
        return "minimal"

    def requirements_prompt(self, goal: str, context: str) -> str:
        return f"Requirements for: {goal}"

    def design_prompt(self, goal: str, requirements: str) -> str:
        return f"Design for: {goal}"

    def tasks_prompt(self, goal: str, requirements: str, design: str) -> str:
        return f"Tasks for: {goal}"

    def decompose_prompt(self, goal: str) -> str:
        return f"Decompose: {goal}"


async def test_planner_with_custom_methodology():
    """Planner works with a custom methodology that satisfies the protocol."""
    methodology = MinimalMethodology()
    assert isinstance(methodology, PlanningMethodology)

    model = MockModel(script=[
        '["Step A", "Step B"]',
    ])
    planner = Planner(model=model, methodology=methodology)

    assert planner.methodology.name == "minimal"

    result = await planner.decompose("Do something")
    assert result.steps == ["Step A", "Step B"]


async def test_planner_with_context():
    """Planner passes context to the methodology's requirements prompt."""
    model = MockModel(script=[SAMPLE_REQUIREMENTS, SAMPLE_DESIGN, SAMPLE_TASKS])
    planner = Planner(model=model, context="This is a Python web app using FastAPI")

    await planner.plan("Add authentication")

    # Verify context was included in the first prompt (requirements phase)
    first_call_messages = model.calls[0]
    prompt_text = first_call_messages[0].text
    assert "FastAPI" in prompt_text


async def test_planner_methodology_defaults_to_ears():
    """Planner uses EARS methodology by default."""
    planner = Planner(model=MockModel())
    assert planner.methodology.name == "ears"
    assert isinstance(planner.methodology, EARSMethodology)
