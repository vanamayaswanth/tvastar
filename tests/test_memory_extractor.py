"""Tests for MemoryExtractor — Requirement 7.

Tests:
- LLM mode extracts facts
- Pattern mode extracts via regex
- Failure doesn't block close
- Opt-out skips extraction
- Extracted facts go through remember()
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.contrib.ltm.extractor import ExtractedFact, MemoryExtractor, _PATTERN_RE
from tvastar.contrib.ltm.store import LTMStore
from tvastar.types import Message


# --- Helpers ---


@dataclass
class FakeModelResponse:
    """Minimal mock for model.generate() return value."""

    class _Msg:
        def __init__(self, text: str):
            self.text = text

    def __init__(self, text: str):
        self.message = self._Msg(text)


def _make_messages(*texts: str) -> list[Message]:
    """Create alternating user/assistant messages from text strings."""
    msgs = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role, t))
    return msgs


# --- Unit tests: LLM extraction ---


class TestLLMExtraction:
    @pytest.mark.asyncio
    async def test_llm_mode_extracts_facts(self):
        """LLM mode prompts model and returns extracted facts."""
        model = AsyncMock()
        model.generate.return_value = FakeModelResponse(
            json.dumps([
                {"key": "stack", "value": "TypeScript + Supabase", "confidence": 0.9},
                {"key": "db", "value": "PostgreSQL 16", "confidence": 0.85},
            ])
        )

        extractor = MemoryExtractor(mode="llm", model=model)
        messages = _make_messages(
            "What stack should we use?",
            "I recommend TypeScript + Supabase with PostgreSQL 16.",
        )

        facts = await extractor.extract(messages)

        assert len(facts) == 2
        assert facts[0].key == "stack"
        assert facts[0].value == "TypeScript + Supabase"
        assert facts[0].confidence == 0.9
        assert facts[1].key == "db"
        model.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_mode_empty_messages_returns_empty(self):
        """LLM mode with no messages returns empty list."""
        model = AsyncMock()
        extractor = MemoryExtractor(mode="llm", model=model)
        facts = await extractor.extract([])
        assert facts == []
        model.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_mode_invalid_json_returns_empty(self):
        """LLM mode handles model returning invalid JSON gracefully."""
        model = AsyncMock()
        model.generate.return_value = FakeModelResponse("not valid json at all")

        extractor = MemoryExtractor(mode="llm", model=model)
        messages = _make_messages("hello", "world")
        facts = await extractor.extract(messages)
        assert facts == []

    @pytest.mark.asyncio
    async def test_llm_mode_model_exception_propagates(self):
        """LLM extraction raises if model fails — caller wraps in try/except."""
        model = AsyncMock()
        model.generate.side_effect = RuntimeError("timeout")

        extractor = MemoryExtractor(mode="llm", model=model)
        messages = _make_messages("hello", "world")

        with pytest.raises(RuntimeError, match="timeout"):
            await extractor.extract(messages)


# --- Unit tests: Pattern extraction ---


class TestPatternExtraction:
    @pytest.mark.asyncio
    async def test_pattern_mode_extracts_is_statements(self):
        """Pattern mode extracts 'X is Y' statements."""
        extractor = MemoryExtractor(mode="pattern")
        messages = _make_messages(
            "The database is PostgreSQL. The framework is Django.",
            "Got it, I'll configure both.",
        )

        facts = await extractor.extract(messages)

        assert len(facts) >= 2
        keys = [f.key for f in facts]
        assert "database" in keys or "the_database" in keys
        # Verify values contain expected content
        db_fact = next(f for f in facts if "database" in f.key)
        assert "PostgreSQL" in db_fact.value

    @pytest.mark.asyncio
    async def test_pattern_mode_deduplicates_keys(self):
        """Pattern mode doesn't produce duplicate keys."""
        extractor = MemoryExtractor(mode="pattern")
        messages = _make_messages(
            "The server is nginx. The server is apache.",
            "Which one?",
        )

        facts = await extractor.extract(messages)
        keys = [f.key for f in facts]
        # Should only have one 'server' entry (first match wins)
        assert keys.count("server") <= 1

    @pytest.mark.asyncio
    async def test_pattern_mode_empty_messages(self):
        """Pattern mode with empty messages returns empty."""
        extractor = MemoryExtractor(mode="pattern")
        facts = await extractor.extract([])
        assert facts == []


