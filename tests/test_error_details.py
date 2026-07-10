"""Tests for TvastarError.details property and DurableError structured details."""

from tvastar.errors import DurableError, ModelError, TvastarError


class TestTvastarErrorDetails:
    def test_default_details_is_empty_dict(self):
        e = TvastarError("something broke")
        assert e.details == {}

    def test_details_kwarg_stored(self):
        e = TvastarError("fail", details={"error_code": "rate_limit"})
        assert e.details == {"error_code": "rate_limit"}

    def test_none_details_returns_empty_dict(self):
        e = TvastarError("fail", details=None)
        assert e.details == {}

    def test_subclass_inherits_details(self):
        e = ModelError("timeout", details={"provider": "openai"})
        assert e.details == {"provider": "openai"}

    def test_positional_message_still_works(self):
        e = TvastarError("hello", "world")
        assert e.args == ("hello", "world")
        assert e.details == {}


class TestDurableErrorDetails:
    def test_backward_compat_message_only(self):
        e = DurableError("checkpoint save failed: timeout")
        assert str(e) == "checkpoint save failed: timeout"
        assert e.details == {}

    def test_session_id_and_operation_in_details(self):
        e = DurableError("write failed", session_id="sess_123", operation="append")
        assert e.details == {"session_id": "sess_123", "operation": "append"}

    def test_only_session_id(self):
        e = DurableError("fail", session_id="s1")
        assert e.details == {"session_id": "s1"}

    def test_only_operation(self):
        e = DurableError("fail", operation="resume")
        assert e.details == {"operation": "resume"}

    def test_extra_details_merged(self):
        e = DurableError(
            "fail",
            session_id="s1",
            operation="append",
            details={"retry_count": 3},
        )
        assert e.details == {"retry_count": 3, "session_id": "s1", "operation": "append"}
