"""Unit tests for ModelRetryPolicy circuit breaker — task 9.1."""

import time

from tvastar.model.base import ModelRetryPolicy


def test_circuit_starts_closed():
    p = ModelRetryPolicy()
    assert p.circuit_state == "closed"
    assert p.should_allow_request() is True


def test_circuit_opens_after_threshold_failures():
    p = ModelRetryPolicy(circuit_breaker_threshold=3)
    for _ in range(3):
        p._record_failure()
    assert p.circuit_state == "open"
    assert p.should_allow_request() is False


def test_circuit_stays_closed_below_threshold():
    p = ModelRetryPolicy(circuit_breaker_threshold=5)
    for _ in range(4):
        p._record_failure()
    assert p.circuit_state == "closed"
    assert p.should_allow_request() is True


def test_half_open_after_cooldown():
    p = ModelRetryPolicy(circuit_breaker_threshold=2, circuit_breaker_cooldown=0.05)
    p._record_failure()
    p._record_failure()
    assert p.circuit_state == "open"
    time.sleep(0.1)
    assert p.circuit_state == "half_open"
    assert p.should_allow_request() is True


def test_probe_success_closes_circuit():
    p = ModelRetryPolicy(circuit_breaker_threshold=2, circuit_breaker_cooldown=0.05)
    p._record_failure()
    p._record_failure()
    time.sleep(0.1)
    assert p.circuit_state == "half_open"
    p._record_success()
    assert p.circuit_state == "closed"
    assert p._consecutive_failures == 0


def test_probe_failure_resets_cooldown():
    p = ModelRetryPolicy(circuit_breaker_threshold=2, circuit_breaker_cooldown=0.05)
    p._record_failure()
    p._record_failure()
    time.sleep(0.1)
    assert p.circuit_state == "half_open"
    p._record_failure()
    assert p.circuit_state == "open"
    # Cooldown restarted — should still be open immediately
    assert p.should_allow_request() is False


def test_success_resets_failure_counter():
    p = ModelRetryPolicy(circuit_breaker_threshold=5)
    for _ in range(4):
        p._record_failure()
    p._record_success()
    assert p._consecutive_failures == 0
    assert p.circuit_state == "closed"
