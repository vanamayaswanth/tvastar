"""Tests for Harness.list_sessions(), delete_session(), and SessionInfo metadata."""

import pytest

from tvastar.conversation.session_info import SessionInfo
from tvastar.memory.store import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def harness(store):
    """Create a Harness with an InMemoryStore for testing."""
    from tvastar import Harness, create_agent
    from tvastar.model import MockModel

    agent = create_agent("test", model=MockModel(script=["ok"]), instructions="test")
    return Harness(agent, store=store)


class TestListSessionsReturnsSessionInfo:
    def test_returns_empty_list_when_no_sessions(self, harness):
        result = harness.list_sessions()
        assert result == []

    def test_returns_session_info_for_in_memory_sessions(self, harness):
        harness.session("my-session")
        result = harness.list_sessions()
        assert len(result) == 1
        info = result[0]
        assert isinstance(info, SessionInfo)
        assert info.id == "my-session"

    def test_returns_session_info_for_persisted_sessions(self, harness, store):
        store.set(
            "session_meta:old-session",
            {
                "id": "old-session",
                "last_activity": 1700000000.0,
            },
        )
        result = harness.list_sessions()
        assert len(result) == 1
        info = result[0]
        assert isinstance(info, SessionInfo)
        assert info.id == "old-session"
        assert info.last_activity == 1700000000.0

    def test_merges_in_memory_and_persisted_sessions(self, harness, store):
        store.set(
            "session_meta:old-session",
            {
                "id": "old-session",
                "last_activity": 1700000000.0,
            },
        )
        harness.session("new-session")
        result = harness.list_sessions()
        ids = {s.id for s in result}
        assert "old-session" in ids
        assert "new-session" in ids
        assert len(result) == 2

    def test_deduplicates_when_session_in_both(self, harness, store):
        store.set(
            "session_meta:shared",
            {
                "id": "shared",
                "last_activity": 1700000000.0,
            },
        )
        harness.session("shared")
        result = harness.list_sessions()
        assert len(result) == 1
        assert result[0].id == "shared"
        # Persisted metadata takes precedence (it has the real last_activity)
        assert result[0].last_activity == 1700000000.0


class TestListSessionsFilter:
    def test_filter_none_returns_all(self, harness, store):
        store.set(
            "session_meta:api-review",
            {
                "id": "api-review",
                "last_activity": 1.0,
            },
        )
        store.set(
            "session_meta:auth-review",
            {
                "id": "auth-review",
                "last_activity": 2.0,
            },
        )
        result = harness.list_sessions(filter=None)
        assert len(result) == 2

    def test_filter_matches_substring(self, harness, store):
        store.set(
            "session_meta:api-review",
            {
                "id": "api-review",
                "last_activity": 1.0,
            },
        )
        store.set(
            "session_meta:auth-review",
            {
                "id": "auth-review",
                "last_activity": 2.0,
            },
        )
        store.set(
            "session_meta:build-check",
            {
                "id": "build-check",
                "last_activity": 3.0,
            },
        )
        result = harness.list_sessions(filter="review")
        ids = {s.id for s in result}
        assert ids == {"api-review", "auth-review"}

    def test_filter_no_match_returns_empty(self, harness, store):
        store.set(
            "session_meta:api-review",
            {
                "id": "api-review",
                "last_activity": 1.0,
            },
        )
        result = harness.list_sessions(filter="nonexistent")
        assert result == []

    def test_filter_empty_string_returns_all(self, harness, store):
        store.set(
            "session_meta:api-review",
            {
                "id": "api-review",
                "last_activity": 1.0,
            },
        )
        result = harness.list_sessions(filter="")
        assert len(result) == 1


class TestListSessionsLimit:
    def test_limit_caps_results(self, harness, store):
        for i in range(10):
            store.set(
                f"session_meta:session-{i}",
                {
                    "id": f"session-{i}",
                    "last_activity": float(i),
                },
            )
        result = harness.list_sessions(limit=3)
        assert len(result) == 3

    def test_limit_default_is_100(self, harness, store):
        for i in range(5):
            store.set(
                f"session_meta:s-{i}",
                {
                    "id": f"s-{i}",
                    "last_activity": float(i),
                },
            )
        # Default limit=100 should return all 5
        result = harness.list_sessions()
        assert len(result) == 5


class TestDeleteSession:
    def test_delete_existing_session_returns_true(self, harness, store):
        store.set("session_meta:doomed", {"id": "doomed", "last_activity": 1.0})
        store.set("event_log:doomed", [{"type": "session_start", "seq": 0}])
        assert harness.delete_session("doomed") is True

    def test_delete_removes_meta_and_log(self, harness, store):
        store.set("session_meta:doomed", {"id": "doomed", "last_activity": 1.0})
        store.set("event_log:doomed", [{"type": "session_start", "seq": 0}])
        harness.delete_session("doomed")
        assert store.get("session_meta:doomed") is None
        assert store.get("event_log:doomed") is None

    def test_delete_nonexistent_returns_false(self, harness):
        assert harness.delete_session("ghost") is False

    def test_delete_removes_from_in_memory_sessions(self, harness, store):
        store.set("session_meta:live", {"id": "live", "last_activity": 1.0})
        harness.session("live")
        assert harness.delete_session("live") is True
        assert "live" not in harness._sessions

    def test_after_delete_resume_returns_none(self, harness, store):
        store.set(
            "event_log:gone", [{"type": "session_start", "seq": 0, "timestamp": 1.0, "data": {}}]
        )
        store.set("session_meta:gone", {"id": "gone", "last_activity": 1.0})
        harness.delete_session("gone")
        assert harness.resume("gone") is None

    def test_after_delete_list_sessions_excludes(self, harness, store):
        store.set("session_meta:keep", {"id": "keep", "last_activity": 1.0})
        store.set("session_meta:remove", {"id": "remove", "last_activity": 2.0})
        harness.delete_session("remove")
        result = harness.list_sessions()
        ids = {s.id for s in result}
        assert "remove" not in ids
        assert "keep" in ids


class TestSessionMetaWrittenOnLifecycle:
    @pytest.mark.asyncio
    async def test_session_start_writes_meta(self, harness, store):
        s = harness.session("lifecycle-test")
        async with s:
            meta = store.get("session_meta:lifecycle-test")
            assert meta is not None
            assert meta["id"] == "lifecycle-test"
            assert meta["last_activity"] > 0

    @pytest.mark.asyncio
    async def test_session_close_updates_meta(self, harness, store):
        s = harness.session("close-test")
        async with s:
            meta_start = store.get("session_meta:close-test")
            start_time = meta_start["last_activity"]

        meta_end = store.get("session_meta:close-test")
        assert meta_end is not None
        assert meta_end["last_activity"] >= start_time

    @pytest.mark.asyncio
    async def test_list_sessions_finds_started_session(self, harness, store):
        s = harness.session("active")
        async with s:
            result = harness.list_sessions()
            ids = {info.id for info in result}
            assert "active" in ids
