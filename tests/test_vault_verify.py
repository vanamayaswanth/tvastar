"""Tests for tvastar.comply.vault_verify.verify_pii_protection."""

from __future__ import annotations

from dataclasses import dataclass

from tvastar.comply.vault_verify import verify_pii_protection


@dataclass
class _FakeReceipt:
    """Minimal stand-in for ExecutionReceipt with prompt and content_hash."""

    prompt: str
    content_hash: str = "sha256:abc123"


def test_clean_prompt_with_tokens_only():
    """Prompt with only opaque tokens → no leaks, vault active."""
    receipt = _FakeReceipt(prompt="Hello <<EMAIL_1>> please send to <<SSN_1>>")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.vault_active is True
    assert result.token_count == 2
    assert result.leak_count == 0
    assert result.leaked_types == []
    assert result.content_hash == "sha256:abc123"


def test_leaked_ssn():
    """Prompt containing a raw SSN → leak detected."""
    receipt = _FakeReceipt(prompt="Patient SSN is 123-45-6789")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.leak_count == 1
    assert "SSN" in result.leaked_types


def test_leaked_email():
    """Prompt containing a raw email → leak detected."""
    receipt = _FakeReceipt(prompt="Contact user@example.com for details")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.leak_count == 1
    assert "EMAIL" in result.leaked_types


def test_leaked_phone():
    """Prompt containing a raw phone number → leak detected."""
    receipt = _FakeReceipt(prompt="Call (555) 123-4567 immediately")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert "PHONE" in result.leaked_types


def test_leaked_ip():
    """Prompt containing a raw IP → leak detected."""
    receipt = _FakeReceipt(prompt="Server at 192.168.1.100 is down")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert "IP" in result.leaked_types


def test_leaked_dob():
    """Prompt containing a raw DOB → leak detected."""
    receipt = _FakeReceipt(prompt="Born on 01/15/1990")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert "DOB" in result.leaked_types


def test_leaked_bearer_token():
    """Prompt containing a bearer token → leak detected."""
    receipt = _FakeReceipt(prompt="Use Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert "BEARER_TOKEN" in result.leaked_types


def test_leaked_api_key():
    """Prompt containing an API key → leak detected."""
    receipt = _FakeReceipt(prompt="Set api_key=sk-abc123xyz")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert "API_KEY" in result.leaked_types


def test_multiple_leaks():
    """Prompt with multiple PII types → all detected."""
    receipt = _FakeReceipt(prompt="SSN 123-45-6789, email foo@bar.com, IP 10.0.0.1")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.leak_count == 3
    assert "SSN" in result.leaked_types
    assert "EMAIL" in result.leaked_types
    assert "IP" in result.leaked_types


def test_vault_not_configured():
    """vault_configured=False → vault_active is False even with tokens."""
    receipt = _FakeReceipt(prompt="Hello <<EMAIL_1>>")
    result = verify_pii_protection(receipt, vault_configured=False)
    assert result.vault_active is False
    assert result.token_count == 1


def test_vault_configured_no_tokens():
    """vault_configured=True but no tokens in prompt → vault_active is False."""
    receipt = _FakeReceipt(prompt="Plain text with no tokens")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.vault_active is False
    assert result.token_count == 0


def test_empty_prompt():
    """Empty prompt → zero counts, no leaks."""
    receipt = _FakeReceipt(prompt="")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.vault_active is False
    assert result.token_count == 0
    assert result.leak_count == 0
    assert result.leaked_types == []


def test_content_hash_passthrough():
    """content_hash is passed through from receipt unchanged."""
    receipt = _FakeReceipt(prompt="<<SSN_1>>", content_hash="sha256:deadbeef")
    result = verify_pii_protection(receipt, vault_configured=True)
    assert result.content_hash == "sha256:deadbeef"
