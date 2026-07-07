"""Tests for MetricsCollector — task 5.2."""

from tvastar.loop import LoopEvent, LoopState
from tvastar.loop.metrics import MetricsCollector


def _event(state, loop_name="ci-sweeper", run_id="run_1", at=100.0, data=None):
    return LoopEvent(
        loop_name=loop_name,
        run_id=run_id,
        state=state,
        at=at,
        data=data or {},
    )


class TestCounters:
    def test_runs_increments_on_running(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING))
        m(_event(LoopState.RUNNING, run_id="run_2", at=200.0))
        output = m.render()
        assert 'tvastar_loop_runs_total{loop="ci-sweeper"} 2' in output

    def test_passes_increments_on_pass(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, at=1.0))
        m(_event(LoopState.PASS, at=2.0))
        output = m.render()
        assert 'tvastar_loop_passes_total{loop="ci-sweeper"} 1' in output

    def test_fails_increments_on_fail(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, at=1.0))
        m(_event(LoopState.FAIL, at=2.0))
        output = m.render()
        assert 'tvastar_loop_fails_total{loop="ci-sweeper"} 1' in output

    def test_handoffs_increments_on_handoff(self):
        m = MetricsCollector()
        m(_event(LoopState.HANDOFF))
        output = m.render()
        assert 'tvastar_loop_handoffs_total{loop="ci-sweeper"} 1' in output

    def test_ignores_unrelated_states(self):
        m = MetricsCollector()
        m(_event(LoopState.IDLE))
        m(_event(LoopState.TRIGGERED))
        m(_event(LoopState.VERIFYING))
        m(_event(LoopState.RETRY))
        m(_event(LoopState.SUSPENDED))
        # No loop names registered — render returns empty
        assert m.render() == ""

    def test_multiple_loops_tracked_independently(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, loop_name="loop-a"))
        m(_event(LoopState.RUNNING, loop_name="loop-b"))
        m(_event(LoopState.RUNNING, loop_name="loop-b", run_id="run_2"))
        output = m.render()
        assert 'tvastar_loop_runs_total{loop="loop-a"} 1' in output
        assert 'tvastar_loop_runs_total{loop="loop-b"} 2' in output


class TestDurationHistogram:
    def test_duration_placed_in_correct_bucket(self):
        m = MetricsCollector()
        # 0.5s duration → should be in le="1.0" bucket and all above
        m(_event(LoopState.RUNNING, at=10.0))
        m(_event(LoopState.PASS, at=10.5))
        output = m.render()
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="1.0"} 1' in output
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="5.0"} 1' in output
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="+Inf"} 1' in output

    def test_duration_skips_lower_buckets(self):
        m = MetricsCollector()
        # 3s duration → NOT in le="1.0", but in le="5.0" and above
        m(_event(LoopState.RUNNING, at=10.0))
        m(_event(LoopState.PASS, at=13.0))
        output = m.render()
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="1.0"} 0' in output
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="5.0"} 1' in output

    def test_duration_sum_and_count(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, run_id="r1", at=0.0))
        m(_event(LoopState.PASS, run_id="r1", at=2.0))
        m(_event(LoopState.RUNNING, run_id="r2", at=10.0))
        m(_event(LoopState.FAIL, run_id="r2", at=13.5))
        output = m.render()
        assert 'tvastar_loop_duration_seconds_sum{loop="ci-sweeper"} 5.5' in output
        assert 'tvastar_loop_duration_seconds_count{loop="ci-sweeper"} 2' in output

    def test_no_duration_without_running_event(self):
        m = MetricsCollector()
        # PASS without a preceding RUNNING — no duration tracked
        m(_event(LoopState.PASS, at=50.0))
        output = m.render()
        assert 'tvastar_loop_duration_seconds_count{loop="ci-sweeper"} 0' in output

    def test_fail_also_records_duration(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, at=100.0))
        m(_event(LoopState.FAIL, at=145.0))  # 45s → in le="60.0" bucket
        output = m.render()
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="30.0"} 0' in output
        assert 'tvastar_loop_duration_seconds_bucket{loop="ci-sweeper",le="60.0"} 1' in output


class TestRender:
    def test_empty_collector_returns_empty_string(self):
        m = MetricsCollector()
        assert m.render() == ""

    def test_render_contains_help_and_type_lines(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING))
        output = m.render()
        assert "# HELP tvastar_loop_runs_total Total loop runs" in output
        assert "# TYPE tvastar_loop_runs_total counter" in output
        assert "# HELP tvastar_loop_duration_seconds Run duration histogram" in output
        assert "# TYPE tvastar_loop_duration_seconds histogram" in output

    def test_render_format_matches_prometheus_spec(self):
        m = MetricsCollector()
        m(_event(LoopState.RUNNING, at=1.0))
        m(_event(LoopState.PASS, at=2.5))
        output = m.render()
        # Prometheus requires trailing newline
        assert output.endswith("\n")
        # Each metric line should have exactly one space between metric and value
        for line in output.splitlines():
            if line.startswith("#") or line == "":
                continue
            parts = line.rsplit(" ", 1)
            assert len(parts) == 2

    def test_on_event_callable(self):
        """MetricsCollector is callable and works as an on_event listener."""
        m = MetricsCollector()
        event = _event(LoopState.RUNNING)
        m(event)
        assert 'tvastar_loop_runs_total{loop="ci-sweeper"} 1' in m.render()
