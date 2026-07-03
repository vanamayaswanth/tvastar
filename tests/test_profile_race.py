"""Tests for profile routing race condition fix (Req 22).

Validates that concurrent session.task() calls with distinct profile names
do not corrupt shared model state.
"""

from __future__ import annotations

import asyncio

import pytest

from tvastar import Harness, create_agent, define_agent_profile
from tvastar.model.mock import MockModel


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_mutate_shared_model_profile():
    """Concurrent task() calls with profile routing must not corrupt the
    shared model's _profile field.

    Requirements: 22.1, 22.2
    """
    shared_model = MockModel(
        scripts={
            "alpha": ["alpha-done"],
            "beta": ["beta-done"],
            "gamma": ["gamma-done"],
        }
    )

    # Capture the initial _profile state
    initial_profile = shared_model._profile

    profiles = [
        define_agent_profile("alpha", description="alpha agent"),
        define_agent_profile("beta", description="beta agent"),
        define_agent_profile("gamma", description="gamma agent"),
    ]

    agent = create_agent(
        "race-test",
        model=shared_model,
        instructions="test",
        subagents=profiles,
    )
    h = Harness(agent)
    sess = h.session()

    async with sess:
        # Launch concurrent tasks that all use the shared model via profiles
        results = await asyncio.gather(
            sess.task("do alpha work", agent="alpha"),
            sess.task("do beta work", agent="beta"),
            sess.task("do gamma work", agent="gamma"),
        )

    # Verify each child got the correct profile-keyed response
    texts = {r.text for r in results}
    assert "alpha-done" in texts, f"Expected 'alpha-done' in results, got {texts}"
    assert "beta-done" in texts, f"Expected 'beta-done' in results, got {texts}"
    assert "gamma-done" in texts, f"Expected 'gamma-done' in results, got {texts}"

    # CRITICAL: Shared model's _profile must be unchanged after all calls
    assert shared_model._profile == initial_profile, (
        f"Shared model's _profile was mutated from {initial_profile!r} "
        f"to {shared_model._profile!r} — race condition not fixed!"
    )


@pytest.mark.asyncio
async def test_shared_model_fields_unchanged_after_concurrent_tasks():
    """After concurrent task() calls complete, the shared model instance
    must have no fields modified.

    Requirements: 22.2
    """
    shared_model = MockModel(
        scripts={
            "worker-a": ["a-response"],
            "worker-b": ["b-response"],
        }
    )

    # Snapshot all public and _profile fields before
    pre_state = {
        "_profile": shared_model._profile,
        "name": shared_model.name,
        "system": shared_model.system,
    }

    profiles = [
        define_agent_profile("worker-a", description="worker a"),
        define_agent_profile("worker-b", description="worker b"),
    ]

    agent = create_agent(
        "field-check",
        model=shared_model,
        instructions="test",
        subagents=profiles,
    )
    h = Harness(agent)
    sess = h.session()

    async with sess:
        await asyncio.gather(
            sess.task("job a", agent="worker-a"),
            sess.task("job b", agent="worker-b"),
        )

    # Verify fields are unchanged
    assert shared_model._profile == pre_state["_profile"]
    assert shared_model.name == pre_state["name"]
    assert shared_model.system == pre_state["system"]


@pytest.mark.asyncio
async def test_per_child_profile_isolation():
    """Each child session receives the correct profile-keyed response even
    under concurrency, proving profile isolation works.

    Requirements: 22.1
    """
    shared_model = MockModel(
        scripts={
            "fast": ["fast-done"],
            "slow": ["slow-done"],
        }
    )

    profiles = [
        define_agent_profile("fast", description="fast worker"),
        define_agent_profile("slow", description="slow worker"),
    ]

    agent = create_agent(
        "isolation-test",
        model=shared_model,
        instructions="test",
        subagents=profiles,
    )
    h = Harness(agent)
    sess = h.session()

    async with sess:
        results = await asyncio.gather(
            sess.task("do fast work", agent="fast"),
            sess.task("do slow work", agent="slow"),
        )

    # Each child must have gotten its own profile-keyed response
    texts = {r.text for r in results}
    assert "fast-done" in texts, f"Expected 'fast-done' in {texts}"
    assert "slow-done" in texts, f"Expected 'slow-done' in {texts}"

    # The shared model's _profile should be restored to its initial value (None)
    # after all concurrent calls complete — proving no persistent mutation.
    assert shared_model._profile is None, (
        f"Shared model _profile should be None but is {shared_model._profile!r}"
    )
