"""Tests for MockModel structured output support (dict script entries)."""

from __future__ import annotations

import json

import pytest

from tvastar.model.mock import MockModel
from tvastar.types import Message, StopReason, TextBlock


class TestDictScriptEntry:
    """Dict script entries produce JSON text for structured output parsing."""

    async def test_dict_entry_produces_json_text(self):
        data = {"summary": "foo", "score": 0.9}
        model = MockModel(script=[data])
        response = await model.generate([Message("user", [TextBlock(text="hello")])])
        # The message text should be valid JSON matching the dict
        text = response.message.text
        parsed = json.loads(text)
        assert parsed == data

    async def test_dict_entry_stop_reason_is_end_turn(self):
        data = {"key": "value"}
        model = MockModel(script=[data])
        response = await model.generate([Message("user", [TextBlock(text="go")])])
        assert response.stop_reason == StopReason.END_TURN

    async def test_dict_entry_in_profile_script(self):
        data = {"result": 42, "status": "ok"}
        model = MockModel(scripts={"agent": [data]})
        model._profile = "agent"
        response = await model.generate([Message("user", [TextBlock(text="go")])])
        text = response.message.text
        parsed = json.loads(text)
        assert parsed == data

    async def test_nested_dict_serialized_correctly(self):
        data = {"outer": {"inner": [1, 2, 3]}, "flag": True}
        model = MockModel(script=[data])
        response = await model.generate([Message("user", [TextBlock(text="go")])])
        assert json.loads(response.message.text) == data

    async def test_empty_dict_produces_json_object(self):
        model = MockModel(script=[{}])
        response = await model.generate([Message("user", [TextBlock(text="go")])])
        assert json.loads(response.message.text) == {}

    async def test_mixed_script_str_and_dict(self):
        """A script can mix string and dict entries."""
        model = MockModel(script=["hello", {"x": 1}])
        msgs = [Message("user", [TextBlock(text="go")])]

        r1 = await model.generate(msgs)
        assert r1.message.text == "hello"

        r2 = await model.generate(msgs)
        assert json.loads(r2.message.text) == {"x": 1}

    async def test_dict_with_data_key(self):
        """The common pattern: dict with a 'data' key for structured output."""
        data = {"data": {"name": "test", "value": 123}}
        model = MockModel(script=[data])
        response = await model.generate([Message("user", [TextBlock(text="go")])])
        assert json.loads(response.message.text) == data
