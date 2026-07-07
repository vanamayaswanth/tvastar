"""Conformance tests for Model adapters.

Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6
"""

from __future__ import annotations

import inspect
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.model.base import Model
from tvastar.model.mock import MockModel
from tvastar.types import Message, ModelResponse, StopReason, TextBlock, ToolSpec, ToolUseBlock


# ---------------------------------------------------------------------------
# 16.1 Model ABC defines generate() with expected parameters
# ---------------------------------------------------------------------------


def test_model_is_abstract_class():
    assert inspect.isabstract(Model)


def test_generate_is_abstract():
    assert "generate" in Model.__abstractmethods__


def test_generate_signature_has_expected_params():
    sig = inspect.signature(Model.generate)
    params = list(sig.parameters.keys())
    assert "messages" in params
    assert "system" in params
    assert "tools" in params
    assert "max_tokens" in params
    assert "temperature" in params
    assert "stop_sequences" in params
    assert "thinking_level" in params


def test_generate_returns_model_response_annotation():
    hints = Model.generate.__annotations__
    assert "return" in hints
    ret = hints["return"]
    assert ret is ModelResponse or "ModelResponse" in str(ret)


def test_cannot_instantiate_model_abc_directly():
    with pytest.raises(TypeError, match="abstract"):
        Model()


def test_model_has_name_and_system_defaults():
    assert Model.name == "model"
    assert Model.system == "unknown"


# ---------------------------------------------------------------------------
# 16.2 AnthropicModel maps thinking_level to budget_tokens
# ---------------------------------------------------------------------------


def test_anthropic_thinking_level_low_maps_to_1024():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    kw = m._thinking_kwargs("low")
    assert kw["thinking"]["budget_tokens"] == 1024


def test_anthropic_thinking_level_medium_maps_to_8000():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    kw = m._thinking_kwargs("medium")
    assert kw["thinking"]["budget_tokens"] == 8000


def test_anthropic_thinking_level_high_maps_to_16000():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    kw = m._thinking_kwargs("high")
    assert kw["thinking"]["budget_tokens"] == 16000


def test_anthropic_thinking_level_none_returns_empty():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    assert m._thinking_kwargs(None) == {}


def test_anthropic_thinking_forces_temperature_one():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    for level in ("low", "medium", "high"):
        kw = m._thinking_kwargs(level)
        assert kw["temperature"] == 1.0


def test_anthropic_thinking_includes_beta_header():
    from tvastar.model.anthropic import AnthropicModel

    m = AnthropicModel.__new__(AnthropicModel)
    kw = m._thinking_kwargs("high")
    assert "betas" in kw
    assert "interleaved-thinking-2025-05-14" in kw["betas"]


async def test_anthropic_generate_passes_thinking_to_beta_api():
    """AnthropicModel.generate() sends thinking kwargs to the beta Anthropic API."""
    from tvastar.model.anthropic import AnthropicModel

    fake_resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Thought about it.")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    stub_client = MagicMock()
    stub_client.beta.messages.create = AsyncMock(return_value=fake_resp)
    stub_client.messages.create = AsyncMock(return_value=fake_resp)

    m = AnthropicModel("claude-opus-4-8", client=stub_client)
    result = await m.generate(
        [Message("user", "Think hard")],
        thinking_level="high",
    )

    stub_client.beta.messages.create.assert_called_once()
    call_kwargs = stub_client.beta.messages.create.call_args[1]
    assert call_kwargs["thinking"]["budget_tokens"] == 16000
    assert call_kwargs["temperature"] == 1.0
    assert result.message.text == "Thought about it."


# ---------------------------------------------------------------------------
# 16.3 OpenAIModel passes thinking_level as reasoning_effort
# ---------------------------------------------------------------------------


async def test_openai_thinking_level_high_passed_as_reasoning_effort():
    """OpenAIModel.generate() includes reasoning_effort when thinking_level is set."""
    from tvastar.model.openai import OpenAIModel

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Reasoned.", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    m = OpenAIModel("o1-preview", client=stub_client)
    result = await m.generate(
        [Message("user", "Think about this")],
        thinking_level="high",
    )

    call_kwargs = stub_client.chat.completions.create.call_args[1]
    assert call_kwargs["reasoning_effort"] == "high"
    assert result.message.text == "Reasoned."


async def test_openai_thinking_level_low_passed_as_low():
    from tvastar.model.openai import OpenAIModel

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Quick.", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    m = OpenAIModel("o1-mini", client=stub_client)
    await m.generate([Message("user", "Quick")], thinking_level="low")

    call_kwargs = stub_client.chat.completions.create.call_args[1]
    assert call_kwargs["reasoning_effort"] == "low"


