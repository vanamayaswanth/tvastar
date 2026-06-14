"""Tvastar command-line interface.

    tvastar chat   path/to/agent.py:agent      interactive REPL (core only)
    tvastar serve  path/to/agent.py:agent      HTTP/WebSocket server (needs serve)
    tvastar run    path/to/agent.py:agent "…"  one-shot prompt
    tvastar info   path/to/agent.py:agent      show agent config

Uses argparse (stdlib) so the CLI itself adds no dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..harness import Harness
from ..observability import ConsoleExporter, Tracer
from .loader import load_agent


def _force_utf8_stdout() -> None:
    """Avoid UnicodeEncodeError on legacy consoles (e.g. Windows cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(prog="tvastar", description="Tvastar agent harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("chat", "serve", "run", "info"):
        p = sub.add_parser(name)
        p.add_argument("agent", help="agent reference, e.g. file.py:agent")
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
        if name == "run":
            p.add_argument("prompt", help="the prompt to send")
        if name in ("chat", "run"):
            p.add_argument("--trace", action="store_true", help="print trace spans")

    ui_p = sub.add_parser("ui", help="open the local trace viewer UI")
    ui_p.add_argument(
        "--trace",
        default="tvastar-trace.jsonl",
        help="JSONL trace file to read (default: tvastar-trace.jsonl)",
    )
    ui_p.add_argument("--port", type=int, default=7878, help="port (default: 7878)")
    ui_p.add_argument("--no-open", action="store_true", help="don't auto-open browser")

    logs_p = sub.add_parser("logs", help="inspect a workflow run by ID")
    logs_p.add_argument("run_id", help="workflow run ID (e.g. run_abc123)")
    logs_p.add_argument(
        "--registry", default=".tvastar-runs", help="path to run registry directory"
    )

    bench_p = sub.add_parser("bench", help="run standardised benchmarks (SWE-bench etc.)")
    bench_p.add_argument("agent", help="agent reference, e.g. file.py:agent")
    bench_p.add_argument(
        "--suite",
        default="swe-lite",
        help="benchmark suite: 'swe-lite' (HuggingFace, default) or path to a .jsonl file",
    )
    bench_p.add_argument("--max-tasks", type=int, default=None, help="limit number of tasks")
    bench_p.add_argument("--concurrency", type=int, default=2, help="parallel tasks (default 2)")
    bench_p.add_argument("--out", default=None, help="write JSON report to this file")

    args = parser.parse_args(argv)

    if args.cmd == "ui":
        from tvastar.ui import run_ui

        run_ui(args.trace, port=args.port, auto_open=not args.no_open)
        return 0
    if args.cmd == "info":
        return _info(args.agent)
    if args.cmd == "serve":
        return _serve(args.agent, args.host, args.port)
    if args.cmd == "run":
        return asyncio.run(_run(args.agent, args.prompt, args.trace))
    if args.cmd == "chat":
        return asyncio.run(_chat(args.agent, args.trace))
    if args.cmd == "logs":
        from ..workflow import cli_logs

        return cli_logs(args.run_id, args.registry)
    if args.cmd == "bench":
        return asyncio.run(
            _bench(args.agent, args.suite, args.max_tasks, args.concurrency, args.out)
        )
    return 1


def _tracer(enabled: bool) -> Tracer:
    return Tracer([ConsoleExporter()]) if enabled else Tracer()


def _info(ref: str) -> int:
    spec = load_agent(ref)
    print(f"Agent:  {spec.name}")
    print(f"Model:  {spec.model.name}")
    print(f"Tools:  {', '.join(spec.tools.names()) or '(none)'}")
    print(f"Skills: {', '.join(spec.skills.names()) or '(none)'}")
    print(f"Max steps: {spec.max_steps}")
    return 0


def _serve(ref: str, host: str, port: int) -> int:
    from .http import serve

    spec = load_agent(ref)
    print(f"Serving '{spec.name}' on http://{host}:{port}  (Ctrl-C to stop)")
    serve(spec, host=host, port=port)
    return 0


async def _run(ref: str, prompt: str, trace: bool) -> int:
    spec = load_agent(ref)
    harness = Harness(spec, tracer=_tracer(trace))
    result = await harness.run(prompt)
    print(result.text)
    return 0


async def _chat(ref: str, trace: bool) -> int:
    spec = load_agent(ref)
    harness = Harness(spec, tracer=_tracer(trace))
    session = harness.session()
    await session.start()
    print(f"Tvastar · {spec.name} ({spec.model.name}). Type 'exit' to quit.\n")
    try:
        while True:
            try:
                line = input("you › ").strip()
            except EOFError:
                break
            if line.lower() in ("exit", "quit", ":q"):
                break
            if not line:
                continue
            print("agent › ", end="", flush=True)
            async for ev in session.stream(line):
                if ev.type == "text_delta":
                    print(ev.data["text"], end="", flush=True)
                elif ev.type == "tool_call":
                    print(f"\n  ⚙ {ev.data['name']}({ev.data['input']})", flush=True)
                elif ev.type == "tool_result":
                    snippet = str(ev.data["content"])[:200]
                    print(f"  ↳ {snippet}", flush=True)
            print()
    finally:
        await session.close()
    return 0


async def _bench(
    ref: str,
    suite_name: str,
    max_tasks: int | None,
    concurrency: int,
    out: str | None,
) -> int:
    import json as _json

    from ..bench import BenchSuite, swe_bench_tasks

    spec = load_agent(ref)
    suite = BenchSuite(spec, concurrency=concurrency)
    suite.name = suite_name

    print(f"Loading benchmark tasks ({suite_name!r}) …")
    if suite_name.endswith(".jsonl"):
        tasks = swe_bench_tasks(source="jsonl", path=suite_name, max_tasks=max_tasks)
    else:
        tasks = swe_bench_tasks(source="hf", split="lite", max_tasks=max_tasks)

    suite.add_many(tasks)
    print(f"Running {len(tasks)} task(s) with concurrency={concurrency} …\n")

    report = await suite.run()
    report.print()

    if out:
        Path(out).write_text(_json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"Report written to {out}")

    return 0 if report.score > 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
