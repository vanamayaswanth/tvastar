"""Tests for tvastar.comply.dashboard — ComplianceDashboard."""

from __future__ import annotations

import json
import threading
import time

from tvastar.comply import ComplianceDashboard
from tvastar.comply.models import AuditResult


def _make_result(
    loop_name: str, status: str = "COMPLIANT", timestamp: float | None = None
) -> AuditResult:
    return AuditResult(
        loop_name=loop_name,
        status=status,
        framework="EU_AI_Act",
        checks=[],
        pii_verification=None,
        timestamp=timestamp if timestamp is not None else time.time(),
    )


def test_empty_dashboard_returns_zero_summary():
    db = ComplianceDashboard()
    s = db.query()
    assert s.total == 0
    assert s.compliant == 0
    assert s.non_compliant == 0
    assert s.stale == 0
    assert s.fleet_compliance_pct == 0.0
    assert s.per_loop == []
    assert s.compliance_overhead is None


def test_fleet_math_invariant():
    """Property 8: total == compliant + non_compliant + stale."""
    db = ComplianceDashboard(check_interval=60.0)
    now = time.time()
    db.update("a", _make_result("a", "COMPLIANT", now))
    db.update("b", _make_result("b", "NON_COMPLIANT", now))
    db.update("c", _make_result("c", "COMPLIANT", now - 200))  # stale

    s = db.query()
    assert s.total == s.compliant + s.non_compliant + s.stale
    assert s.compliant == 1
    assert s.non_compliant == 1
    assert s.stale == 1
    assert s.fleet_compliance_pct == (1 / 3) * 100


def test_staleness_marking():
    """Property 9: loop with last_check older than 2×interval → STALE."""
    db = ComplianceDashboard(check_interval=30.0)
    old_ts = time.time() - 100  # older than 2*30=60
    db.update("stale-loop", _make_result("stale-loop", "COMPLIANT", old_ts))

    s = db.query()
    loop = s.per_loop[0]
    assert loop.status == "STALE"


def test_consecutive_compliant_increments():
    db = ComplianceDashboard()
    for _ in range(5):
        db.update("x", _make_result("x", "COMPLIANT"))

    s = db.query()
    loop = [l for l in s.per_loop if l.loop_name == "x"][0]
    assert loop.consecutive_compliant == 5


def test_consecutive_compliant_resets_on_non_compliant():
    db = ComplianceDashboard()
    db.update("x", _make_result("x", "COMPLIANT"))
    db.update("x", _make_result("x", "COMPLIANT"))
    db.update("x", _make_result("x", "NON_COMPLIANT"))

    s = db.query()
    loop = [l for l in s.per_loop if l.loop_name == "x"][0]
    assert loop.consecutive_compliant == 0


def test_to_json_produces_valid_json():
    db = ComplianceDashboard()
    db.update("a", _make_result("a", "COMPLIANT"))
    j = db.to_json()
    parsed = json.loads(j)
    assert "total" in parsed
    assert "per_loop" in parsed
    assert parsed["total"] == 1


def test_compliance_overhead_included_when_set():
    db = ComplianceDashboard()
    db.update("a", _make_result("a", "COMPLIANT"))
    db.set_overhead("a", 0.08)
    s = db.query()
    assert s.compliance_overhead == {"a": 0.08}


def test_thread_safety_no_deadlock():
    db = ComplianceDashboard()

    def writer(prefix: str):
        for i in range(20):
            db.update(f"{prefix}-{i}", _make_result(f"{prefix}-{i}"))

    threads = [threading.Thread(target=writer, args=(f"t{t}",)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = db.query()
    assert s.total == 80
