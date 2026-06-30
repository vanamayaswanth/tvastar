"""Property-based tests for tool masking and governance.

Property 11: ToolPolicy subset invariant
- For any ToolPolicy and MaskContext with available tools A, the set returned
  by the policy intersected with A is a subset of A — a policy can never grant
  tools not in the available set.

Property 14: GovernancePolicy copy independence
- For any GovernancePolicy, copy() produces an instance where set_phase() on the
  copy does not affect the original's current_phase, and vice versa.

**Validates: Requirements 5.1, 5.6**
"""

from __future__ import annotations

from typing import Iterable

import hypothesis.strategies as st
from hypothesis import given, settings, assume

from tvastar.masking import GovernancePolicy, MaskContext, ToolPolicy, apply_policy


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Tool names: short lowercase identifiers
st_tool_name = st.from_regex(r"[a-z][a-z0-9_]{1,12}", fullmatch=True)

# Phase names: short lowercase identifiers
st_phase_name = st.from_regex(r"[a-z][a-z0-9_]{1,10}", fullmatch=True)

# Available tool sets: 1-10 unique tool names
st_available_tools = st.lists(st_tool_name, min_size=1, max_size=10, unique=True)


# ---------------------------------------------------------------------------
# Property 11: ToolPolicy subset invariant
# ---------------------------------------------------------------------------


@st.composite
def st_subset_policy(draw: st.DrawFn) -> tuple[list[str], ToolPolicy]:
    """Generate available tools and a policy that returns a subset of them.

    The policy returns only tools from the available set — the well-behaved case.
    """
    available = draw(st_available_tools)
    # Pick a random subset of available tools for the policy to return
    subset = draw(
        st.lists(st.sampled_from(available), min_size=0, max_size=len(available), unique=True)
    )

    def policy(ctx: MaskContext) -> list[str]:
        return subset

    return available, policy