# --- Unit tests: None mode (opt-out) ---


class TestOptOut:
    @pytest.mark.asyncio
    async def test_none_mode_skips_extraction(self):
        """mode=None skips all extraction."""
        extractor = MemoryExtractor(mode=None)
        messages = _make_messages("The stack is Python.", "Great choice!")
        facts = await extractor.extract(messages)
        assert facts == []

    @pytest.mark.asyncio
    async def test_unset_mode_skips(self):
        """Default MemoryExtractor (no mode) skips extraction."""
        extractor = MemoryExtractor()
        messages = _make_messages("hello", "world")
        facts = await extractor.extract(messages)
        assert facts == []


# --- Integration: extract_and_remember ---


class TestExtractAndRemember:
    @pytest.mark.asyncio
    async def test_facts_fed_to_ltm_store(self, tmp_path):
        """Extracted facts are persisted via LTMStore.remember()."""
        db_path = str(tmp_path / "test.db")
        ltm_store = LTMStore(db_path)

        model = AsyncMock()
        model.generate.return_value = FakeModelResponse(
            json.dumps([
                {"key": "language", "value": "Python 3.12", "confidence": 0.95},
            ])
        )

        extractor = MemoryExtractor(mode="llm", model=model)
        messages = _make_messages("What language?", "We use Python 3.12.")

        facts = await extractor.extract_and_remember(messages, ltm_store)

        assert len(facts) == 1
        # Verify it went through remember() — should be recallable
        recalled = ltm_store.recall("language")
        assert recalled == "Python 3.12"

    @pytest.mark.asyncio
    async def test_pattern_facts_fed_to_ltm_store(self, tmp_path):
        """Pattern-extracted facts go through remember()."""
        db_path = str(tmp_path / "test.db")
        ltm_store = LTMStore(db_path)

        extractor = MemoryExtractor(mode="pattern")
        messages = _make_messages(
            "The deployment target is AWS Lambda.",
            "Understood.",
        )

        facts = await extractor.extract_and_remember(messages, ltm_store)

        assert len(facts) >= 1
        # At least one fact should be stored
        all_facts = ltm_store.all_facts()
        assert len(all_facts) >= 1


# --- Session integration: failure doesn't block close ---


class TestSessionIntegration:
    @pytest.mark.asyncio
    async def test_extraction_failure_doesnt_block_close(self):
        """If extraction raises, session still closes normally."""
        from tvastar.agent import create_agent
        from tvastar.harness import Harness

        # Build a mock model that fails during extraction
        model = AsyncMock()
        model.name = "mock"
        model.system = ""
        # First call is for session prompt, subsequent for extraction
        model.generate.side_effect = RuntimeError("extraction boom")

        spec = create_agent(
            "test",
            model=model,
            memory_extraction="llm",
        )
        harness = Harness(spec)

        # Session should close without raising even though extraction fails
        session = harness.session(spec=spec)
        session.messages = _make_messages("hello", "world")
        session._started = True

        # Manually invoke __aexit__ — should not raise
        await session.__aexit__(None, None, None)
        # Session should be closed
        assert session._started is False

    @pytest.mark.asyncio
    async def test_opt_out_skips_extraction_in_session(self):
        """Session with memory_extraction=None does not call extractor."""
        from tvastar.agent import create_agent
        from tvastar.harness import Harness

        model = AsyncMock()
        model.name = "mock"
        model.system = ""

        spec = create_agent(
            "test",
            model=model,
            memory_extraction=None,
        )
        harness = Harness(spec)

        session = harness.session(spec=spec)
        session.messages = _make_messages("hello", "world")
        session._started = True

        await session.__aexit__(None, None, None)

        # Model should NOT have been called (no extraction)
        model.generate.assert_not_called()
        assert session._started is False

    @pytest.mark.asyncio
    async def test_memory_extraction_unset_does_not_extract(self):
        """AgentSpec without memory_extraction set does NOT extract."""
        from tvastar.agent import create_agent
        from tvastar.harness import Harness

        model = AsyncMock()
        model.name = "mock"
        model.system = ""

        # Default: memory_extraction is None
        spec = create_agent("test", model=model)
        harness = Harness(spec)

        session = harness.session(spec=spec)
        session.messages = _make_messages("The stack is Rust.", "Nice!")
        session._started = True

        await session.__aexit__(None, None, None)

        # No extraction calls
        model.generate.assert_not_called()
