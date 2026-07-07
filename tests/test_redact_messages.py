"""Tests for redact_messages() in tvastar.boundary."""

from __future__ import annotations

from tvastar.boundary import redact_messages
from tvastar.types import Message, TextBlock


def _user(text):
    return Message("user", text)


def _asst(text):
    return Message("assistant", text)


class TestEmailRedaction:
    def test_email_replaced_with_placeholder(self):
        msgs = [_user("Contact me at alice@example.com")]
        result = redact_messages(msgs)
        assert "[EMAIL_1]" in result.messages[0].text
        assert "alice@example.com" not in result.messages[0].text

    def test_two_different_emails_get_different_indices(self):
        msgs = [_user("alice@ex.com and bob@ex.com are here")]
        result = redact_messages(msgs)
        text = result.messages[0].text
        assert "[EMAIL_1]" in text
        assert "[EMAIL_2]" in text
        assert result.redaction_count == 2

    def test_same_email_repeated_gets_same_placeholder(self):
        msgs = [
            _user("Email: alice@example.com"),
            _user("Again: alice@example.com"),
        ]
        result = redact_messages(msgs)
        assert result.messages[0].text == "Email: [EMAIL_1]"
        assert result.messages[1].text == "Again: [EMAIL_1]"
        assert result.redaction_count == 1


class TestPhoneRedaction:
    def test_us_phone_replaced(self):
        msgs = [_user("Call me at 555-123-4567 please.")]
        result = redact_messages(msgs)
        assert "[PHONE_1]" in result.messages[0].text
        assert "555-123-4567" not in result.messages[0].text

    def test_international_phone_replaced(self):
        msgs = [_user("My number is +1-555-123-4567.")]
        result = redact_messages(msgs)
        assert "[PHONE_1]" in result.messages[0].text


class TestMultiplePIITypes:
    def test_email_and_phone_both_redacted(self):
        msgs = [_user("alice@test.com, phone: 555-123-4567")]
        result = redact_messages(msgs)
        text = result.messages[0].text
        assert "[EMAIL_1]" in text
        assert "[PHONE_1]" in text
        assert result.redaction_count == 2
        assert "email" in result.redacted_types
        assert "phone" in result.redacted_types


class TestNoPII:
    def test_messages_without_pii_unchanged(self):
        msgs = [_user("Hello world"), _asst("Hi there")]
        result = redact_messages(msgs)
        assert result.messages[0].text == "Hello world"
        assert result.messages[1].text == "Hi there"
        assert result.redaction_count == 0
        assert result.redacted_types == []


class TestMetadata:
    def test_redaction_count_correct(self):
        msgs = [_user("a@b.com c@d.com 555-111-2222")]
        result = redact_messages(msgs)
        assert result.redaction_count == 3

    def test_redacted_types_correct(self):
        msgs = [_user("a@b.com and 555-111-2222")]
        result = redact_messages(msgs)
        assert sorted(result.redacted_types) == ["email", "phone"]

    def test_email_only_type(self):
        msgs = [_user("a@b.com")]
        result = redact_messages(msgs)
        assert result.redacted_types == ["email"]


class TestContentBlocks:
    def test_text_blocks_redacted(self):
        msgs = [Message("user", [TextBlock(text="alice@test.com")])]
        result = redact_messages(msgs)
        blocks = result.messages[0].blocks
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextBlock)
        assert "[EMAIL_1]" in blocks[0].text
