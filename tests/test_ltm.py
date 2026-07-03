"""Tests for tvastar.contrib.ltm.store — SQLite-backed long-term memory."""

from __future__ import annotations

import pytest

from tvastar.contrib.ltm.store import Knowledge, LTMStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary LTMStore for testing."""
    db_path = str(tmp_path / "test_memory.db")
    with LTMStore(db_path) as s:
        yield s


# --- Facts ---


class TestFacts:
    def test_remember_recall_string(self, store: LTMStore):
        store.remember("name", "Alice", agent="assistant")
        assert store.recall("name") == "Alice"

    def test_remember_recall_int(self, store: LTMStore):
        store.remember("count", 42, agent="assistant")
        assert store.recall("count") == 42

    def test_remember_recall_list(self, store: LTMStore):
        store.remember("items", [1, 2, 3], agent="assistant")
        assert store.recall("items") == [1, 2, 3]

    def test_remember_recall_dict(self, store: LTMStore):
        store.remember("config", {"debug": True, "level": 5}, agent="assistant")
        assert store.recall("config") == {"debug": True, "level": 5}

    def test_recall_missing_key_returns_none(self, store: LTMStore):
        assert store.recall("nonexistent") is None

    def test_recall_fact_missing_returns_none(self, store: LTMStore):
        assert store.recall_fact("nonexistent") is None

    def test_recall_fact_returns_full_record(self, store: LTMStore):
        store.remember("lang", "Python", agent="coder", confidence=0.95)
        fact = store.recall_fact("lang")
        assert fact is not None
        assert fact.key == "lang"
        assert fact.value == "Python"
        assert fact.agent == "coder"
        assert fact.confidence == 0.95
        assert fact.version == 1
        assert fact.updated_at > 0

    def test_forget_removes_fact(self, store: LTMStore):
        store.remember("temp", "value", agent="a")
        assert store.forget("temp") is True
        assert store.recall("temp") is None

    def test_forget_nonexistent_returns_false(self, store: LTMStore):
        assert store.forget("nope") is False

    def test_version_increments_on_update(self, store: LTMStore):
        f1 = store.remember("key", "v1", agent="a")
        assert f1.version == 1
        f2 = store.remember("key", "v2", agent="a")
        assert f2.version == 2
        f3 = store.remember("key", "v3", agent="b")
        assert f3.version == 3
        # recall_fact should show the latest
        fact = store.recall_fact("key")
        assert fact is not None
        assert fact.value == "v3"
        assert fact.version == 3

    def test_all_facts(self, store: LTMStore):
        store.remember("a", 1, agent="x")
        store.remember("b", 2, agent="y")
        store.remember("c", 3, agent="x")
        facts = store.all_facts()
        assert len(facts) == 3

    def test_all_facts_filtered_by_agent(self, store: LTMStore):
        store.remember("a", 1, agent="x")
        store.remember("b", 2, agent="y")
        store.remember("c", 3, agent="x")
        facts = store.all_facts(agent="x")
        assert len(facts) == 2
        assert all(f.agent == "x" for f in facts)


# --- Episodes ---


class TestEpisodes:
    def test_record_and_retrieve_episode(self, store: LTMStore):
        ep = store.record_episode("bot", "task_complete", {"task": "lint", "status": "ok"})
        assert ep.id is not None
        assert ep.agent == "bot"
        assert ep.event == "task_complete"
        assert ep.data == {"task": "lint", "status": "ok"}
        assert ep.timestamp > 0

    def test_recent_episodes_returns_latest_first(self, store: LTMStore):
        store.record_episode("a", "start", {"step": 1})
        store.record_episode("a", "middle", {"step": 2})
        store.record_episode("a", "end", {"step": 3})
        episodes = store.recent_episodes("a")
        assert len(episodes) == 3
        assert episodes[0].event == "end"
        assert episodes[-1].event == "start"

    def test_recent_episodes_limit(self, store: LTMStore):
        for i in range(10):
            store.record_episode("a", f"event_{i}", {"i": i})
        episodes = store.recent_episodes("a", limit=3)
        assert len(episodes) == 3

    def test_recent_episodes_filter_by_agent(self, store: LTMStore):
        store.record_episode("a", "e1", {})
        store.record_episode("b", "e2", {})
        store.record_episode("a", "e3", {})
        episodes = store.recent_episodes("a")
        assert len(episodes) == 2
        assert all(e.agent == "a" for e in episodes)

    def test_recent_episodes_filter_by_event(self, store: LTMStore):
        store.record_episode("a", "login", {})
        store.record_episode("a", "logout", {})
        store.record_episode("a", "login", {})
        episodes = store.recent_episodes("a", event="login")
        assert len(episodes) == 2
        assert all(e.event == "login" for e in episodes)

    def test_recent_episodes_no_filter(self, store: LTMStore):
        store.record_episode("a", "e1", {})
        store.record_episode("b", "e2", {})
        episodes = store.recent_episodes()
        assert len(episodes) == 2


# --- Knowledge ---


class TestKnowledge:
    def test_store_and_search_knowledge(self, store: LTMStore):
        store.store_knowledge(
            "Transformers use self-attention mechanisms to process sequences in parallel",
            source="paper.pdf",
            agent="researcher",
        )
        store.store_knowledge(
            "Python is a dynamically typed programming language",
            source="docs",
            agent="researcher",
        )
        results = store.search_knowledge("attention mechanism")
        assert len(results) >= 1
        assert any("attention" in r.text.lower() for r in results)

    def test_search_knowledge_returns_ranked(self, store: LTMStore):
        store.store_knowledge("The quick brown fox jumps over the lazy dog", source="a", agent="x")
        store.store_knowledge("Foxes are cunning animals found worldwide", source="b", agent="x")
        store.store_knowledge("Database indexing improves query performance", source="c", agent="x")
        results = store.search_knowledge("fox")
        assert len(results) >= 1
        # The most relevant results should mention fox
        assert "fox" in results[0].text.lower() or "fox" in results[0].text.lower()

    def test_search_knowledge_limit(self, store: LTMStore):
        for i in range(10):
            store.store_knowledge(f"Document about testing number {i}", source=f"doc{i}", agent="a")
        results = store.search_knowledge("testing", limit=3)
        assert len(results) <= 3

    def test_search_knowledge_filter_by_agent(self, store: LTMStore):
        store.store_knowledge("Rust is memory safe", source="a", agent="rust_fan")
        store.store_knowledge("Rust has no garbage collector", source="b", agent="rust_fan")
        store.store_knowledge("Python uses garbage collection", source="c", agent="py_fan")

        results = store.search_knowledge("garbage", agent="rust_fan")
        assert len(results) >= 1
        assert all(r.agent == "rust_fan" for r in results)

    def test_store_knowledge_returns_record(self, store: LTMStore):
        k = store.store_knowledge("Test content", source="test.txt", agent="bot")
        assert isinstance(k, Knowledge)
        assert k.id is not None
        assert k.text == "Test content"
        assert k.source == "test.txt"
        assert k.agent == "bot"
        assert k.created_at > 0


# --- Lifecycle ---


class TestLifecycle:
    def test_context_manager(self, tmp_path):
        db_path = str(tmp_path / "ctx_test.db")
        with LTMStore(db_path) as store:
            store.remember("key", "val", agent="a")
        # After close, re-open and check persistence
        with LTMStore(db_path) as store2:
            assert store2.recall("key") == "val"

    def test_close(self, tmp_path):
        db_path = str(tmp_path / "close_test.db")
        store = LTMStore(db_path)
        store.remember("x", 1, agent="a")
        store.close()
        # Re-open and verify data persists
        store2 = LTMStore(db_path)
        assert store2.recall("x") == 1
        store2.close()

    def test_path_property(self, tmp_path):
        db_path = str(tmp_path / "path_test.db")
        with LTMStore(db_path) as store:
            assert store.path == db_path
