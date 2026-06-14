"""`tvastar-fix` — auto-fix a failing test suite with a Tvastar agent.

    tvastar-fix                          # fix ./ using `pytest -q`
    tvastar-fix --test-cmd "pytest tests/ -q"
    tvastar-fix --path packages/api --check     # CI gate: exit 1 if unfixed

Model is picked automatically (Groq free tier / local Ollama / OpenAI /
Anthropic) — see `--help`. Success is decided by re-running the tests, not by
trusting the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from ..errors import ModelError
from .fixer import FixResult, fix_tests
from .models import resolve_model


def _force_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tvastar-fix",
        description="Auto-fix a failing test suite with a Tvastar agent "
        "(verified by re-running the tests).",
    )
    p.add_argument("--path", default=".", help="project directory (default: .)")
    p.add_argument("--test-cmd", default="pytest -q", help="test command (default: 'pytest -q')")
    p.add_argument("--model", default=None, help="model name (provider-specific)")
    p.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint URL")
    p.add_argument("--api-key", default=None, help="API key for --base-url")
    p.add_argument("--max-steps", type=int, default=15, help="max agent steps")
    p.add_argument("--timeout", type=float, default=180.0, help="per-test-run timeout (s)")
    p.add_argument("--no-network", action="store_true", help="block network in the sandbox")
    p.add_argument(
        "--max-cpu",
        type=float,
        default=None,
        metavar="SECS",
        help="max CPU seconds per sandbox command (default: --timeout value)",
    )
    p.add_argument(
        "--max-memory",
        type=int,
        default=None,
        metavar="MB",
        help="max memory per sandbox command in MB (Linux/macOS only)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the suite still fails (for CI gating)",
    )
    p.add_argument("--quiet", action="store_true", help="less output")
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)

    try:
        model = resolve_model(model=args.model, base_url=args.base_url, api_key=args.api_key)
    except ModelError as e:
        print(f"tvastar-fix: {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"tvastar-fix · model={model.name} · cmd={args.test_cmd!r}\n")

    try:
        result = asyncio.run(
            fix_tests(
                args.path,
                model=model,
                test_command=args.test_cmd,
                max_steps=args.max_steps,
                timeout=args.timeout,
                network=not args.no_network,
                max_cpu_seconds=args.max_cpu,
                max_memory_mb=args.max_memory,
            )
        )
    except ModelError as e:
        # e.g. bad/expired API key, rate limit, model not found.
        print(f"tvastar-fix: model error — {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("tvastar-fix: interrupted", file=sys.stderr)
        return 130

    _report(result, quiet=args.quiet)
    _write_github_output(result)

    if result.already_green:
        return 0
    if result.fixed:
        return 0
    return 1 if args.check else 0


def _report(r: FixResult, *, quiet: bool) -> None:
    icon = {"already-green": "✓", "fixed": "✓", "unfixed": "✗"}[r.status]
    headline = {
        "already-green": "Tests already pass — nothing to fix.",
        "fixed": "Fixed! The test suite passes now.",
        "unfixed": "Could not make the tests pass.",
    }[r.status]
    print(f"{icon} {headline}")
    if r.already_green:
        return

    print(f"  attempts: {r.attempts}   model: {r.model}")
    if r.changed_files:
        print(f"  changed files: {', '.join(r.changed_files)}")
    if r.findings and not quiet:
        for f in r.findings:
            print(f"  ! {f}")
    if r.summary and not quiet:
        print(f"\n  agent: {r.summary[:400]}")
    if not r.fixed and not quiet:
        print("\n  last test output:")
        tail = "\n".join(r.after_output.splitlines()[-15:])
        print("    " + tail.replace("\n", "\n    "))


def _write_github_output(r: FixResult) -> None:
    """Expose results to a GitHub Actions workflow when running in CI."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"status={r.status}\n")
            f.write(f"fixed={'true' if r.fixed else 'false'}\n")
            f.write(f"changed_files={','.join(r.changed_files)}\n")
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
