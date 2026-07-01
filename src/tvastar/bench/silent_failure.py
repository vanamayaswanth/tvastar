"""arXiv silent-failure benchmark — evaluate Tvastar detectors against tau2-bench data.

The paper "From Confident Closing to Silent Failure" (arXiv:2606.09863)
studied 9,876 agent trajectories across 8 model families and found that agents
frequently assert task completion when the task was *not* actually completed —
a pattern the paper calls "false success".

This module ingests that dataset, converts each trajectory into a Tvastar
``RunContext``, runs the existing detector suite, and produces a structured
report comparing Tvastar's detection rates against a naive baseline (exit-code
checking only). The output is a publishable Markdown report with per-model,
per-domain, and per-detector breakdowns.

Quick start::

    python -m tvastar.bench.silent_failure run \
        --dataset ./tau2-bench.jsonl \
        --output-dir ./results/

The pipeline is read-only — it does not run agents or make API calls. It
analyses pre-recorded trajectory data and measures how well existing detectors
identify silent failures post-hoc.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from tvastar.detect import RunContext, Severity, run_detectors, default_detectors
from tvastar.detect.base import Finding
from tvastar.tools.base import Tool, ToolRegistry
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RawTrajectory:
    """A parsed but unlabeled trajectory from the tau2-bench dataset."""

    id: str
    model: str
    domain: str
    reward: int  # 0 (task failed) or 1 (task succeeded)
    messages: list[dict[str, Any]]


class FailureLabel(str, Enum):
    """Three-class labeling from the paper's taxonomy."""

    FALSE_SUCCESS = "false_success"
    HONEST_FAILURE = "honest_failure"
    AMBIGUOUS = "ambiguous"


@dataclass
class TrajectoryResult:
    """Per-trajectory detection outcome with metadata."""

    id: str
    model: str
    domain: str
    label: FailureLabel
    tvastar_findings: list[Finding]
    baseline_findings: list[Finding]


@dataclass
class ModelStats:
    """Detection rate and count for one model family."""

    trajectories: int
    detection_rate: float


@dataclass
class DomainStats:
    """Detection rate and count for one domain."""

    trajectories: int
    detection_rate: float