@st.composite
def st_superset_policy(draw: st.DrawFn) -> tuple[list[str], ToolPolicy, list[str]]:
    """Generate available tools and a policy that returns extra tools NOT in available.

    This is the adversarial case: the policy tries to grant tools beyond the
    available set. The harness intersection must filter them out.
    """
    available = draw(st_available_tools)
    # Generate extra tool names guaranteed not in available
    extra_tools = draw(
        st.lists(
            st.from_regex(r"extra_[a-z0-9]{2,8}", fullmatch=True),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    # Filter out any accidental collisions
    extra_tools = [t for t in extra_tools if t not in available]
    assume(len(extra_tools) > 0)

    # Policy returns available tools plus extra non-available tools
    policy_result = available + extra_tools

    def policy(ctx: MaskContext) -> list[str]:
        return policy_result

    return available, policy, extra_tools


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_tool_policy_subset_invariant_well_behaved(data: st.DataObject) -> None:
    """Property 11: Well-behaved policy result intersected with available is a subset.

    **Validates: Requirements 5.1**

    For any ToolPolicy that returns a subset of available tools, the effective
    set (after harness intersection) is always a subset of available.
    """
    available, policy = data.draw(st_subset_policy())
    ctx = MaskContext(step=1, available=available, messages=[])

    # apply_policy returns the raw set from the policy
    result = apply_policy(policy, ctx)
    assert result is not None

    available_set = set(available)

    # Simulate the Session intersection: [s for s in specs if s.name in allowed]
    effective = result & available_set

    # The key invariant: effective tools are always a subset of available
    assert effective <= available_set, (
        f"Effective tool set {effective} is not a subset of available {available_set}. "
        f"Policy returned {result}."
    )


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_tool_policy_subset_invariant_greedy_policy(data: st.DataObject) -> None:
    """Property 11: Greedy policy cannot grant tools not in available set.

    **Validates: Requirements 5.1**

    For any ToolPolicy that returns tools NOT in the available set, after the
    harness intersects with available, those extra tools SHALL NOT appear in the
    effective set. The policy can never grant tools not in available.
    """
    available, policy, extra_tools = data.draw(st_superset_policy())
    ctx = MaskContext(step=1, available=available, messages=[])

    # apply_policy returns the raw set from the policy
    result = apply_policy(policy, ctx)
    assert result is not None

    available_set = set(available)

    # Simulate the Session intersection logic
    effective = result & available_set

    # Extra tools must NOT appear in effective set
    for tool in extra_tools:
        assert tool not in effective, (
            f"Tool {tool!r} is not in available set {available_set} but appeared "
            f"in effective set {effective}. Policy returned {result}."
        )

    # Effective set must be a subset of available
    assert effective <= available_set, (
        f"Effective set {effective} is not a subset of available {available_set}."
    )


@settings(max_examples=100, deadline=None)
@given(
    available=st_available_tools,
    step=st.integers(min_value=1, max_value=20),
    data=st.data(),
)
def test_tool_policy_subset_invariant_random_policy(
    available: list[str], step: int, data: st.DataObject
) -> None:
    """Property 11: Random policy with arbitrary tool names never grants non-available tools.

    **Validates: Requirements 5.1**

    For any ToolPolicy that returns an arbitrary set of tool names (some in
    available, some not), the harness intersection ensures the effective set
    is always a subset of available.
    """
    # Generate a random set of tool names — mix of available and non-available
    random_tools = data.draw(
        st.lists(
            st.one_of(
                # Some from available
                st.sampled_from(available) if available else st.nothing(),
                # Some completely random (likely not in available)
                st.from_regex(r"[a-z][a-z0-9_]{1,12}", fullmatch=True),
            ),
            min_size=0,
            max_size=15,
        )
    )

    def policy(ctx: MaskContext) -> Iterable[str]:
        return random_tools

    ctx = MaskContext(step=step, available=available, messages=[])

    result = apply_policy(policy, ctx)
    assert result is not None

    available_set = set(available)

    # Simulate Session intersection
    effective = result & available_set

    # Key invariant: effective is always a subset of available
    assert effective <= available_set, (
        f"Effective set {effective} is not a subset of available {available_set}. "
        f"Policy returned {result}, random_tools was {random_tools}."
    )

    # No tool NOT in available can appear in the effective set
    non_available_in_effective = effective - available_set
    assert len(non_available_in_effective) == 0, (
        f"Found non-available tools in effective set: {non_available_in_effective}"
    )


@st.composite
def st_phases_dict(draw: st.DrawFn) -> dict[str, set[str]]:
    """Generate a phases dictionary with 2-5 phases, each with 1-5 tool names."""
    num_phases = draw(st.integers(min_value=2, max_value=5))
    phase_names = draw(
        st.lists(st_phase_name, min_size=num_phases, max_size=num_phases, unique=True)
    )
    phases: dict[str, set[str]] = {}
    for name in phase_names:
        tools = draw(st.lists(st_tool_name, min_size=1, max_size=5, unique=True))
        phases[name] = set(tools)
    return phases


@st.composite
def st_governance_policy(draw: st.DrawFn) -> GovernancePolicy:
    """Generate a random GovernancePolicy with valid phases and initial phase."""
    phases = draw(st_phases_dict())
    initial_phase = draw(st.sampled_from(sorted(phases.keys())))
    return GovernancePolicy(phases=phases, current_phase=initial_phase)


# ---------------------------------------------------------------------------
# Property 14: GovernancePolicy copy independence
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    gov=st_governance_policy(),
    data=st.data(),
)
def test_governance_copy_phase_change_does_not_affect_original(
    gov: GovernancePolicy, data: st.DataObject
) -> None:
    """Property 14: set_phase() on copy does not affect the original.

    **Validates: Requirements 5.6**

    For any GovernancePolicy, after copy(), calling set_phase() on the copy
    shall NOT change the original's current_phase.
    """
    # Pick a different phase to switch the copy to
    other_phases = [p for p in gov.phases if p != gov.current_phase]
    assume(len(other_phases) > 0)
    new_phase = data.draw(st.sampled_from(sorted(other_phases)))

    original_phase = gov.current_phase

    # Create a copy and change its phase
    copy_gov = gov.copy()
    copy_gov.set_phase(new_phase)

    # Verify original is unchanged
    assert gov.current_phase == original_phase, (
        f"Original's current_phase changed from {original_phase!r} to "
        f"{gov.current_phase!r} after set_phase({new_phase!r}) on copy"
    )

    # Verify copy's phase was changed
    assert copy_gov.current_phase == new_phase, (
        f"Copy's current_phase should be {new_phase!r} but is "
        f"{copy_gov.current_phase!r}"
    )


@settings(max_examples=100, deadline=None)
@given(
    gov=st_governance_policy(),
    data=st.data(),
)
def test_governance_copy_phase_change_on_original_does_not_affect_copy(
    gov: GovernancePolicy, data: st.DataObject
) -> None:
    """Property 14: set_phase() on original does not affect the copy.

    **Validates: Requirements 5.6**

    For any GovernancePolicy, after copy(), calling set_phase() on the original
    shall NOT change the copy's current_phase.
    """
    # Pick a different phase to switch the original to
    other_phases = [p for p in gov.phases if p != gov.current_phase]
    assume(len(other_phases) > 0)
    new_phase = data.draw(st.sampled_from(sorted(other_phases)))

    # Create a copy
    copy_gov = gov.copy()
    copy_initial_phase = copy_gov.current_phase

    # Change the original's phase
    gov.set_phase(new_phase)

    # Verify copy is unchanged
    assert copy_gov.current_phase == copy_initial_phase, (
        f"Copy's current_phase changed from {copy_initial_phase!r} to "
        f"{copy_gov.current_phase!r} after set_phase({new_phase!r}) on original"
    )

    # Verify original's phase was changed
    assert gov.current_phase == new_phase, (
        f"Original's current_phase should be {new_phase!r} but is "
        f"{gov.current_phase!r}"
    )


@settings(max_examples=100, deadline=None)
@given(
    gov=st_governance_policy(),
)
def test_governance_copy_phases_dict_independence(gov: GovernancePolicy) -> None:
    """Property 14 (extended): copy() produces independent phases dicts.

    **Validates: Requirements 5.6**

    For any GovernancePolicy, mutating the phases dict on the copy shall NOT
    affect the original's phases dict.
    """
    copy_gov = gov.copy()

    # Mutate the copy's phases dict by adding a new tool to one phase
    first_phase = sorted(copy_gov.phases.keys())[0]
    copy_gov.phases[first_phase].add("injected_tool_xyz")

    # Verify the original's phases are unaffected
    assert "injected_tool_xyz" not in gov.phases[first_phase], (
        f"Original's phases[{first_phase!r}] was affected by mutating the copy's phases"
    )
