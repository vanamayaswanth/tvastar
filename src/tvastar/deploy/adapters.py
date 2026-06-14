"""Concrete deploy adapters. See package docstring for the menu."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

from ..agent import AgentSpec
from ..harness import Harness
from ..memory.store import Store


# --------------------------------------------------------------------------
# ASGI (Render / Fly / Railway / Cloudflare Python Workers / any ASGI host)
# --------------------------------------------------------------------------


def asgi_app(spec: AgentSpec, *, store: Optional[Store] = None) -> Any:
    """Return an ASGI app exposing the agent over HTTP + WebSocket.

    Point any ASGI server at it::

        # app.py
        from tvastar.deploy import asgi_app
        from my_agent import agent
        app = asgi_app(agent)

        # then: uvicorn app:app   (or gunicorn -k uvicorn.workers.UvicornWorker)

    Requires ``tvastar[serve]``.
    """
    from ..serving.http import create_app

    return create_app(spec, store=store)


# --------------------------------------------------------------------------
# Generic FaaS: (event) -> result
# --------------------------------------------------------------------------


def serverless_handler(spec: AgentSpec, *, store: Optional[Store] = None):
    """Build a generic ``handler(event) -> dict`` for any function platform.

    ``event`` is a dict with at least ``{"prompt": "..."}`` and optionally
    ``{"session_id": "..."}``. Returns ``{"text", "steps", "stopped"}``.
    """
    harness = Harness(spec, store=store)

    def handler(event: dict) -> dict:
        prompt = event.get("prompt") or event.get("input") or ""
        session_id = event.get("session_id")
        result = asyncio.run(harness.run(prompt, session_id=session_id))
        return {
            "text": result.text,
            "steps": result.steps,
            "stopped": result.stopped,
        }

    return handler


# --------------------------------------------------------------------------
# AWS Lambda / API Gateway
# --------------------------------------------------------------------------


def lambda_handler(spec: AgentSpec, *, store: Optional[Store] = None):
    """Build an AWS Lambda handler ``(event, context) -> response``.

    Accepts either a direct ``{"prompt": ...}`` invocation or an API Gateway
    proxy event (JSON body). Returns an API-Gateway-shaped response.
    """
    inner = serverless_handler(spec, store=store)

    def handler(event: dict, context: Any = None) -> dict:
        # API Gateway proxy integration puts the payload in a JSON string body.
        if "body" in event and isinstance(event.get("body"), str):
            try:
                payload = json.loads(event["body"] or "{}")
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = event
        result = inner(payload)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    return handler


# --------------------------------------------------------------------------
# GitHub Actions / GitLab CI step
# --------------------------------------------------------------------------


def run_github_action(spec: AgentSpec, *, store: Optional[Store] = None) -> int:
    """Run the agent as a CI step.

    Reads the prompt from ``INPUT_PROMPT`` (GitHub Actions ``with: prompt:``) or
    ``$TVASTAR_PROMPT``, runs it, prints the result, and appends a ``result``
    output to ``$GITHUB_OUTPUT`` when present. Returns a process exit code.
    """
    prompt = (os.environ.get("INPUT_PROMPT") or os.environ.get("TVASTAR_PROMPT") or "").strip()
    if not prompt:
        print("::error::No prompt provided (set INPUT_PROMPT or TVASTAR_PROMPT)")
        return 2

    harness = Harness(spec, store=store)
    result = asyncio.run(harness.run(prompt))
    print(result.text)

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        # Multiline-safe output using the heredoc delimiter convention.
        with open(out_path, "a", encoding="utf-8") as f:
            f.write("result<<TVASTAR_EOF\n")
            f.write(result.text + "\n")
            f.write("TVASTAR_EOF\n")
            f.write(f"steps={result.steps}\n")
            f.write(f"stopped={result.stopped}\n")

    return 0 if result.stopped != "error" else 1
