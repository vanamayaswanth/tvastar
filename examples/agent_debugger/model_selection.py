"""Model selection logic for the Agent Debugger.

Determines whether to use MockModel (offline demo) or a real model provider
based on environment variables and configuration. The demo MockModel uses
profile-keyed scripts so each sub-agent gets its own scripted responses.

Requirements: 12.1, 12.2
"""

from __future__ import annotations

import logging
import os

from tvastar.model import Model

from .mock_responses import create_demo_model

logger = logging.getLogger(__name__)

# Supported provider configurations: (env var for API key, provider module attribute)
_PROVIDER_CONFIG = [
    ("ANTHROPIC_API_KEY", "AnthropicModel"),
    ("OPENAI_API_KEY", "OpenAIModel"),
]


def select_model(use_real: bool = False) -> Model:
    """Select the appropriate model for the Agent Debugger pipeline.

    Decision logic:
        1. If ``use_real=True`` OR env var ``TVASTAR_REAL=1``:
           - Check for API key env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY)
           - If a key is found, import the corresponding provider and return it
        2. Otherwise (or if no API key is found): return a MockModel from
           ``create_demo_model()`` with scripted responses for the full pipeline.

    Args:
        use_real: Explicitly request a real model. Can also be triggered via
            the ``TVASTAR_REAL=1`` environment variable.

    Returns:
        A Model instance — either a real provider model or the demo MockModel.
    """
    real_requested = use_real or os.environ.get("TVASTAR_REAL") == "1"

    if real_requested:
        model = _try_real_model()
        if model is not None:
            return model
        # No API key found — fall back to MockModel
        logger.warning(
            "Real model requested but no API key found "
            "(checked ANTHROPIC_API_KEY, OPENAI_API_KEY). "
            "Falling back to MockModel."
        )

    model = create_demo_model()
    logger.info("Using MockModel with scripted demo responses.")
    return model


def _try_real_model() -> Model | None:
    """Attempt to instantiate a real model from available API keys.

    Checks providers in priority order (Anthropic first, then OpenAI).
    Returns None if no API key is available or the provider cannot be imported.
    """
    for env_var, model_attr in _PROVIDER_CONFIG:
        api_key = os.environ.get(env_var)
        if not api_key:
            continue

        try:
            # Lazy import via tvastar.model's __getattr__
            import tvastar.model as model_module

            model_cls = getattr(model_module, model_attr)
            model = model_cls()
            logger.info("Using real model: %s (key from %s)", model_attr, env_var)
            return model
        except (ImportError, AttributeError, Exception) as exc:
            logger.warning(
                "Failed to initialize %s from %s: %s",
                model_attr,
                env_var,
                exc,
            )
            continue

    return None
