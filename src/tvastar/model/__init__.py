"""Model layer: provider-agnostic Model interface + adapters.

Adapters with third-party deps are imported lazily so importing
`tvastar.model` never fails on a missing optional package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Model, ModelRetryPolicy
from .mock import MockModel

if TYPE_CHECKING:  # pragma: no cover
    from .anthropic import AnthropicModel
    from .litellm import LiteLLMModel
    from .openai import OpenAIModel

__all__ = ["Model", "ModelRetryPolicy", "MockModel", "AnthropicModel", "OpenAIModel", "LiteLLMModel"]


def __getattr__(name: str):
    if name == "AnthropicModel":
        from .anthropic import AnthropicModel

        return AnthropicModel
    if name == "OpenAIModel":
        from .openai import OpenAIModel

        return OpenAIModel
    if name == "LiteLLMModel":
        from .litellm import LiteLLMModel

        return LiteLLMModel
    raise AttributeError(f"module 'tvastar.model' has no attribute {name!r}")
