"""OpenAI adapter — pluggable alternative provider.

Requires the ``openai`` package: ``uv add openai``. Maps Tvastar types to the
Chat Completions API with tool calling.

thinking_level mapping
----------------------
``None``     → no reasoning effort override (default behaviour)
``'low'``    → ``reasoning_effort='low'``
``'medium'`` → ``reasoning_effort='medium'``
``'high'``   → ``reasoning_effort='high'``

``reasoning_effort`` is an o-series model parameter. For non-reasoning models
it is silently ignored by the API.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Optional

from ..errors import ModelError
from ..types import (
    ImageBlock,
    Message,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from .base import Model, ModelRetryPolicy, _default_retryable


class OpenAIModel(Model):
    """OpenAI Chat Completions adapter.

    Works with OpenAI *and* any OpenAI-compatible provider via ``base_url`` —
    Cloudflare Workers AI, Groq, Together, Fireworks, OpenRouter, Ollama, vLLM,
    etc. Pass ``base_url`` (and that provider's ``api_key``), or hand in a fully
    configured ``client``.
    """

    system = "openai"

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Any = None,
        retry: Optional[ModelRetryPolicy] = None,
    ):
        self.name = model
        self._model = model
        self.retry = retry
        if client is not None:
            self._client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:  # pragma: no cover
                raise ModelError("openai package not installed. Run: uv add openai") from e
            self._client = AsyncOpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=base_url,
            )

    def _to_openai(self, messages: list[Message], system: Optional[str]) -> list[dict]:
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "system":
                out.append({"role": "system", "content": m.text})
                continue
            tool_calls = []
            text_parts = []
            image_parts: list[dict] = []
            tool_results = []
            for b in m.blocks:
                if isinstance(b, TextBlock):
                    text_parts.append(b.text)
                elif isinstance(b, ImageBlock):
                    url = (
                        b.data if b.source_type == "url" else f"data:{b.media_type};base64,{b.data}"
                    )
                    image_parts.append({"type": "image_url", "image_url": {"url": url}})
                elif isinstance(b, ToolUseBlock):
                    tool_calls.append(
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.input),
                            },
                        }
                    )
                elif isinstance(b, ToolResultBlock):
                    tool_results.append(b)
            if tool_results:
                for tr in tool_results:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.tool_use_id,
                            "content": tr.content,
                        }
                    )
                continue
            if image_parts:
                content_parts: list[Any] = []
                if text_parts:
                    content_parts.append({"type": "text", "text": "".join(text_parts)})
                content_parts.extend(image_parts)
                msg: dict[str, Any] = {"role": m.role, "content": content_parts}
            else:
                msg = {"role": m.role, "content": "".join(text_parts) or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        return out

    @staticmethod
    def _tools(tools: Optional[list[ToolSpec]]) -> Optional[list[dict]]:
        if not tools:
            return None
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_openai(messages, system),
        }
        oa_tools = self._tools(tools)
        if oa_tools:
            kwargs["tools"] = oa_tools
        if stop_sequences:
            kwargs["stop"] = stop_sequences
        if thinking_level:
            # OpenAI only supports low/medium/high; cap xhigh at high
            effort = "high" if thinking_level == "xhigh" else thinking_level
            kwargs["reasoning_effort"] = effort

        policy = self.retry
        max_attempts = policy.max_attempts if policy else 1
        for attempt in range(max_attempts):
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as e:  # pragma: no cover - network
                is_last = attempt >= max_attempts - 1
                if not is_last and policy is not None:
                    check = policy.retryable or _default_retryable
                    if check(e):
                        delay = min(
                            policy.backoff_base * (2**attempt) + random.uniform(0, policy.jitter),
                            policy.backoff_max,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise ModelError(f"OpenAI request failed: {e}") from e

        choice = resp.choices[0]
        blocks: list[Any] = []
        if choice.message.content:
            blocks.append(TextBlock(text=choice.message.content))
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            blocks.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))

        stop = StopReason.TOOL_USE if choice.finish_reason == "tool_calls" else StopReason.END_TURN
        usage = Usage()
        if resp.usage:
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
            )
        return ModelResponse(
            message=Message("assistant", blocks),
            stop_reason=stop,
            usage=usage,
            raw=resp,
        )
