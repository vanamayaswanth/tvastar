"""Webhook trigger endpoint for loops — validates signatures and triggers named loops.

POST /webhooks/{loop_name}
  → validate signature (GitHub HMAC-SHA256 or Slack signing secret)
  → lookup loop in LoopRegistry
  → trigger with request body as context
  → 202 with run_id

Error responses:
  400 — invalid JSON or body > 1 MB
  401 — signature validation failed
  404 — loop not found in registry
  409 — loop is SUSPENDED
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..loop.registry import LoopRegistry


@dataclass
class WebhookSecret:
    """Per-loop webhook signature verification config."""

    github_secret: Optional[str] = None
    slack_secret: Optional[str] = None


_MAX_BODY = 1_048_576  # 1 MB


def create_loop_webhook_router(
    registry: "LoopRegistry",
    secrets: "Optional[dict[str, WebhookSecret]]" = None,
):
    """Create a FastAPI router for loop webhook triggers.

    Args:
        registry: The LoopRegistry to look up loops in.
        secrets: Optional mapping of {loop_name: WebhookSecret} for signature validation.
    """
    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError:
        raise ImportError(
            "Webhook trigger requires the 'tvastar[serve]' extra. "
            "Install it with: pip install tvastar[serve]"
        ) from None

    router = APIRouter(tags=["loop-webhooks"])
    secrets = secrets or {}

    @router.post("/webhooks/{loop_name}")
    async def trigger_loop(loop_name: str, request: Request):
        # 1. Check body size (reject > 1 MB)
        body = await request.body()
        if len(body) > _MAX_BODY:
            return JSONResponse({"error": "Payload exceeds 1 MB"}, status_code=400)

        # 2. Parse JSON
        import json

        try:
            payload = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

        # 3. Validate signature (if header present)
        loop_secrets = secrets.get(loop_name)

        # GitHub HMAC-SHA256
        github_sig = request.headers.get("x-hub-signature-256")
        if github_sig:
            if not loop_secrets or not loop_secrets.github_secret:
                return JSONResponse({"error": "Signature validation failed"}, status_code=401)
            expected = (
                "sha256="
                + hmac.new(
                    loop_secrets.github_secret.encode(),
                    body,
                    hashlib.sha256,
                ).hexdigest()
            )
            if not hmac.compare_digest(github_sig, expected):
                return JSONResponse({"error": "Invalid signature"}, status_code=401)

        # Slack signature
        slack_sig = request.headers.get("x-slack-signature")
        if slack_sig:
            slack_ts = request.headers.get("x-slack-request-timestamp", "")
            if not loop_secrets or not loop_secrets.slack_secret:
                return JSONResponse({"error": "Signature validation failed"}, status_code=401)
            # Check timestamp drift (>300s → reject)
            try:
                ts = int(slack_ts)
                if abs(time.time() - ts) > 300:
                    return JSONResponse({"error": "Request timestamp too old"}, status_code=401)
            except (ValueError, TypeError):
                return JSONResponse({"error": "Invalid request timestamp"}, status_code=401)
            # Validate signature
            sig_basestring = f"v0:{slack_ts}:{body.decode()}"
            expected = (
                "v0="
                + hmac.new(
                    loop_secrets.slack_secret.encode(),
                    sig_basestring.encode(),
                    hashlib.sha256,
                ).hexdigest()
            )
            if not hmac.compare_digest(slack_sig, expected):
                return JSONResponse({"error": "Invalid signature"}, status_code=401)

        # 4. Look up the loop
        loop = registry.get(loop_name)
        if loop is None:
            return JSONResponse({"error": f"Loop '{loop_name}' not found"}, status_code=404)

        # 5. Check if suspended
        from ..loop import LoopState

        if loop.state == LoopState.SUSPENDED:
            return JSONResponse({"error": f"Loop '{loop_name}' is suspended"}, status_code=409)

        # 6. Trigger
        try:
            run = await loop.trigger(context={"webhook": payload})
            return JSONResponse({"run_id": run.run_id}, status_code=202)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    return router
