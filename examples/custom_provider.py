"""Using other model providers — e.g. Cloudflare Workers AI.

Tvastar's `Model` interface is the single extension point. There are two ways to
plug in a provider that isn't built in:

  ROUTE A — OpenAI-compatible (easiest).
    Most providers (Cloudflare Workers AI, Groq, Together, Fireworks, OpenRouter,
    Ollama, vLLM, ...) expose an OpenAI-compatible endpoint. Just point the
    built-in `OpenAIModel` at their `base_url`. Tool calling works if the model
    supports it. Needs `tvastar[openai]`.

  ROUTE B — a custom `Model` subclass (works for ANY HTTP API).
    Subclass `Model`, implement `generate()`. Below is a zero-dependency native
    Cloudflare Workers AI adapter using only stdlib (urllib).

Run the offline self-check (no creds needed):
    uv run python examples/custom_provider.py
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Optional

from tvastar import Harness, Message, ModelResponse, create_agent, default_toolset
from tvastar.model import Model
from tvastar.types import StopReason, TextBlock, Usage


# ---------------------------------------------------------------------------
# ROUTE A — Cloudflare via the OpenAI-compatible endpoint (one-liner)
# ---------------------------------------------------------------------------


def cloudflare_openai_compatible():
    """Cloudflare exposes /ai/v1 which speaks the OpenAI API. So we reuse the
    built-in OpenAIModel — no new code, and tool calling works on models that
    support it."""
    from tvastar.model import OpenAIModel

    account = os.environ["CF_ACCOUNT_ID"]
    return OpenAIModel(
        model="@cf/meta/llama-3.1-8b-instruct",
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
        api_key=os.environ["CF_API_TOKEN"],
    )


# ---------------------------------------------------------------------------
# ROUTE B — a from-scratch native adapter (zero dependencies)
# ---------------------------------------------------------------------------


class CloudflareWorkersAI(Model):
    """Native Cloudflare Workers AI adapter via the /ai/run REST endpoint.

    Text generation only (no tool calling) — for tools use Route A. Shows the
    whole contract: convert messages in, call the API, return a ModelResponse.
    """

    def __init__(
        self,
        model: str = "@cf/meta/llama-3.1-8b-instruct",
        *,
        account_id: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        self.name = model
        self._model = model
        self._account = account_id or os.environ.get("CF_ACCOUNT_ID", "")
        self._token = api_token or os.environ.get("CF_API_TOKEN", "")

    @property
    def _url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self._account}/ai/run/{self._model}"

    def _to_cf_messages(self, messages: list[Message], system: Optional[str]):
        out = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role = "assistant" if m.role == "assistant" else "user"
            out.append({"role": role, "content": m.text})
        return out

    @staticmethod
    def _parse(payload: dict) -> ModelResponse:
        """Turn a Cloudflare /ai/run response into a Tvastar ModelResponse."""
        result = payload.get("result", {})
        text = result.get("response", "") if isinstance(result, dict) else str(result)
        usage = (result.get("usage") or {}) if isinstance(result, dict) else {}
        return ModelResponse(
            message=Message("assistant", [TextBlock(text=text)]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
            raw=payload,
        )

    async def generate(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools=None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences=None,
    ) -> ModelResponse:
        body = json.dumps(
            {
                "messages": self._to_cf_messages(messages, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        ).encode("utf-8")
        # Run the blocking HTTP call off the event loop.
        payload = await asyncio.to_thread(self._post, body)
        return self._parse(payload)

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Offline self-check: proves the adapter's contract without any network/creds.
# ---------------------------------------------------------------------------


async def _offline_selfcheck() -> None:
    sample = {
        "result": {
            "response": "Hello from Cloudflare Workers AI!",
            "usage": {"prompt_tokens": 7, "completion_tokens": 6},
        }
    }
    resp = CloudflareWorkersAI._parse(sample)
    assert resp.message.text == "Hello from Cloudflare Workers AI!"
    assert resp.usage.output_tokens == 6
    print("Route B adapter parses a real CF response shape correctly:")
    print(f"  text  = {resp.message.text!r}")
    print(f"  usage = {resp.usage}")
    print("\nTo use it for real, set CF_ACCOUNT_ID and CF_API_TOKEN, then:")
    print("  agent = create_agent('a', model=CloudflareWorkersAI(), tools=default_toolset())")


async def main() -> None:
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        # Live run against Cloudflare (Route B).
        agent = create_agent(
            "cf-agent",
            model=CloudflareWorkersAI(),
            instructions="You are a concise assistant.",
            tools=default_toolset(),
        )
        result = await Harness(agent).run("Say hello in one short sentence.")
        print(result.text)
    else:
        await _offline_selfcheck()


if __name__ == "__main__":
    asyncio.run(main())
