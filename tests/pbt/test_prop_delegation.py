"""Property-based tests for task delegation depth bound and child spec precedence.

Property 32: Task delegation depth bound
- For any chain of session.task() delegations, task_depth never exceeds
  MAX_TASK_DEPTH (4); at the limit, RuntimeError is raised.
- For depth < MAX_TASK_DEPTH: task succeeds.
- For depth >= MAX_TASK_DEPTH: RuntimeError raised.

Property 33: Child spec precedence
- For any combination of parent AgentSpec, AgentProfile, and task() overrides,
  child spec resolution follows: task override > profile > parent.

**Validates: Requirements 18.2, 18.3**
"""

from __future__ import annotations

import pytest
import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar import Harness, MAX_TASK_DEPTH, create_agent, define_agent_profile
from tvastar.model.mock import MockModel


# ---------------------------------------------------------------------------
# Property 32: Task delegation depth bound
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    depth=st.integers(min_value=MAX_TASK_DEPTH, max_value=10),
)
async def test_task_delegation_raises_at_max_depth(depth: int):
    """Property 32: Task delegation depth bound (at/above limit).

    For any session with _task_depth >= MAX_TASK_DEPTH, calling session.task()
    SHALL raise RuntimeError. The depth guard prevents unbounded delegation
    chains regardless of how deep the nesting goes.

    **Validates: Requirements 18.2**
    """
    model = MockModel(["should not be reached"])
    spec = create_agent(
        "depth-bound-test",
        model=model,
        instructions="Test agent",
    )
    h = Harness(spec)
    sess = h.session()
    sess._task_depth = depth

    async with sess:
        with pytest.raises(RuntimeError, match="Task depth limit"):
            await sess.task("delegated work")


@settings(max_examples=100, deadline=None)
@given(
    depth=st.integers(min_value=0, max_value=MAX_TASK_DEPTH - 1),
)
async def test_task_delegation_succeeds_below_max_depth(depth: int):
    """Property 32: Task delegation depth bound (below limit).

    For any session with _task_depth < MAX_TASK_DEPTH, calling session.task()
    SHALL succeed (no RuntimeError raised). The child session gets
    _task_depth = parent._task_depth + 1 and completes normally.

    **Validates: Requirements 18.2**
    """
    model = MockModel(["child completed."])
    spec = create_agent(
        "depth-success-test",
        model=model,
        instructions="Test agent",
        detect=False,
    )
    h = Harness(spec)
    sess = h.session()
    sess._task_depth = depth

    async with sess:
        result = await sess.task("delegated work")
    assert result.text == "child completed."
    assert result.stopped == "end_turn"


@settings(max_examples=100, deadline=None)
@given(
    depth=st.integers(min_value=0, max_value=MAX_TASK_DEPTH - 1),
)
async def test_child_task_depth_increments_correctly(depth: int):
    """Property 32: Child session depth is always parent + 1.

    For any parent session with _task_depth = D (where D < MAX_TASK_DEPTH),
    the child session created by task() SHALL have _task_depth = D + 1.
    This ensures task_depth never exceeds MAX_TASK_DEPTH because the guard
    fires at _task_depth >= MAX_TASK_DEPTH before creating a child.

    **Validates: Requirements 18.2**
    """
    # Track child sessions after task() sets their depth
    child_sessions: list = []

    model = MockModel(["done."])
    spec = create_agent(
        "depth-increment-test",
        model=model,
        instructions="Test agent",
        detect=False,
    )
    h = Harness(spec)

    # Wrap h.session to capture child session objects
    original_session = h.session

    def tracking_session(**kwargs):
        sess = original_session(**kwargs)
        child_sessions.append(sess)
        return sess

    h.session = tracking_session

    parent = original_session()
    parent._task_depth = depth

    async with parent:
        await parent.task("work")

    # task() calls h.session() then sets child._task_depth = parent._task_depth + 1
    # After task() completes, the child session should have the incremented depth
    assert len(child_sessions) >= 1, "Expected at least one child session to be created"
    assert child_sessions[0]._task_depth == depth + 1, (
        f"Expected child _task_depth={depth + 1}, "
        f"but got {child_sessions[0]._task_depth}"
    )


# ---------------------------------------------------------------------------
# Property 33: Child spec precedence
# ---------------------------------------------------------------------------

# Strategies for generating distinct values per precedence level
_MODEL_LABELS = st.sampled_from(["parent-model", "profile-model", "override-model"])
_INSTRUCTIONS = st.sampled_from(["parent-instr", "profile-instr", "override-instr"])
_MAX_STEPS = st.sampled_from([5, 10, 20, 30, 40])
_THINKING_LEVELS = st.sampled_from(["low", "medium", "high"])