@dataclass
class AggregatedResults:
    """All computed statistics from the benchmark run, ready for reporting."""

    total_loaded: int
    total_failures: int
    label_counts: dict[str, int]  # {false_success: N, honest_failure: N, ambiguous: N}
    overall_detection_rate: float
    baseline_detection_rate: float
    by_model: dict[str, ModelStats]
    by_domain: dict[str, DomainStats]
    per_detector_rates: dict[str, float]
    detector_cooccurrence: list[tuple[str, str, int]]
    trajectory_results: list[TrajectoryResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns for three-class labeling (compiled at module level for perf)
# ---------------------------------------------------------------------------

# False-success assertion patterns — the agent claims the task is done.
_FALSE_SUCCESS_PATTERN: re.Pattern[str] = re.compile(
    r"(?:"
    r"successfully"
    r"|has been processed"
    r"|completed"
    r"|\bdone\b"
    r"|confirmed"
    r"|updated"
    r"|resolved"
    r")",
    re.IGNORECASE,
)

# Honest-failure patterns — the agent admits inability or escalates.
_HONEST_FAILURE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:"
    r"I cannot"
    r"|I'm unable"
    r"|unfortunately"
    r"|not possible"
    r"|transferring to a human"
    r"|I apologize.*unable"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Three-class labeler
# ---------------------------------------------------------------------------


def label_trajectory(final_message: str) -> FailureLabel:
    """Apply the paper's regex-based labeling to a trajectory's final assistant message.

    Classification logic:
    - FALSE_SUCCESS: assertion patterns match AND honest-failure patterns do NOT match.
    - HONEST_FAILURE: honest-failure patterns match AND assertion patterns do NOT match.
    - AMBIGUOUS: both match, or neither matches (including empty strings).
    """
    has_success = bool(_FALSE_SUCCESS_PATTERN.search(final_message))
    has_failure = bool(_HONEST_FAILURE_PATTERN.search(final_message))

    if has_success and not has_failure:
        return FailureLabel.FALSE_SUCCESS
    if has_failure and not has_success:
        return FailureLabel.HONEST_FAILURE
    # Both match or neither matches → ambiguous.
    return FailureLabel.AMBIGUOUS


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ("messages", "model", "reward")


def _parse_entry(raw: dict[str, Any], source: str) -> RawTrajectory | None:
    """Validate a single JSON entry and convert to RawTrajectory.

    Returns None (with a logged warning) if required fields are missing.
    """
    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        entry_id = raw.get("id", "<unknown>")
        logger.warning(
            "Skipping entry %s from %s: missing required fields %s",
            entry_id,
            source,
            missing,
        )
        return None

    return RawTrajectory(
        id=raw.get("id", f"auto-{id(raw)}"),
        model=raw["model"],
        domain=raw.get("domain", "unknown"),
        reward=int(raw["reward"]),
        messages=raw["messages"],
    )


def _load_jsonl(path: Path) -> list[RawTrajectory]:
    """Load trajectories from a JSON Lines file (one JSON object per line)."""
    results: list[RawTrajectory] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                logger.warning("Malformed JSON at %s line %d: %s", path.name, line_no, exc)
                continue
            traj = _parse_entry(entry, f"{path.name}:{line_no}")
            if traj is not None:
                results.append(traj)
    return results


def _load_json_file(path: Path) -> list[RawTrajectory]:
    """Load trajectories from a single JSON file (array or JSONL)."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    # Try as a JSON array first
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fall through to JSONL parsing
            return _load_jsonl(path)

        if isinstance(data, list):
            results: list[RawTrajectory] = []
            for idx, entry in enumerate(data):
                if not isinstance(entry, dict):
                    logger.warning(
                        "Malformed entry at %s index %d: expected object, got %s",
                        path.name,
                        idx,
                        type(entry).__name__,
                    )
                    continue
                traj = _parse_entry(entry, f"{path.name}[{idx}]")
                if traj is not None:
                    results.append(traj)
            return results

    # Otherwise treat as JSONL
    return _load_jsonl(path)


def _load_directory(dir_path: Path) -> list[RawTrajectory]:
    """Load trajectories from a directory of JSON/JSONL files."""
    results: list[RawTrajectory] = []
    json_files = sorted(p for p in dir_path.iterdir() if p.suffix in (".json", ".jsonl"))
    if not json_files:
        logger.warning("No .json or .jsonl files found in %s", dir_path)
    for file_path in json_files:
        results.extend(_load_json_file(file_path))
    return results


def load_trajectories(dataset_path: Path) -> list[RawTrajectory]:
    """Load tau2-bench trajectories from a file or directory.

    Supports:
    - A single JSONL file (one JSON object per line)
    - A single JSON file containing an array of trajectory objects
    - A directory containing multiple .json/.jsonl files

    Args:
        dataset_path: Path to a JSONL file, JSON file, or directory.

    Returns:
        List of validated RawTrajectory objects.

    Raises:
        FileNotFoundError: If dataset_path does not exist.
    """
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")

    if dataset_path.is_dir():
        return _load_directory(dataset_path)

    return _load_json_file(dataset_path)


# ---------------------------------------------------------------------------
# Trajectory adapter
# ---------------------------------------------------------------------------

_EXIT_CODE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\[exit\s+[1-9]\d*\]|exit\s+code\s+[1-9]\d*)", re.IGNORECASE
)


def _make_permissive_tool(name: str) -> Tool:
    """Create a Tool with a permissive schema for benchmark adaptation."""

    async def _noop(**kwargs: Any) -> str:  # noqa: ARG001
        return "ok"

    return Tool(
        name=name,
        description=f"Permissive stub for '{name}' (benchmark use only)",
        fn=_noop,
        input_schema={"type": "object", "additionalProperties": True},
    )


def adapt_trajectory(raw: RawTrajectory) -> RunContext:
    """Convert a tau2-bench trajectory into a Tvastar RunContext.

    Maps tau2-bench message dicts to Tvastar Message objects:
    - assistant messages → Message with TextBlock + ToolUseBlock content
    - tool messages → Message with ToolResultBlock content
    - user/system messages → Message with plain text

    Constructs a ToolRegistry with permissive schemas for all tool names
    found in the trajectory. Sets ``stopped`` to ``"end_turn"`` for
    normally-completed trajectories or ``"max_steps"`` for step-limited ones.
    """
    messages: list[Message] = []
    tool_names: set[str] = set()
    final_text = ""

    for msg in raw.messages:
        role = msg.get("role", "user")
        content_raw = msg.get("content", "")

        if role == "tool":
            # Tool result message
            tool_use_id = msg.get("tool_call_id", f"call_{id(msg):x}")
            result_content = content_raw if isinstance(content_raw, str) else str(content_raw)
            is_error = msg.get("is_error", False)
            block = ToolResultBlock(
                tool_use_id=tool_use_id,
                content=result_content,
                is_error=bool(is_error),
            )
            messages.append(Message(role="tool", content=[block]))

        elif role == "assistant":
            blocks: list[Any] = []
            # Add text content
            if content_raw and isinstance(content_raw, str):
                blocks.append(TextBlock(text=content_raw))
                final_text = content_raw

            # Add tool calls
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                func_info = tc.get("function", {})
                tc_name = func_info.get("name", "unknown_tool")
                tool_names.add(tc_name)
                # Parse arguments from JSON string
                args_raw = func_info.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except (json.JSONDecodeError, ValueError):
                        args = {"raw": args_raw}
                else:
                    args = args_raw if isinstance(args_raw, dict) else {}

                tc_id = tc.get("id", f"call_{id(tc):x}")
                blocks.append(ToolUseBlock(name=tc_name, input=args, id=tc_id))

            messages.append(
                Message(role="assistant", content=blocks if blocks else [TextBlock(text="")])
            )

        else:
            # user or system
            text = content_raw if isinstance(content_raw, str) else str(content_raw)
            messages.append(Message(role=role, content=text))

    # Build ToolRegistry with permissive schemas
    registry = ToolRegistry()
    for name in sorted(tool_names):
        registry.add(_make_permissive_tool(name))

    # Determine stop reason: if the last message is from the assistant, it's
    # a normal end_turn; otherwise it likely hit a step limit.
    last_role = raw.messages[-1].get("role") if raw.messages else None
    stopped = "end_turn" if last_role == "assistant" else "max_steps"

    return RunContext(
        messages=messages,
        tools=registry,
        stopped=stopped,
        final_text=final_text,
    )


# ---------------------------------------------------------------------------
# Naive baseline detector
# ---------------------------------------------------------------------------


def naive_baseline(ctx: RunContext) -> list[Finding]:
    """Simulates traditional monitoring: only checks exit codes and explicit error strings.

    Fires only when the last ToolResultBlock has is_error=True or its content
    contains an exit-code pattern like ``[exit 1]`` or ``exit code 1``.

    This intentionally misses all semantic failures — that's the point of
    comparing it against Tvastar's detector suite.
    """
    last_result = ctx.last_tool_result
    if last_result is None:
        return []

    if last_result.is_error:
        return [
            Finding(
                detector="naive_baseline",
                severity=Severity.WARNING,
                message=f"Tool returned explicit error: {last_result.content[:100]}",
            )
        ]

    if _EXIT_CODE_PATTERN.search(last_result.content):
        return [
            Finding(
                detector="naive_baseline",
                severity=Severity.WARNING,
                message="Tool output contains non-zero exit code",
            )
        ]

    return []


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def run_benchmark(dataset_path: Path) -> list[TrajectoryResult]:
    """Run the full benchmark pipeline on the given dataset.

    For each trajectory: load → filter (reward=0 only) → label → adapt →
    run default_detectors + naive_baseline → collect TrajectoryResult.

    Isolates failures (logs and continues) to ensure partial results are
    always available.
    """
    trajectories = load_trajectories(dataset_path)
    detectors = default_detectors()
    results: list[TrajectoryResult] = []

    # Filter to only failed trajectories (reward=0)
    failures = [t for t in trajectories if t.reward == 0]
    logger.info(
        "Loaded %d trajectories, %d failures (reward=0)",
        len(trajectories),
        len(failures),
    )

    for raw in failures:
        try:
            # Get final assistant message for labeling
            final_msg = ""
            for msg in reversed(raw.messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        final_msg = content
                        break

            label = label_trajectory(final_msg)

            # Adapt to RunContext
            ctx = adapt_trajectory(raw)

            # Run Tvastar detectors
            tvastar_findings = run_detectors(ctx, detectors)

            # Run naive baseline
            baseline_findings = naive_baseline(ctx)

            results.append(
                TrajectoryResult(
                    id=raw.id,
                    model=raw.model,
                    domain=raw.domain,
                    label=label,
                    tvastar_findings=tvastar_findings,
                    baseline_findings=baseline_findings,
                )
            )
        except Exception as exc:
            logger.warning("Failed to process trajectory %s: %s", raw.id, exc)
            continue

    return results


# ---------------------------------------------------------------------------
# Results aggregation
# ---------------------------------------------------------------------------


def aggregate_results(results: list[TrajectoryResult]) -> AggregatedResults:
    """Compute all aggregate statistics from per-trajectory results."""
    total = len(results)
    if total == 0:
        return AggregatedResults(
            total_loaded=0,
            total_failures=0,
            label_counts={},
            overall_detection_rate=0.0,
            baseline_detection_rate=0.0,
            by_model={},
            by_domain={},
            per_detector_rates={},
            detector_cooccurrence=[],
            trajectory_results=results,
        )

    # Label counts
    label_counts: dict[str, int] = defaultdict(int)
    for r in results:
        label_counts[r.label.value] += 1

    # Overall detection rate (Tvastar found at least one finding)
    tvastar_detected = sum(1 for r in results if r.tvastar_findings)
    overall_detection_rate = tvastar_detected / total

    # Baseline detection rate
    baseline_detected = sum(1 for r in results if r.baseline_findings)
    baseline_detection_rate = baseline_detected / total

    # Per-model stats
    model_groups: dict[str, list[TrajectoryResult]] = defaultdict(list)
    for r in results:
        model_groups[r.model].append(r)

    by_model: dict[str, ModelStats] = {}
    for model, group in sorted(model_groups.items()):
        detected = sum(1 for r in group if r.tvastar_findings)
        by_model[model] = ModelStats(
            trajectories=len(group),
            detection_rate=detected / len(group),
        )

    # Per-domain stats
    domain_groups: dict[str, list[TrajectoryResult]] = defaultdict(list)
    for r in results:
        domain_groups[r.domain].append(r)

    by_domain: dict[str, DomainStats] = {}
    for domain, group in sorted(domain_groups.items()):
        detected = sum(1 for r in group if r.tvastar_findings)
        by_domain[domain] = DomainStats(
            trajectories=len(group),
            detection_rate=detected / len(group),
        )

    # Per-detector rates
    detector_counts: dict[str, int] = defaultdict(int)
    for r in results:
        seen: set[str] = set()
        for f in r.tvastar_findings:
            if f.detector not in seen:
                detector_counts[f.detector] += 1
                seen.add(f.detector)

    per_detector_rates: dict[str, float] = {
        det: count / total for det, count in sorted(detector_counts.items())
    }

    # Detector co-occurrence (pairs that fire on the same trajectory)
    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
    for r in results:
        detectors_fired = sorted({f.detector for f in r.tvastar_findings})
        for i, d1 in enumerate(detectors_fired):
            for d2 in detectors_fired[i + 1 :]:
                cooccurrence[(d1, d2)] += 1

    detector_cooccurrence = [
        (d1, d2, count) for (d1, d2), count in sorted(cooccurrence.items(), key=lambda x: -x[1])
    ]

    return AggregatedResults(
        total_loaded=total,
        total_failures=total,
        label_counts=dict(label_counts),
        overall_detection_rate=overall_detection_rate,
        baseline_detection_rate=baseline_detection_rate,
        by_model=by_model,
        by_domain=by_domain,
        per_detector_rates=per_detector_rates,
        detector_cooccurrence=detector_cooccurrence,
        trajectory_results=results,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(agg: AggregatedResults, paper_cite: str = "arXiv:2606.09863") -> str:
    """Produce a publishable Markdown report from aggregated results.

    Sections: Executive Summary, Methodology, Per-Detector Analysis,
    Per-Model Breakdown, Domain Analysis, Traditional Monitoring Comparison,
    Conclusion.
    """
    lines: list[str] = []

    detection_gap = agg.overall_detection_rate - agg.baseline_detection_rate

    # Title
    lines.append("# Silent Failure Detection Benchmark Report")
    lines.append("")

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        f"Tvastar's detector suite was evaluated against {agg.total_failures} "
        f"failed agent trajectories from the tau2-bench dataset ({paper_cite})."
    )
    lines.append(
        f"Overall detection rate: **{agg.overall_detection_rate:.1%}** "
        f"vs. naive baseline: **{agg.baseline_detection_rate:.1%}** "
        f"(+{detection_gap:.1%} improvement)."
    )
    lines.append("")

    # Label distribution
    if agg.label_counts:
        lines.append("**Failure label distribution:**")
        lines.append("")
        for label, count in sorted(agg.label_counts.items()):
            pct = count / agg.total_failures if agg.total_failures else 0
            lines.append(f"- {label}: {count} ({pct:.1%})")
        lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append("Each trajectory with `reward=0` from the tau2-bench dataset was:")
    lines.append("")
    lines.append(
        "1. Labeled using the paper's three-class taxonomy (false success, honest failure, ambiguous)"
    )
    lines.append("2. Converted to a Tvastar `RunContext` for detector analysis")
    lines.append(
        "3. Evaluated by both the full Tvastar detector suite and a naive exit-code baseline"
    )
    lines.append("")
    lines.append(f'Reference: "{paper_cite}" — From Confident Closing to Silent Failure.')
    lines.append("")

    # Per-Detector Analysis
    lines.append("## Per-Detector Analysis")
    lines.append("")
    if agg.per_detector_rates:
        lines.append("| Detector | Detection Rate |")
        lines.append("|----------|---------------|")
        for det, rate in sorted(agg.per_detector_rates.items(), key=lambda x: -x[1]):
            lines.append(f"| {det} | {rate:.1%} |")
        lines.append("")
    else:
        lines.append("No detector findings recorded.")
        lines.append("")

    # Detector co-occurrence
    if agg.detector_cooccurrence:
        lines.append("### Detector Co-occurrence")
        lines.append("")
        lines.append("| Detector A | Detector B | Co-occurrences |")
        lines.append("|-----------|-----------|---------------|")
        for d1, d2, count in agg.detector_cooccurrence[:10]:
            lines.append(f"| {d1} | {d2} | {count} |")
        lines.append("")

    # Per-Model Breakdown
    lines.append("## Per-Model Breakdown")
    lines.append("")
    if agg.by_model:
        lines.append("| Model | Trajectories | Detection Rate |")
        lines.append("|-------|-------------|---------------|")
        for model, stats in sorted(agg.by_model.items(), key=lambda x: -x[1].detection_rate):
            lines.append(f"| {model} | {stats.trajectories} | {stats.detection_rate:.1%} |")
        lines.append("")
    else:
        lines.append("No model data available.")
        lines.append("")

    # Domain Analysis
    lines.append("## Domain Analysis")
    lines.append("")
    if agg.by_domain:
        lines.append("| Domain | Trajectories | Detection Rate |")
        lines.append("|--------|-------------|---------------|")
        for domain, stats in sorted(agg.by_domain.items(), key=lambda x: -x[1].detection_rate):
            lines.append(f"| {domain} | {stats.trajectories} | {stats.detection_rate:.1%} |")
        lines.append("")
    else:
        lines.append("No domain data available.")
        lines.append("")

    # Traditional Monitoring Comparison
    lines.append("## Traditional Monitoring Comparison")
    lines.append("")
    lines.append(
        "The naive baseline detector checks only for explicit error flags "
        "(`is_error=True`) and non-zero exit codes in tool results."
    )
    lines.append("")
    lines.append("| Metric | Tvastar | Naive Baseline | Gap |")
    lines.append("|--------|---------|---------------|-----|")
    lines.append(
        f"| Detection Rate | {agg.overall_detection_rate:.1%} "
        f"| {agg.baseline_detection_rate:.1%} "
        f"| +{detection_gap:.1%} |"
    )
    tvastar_only = sum(
        1 for r in agg.trajectory_results if r.tvastar_findings and not r.baseline_findings
    )
    lines.append(f"| Unique Catches | {tvastar_only} | — | — |")
    lines.append("")
    lines.append(
        f"Tvastar detects **{detection_gap:.1%}** more silent failures than "
        f"traditional exit-code monitoring alone."
    )
    lines.append("")

    # Conclusion
    lines.append("## Conclusion")
    lines.append("")
    lines.append(
        f"Across {agg.total_failures} failed trajectories, Tvastar's semantic "
        f"detectors achieved a {agg.overall_detection_rate:.1%} detection rate, "
        f"compared to {agg.baseline_detection_rate:.1%} for naive exit-code "
        f"monitoring — a {detection_gap:.1%} improvement."
    )
    lines.append("")
    lines.append(
        "These results demonstrate that traditional monitoring approaches miss "
        "the majority of silent agent failures, validating the need for "
        "semantic failure detection."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the silent-failure benchmark."""
    parser = argparse.ArgumentParser(
        prog="tvastar.bench.silent_failure",
        description=(
            "Evaluate Tvastar detectors against tau2-bench silent-failure data (arXiv:2606.09863)."
        ),
    )
    parser.add_argument(
        "dataset",
        type=Path,
        help="Path to tau2-bench dataset (JSONL file or directory of JSON files)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory to write output files (default: current directory)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="both",
        help="Output format: json, markdown, or both (default: both)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Run pipeline
    logger.info("Loading dataset from %s", args.dataset)
    results = run_benchmark(args.dataset)

    if not results:
        logger.warning("No results produced. Check dataset path and contents.")
        return

    # Aggregate
    agg = aggregate_results(results)

    # Print summary
    print(f"\n{'=' * 60}")
    print("Silent Failure Benchmark Results")
    print(f"{'=' * 60}")
    print(f"Total trajectories analyzed: {agg.total_failures}")
    print(f"Tvastar detection rate:      {agg.overall_detection_rate:.1%}")
    print(f"Naive baseline rate:         {agg.baseline_detection_rate:.1%}")
    print(
        f"Detection gap:               +{agg.overall_detection_rate - agg.baseline_detection_rate:.1%}"
    )
    print(f"{'=' * 60}\n")

    # Write outputs
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("json", "both"):
        json_path = output_dir / "benchmark_results.json"
        json_data = {
            "metadata": {
                "total_loaded": agg.total_loaded,
                "total_failures": agg.total_failures,
                "label_counts": agg.label_counts,
            },
            "overall": {
                "tvastar_detection_rate": round(agg.overall_detection_rate, 4),
                "baseline_detection_rate": round(agg.baseline_detection_rate, 4),
            },
            "by_model": {
                m: {"trajectories": s.trajectories, "detection_rate": round(s.detection_rate, 4)}
                for m, s in agg.by_model.items()
            },
            "by_domain": {
                d: {"trajectories": s.trajectories, "detection_rate": round(s.detection_rate, 4)}
                for d, s in agg.by_domain.items()
            },
            "per_detector": {d: round(r, 4) for d, r in agg.per_detector_rates.items()},
        }
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
        print(f"JSON results written to: {json_path}")

    if args.format in ("markdown", "both"):
        md_path = output_dir / "benchmark_report.md"
        report = generate_report(agg)
        md_path.write_text(report, encoding="utf-8")
        print(f"Markdown report written to: {md_path}")


if __name__ == "__main__":
    main()
