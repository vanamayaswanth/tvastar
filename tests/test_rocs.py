"""Tests for tvastar.rocs — ROCSTracker."""

from tvastar.memory.store import InMemoryStore
from tvastar.rocs import ROCSTracker


def test_record_basic():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "test-loop")
    result = tracker.record("run-1", tokens_consumed=1000, quality_score=80)
    assert result.loop_name == "test-loop"
    assert result.run_id == "run-1"
    assert result.value_delivered == 0.8
    assert result.tokens_consumed == 1000
    assert result.score == 0.8 / 1000


def test_record_zero_tokens():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop")
    result = tracker.record("r1", tokens_consumed=0, quality_score=50)
    assert result.score == 0.0


def test_record_with_policy():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop", policy=lambda: 0.75)
    result = tracker.record("r1", tokens_consumed=100)
    assert result.value_delivered == 0.75
    assert result.score == 0.75 / 100


def test_policy_clamped_above():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop", policy=lambda: 5.0)
    result = tracker.record("r1", tokens_consumed=100)
    assert result.value_delivered == 1.0


def test_policy_clamped_below():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop", policy=lambda: -2.0)
    result = tracker.record("r1", tokens_consumed=100)
    assert result.value_delivered == 0.0


def test_policy_exception():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop", policy=lambda: 1 / 0)
    result = tracker.record("r1", tokens_consumed=100)
    assert result.score == 0.0
    assert result.value_delivered == 0.0


def test_aggregate_basic():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop")
    tracker.record("r1", 1000, 80)
    tracker.record("r2", 500, 50)
    mean = tracker.aggregate(n=10)
    expected = ((0.8 / 1000) + (0.5 / 500)) / 2
    assert abs(mean - expected) < 1e-10


def test_aggregate_empty():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop")
    assert tracker.aggregate() == 0.0


def test_aggregate_clamps_n():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "loop")
    tracker.record("r1", 100, 50)
    # n=0 clamped to 1
    assert tracker.aggregate(n=0) == tracker.aggregate(n=1)
    # n=9999 clamped to 1000
    assert tracker.aggregate(n=9999) == tracker.aggregate(n=1000)


def test_store_key_format():
    store = InMemoryStore()
    tracker = ROCSTracker(store, "my-loop")
    tracker.record("run-42", 200, 60)
    assert store.get("rocs:my-loop:run-42") is not None
