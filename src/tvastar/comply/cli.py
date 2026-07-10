"""CLI entry point for `tvastar comply` command namespace.

Subcommands:
  audit           One-shot compliance check
  report          Generate regulator-ready report for a run_id
  watch           Start continuous monitoring daemon
  dashboard       Query fleet compliance status
  compliance-cost Report compliance overhead metrics

Global flags:
  --format json|text   Output format (default: text)
  --config PATH        Config file (YAML/JSON) for loops, frameworks, sinks

Exit codes:
  0 — success
  1 — operational error (invalid args, missing loop, I/O)
  2 — compliance violation (NON_COMPLIANT audit result)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

__all__ = ["main"]


def _load_config(path: str) -> dict[str, Any]:
    """Load YAML or JSON config file. Returns parsed dict.

    Supports .json natively. YAML requires PyYAML (optional).
    Raises SystemExit(1) on failure.
    """
    p = Path(path)
    if not p.exists():
        sys.stderr.write(f"Error: config file not found: {path}\n")
        raise SystemExit(1)

    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()

    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"Error: invalid JSON in {path}: {exc}\n")
            raise SystemExit(1)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            sys.stderr.write(
                "Error: PyYAML is required for YAML config files. "
                "Install with: pip install pyyaml\n"
            )
            raise SystemExit(1)
        try:
            return yaml.safe_load(text) or {}
        except Exception as exc:
            sys.stderr.write(f"Error: invalid YAML in {path}: {exc}\n")
            raise SystemExit(1)
    else:
        # Try JSON first, then YAML
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore[import-untyped]

                return yaml.safe_load(text) or {}
            except ImportError:
                sys.stderr.write(
                    f"Error: cannot parse {path} (unknown format, PyYAML not installed)\n"
                )
                raise SystemExit(1)
            except Exception as exc:
                sys.stderr.write(f"Error: cannot parse {path}: {exc}\n")
                raise SystemExit(1)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for `tvastar comply`."""
    parser = argparse.ArgumentParser(
        prog="tvastar comply",
        description="Continuous compliance operations for Tvastar AI agents.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        dest="output_format",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Config file (YAML/JSON) for loops, frameworks, sinks",
    )

    subparsers = parser.add_subparsers(dest="command")

    # audit
    audit_parser = subparsers.add_parser("audit", help="One-shot compliance check")
    audit_parser.add_argument("loop", help="Loop name/identifier to audit")
    audit_parser.add_argument(
        "--framework", default=None, help="Regulatory framework (default: EU_AI_Act)"
    )

    # report
    report_parser = subparsers.add_parser(
        "report", help="Generate regulator-ready report for a run_id"
    )
    report_parser.add_argument("run_id", help="Run identifier to generate report for")
    report_parser.add_argument(
        "--output", "-o", default=None, help="Output file path (default: stdout)"
    )
    report_parser.add_argument(
        "--fmt",
        choices=["text", "html", "json"],
        default=None,
        help="Report format (default: uses global --format)",
    )

    # watch
    subparsers.add_parser("watch", help="Start continuous monitoring daemon")

    # dashboard
    subparsers.add_parser("dashboard", help="Query fleet compliance status")

    # compliance-cost
    cost_parser = subparsers.add_parser(
        "compliance-cost", help="Report compliance overhead metrics"
    )
    cost_parser.add_argument(
        "--window-hours",
        type=float,
        default=24.0,
        help="Time window in hours (default: 24)",
    )

    return parser


# ------------------------------------------------------------------ subcommands


