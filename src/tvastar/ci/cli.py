"""CLI for tvastar-ci.

Commands:
    tvastar-ci init    — Generate config file for your repo
    tvastar-ci run     — One-shot: run tests, fix if broken
    tvastar-ci watch   — Start continuous monitoring loop
    tvastar-ci status  — Show last run results
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .config import CIConfig


def main() -> None:
    """Entry point for tvastar-ci CLI."""
    parser = argparse.ArgumentParser(
        prog="tvastar-ci",
        description="Autonomous CI agent \u2014 monitors tests, auto-fixes failures.",
    )
    sub = parser.add_subparsers(dest="command")

    # init
    init_p = sub.add_parser("init", help="Generate config file")
    init_p.add_argument("--test-command", default="pytest -q", help="Test command")
    init_p.add_argument("--branch", default="main", help="Branch to watch")

    # run
    run_p = sub.add_parser("run", help="One-shot: check and fix")
    run_p.add_argument("--test-command", default=None, help="Override test command")
    run_p.add_argument("--model", default="mock", help="Model to use (mock|anthropic|openai)")
    run_p.add_argument("--timeout", type=float, default=300.0, help="Timeout seconds")

    # watch
    watch_p = sub.add_parser("watch", help="Continuous monitoring")
    watch_p.add_argument("--schedule", default="*/15 * * * *", help="Cron schedule")

    # status
    sub.add_parser("status", help="Show last run result")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "init":
        _cmd_init(args)
    elif args.command == "run":
        sys.exit(asyncio.run(_cmd_run(args)))
    elif args.command == "watch":
        asyncio.run(_cmd_watch(args))
    elif args.command == "status":
        _cmd_status()


def _cmd_init(args: Any) -> None:
    """Generate a .tvastar-ci.json config file."""
    config = CIConfig(
        test_command=args.test_command,
        branch=args.branch,
    )
    config.save()
    print("Created .tvastar-ci.json")
    print(f"  test_command: {config.test_command}")
    print(f"  branch: {config.branch}")
    print("\nEdit the file to configure notifications, model, etc.")


async def _cmd_run(args: Any) -> int:
    """Run one CI cycle."""
    config = CIConfig.from_file()
    if args.test_command:
        config.test_command = args.test_command
    config.timeout = args.timeout

    # Resolve model
    model = _resolve_model(args.model)
    if model is None:
        print("Error: could not resolve model. Use --model=mock for testing.", file=sys.stderr)
        return 1

    from .reporter import format_ci_report, notify_result
    from .runner import CIRunner

    runner = CIRunner(config)
    print(f"Running: {config.test_command}")
    result = await runner.run(model=model)

    print(format_ci_report(result))
    notify_result(result, config=config.notifications)

    # Save result for status command
    _save_last_result(result)

    return 0 if result.status in ("green", "fixed") else 1


async def _cmd_watch(args: Any) -> None:
    """Start continuous monitoring loop."""
    config = CIConfig.from_file()
    config.schedule = args.schedule

    model = _resolve_model("mock")  # Default to mock for watch mode demo

    from .runner import CIRunner

    runner = CIRunner(config)
    loop = runner.as_loop(model)

    print(f"Watching {config.repo_path} on schedule: {config.schedule}")
    print("Press Ctrl+C to stop.")

    try:
        await loop.start()
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        await loop.stop()
        print("\nStopped.")


def _cmd_status() -> None:
    """Show last run result."""
    path = Path(".tvastar-ci/last_result.json")
    if not path.exists():
        print("No previous run found. Run `tvastar-ci run` first.")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        from .reporter import format_ci_report
        from .runner import CIRunResult

        result = CIRunResult(
            **{k: v for k, v in data.items() if k in CIRunResult.__dataclass_fields__}
        )
        print(format_ci_report(result))
    except Exception as e:
        print(f"Error reading status: {e}", file=sys.stderr)


def _resolve_model(name: str) -> Any:
    """Resolve a model name to a Model instance."""
    if name == "mock":
        from tvastar.model.mock import MockModel

        return MockModel(script=["All tests pass. SUCCESS."])
    elif name == "anthropic":
        try:
            from tvastar.model.anthropic import AnthropicModel

            return AnthropicModel()
        except ImportError:
            print("Install tvastar[anthropic] for Anthropic model support.", file=sys.stderr)
            return None
    elif name == "openai":
        try:
            from tvastar.model.openai import OpenAIModel

            return OpenAIModel()
        except ImportError:
            print("Install tvastar[openai] for OpenAI model support.", file=sys.stderr)
            return None
    return None


def _save_last_result(result: Any) -> None:
    """Save the last run result for the status command."""
    from dataclasses import asdict

    path = Path(".tvastar-ci")
    path.mkdir(parents=True, exist_ok=True)
    (path / "last_result.json").write_text(
        json.dumps(asdict(result), default=str, indent=2),
        encoding="utf-8",
    )
