"""Tests for LiteLLMModel — all mocked, no real API calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.types import Message, ToolSpec


def _fake_resp(content="Hello", finish_reason="stop", tool_calls=None, tokens=(10, 5)):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls),
        finish_reason=finish_reason,
    )
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def model():
    with patch.dict("sys.modules", {"litellm": MagicMock()}):
        from tvastar.model.litellm import LiteLLMModel
        return LiteLLMModel("gpt-4o")


class TestLiteLLMModel:
    def test_name_stored(self, model):
        assert model.name == "gpt-4o"

    def test_system_is_litellm(self, model):
        assert model.system == "litellm"

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        fake = _fake_resp("World")
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=fake)
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            from tvastar.model.litellm import LiteLLMModel
            m = LiteLLMModel("gpt-4o")
            result = await m.generate([Message("user", "Hi")])
        assert result.message.text == "World"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_tool_call_response(self):
        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="bash", arguments='{"command":"ls"}'),
        )
        fake = _fake_resp(content=None, finish_reason="tool_calls", tool_calls=[tc])
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=fake)
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            from tvastar.model.litellm import LiteLLMModel
            m = LiteLLMModel("gpt-4o")
            result = await m.generate([Message("user", "run ls")])
        uses = result.message.tool_uses
        assert len(uses) == 1
        assert uses[0].name == "bash"
        assert uses[0].input == {"command": "ls"}
        from tvastar.types import StopReason
        assert result.stop_reason == StopReason.TOOL_USE

    @pytest.mark.asyncio
    async def test_system_prompt_injected(self):
        fake = _fake_resp("ok")
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=fake)
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            from tvastar.model.litellm import LiteLLMModel
            m = LiteLLMModel("gpt-4o")
            await m.generate([Message("user", "hi")], system="Be helpful.")
        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["messages"][0] == {"role": "system", "content": "Be helpful."}

    @pytest.mark.asyncio
    async def test_tools_converted(self):
        fake = _fake_resp("ok")
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=fake)
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            from tvastar.model.litellm import LiteLLMModel
            m = LiteLLMModel("gpt-4o")
            spec = ToolSpec(name="add", description="Add two numbers",
                            input_schema={"type": "object", "properties": {}})
            await m.generate([Message("user", "add 1+2")], tools=[spec])
        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["tools"][0]["function"]["name"] == "add"
        assert call_kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_router_used_when_model_list_given(self):
        fake = _fake_resp("via router")
        mock_router = MagicMock()
        mock_router.acompletion = AsyncMock(return_value=fake)
        mock_litellm = MagicMock()
        mock_litellm.Router = MagicMock(return_value=mock_router)
        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            from tvastar.model.litellm import LiteLLMModel
            m = LiteLLMModel(
                "fast",
                model_list=[{"model_name": "fast", "litellm_params": {"model": "gpt-4o-mini"}}],
            )
            result = await m.generate([Message("user", "hi")])
        assert result.message.text == "via router"
        mock_router.acompletion.assert_called_once()

    def test_import_error_on_missing_litellm(self):
        import sys
        # Temporarily hide litellm
        saved = sys.modules.pop("litellm", None)
        sys.modules["litellm"] = None  # type: ignore
        try:
            from tvastar.errors import ModelError
            with pytest.raises((ModelError, ImportError, TypeError)):
                from tvastar.model.litellm import LiteLLMModel
                LiteLLMModel("gpt-4o")
        finally:
            if saved is not None:
                sys.modules["litellm"] = saved
            else:
                sys.modules.pop("litellm", None)
