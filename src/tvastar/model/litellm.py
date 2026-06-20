"""LiteLLM adapter — 100+ providers + Router for load-balancing and fallback.

Requires: pip install litellm
Optional (for Router): pass model_list with multiple deployments.

Usage — single provider::

    from tvastar.model import LiteLLMModel
    model = LiteLLMModel("gpt-4o")          # any litellm-supported model string
    model = LiteLLMModel("anthropic/claude-sonnet-4-6")

Usage — Router (load-balancing + fallback)::

    model = LiteLLMModel(
        "claude-haiku-4-5-20251001",         # default / cheapest
        model_list=[
            {"model_name": "fast",  "litellm_params": {"model": "claude-haiku-4-5-20251001"}},
            {"model_name": "smart", "litellm_params": {"model": "claude-sonnet-4-6"}},
        ],
        routing_strategy="usage-based-routing-v2",
        fallbacks=[{"fast": ["smart"]}],     # escalate on failure
    )
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..errors import ModelError
from ..types import (
    Message,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from .base import Model

__all__ = ["LiteLLMModel"]

# OpenAI stop reason → Tvastar StopReason
_STOP_MAP = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.END_TURN,
}


def _to_litellm_messages(messages: list[Message], system: Optional[str]) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "system":
            out.append({"role": "system", "content": m.text})
        elif m.role in ("user", "assistant"):
            blocks = m.blocks
            has_tool_use = any(isinstance(b, ToolUseBlock) for b in blocks)
            has_tool_result = any(isinstance(b, ToolResultBlock) for b in blocks)

            if has_tool_result:
                for b in blocks:
                    if isinstance(b, ToolResultBlock):
                        out.append(
                            {"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content}
                        )
            elif has_tool_use:
                tool_calls = [
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {"name": b.name, "arguments": json.dumps(b.input)},
                    }
                    for b in blocks
                    if isinstance(b, ToolUseBlock)
                ]
                text_parts = [b.text for b in blocks if isinstance(b, TextBlock) and b.text]
                out.append(
                    {
                        "role": "assistant",
                        "content": " ".join(text_parts) or None,
                        "tool_calls": tool_calls,
                    }
                )
            else:
                text = m.text
                out.append({"role": m.role, "content": text})
    return out


def _to_tool_specs(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _parse_response(resp: Any) -> ModelResponse:
    choice = resp.choices[0]
    msg = choice.message
    finish = choice.finish_reason or "stop"
    blocks: list = []

    if msg.content:
        blocks.append(TextBlock(text=msg.content))

    for tc in getattr(msg, "tool_calls", None) or []:
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            args = {}
        blocks.append(ToolUseBlock(name=tc.function.name, input=args, id=tc.id))

    stop_reason = _STOP_MAP.get(finish, StopReason.END_TURN)
    if blocks and any(isinstance(b, ToolUseBlock) for b in blocks):
        stop_reason = StopReason.TOOL_USE

    usage_obj = getattr(resp, "usage", None)
    usage = Usage(
        input_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
    )
    return ModelResponse(
        message=Message("assistant", blocks),
        stop_reason=stop_reason,
        usage=usage,
        raw=resp,
    )


class LiteLLMModel(Model):
    """LiteLLM-backed model adapter.

    Covers 100+ providers (Anthropic, OpenAI, Groq, Together, Bedrock, Vertex,
    Ollama, …) with optional Router for load-balancing, fallback, and
    usage-based routing between cheap and expensive deployments.

    Args:
        model:            LiteLLM model string (e.g. ``"gpt-4o"``,
                          ``"anthropic/claude-sonnet-4-6"``).
        model_list:       Router deployment list. If provided, a
                          ``litellm.Router`` is created and all calls go
                          through it.
        routing_strategy: LiteLLM Router strategy. Default
                          ``"usage-based-routing-v2"`` picks the least-loaded
                          deployment automatically.
        fallbacks:        List of ``{model_name: [fallback_model_name]}``
                          dicts for the Router.
        api_key:          Provider API key (most providers read from env).
        **router_kwargs:  Extra kwargs forwarded to ``litellm.Router``.
    """

    system = "litellm"

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        model_list: Optional[list[dict]] = None,
        routing_strategy: str = "usage-based-routing-v2",
        fallbacks: Optional[list[dict]] = None,
        api_key: Optional[str] = None,
        **router_kwargs: Any,
    ):
        self.name = model
        self._model = model
        self._api_key = api_key
        self._router: Any = None

        try:
            import litellm  # noqa: F401 — validate install
        except ImportError as e:
            raise ModelError("litellm not installed. Run: pip install litellm") from e

        if model_list:
            try:
                from litellm import Router
            except ImportError as e:
                raise ModelError("litellm not installed. Run: pip install litellm") from e
            self._router = Router(
                model_list=model_list,
                routing_strategy=routing_strategy,
                fallbacks=fallbacks or [],
                **router_kwargs,
            )

    async def generate(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
        thinking_level: Optional[str] = None,
    ) -> ModelResponse:
        import litellm

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _to_litellm_messages(messages, system),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop_sequences:
            kwargs["stop"] = stop_sequences
        if tools:
            kwargs["tools"] = _to_tool_specs(tools)
            kwargs["tool_choice"] = "auto"
        if self._api_key:
            kwargs["api_key"] = self._api_key

        try:
            if self._router is not None:
                resp = await self._router.acompletion(**kwargs)
            else:
                resp = await litellm.acompletion(**kwargs)
        except Exception as e:
            raise ModelError(f"LiteLLM error: {e}") from e

        return _parse_response(resp)