@st.composite
def _precedence_scenario(draw):
    """Generate a random precedence scenario with parent/profile/override values.

    Each field may or may not have a profile override and a task override.
    The expected resolved value follows: task override > profile > parent.
    """
    # Parent values (always set)
    parent_instructions = draw(st.text(min_size=3, max_size=30, alphabet="abcdefghijk "))
    parent_max_steps = draw(st.integers(min_value=5, max_value=50))
    parent_thinking = draw(st.sampled_from(["low", "medium", "high"]))

    # Profile values (optional — None means inherit from parent)
    has_profile_instructions = draw(st.booleans())
    has_profile_max_steps = draw(st.booleans())
    has_profile_thinking = draw(st.booleans())
    has_profile_model = draw(st.booleans())

    profile_instructions = (
        draw(st.text(min_size=3, max_size=30, alphabet="lmnopqrstuv "))
        if has_profile_instructions
        else None
    )
    profile_max_steps = (
        draw(st.integers(min_value=5, max_value=50).filter(lambda x: x != parent_max_steps))
        if has_profile_max_steps
        else None
    )
    profile_thinking = (
        draw(st.sampled_from(["low", "medium", "high"]).filter(lambda x: x != parent_thinking))
        if has_profile_thinking
        else None
    )

    # Task overrides (optional — None means no override)
    has_override_instructions = draw(st.booleans())
    has_override_max_steps = draw(st.booleans())
    has_override_thinking = draw(st.booleans())
    has_override_model = draw(st.booleans())

    override_instructions = (
        draw(st.text(min_size=3, max_size=30, alphabet="wxyz12345 "))
        if has_override_instructions
        else None
    )
    override_max_steps = (
        draw(
            st.integers(min_value=5, max_value=50).filter(
                lambda x: x != parent_max_steps and x != profile_max_steps
            )
        )
        if has_override_max_steps
        else None
    )
    override_thinking = (
        draw(
            st.sampled_from(["low", "medium", "high"]).filter(
                lambda x: x != parent_thinking and x != profile_thinking
            )
        )
        if has_override_thinking
        else None
    )

    # Expected values follow: task override > profile > parent
    expected_instructions = override_instructions or profile_instructions or parent_instructions
    expected_max_steps = override_max_steps or profile_max_steps or parent_max_steps
    expected_thinking = override_thinking or profile_thinking or parent_thinking

    return {
        "parent_instructions": parent_instructions,
        "parent_max_steps": parent_max_steps,
        "parent_thinking": parent_thinking,
        "profile_instructions": profile_instructions,
        "profile_max_steps": profile_max_steps,
        "profile_thinking": profile_thinking,
        "has_profile_model": has_profile_model,
        "override_instructions": override_instructions,
        "override_max_steps": override_max_steps,
        "override_thinking": override_thinking,
        "has_override_model": has_override_model,
        "expected_instructions": expected_instructions,
        "expected_max_steps": expected_max_steps,
        "expected_thinking": expected_thinking,
    }