async def test_openai_thinking_level_none_omits_reasoning_effort():
    from tvastar.model.openai import OpenAIModel

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Done.", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    m = OpenAIModel("gpt-4o", client=stub_client)
    await m.generate([Message("user", "Hi")], thinking_level=None)

    call_kwargs = stub_client.chat.completions.create.call_args[1]
    assert "reasoning_effort" not in call_kwargs


async def test_openai_thinking_level_xhigh_capped_to_high():
    """OpenAI only supports low/medium/high so xhigh maps to high."""
    from tvastar.model.openai import OpenAIModel

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Max.", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    m = OpenAIModel("o1", client=stub_client)
    await m.generate([Message("user", "Max effort")], thinking_level="xhigh")

    call_kwargs = stub_client.chat.completions.create.call_args[1]
    assert call_kwargs["reasoning_effort"] == "high"


# ---------------------------------------------------------------------------
# 16.4 MockModel accepts any generate() and returns scripted responses
# ---------------------------------------------------------------------------


async def test_mock_model_scripted_text_responses():
    model = MockModel(script=["First response", "Second response"])
    r1 = await model.generate([Message("user", "Hello")])
    assert r1.message.text == "First response"
    assert r1.stop_reason == StopReason.END_TURN

    r2 = await model.generate([Message("user", "Next")])
    assert r2.message.text == "Second response"


async def test_mock_model_scripted_tool_use():
    tool_block = ToolUseBlock(id="t1", name="bash", input={"command": "ls"})
    model = MockModel(script=[tool_block])
    r = await model.generate([Message("user", "Run a command")])
    assert r.stop_reason == StopReason.TOOL_USE
    assert len(r.message.tool_uses) == 1
    assert r.message.tool_uses[0].name == "bash"


async def test_mock_model_scripted_message_object():
    msg = Message("assistant", [TextBlock(text="Custom message")])
    model = MockModel(script=[msg])
    r = await model.generate([Message("user", "Go")])
    assert r.message.text == "Custom message"


async def test_mock_model_fallback_when_script_exhausted():
    model = MockModel(script=["Only one"])
    await model.generate([Message("user", "First")])
    r = await model.generate([Message("user", "Second call")])
    assert "[mock]" in r.message.text
    assert r.stop_reason == StopReason.END_TURN


async def test_mock_model_accepts_all_generate_parameters():
    """MockModel.generate() accepts all standard parameters without error."""
    model = MockModel(script=["ok"])
    tool = ToolSpec(name="add", description="Add", input_schema={"type": "object"})
    r = await model.generate(
        [Message("user", "Test")],
        system="Be helpful",
        tools=[tool],
        max_tokens=1024,
        temperature=0.5,
        stop_sequences=["STOP"],
        thinking_level="high",
    )
    assert r.message.text == "ok"


async def test_mock_model_records_calls():
    model = MockModel(script=["r1", "r2"])
    await model.generate([Message("user", "Hello")])
    await model.generate([Message("user", "Hi"), Message("assistant", "...")])
    assert len(model.calls) == 2
    assert len(model.calls[0]) == 1
    assert len(model.calls[1]) == 2


async def test_mock_model_thinking_level_reflected_in_echo():
    """When thinking_level is given and script is exhausted, echo includes a note."""
    model = MockModel()
    r = await model.generate(
        [Message("user", "Deep thought")],
        thinking_level="high",
    )
    assert "thinking=high" in r.message.text


def test_mock_model_conforms_to_abc():
    """MockModel is a proper subclass of the Model ABC."""
    assert isinstance(MockModel(), Model)
    assert issubclass(MockModel, Model)


# ---------------------------------------------------------------------------
# 16.5 ImportError with clear install instruction for missing SDKs
# ---------------------------------------------------------------------------


def test_anthropic_import_error_with_install_hint():
    """Importing AnthropicModel without the anthropic SDK raises with install hint."""
    from tvastar.errors import ModelError

    with patch.dict(sys.modules, {"anthropic": None}):
        from tvastar.model.anthropic import AnthropicModel

        with pytest.raises((ModelError, ImportError, TypeError)) as exc_info:
            AnthropicModel("claude-opus-4-8")
        error_msg = str(exc_info.value).lower()
        assert "anthropic" in error_msg
        assert "install" in error_msg or "not installed" in error_msg


