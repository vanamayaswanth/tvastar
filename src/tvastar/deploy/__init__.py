"""Multi-platform deploy adapters — run the same agent anywhere.

Tvastar agents are plain Python, so "deploy" is mostly about giving each platform
the entrypoint shape it expects. These adapters are thin, dependency-free
(except the ASGI server, which reuses ``tvastar[serve]``) wrappers around a single
:func:`tvastar.harness.Harness`:

* :func:`asgi_app`        — an ASGI/HTTP+WebSocket app (Render, Fly, Railway,
                            Cloudflare Python Workers, any ASGI host, "Node-equiv").
* :func:`lambda_handler`  — an AWS Lambda / API Gateway handler.
* :func:`run_github_action` — a GitHub Actions / GitLab CI step entrypoint that
                            reads inputs from env and writes step outputs.
* :func:`serverless_handler` — a generic ``(event) -> result`` function for any
                            FaaS (GCP Functions, Azure Functions, Vercel, ...).

Every adapter takes an ``AgentSpec`` so the agent definition is written once and
reused across all targets.
"""

from .adapters import (
    asgi_app,
    lambda_handler,
    run_github_action,
    serverless_handler,
)

__all__ = [
    "asgi_app",
    "lambda_handler",
    "run_github_action",
    "serverless_handler",
]