@settings(max_examples=100, deadline=None)
@given(scenario=_precedence_scenario())
async def test_child_spec_precedence_resolution(scenario):
    """Property 33: Child spec precedence.

    For any combination of parent AgentSpec, AgentProfile, and task() overrides,
    child spec resolution SHALL follow: task override > profile > parent.

    This test generates random combinations of parent/profile/override values
    for instructions, max_steps, and thinking_level, then verifies the child
    spec always resolves to the highest-precedence value.

    **Validates: Requirements 18.3**
    """
    # Build parent model — always present
    parent_model = MockModel(["parent-response"])

    # Build profile and override models (distinct instances to verify precedence)
    profile_model = MockModel(["profile-response"]) if scenario["has_profile_model"] else None
    override_model = MockModel(["override-response"]) if scenario["has_override_model"] else None

    # Create profile
    profile = define_agent_profile(
        "worker",
        instructions=scenario["profile_instructions"],
        model=profile_model,
        max_steps=scenario["profile_max_steps"],
        thinking_level=scenario["profile_thinking"],
    )

    # Create parent spec
    spec = create_agent(
        "parent",
        model=parent_model,
        instructions=scenario["parent_instructions"],
        max_steps=scenario["parent_max_steps"],
        thinking_level=scenario["parent_thinking"],
        subagents=[profile],
        detect=False,
    )

    h = Harness(spec)
    sess = h.session()

    # Call _build_child_spec directly to inspect the resolved spec

    child_spec = sess._build_child_spec(
        profile=profile,
        instructions_override=scenario["override_instructions"],
        model_override=override_model,
        thinking_level_override=scenario["override_thinking"],
        max_steps_override=scenario["override_max_steps"],
    )

    # Verify precedence: task override > profile > parent
    assert child_spec.instructions == scenario["expected_instructions"], (
        f"Instructions precedence failed: "
        f"override={scenario['override_instructions']!r}, "
        f"profile={scenario['profile_instructions']!r}, "
        f"parent={scenario['parent_instructions']!r}, "
        f"got={child_spec.instructions!r}, "
        f"expected={scenario['expected_instructions']!r}"
    )
    assert child_spec.max_steps == scenario["expected_max_steps"], (
        f"max_steps precedence failed: "
        f"override={scenario['override_max_steps']}, "
        f"profile={scenario['profile_max_steps']}, "
        f"parent={scenario['parent_max_steps']}, "
        f"got={child_spec.max_steps}, "
        f"expected={scenario['expected_max_steps']}"
    )
    assert child_spec.thinking_level == scenario["expected_thinking"], (
        f"thinking_level precedence failed: "
        f"override={scenario['override_thinking']!r}, "
        f"profile={scenario['profile_thinking']!r}, "
        f"parent={scenario['parent_thinking']!r}, "
        f"got={child_spec.thinking_level!r}, "
        f"expected={scenario['expected_thinking']!r}"
    )

    # Verify model precedence
    if override_model is not None:
        assert child_spec.model is override_model, "Task override model should win"
    elif profile_model is not None:
        assert child_spec.model is profile_model, "Profile model should win over parent"
    else:
        assert child_spec.model is parent_model, "Parent model used when no overrides"


@settings(max_examples=100, deadline=None)
@given(
    has_profile_model=st.booleans(),
    has_override_model=st.booleans(),
)
async def test_child_spec_model_precedence(has_profile_model: bool, has_override_model: bool):
    """Property 33: Model precedence specifically.

    For any combination of parent model, profile model, and task override model,
    the resolved child model SHALL follow: task override > profile > parent.

    **Validates: Requirements 18.3**
    """
    parent_model = MockModel(["parent-out"])
    profile_model = MockModel(["profile-out"]) if has_profile_model else None
    override_model = MockModel(["override-out"]) if has_override_model else None

    profile = define_agent_profile(
        "worker",
        model=profile_model,
    )

    spec = create_agent(
        "parent",
        model=parent_model,
        instructions="parent",
        subagents=[profile],
        detect=False,
    )

    h = Harness(spec)
    sess = h.session()

    child_spec = sess._build_child_spec(
        profile=profile,
        instructions_override=None,
        model_override=override_model,
        thinking_level_override=None,
        max_steps_override=None,
    )

    # Verify model precedence: override > profile > parent
    if override_model is not None:
        assert child_spec.model is override_model
    elif profile_model is not None:
        assert child_spec.model is profile_model
    else:
        assert child_spec.model is parent_model


@settings(max_examples=100, deadline=None)
@given(
    parent_steps=st.integers(min_value=5, max_value=50),
    profile_steps=st.one_of(st.none(), st.integers(min_value=5, max_value=50)),
    override_steps=st.one_of(st.none(), st.integers(min_value=5, max_value=50)),
)
async def test_child_spec_max_steps_precedence(
    parent_steps: int,
    profile_steps: int | None,
    override_steps: int | None,
):
    """Property 33: max_steps precedence specifically.

    For any combination of parent max_steps, profile max_steps, and task override
    max_steps, the resolved value SHALL follow: task override > profile > parent.

    **Validates: Requirements 18.3**
    """
    model = MockModel(["done."])

    profile = define_agent_profile(
        "worker",
        max_steps=profile_steps,
    )

    spec = create_agent(
        "parent",
        model=model,
        instructions="parent",
        max_steps=parent_steps,
        subagents=[profile],
        detect=False,
    )

    h = Harness(spec)
    sess = h.session()

    child_spec = sess._build_child_spec(
        profile=profile,
        instructions_override=None,
        model_override=None,
        thinking_level_override=None,
        max_steps_override=override_steps,
    )

    # Expected: override > profile > parent
    expected = override_steps or profile_steps or parent_steps
    assert child_spec.max_steps == expected, (
        f"max_steps: override={override_steps}, profile={profile_steps}, "
        f"parent={parent_steps}, got={child_spec.max_steps}, expected={expected}"
    )