def test_openai_import_error_with_install_hint():
    """Importing OpenAIModel without the openai SDK raises with install hint."""
    from tvastar.errors import ModelError

    with patch.dict(sys.modules, {"openai": None}):
        from tvastar.model.openai import OpenAIModel

        with pytest.raises((ModelError, ImportError, TypeError)) as exc_info:
            OpenAIModel("gpt-4o")
        error_msg = str(exc_info.value).lower()
        assert "openai" in error_msg
        assert "install" in error_msg or "not installed" in error_msg


def test_litellm_import_error_with_install_hint():
    """Importing LiteLLMModel without litellm raises with install hint."""
    from tvastar.errors import ModelError

    saved = sys.modules.pop("litellm", None)
    sys.modules["litellm"] = None  # type: ignore
    try:
        from tvastar.model.litellm import LiteLLMModel

        with pytest.raises((ModelError, ImportError, TypeError)) as exc_info:
            LiteLLMModel("gpt-4o")
        error_msg = str(exc_info.value).lower()
        assert "litellm" in error_msg
        assert "install" in error_msg or "not installed" in error_msg
    finally:
        if saved is not None:
            sys.modules["litellm"] = saved
        else:
            sys.modules.pop("litellm", None)


# ---------------------------------------------------------------------------
# 16.6 LiteLLMModel supports model_list routing with fallback
# ---------------------------------------------------------------------------


def _fake_litellm_resp(content="Hello"):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=None),
        finish_reason="stop",
    )
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_litellm_router_created_when_model_list_given():
    """LiteLLMModel creates a Router when model_list is provided."""
    mock_router_instance = MagicMock()
    mock_router_instance.acompletion = AsyncMock(return_value=_fake_litellm_resp("routed"))
    mock_litellm = MagicMock()
    mock_litellm.Router = MagicMock(return_value=mock_router_instance)

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        from tvastar.model.litellm import LiteLLMModel

        model_list = [
            {"model_name": "fast", "litellm_params": {"model": "gpt-4o-mini"}},
            {"model_name": "smart", "litellm_params": {"model": "gpt-4o"}},
        ]
        m = LiteLLMModel("fast", model_list=model_list)
        result = await m.generate([Message("user", "hi")])

    assert result.message.text == "routed"
    mock_router_instance.acompletion.assert_called_once()


async def test_litellm_router_receives_fallback_config():
    """LiteLLMModel passes fallback configuration to the Router."""
    mock_router_instance = MagicMock()
    mock_router_instance.acompletion = AsyncMock(return_value=_fake_litellm_resp("ok"))
    mock_litellm = MagicMock()
    mock_litellm.Router = MagicMock(return_value=mock_router_instance)

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        from tvastar.model.litellm import LiteLLMModel

        model_list = [
            {"model_name": "fast", "litellm_params": {"model": "gpt-4o-mini"}},
            {"model_name": "smart", "litellm_params": {"model": "gpt-4o"}},
        ]
        fallbacks = [{"fast": ["smart"]}]
        LiteLLMModel(
            "fast",
            model_list=model_list,
            fallbacks=fallbacks,
            routing_strategy="usage-based-routing-v2",
        )

    router_call_kwargs = mock_litellm.Router.call_args
    assert router_call_kwargs[1]["fallbacks"] == fallbacks
    assert router_call_kwargs[1]["routing_strategy"] == "usage-based-routing-v2"


async def test_litellm_direct_mode_without_router():
    """LiteLLMModel calls litellm.acompletion directly when no model_list is given."""
    mock_litellm = MagicMock()
    mock_litellm.acompletion = AsyncMock(return_value=_fake_litellm_resp("direct"))

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        from tvastar.model.litellm import LiteLLMModel

        m = LiteLLMModel("gpt-4o")
        result = await m.generate([Message("user", "hi")])

    assert result.message.text == "direct"
    mock_litellm.acompletion.assert_called_once()


async def test_litellm_router_error_raises_model_error():
    """LiteLLMModel wraps router errors in ModelError."""
    from tvastar.errors import ModelError

    mock_router_instance = MagicMock()
    mock_router_instance.acompletion = AsyncMock(side_effect=Exception("All deployments failed"))
    mock_litellm = MagicMock()
    mock_litellm.Router = MagicMock(return_value=mock_router_instance)

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        from tvastar.model.litellm import LiteLLMModel

        m = LiteLLMModel(
            "fast",
            model_list=[{"model_name": "fast", "litellm_params": {"model": "gpt-4o-mini"}}],
        )
        with pytest.raises(ModelError, match="LiteLLM error"):
            await m.generate([Message("user", "hi")])
