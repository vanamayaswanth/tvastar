"""Prometheus-format metrics collector for loop state transitions.

Zero dependencies beyond stdlib (string formatting only).
Installable via loop.on_event(MetricsCollector()) or registry.on_event(MetricsCollector()).
"""

from __future__ import annotations

from collections import defaultdict

from . import LoopEvent, LoopState

_DURATION_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0)


class MetricsCollector:
    """Collect loop metrics, expose in Prometheus text exposition format.

    Tracks per-loop counters (runs, passes, fails, handoffs) and a duration
    histogram (time from RUNNING to PASS/FAIL).
    """

    def __init__(self) -> None:
        # Per-loop counters: {loop_name: count}
        self._runs: dict[str, int] = defaultdict(int)
        self._passes: dict[str, int] = defaultdict(int)
        self._fails: dict[str, int] = defaultdict(int)
        self._handoffs: dict[str, int] = defaultdict(int)

        # Duration tracking: run_id → started_at (when RUNNING event fires)
        self._started: dict[str, float] = {}

        # Histogram data per loop: {loop_name: {bucket_le: count}}
        self._histogram_buckets: dict[str, list[int]] = defaultdict(
            lambda: [0] * (len(_DURATION_BUCKETS) + 1)  # +1 for +Inf
        )
        self._histogram_sum: dict[str, float] = defaultdict(float)
        self._histogram_count: dict[str, int] = defaultdict(int)

    def __call__(self, event: LoopEvent) -> None:
        name = event.loop_name

        if event.state == LoopState.RUNNING:
            self._runs[name] += 1
            self._started[event.run_id] = event.at

        elif event.state == LoopState.PASS:
            self._passes[name] += 1
            self._record_duration(name, event)

        elif event.state == LoopState.FAIL:
            self._fails[name] += 1
            self._record_duration(name, event)

        elif event.state == LoopState.HANDOFF:
            self._handoffs[name] += 1

    def _record_duration(self, name: str, event: LoopEvent) -> None:
        started = self._started.pop(event.run_id, None)
        if started is None:
            return
        duration = event.at - started
        # Increment all buckets >= observed value
        buckets = self._histogram_buckets[name]
        for i, le in enumerate(_DURATION_BUCKETS):
            if duration <= le:
                buckets[i] += 1
        # +Inf always gets incremented
        buckets[-1] += 1
        self._histogram_sum[name] += duration
        self._histogram_count[name] += 1

    def render(self) -> str:
        """Produce Prometheus text exposition format output."""
        # Collect all known loop names across all counters and histograms
        loops = sorted(
            set(self._runs)
            | set(self._passes)
            | set(self._fails)
            | set(self._handoffs)
            | set(self._histogram_count)
        )
        if not loops:
            return ""

        lines: list[str] = []

        # --- runs_total ---
        lines.append("# HELP tvastar_loop_runs_total Total loop runs")
        lines.append("# TYPE tvastar_loop_runs_total counter")
        for name in loops:
            lines.append(f'tvastar_loop_runs_total{{loop="{name}"}} {self._runs[name]}')

        # --- passes_total ---
        lines.append("# HELP tvastar_loop_passes_total Total successful runs")
        lines.append("# TYPE tvastar_loop_passes_total counter")
        for name in loops:
            lines.append(f'tvastar_loop_passes_total{{loop="{name}"}} {self._passes[name]}')

        # --- fails_total ---
        lines.append("# HELP tvastar_loop_fails_total Total failed runs")
        lines.append("# TYPE tvastar_loop_fails_total counter")
        for name in loops:
            lines.append(f'tvastar_loop_fails_total{{loop="{name}"}} {self._fails[name]}')

        # --- handoffs_total ---
        lines.append("# HELP tvastar_loop_handoffs_total Total handoffs triggered")
        lines.append("# TYPE tvastar_loop_handoffs_total counter")
        for name in loops:
            lines.append(f'tvastar_loop_handoffs_total{{loop="{name}"}} {self._handoffs[name]}')

        # --- duration histogram ---
        lines.append("# HELP tvastar_loop_duration_seconds Run duration histogram")
        lines.append("# TYPE tvastar_loop_duration_seconds histogram")
        for name in loops:
            buckets = self._histogram_buckets[name]
            for i, le in enumerate(_DURATION_BUCKETS):
                lines.append(
                    f'tvastar_loop_duration_seconds_bucket{{loop="{name}",le="{le}"}} {buckets[i]}'
                )
            lines.append(
                f'tvastar_loop_duration_seconds_bucket{{loop="{name}",le="+Inf"}} {buckets[-1]}'
            )
            lines.append(
                f'tvastar_loop_duration_seconds_sum{{loop="{name}"}} {self._histogram_sum[name]}'
            )
            lines.append(
                f'tvastar_loop_duration_seconds_count{{loop="{name}"}} {self._histogram_count[name]}'
            )

        lines.append("")  # trailing newline
        return "\n".join(lines)