def _cmd_audit(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Run one-shot audit for the specified loop."""
    from .audit import audit_compliance

    # Resolve loop from config
    loop_name = args.loop
    loops_config = config.get("loops", [])

    # Find loop config by name
    loop_cfg = None
    for lc in loops_config:
        if lc.get("name") == loop_name:
            loop_cfg = lc
            break

    if loop_cfg is None and not loops_config:
        # No config — try loading loop directly (requires tvastar.loop)
        try:
            from tvastar.loop import Loop  # noqa: F401
        except ImportError:
            pass
        sys.stderr.write(
            f"Error: loop '{loop_name}' not found in config. "
            f"Provide a --config file with loop definitions.\n"
        )
        return 1

    if loop_cfg is None:
        sys.stderr.write(
            f"Error: loop '{loop_name}' not found in config. "
            f"Available loops: {[lc.get('name') for lc in loops_config]}\n"
        )
        return 1

    # ponytail: for CLI purposes, we construct a minimal audit via config
    # The full Loop resolution happens in task 12.2 (config loader).
    # For now, attempt to load the loop from config trust_log path.
    try:
        from ..assurance.log import TrustLog  # noqa: F401
    except ImportError:
        pass  # TrustLog not available

    trust_log_path = loop_cfg.get("trust_log")  # noqa: F841 — used in task 12.2
    framework = (
        args.framework or loop_cfg.get("frameworks", [None])[0]
        if loop_cfg.get("frameworks")
        else args.framework
    )

    # Attempt to create a Loop object from config
    loop_obj: Any = None
    try:
        from tvastar.loop import Loop  # noqa: F401 — used in task 12.2

        # ponytail: Loop construction from config is task 12.2 scope.
        # Here we try basic construction if possible.
        loop_obj = None
    except ImportError:
        pass

    if loop_obj is None:
        # Cannot construct Loop — run audit with config info for error messaging
        sys.stderr.write(
            f"Error: cannot construct Loop '{loop_name}' from config. "
            f"Ensure the loop is properly configured.\n"
        )
        return 1

    result = audit_compliance(loop_obj, framework=framework)

    # Output
    if args.output_format == "json":
        output = json.dumps(asdict(result), default=_json_fallback)
        sys.stdout.write(output + "\n")
    else:
        _print_audit_text(result)

    return 0 if result.status == "COMPLIANT" else 2


def _cmd_report(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Generate a formatted report for a run_id."""
    from .report import ReportGenerator

    # Determine which trust log to use
    trust_log_path = None
    loops_config = config.get("loops", [])
    if loops_config:
        # Use the first loop's trust log as default
        trust_log_path = loops_config[0].get("trust_log")

    if trust_log_path is None:
        trust_log_path = ".tvastar-trust.jsonl"

    try:
        from ..assurance.log import TrustLog

        log = TrustLog(trust_log_path)
    except ImportError:
        sys.stderr.write("Error: cannot import TrustLog from tvastar.assurance\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Error: cannot open TrustLog at '{trust_log_path}': {exc}\n")
        return 1

    # Determine report format: --fmt flag overrides global --format
    fmt = args.fmt if args.fmt else ("json" if args.output_format == "json" else "text")

    gen = ReportGenerator(log)
    try:
        report_text = gen.generate(args.run_id, fmt=fmt, output=args.output)
    except KeyError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Error generating report: {exc}\n")
        return 1

    # If no --output file was specified, write to stdout
    if args.output is None:
        sys.stdout.write(report_text + "\n")

    return 0


def _cmd_watch(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Start the WatchDaemon for continuous monitoring."""
    from .watch import WatchDaemon

    loops_config = config.get("loops", [])
    if not loops_config:
        sys.stderr.write(
            "Error: no loops registered for monitoring. Add loops to your config file.\n"
        )
        return 1

    # ponytail: Loop construction from config is task 12.2 scope.
    # For now, validate config has loops and attempt to start daemon.
    # Full loop resolution will come from the config loader.
    loop_objects: list[Any] = []

    try:
        from tvastar.loop import Loop  # noqa: F401
    except ImportError:
        pass

    # Attempt to construct loops — if we can't, report error
    if not loop_objects:
        sys.stderr.write(
            f"Error: cannot construct Loop objects from config. "
            f"Configured loops: {[lc.get('name') for lc in loops_config]}\n"
        )
        return 1

    thresholds = config.get("thresholds", {})
    interval = thresholds.get("check_interval_seconds", 60.0)

    try:
        daemon = WatchDaemon(loop_objects, interval=interval)
        asyncio.run(daemon.start())
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except KeyboardInterrupt:
        pass

    return 0


def _cmd_dashboard(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Query fleet compliance status and output FleetSummary."""
    from .dashboard import ComplianceDashboard

    dashboard = ComplianceDashboard()

    # ponytail: without active loop monitoring, dashboard returns empty state.
    # Full integration with config-loaded loops is task 12.2 scope.
    summary = dashboard.query()

    if args.output_format == "json":
        output = json.dumps(asdict(summary), default=_json_fallback)
        sys.stdout.write(output + "\n")
    else:
        _print_dashboard_text(summary)

    return 0


def _cmd_compliance_cost(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Report aggregate compliance overhead for a time window."""
    from .cost import CostTracker

    tracker = CostTracker()

    # ponytail: without persistent token records, returns empty reports.
    # Full integration with running system is task 12.2 scope.
    reports = tracker.report(window_hours=args.window_hours)

    if args.output_format == "json":
        output = json.dumps([asdict(r) for r in reports], default=_json_fallback)
        sys.stdout.write(output + "\n")
    else:
        _print_cost_text(reports, args.window_hours)

    return 0


# ------------------------------------------------------------------ formatters


def _print_audit_text(result: Any) -> None:
    """Print AuditResult in human-readable text format."""
    sys.stdout.write(f"Loop: {result.loop_name}\n")
    sys.stdout.write(f"Status: {result.status}\n")
    sys.stdout.write(f"Framework: {result.framework}\n")
    sys.stdout.write(f"Checks: {len(result.checks)}\n")
    for check in result.checks:
        passed = getattr(check, "passed", "?")
        article = getattr(check, "article", "?")
        feature = getattr(check, "feature", "?")
        sym = "✓" if passed else "✗"
        sys.stdout.write(f"  {sym} {article} — {feature}\n")
    if result.remediation:
        sys.stdout.write("Remediation:\n")
        for r in result.remediation:
            sys.stdout.write(f"  - {r}\n")


def _print_dashboard_text(summary: Any) -> None:
    """Print FleetSummary in human-readable text format."""
    sys.stdout.write("Fleet Compliance Summary\n")
    sys.stdout.write(f"  Total:          {summary.total}\n")
    sys.stdout.write(f"  Compliant:      {summary.compliant}\n")
    sys.stdout.write(f"  Non-compliant:  {summary.non_compliant}\n")
    sys.stdout.write(f"  Stale:          {summary.stale}\n")
    sys.stdout.write(f"  Compliance %:   {summary.fleet_compliance_pct:.1f}%\n")
    if summary.per_loop:
        sys.stdout.write("  Loops:\n")
        for loop in summary.per_loop:
            sys.stdout.write(
                f"    {loop.loop_name}: {loop.status} (consecutive: {loop.consecutive_compliant})\n"
            )


def _print_cost_text(reports: list[Any], window_hours: float) -> None:
    """Print ComplianceCostReport list in human-readable text format."""
    sys.stdout.write(f"Compliance Cost Report (window: {window_hours}h)\n")
    if not reports:
        sys.stdout.write("  No cost data recorded.\n")
        return
    for r in reports:
        sys.stdout.write(
            f"  {r.loop_name}: "
            f"{r.compliance_tokens}/{r.total_tokens} tokens "
            f"(overhead: {r.overhead_ratio:.2%})\n"
        )


def _json_fallback(obj: Any) -> Any:
    """JSON serializer fallback for dataclass fields."""
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ------------------------------------------------------------------ entry point


def main(argv: list[str] | None = None) -> int:
    """Entry point for `tvastar comply` command namespace.

    Returns exit code: 0 success, 1 operational error, 2 compliance violation.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 1

    # Load config if provided
    config: dict[str, Any] = {}
    if args.config:
        config = _load_config(args.config)

    # Dispatch to subcommand
    handlers = {
        "audit": _cmd_audit,
        "report": _cmd_report,
        "watch": _cmd_watch,
        "dashboard": _cmd_dashboard,
        "compliance-cost": _cmd_compliance_cost,
    }

    handler = handlers.get(args.command)
    if handler is None:
        sys.stderr.write(f"Error: unknown command '{args.command}'\n")
        return 1

    try:
        return handler(args, config)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
