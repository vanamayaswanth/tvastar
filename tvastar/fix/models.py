"""Free-model-friendly model resolution for `tvastar-fix`.

Picks a model from explicit flags or the environment, preferring options that
cost nothing to try (local Ollama) or have a generous free tier (Groq) so the
test-fixer works out of the box without a paid key.

Resolution order:
  1. Explicit --model/--base-url/--api-key (any OpenAI-compatible endpoint).
  2. TVASTAR_FIX_MODEL (+ TVASTAR_FIX_BASE_URL / TVASTAR_FIX_API_KEY).
  3. GROQ_API_KEY        -> Groq (free tier, OpenAI-compatible).
  4. OPENAI_API_KEY      -> OpenAI.
  5. ANTHROPIC_API_KEY   -> Claude.
  6. A reachable Ollama  -> local, fully free.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Optional

from ..errors import ModelError
from ..model.base import Model

GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_DEFAULT = "llama-3.3-70b-versatile"
OLLAMA_DEFAULT = "llama3.2"


def resolve_model(
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Model:
    """Return a ready Model based on flags/env, or raise a helpful ModelError."""
    from ..model import OpenAIModel

    # 1. Fully explicit OpenAI-compatible endpoint.
    if model and base_url:
        return OpenAIModel(model=model, base_url=base_url, api_key=api_key or "x")

    # 2. Generic env override.
    env_model = os.environ.get("TVASTAR_FIX_MODEL")
    env_base = os.environ.get("TVASTAR_FIX_BASE_URL")
    env_key = os.environ.get("TVASTAR_FIX_API_KEY")
    if env_model and env_base:
        return OpenAIModel(model=env_model, base_url=env_base, api_key=env_key or "x")

    # 3. Groq (free tier).
    if os.environ.get("GROQ_API_KEY"):
        return OpenAIModel(
            model=model or GROQ_DEFAULT,
            base_url=GROQ_BASE,
            api_key=os.environ["GROQ_API_KEY"],
        )

    # 4. OpenAI.
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIModel(model=model or "gpt-4o-mini")

    # 5. Anthropic.
    if os.environ.get("ANTHROPIC_API_KEY"):
        from ..model import AnthropicModel

        return AnthropicModel(model=model or "claude-opus-4-8")

    # 6. Local Ollama, if it's running.
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if _ollama_up(host):
        return OpenAIModel(
            model=model or OLLAMA_DEFAULT,
            base_url=host.rstrip("/") + "/v1",
            api_key="ollama",
        )

    raise ModelError(
        "No model configured. Pick a free option:\n"
        "  • Groq (free tier):  export GROQ_API_KEY=...\n"
        "  • Ollama (local):    run `ollama serve` and `ollama pull llama3.2`\n"
        "  • OpenAI / Anthropic: set OPENAI_API_KEY or ANTHROPIC_API_KEY\n"
        "  • Any OpenAI-compatible endpoint: --model NAME --base-url URL --api-key KEY"
    )


def _ollama_up(host: str) -> bool:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False
