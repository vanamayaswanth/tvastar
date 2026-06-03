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

    args = parser.parse_args(argv)

    if args.cmd == "info":
        return _info(args.agent)
    if args.cmd == "serve":
        return _serve(args.agent, args.host, args.port)
    if args.cmd == "run":
        return asyncio.run(_run(args.agent, args.prompt, args.trace))
    if args.cmd == "chat":
        return asyncio.run(_chat(args.agent, args.trace))
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
