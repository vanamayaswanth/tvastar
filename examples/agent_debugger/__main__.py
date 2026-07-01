"""Main entry point for the Agent Debugger pipeline.

Run with:
    python -m agent_debugger          (from the examples/ directory)
    python -m examples.agent_debugger (from the repo root)

Wires CLI arguments, config parsing, model selection, governance, and workflow
invocation. Prints the final DebuggingReport as Markdown to stdout.

Requirements: 5.2, 12.1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .schemas import DebuggerConfig, DebuggingReport


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser matching DebuggerConfig fields."""
    default_trajectory = str(Path(__file__).parent / "data" / "sample_trajectory.jsonl")

    parser = argparse.ArgumentParser(
        prog="agent_debugger",
        description="Agent Debugger — diagnose, fix, and verify failing agent trajectories.",
    )
    parser.add_argument(
        "trajectory",
        nargs="?",
        default=default_trajectory,
        help="Path to the trajectory JSONL file (default: built-in sample)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=2.0,
        help="Maximum budget in USD (default: 2.0)",
    )
    parser.add_argument(
        "--hitl",
        action="store_true",
        default=False,
        help="Enable human-in-the-loop approval for fix proposals",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum fix-verify retry attempts (default: 3)",
    )
    parser.add_argument(
        "--mcp-server",
        type=str,
        default=None,
        help="MCP server URL for external code analysis tools",
    )
    parser.add_argument(
        "--real-model",
        action="store_true",
        default=False,
        help="Use a real model provider instead of MockModel",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging output",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> DebuggerConfig:
    """Build a DebuggerConfig from parsed CLI arguments."""
    return DebuggerConfig(
        trajectory_path=args.trajectory,
        budget_usd=args.budget,
        hitl=args.hitl,
        max_retries=args.max_retries,
        mcp_server_url=args.mcp_server,
        use_real_model=args.real_model,
    )


async def _run_pipeline(config: DebuggerConfig) -> dict:
    """Invoke the agent_debugger workflow with the given config."""
    from tvastar.workflow import run_workflow

    from .workflow import agent_debugger

    result = await run_workflow(
        agent_debugger,
        payload=config.to_dict(),
    )
    return result


def main() -> None:
    """Entry point: parse args, run the pipeline, print the report."""
    parser = _build_parser()
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    config = _config_from_args(args)

    try:
        result = asyncio.run(_run_pipeline(config))
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Handle stopped/error results
    if isinstance(result, dict) and "stopped" in result:
        stopped_reason = result["stopped"]
        phase = result.get("stopped_at_phase", result.get("phase", "unknown"))
        print(
            f"⚠️  Pipeline stopped: {stopped_reason} (during {phase} phase)",
            file=sys.stderr,
        )
        # Still try to produce a partial report if possible
        if "status" in result:
            try:
                report = DebuggingReport.from_dict(result)
                print(report.to_markdown())
                return
            except (KeyError, TypeError, ValueError):
                pass
        # Fall through to print raw error info
        if "error" in result:
            print(f"Error details: {result['error']}", file=sys.stderr)
        sys.exit(1)

    # Deserialize and print the full report
    if isinstance(result, dict) and "status" in result:
        try:
            report = DebuggingReport.from_dict(result)
            print(report.to_markdown())
        except (KeyError, TypeError, ValueError) as exc:
            print(f"Error deserializing report: {exc}", file=sys.stderr)
            print(f"Raw result: {result}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unexpected result format: {result}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
