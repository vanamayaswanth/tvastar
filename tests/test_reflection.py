"""Tests for tvastar.reflection — self-critique loop."""

from __future__ import annotations

import pytest

from tvastar.reflection import ReflectionPolicy, ReflectionResult, reflect
from tvastar.model.mock import MockModel


# --- Test ReflectionPolicy defaults ---


class TestReflectionPolicy:
    def test_defaults(self):
        policy = ReflectionPolicy()
        assert policy.max_rounds == 2
        assert "ACCEPTABLE" in policy.criteria
        assert policy.critic_model is None
        assert policy.threshold_word == "ACCEPTABLE"

    def test_custom_values(self):
        policy = ReflectionPolicy(
            max_rounds=5,
            criteria="Check for style",
            critic_model="gpt-4",
            threshold_word="PASS",
        )
        assert policy.max_rounds == 5
        assert policy.criteria == "Check for style"
        assert policy.critic_model == "gpt-4"
        assert policy.threshold_word == "PASS"


# --- Test ReflectionResult ---


class TestReflectionResult:
    def test_improved_flag_false_when_unchanged(self):
        result = ReflectionResult(
            original_text="hello",
            final_text="hello",
            rounds=1,
            critiques=[],
            improved=False,
        )
        assert result.improved is False

    def test_improved_flag_true_when_changed(self):
        result = ReflectionResult(
            original_text="hello",
            final_text="hello world",
            rounds=2,
            critiques=["needs more detail"],
            improved=True,
        )
        assert result.improved is True


# --- Test reflect() ---


class TestReflect:
    @pytest.mark.asyncio
    async def test_accepted_on_first_round(self):
        """Model says ACCEPTABLE immediately — no revision needed."""
        model = MockModel(script=["ACCEPTABLE"])
        result = await reflect("some output", model=model)

        assert result.original_text == "some output"
        assert result.final_text == "some output"
        assert result.rounds == 1
        assert result.critiques == []
        assert result.improved is False

    @pytest.mark.asyncio
    async def test_accepted_on_second_round(self):
        """Model critiques first, revises, then accepts on second round."""
        model = MockModel(
            script=[
                "The output is missing error handling.",  # critique round 1
                "some output with error handling",  # revision round 1
                "ACCEPTABLE",  # critique round 2 → accept
            ]
        )
        result = await reflect("some output", model=model)

        assert result.original_text == "some output"
        assert result.final_text == "some output with error handling"
        assert result.rounds == 2
        assert len(result.critiques) == 1
        assert "error handling" in result.critiques[0]
        assert result.improved is True

    @pytest.mark.asyncio
    async def test_max_rounds_reached(self):
        """Model never says ACCEPTABLE — stops at max_rounds."""
        model = MockModel(
            script=[
                "Needs improvement: add types.",  # critique round 1
                "revised version 1",  # revision round 1
                "Still needs improvement: add docs.",  # critique round 2
                "revised version 2",  # revision round 2
            ]
        )
        result = await reflect("original", model=model, max_rounds=2)

        assert result.original_text == "original"
        assert result.final_text == "revised version 2"
        assert result.rounds == 2
        assert len(result.critiques) == 2
        assert result.improved is True

    @pytest.mark.asyncio
    async def test_threshold_word_case_insensitive(self):
        """ACCEPTABLE matching is case-insensitive."""
        model = MockModel(script=["Looks good. Acceptable."])
        result = await reflect("output", model=model)

        assert result.final_text == "output"
        assert result.rounds == 1
        assert result.improved is False

    @pytest.mark.asyncio
    async def test_custom_threshold_word(self):
        """Custom threshold word is respected."""
        model = MockModel(script=["LGTM"])
        result = await reflect("output", model=model, threshold_word="LGTM")

        assert result.final_text == "output"
        assert result.rounds == 1
        assert result.improved is False

    @pytest.mark.asyncio
    async def test_single_round(self):
        """max_rounds=1: one critique + one revision if not accepted."""
        model = MockModel(
            script=[
                "Missing edge case handling.",  # critique
                "improved output",  # revision
            ]
        )
        result = await reflect("output", model=model, max_rounds=1)

        assert result.final_text == "improved output"
        assert result.rounds == 1
        assert len(result.critiques) == 1
        assert result.improved is True

    @pytest.mark.asyncio
    async def test_no_improvement_when_text_unchanged(self):
        """If revision produces same text, improved is False."""
        model = MockModel(
            script=[
                "It could be better.",  # critique
                "output",  # revision same as original
            ]
        )
        result = await reflect("output", model=model, max_rounds=1)

        assert result.final_text == "output"
        assert result.improved is False
