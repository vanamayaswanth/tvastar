"""LoopConfig sealed fields — allow_concurrent and adaptive_scheduling are immutable after construction."""

import pytest
from tvastar.loop import LoopConfig


def test_defaults():
    cfg = LoopConfig(name="x", goal="y")
    assert cfg.allow_concurrent is False
    assert cfg.adaptive_scheduling is False


def test_explicit_values():
    cfg = LoopConfig(name="x", goal="y", allow_concurrent=True, adaptive_scheduling=True)
    assert cfg.allow_concurrent is True
    assert cfg.adaptive_scheduling is True


def test_allow_concurrent_immutable():
    cfg = LoopConfig(name="x", goal="y")
    with pytest.raises(AttributeError, match="immutable"):
        cfg.allow_concurrent = True


def test_adaptive_scheduling_immutable():
    cfg = LoopConfig(name="x", goal="y")
    with pytest.raises(AttributeError, match="immutable"):
        cfg.adaptive_scheduling = True


def test_other_fields_still_mutable():
    cfg = LoopConfig(name="x", goal="y")
    cfg.cancel_after = 99.0
    assert cfg.cancel_after == 99.0
