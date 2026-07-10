"""Tests for session identity stability (REQ-1.1, 1.2, 1.5, 1.6)."""

import pytest

from tvastar.agent import create_agent
from tvastar.harness import Harness


class FakeModel:
    """Minimal model stub for identity tests (no generation needed)."""

    name = "fake"
    system = ""

    async def generate(self, *a, **kw):
        pass  # pragma: no cover


def _make_harness():
    spec = create_agent("test-agent", model=FakeModel(), instructions="hi")
    return Harness(spec, durable=False)


class TestSessionIdentityStability:
    """Requirement 1: Session Identity — stability and immutability."""

    def test_named_session_has_name_as_id(self):
        """When name is passed to harness.session(), that name becomes session.id."""
        h = _make_harness()
        s = h.session(name="my-branch")
        assert s.id == "my-branch"

    def test_unnamed_session_gets_auto_generated_id(self):
        """A session created without a name gets an auto-generated id."""
        h = _make_harness()
        s = h.session()
        assert s.id.startswith("sess_")
        assert len(s.id) == len("sess_") + 12  # sess_ + 12 hex chars

    def test_session_id_immutable_after_creation(self):
        """Session.id cannot be reassigned after construction."""
        h = _make_harness()
        s = h.session(name="frozen")
        with pytest.raises(AttributeError, match="immutable"):
            s.id = "something-else"
        assert s.id == "frozen"

    def test_unnamed_session_id_also_immutable(self):
        """Auto-generated ids are equally frozen."""
        h = _make_harness()
        s = h.session()
        original = s.id
        with pytest.raises(AttributeError, match="immutable"):
            s.id = "overwrite"
        assert s.id == original

    def test_same_name_returns_same_session(self):
        """Requirement 1.6: same name within same Harness returns same instance."""
        h = _make_harness()
        s1 = h.session(name="branch-a")
        s2 = h.session(name="branch-a")
        assert s1 is s2
