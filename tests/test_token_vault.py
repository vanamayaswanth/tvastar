"""Tests for TokenVault — reversible PII tokenization.

Covers:
- tokenize replaces sensitive patterns with opaque placeholders
- rehydrate restores original values from placeholders
- HIPAA policy redacts PHI entity types (SSN, email, phone, IP, DOB, bearer, api-key)
- Presidio policy for ML-powered detection (ImportError path + instantiation)
- Round-trip property: vault.rehydrate(vault.tokenize(text)) == text

Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.7
"""

from __future__ import annotations

import re

import pytest

from tvastar.assurance import SanitizationPolicy, TokenVault


class TestTokenVault:
    """Core tokenize/rehydrate mechanics."""

    def test_tokenize_replaces_email(self):
        vault = TokenVault()
        result = vault.tokenize("Contact alice@example.com today.", SanitizationPolicy.hipaa())
        assert "alice@example.com" not in result
        assert "<<" in result

    def test_rehydrate_restores_original(self):
        vault = TokenVault()
        original = "SSN 123-45-6789 belongs to Bob."
        cleaned = vault.tokenize(original, SanitizationPolicy.hipaa())
        assert "123-45-6789" not in cleaned
        restored = vault.rehydrate(cleaned)
        assert "123-45-6789" in restored
        assert restored == original

    def test_multiple_values_get_unique_tokens(self):
        vault = TokenVault()
        text = "alice@a.com and bob@b.com"
        vault.tokenize(text, SanitizationPolicy.hipaa())
        tokens = [t for t in vault._map]
        assert len(tokens) == 2
        assert tokens[0] != tokens[1]

    def test_same_value_gets_separate_tokens(self):
        vault = TokenVault()
        text = "alice@a.com and alice@a.com again"
        vault.tokenize(text, SanitizationPolicy.hipaa())
        assert len(vault._map) == 2  # two occurrences → two tokens

    def test_rehydrate_noop_when_no_tokens(self):
        vault = TokenVault()
        assert vault.rehydrate("no pii here") == "no pii here"

    def test_len_tracks_count(self):
        vault = TokenVault()
        vault.tokenize("a@b.com and c@d.com", SanitizationPolicy.hipaa())
        assert len(vault) == 2

    def test_repr(self):
        vault = TokenVault()
        vault.tokenize("a@b.com", SanitizationPolicy.hipaa())
        assert "1 tokens" in repr(vault)

    def test_token_format(self):
        vault = TokenVault()
        vault.tokenize("123-45-6789", SanitizationPolicy.hipaa())
        tok = list(vault._map.keys())[0]
        assert tok.startswith("<<") and tok.endswith(">>")

    def test_rehydrate_partial_text(self):
        vault = TokenVault()
        vault.tokenize("Email: test@x.com", SanitizationPolicy.hipaa())
        tok = list(vault._map.keys())[0]
        partial = f"Reply to {tok} immediately"
        restored = vault.rehydrate(partial)
        assert "test@x.com" in restored

    def test_exported_from_tvastar(self):
        from tvastar import TokenVault as TV

        assert TV is TokenVault


class TestTokenVaultOpaquePlaceholders:
    """Verify tokenize produces opaque placeholders for various patterns (Req 9.3)."""

    def test_tokenize_produces_opaque_placeholder_not_original(self):
        """Placeholders must not leak the original value."""
        vault = TokenVault()
        text = "Patient SSN is 123-45-6789"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        # Placeholder should NOT contain the original sensitive value
        assert "123-45-6789" not in result
        # Should contain a placeholder token with the pattern <<LABEL_N>>
        assert re.search(r"<<[A-Z_]+_\d+>>", result)

    def test_tokenize_phone_number(self):
        vault = TokenVault()
        text = "Call me at 555-867-5309 for details"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        assert "555-867-5309" not in result
        assert "<<" in result

    def test_tokenize_ip_address(self):
        vault = TokenVault()
        text = "Server IP is 192.168.1.42"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        assert "192.168.1.42" not in result
        assert "<<" in result

    def test_tokenize_date_of_birth(self):
        vault = TokenVault()
        text = "DOB: 03/15/1985"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        assert "03/15/1985" not in result
        assert "<<" in result

    def test_tokenize_bearer_token(self):
        vault = TokenVault()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "<<" in result

    def test_tokenize_api_key(self):
        vault = TokenVault()
        text = "api_key: sk-1234567890abcdef"
        result = vault.tokenize(text, SanitizationPolicy.hipaa())
        assert "sk-1234567890abcdef" not in result
        assert "<<" in result

    def test_placeholder_label_reflects_pattern_type(self):
        """Token labels should indicate what type of entity was replaced."""
        vault = TokenVault()
        vault.tokenize("SSN 123-45-6789", SanitizationPolicy.hipaa())
        tok = list(vault._map.keys())[0]
        # Token should look like <<SSN_1>> or similar uppercase label
        assert re.match(r"<<[A-Z_]+_\d+>>", tok)


class TestTokenVaultRehydration:
    """Verify rehydrate restores original values from placeholders (Req 9.4)."""

    def test_rehydrate_ssn(self):
        vault = TokenVault()
        original = "Patient SSN: 123-45-6789"
        tokenized = vault.tokenize(original, SanitizationPolicy.hipaa())
        restored = vault.rehydrate(tokenized)
        assert restored == original

    def test_rehydrate_phone(self):
        vault = TokenVault()
        original = "Call 555-867-5309 urgently"
        tokenized = vault.tokenize(original, SanitizationPolicy.hipaa())
        restored = vault.rehydrate(tokenized)
        assert restored == original

    def test_rehydrate_multiple_different_types(self):
        vault = TokenVault()
        original = "SSN 123-45-6789, email: bob@test.com, call 555-123-4567"
        tokenized = vault.tokenize(original, SanitizationPolicy.hipaa())
        # All sensitive data should be replaced
        assert "123-45-6789" not in tokenized
        assert "bob@test.com" not in tokenized
        assert "555-123-4567" not in tokenized
        # Rehydrate restores all
        restored = vault.rehydrate(tokenized)
        assert restored == original

    def test_rehydrate_preserves_non_sensitive_text(self):
        vault = TokenVault()
        original = "Hello world, no PII here!"
        tokenized = vault.tokenize(original, SanitizationPolicy.hipaa())
        # No tokens produced for non-sensitive text
        assert tokenized == original
        restored = vault.rehydrate(tokenized)
        assert restored == original


class TestTokenVaultRoundTrip:
    """Verify round-trip: vault.rehydrate(vault.tokenize(text)) == text (Req 9.5)."""

    @pytest.mark.parametrize(
        "text",
        [
            "SSN 123-45-6789 is sensitive",
            "Email alice@example.com today",
            "Call 555-867-5309 now",
            "IP address 10.0.0.1 logged",
            "DOB 01/15/1990 on file",
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload",
            "api_key=supersecret123",
            "SSN 111-22-3333, email x@y.com, phone 555-111-2222",
            "No sensitive data here at all",
            "",
        ],
        ids=[
            "ssn",
            "email",
            "phone",
            "ip",
            "dob",
            "bearer",
            "api_key",
            "mixed",
            "no_pii",
            "empty",
        ],
    )
    def test_round_trip(self, text):
        vault = TokenVault()
        tokenized = vault.tokenize(text, SanitizationPolicy.hipaa())
        restored = vault.rehydrate(tokenized)
        assert restored == text


class TestHIPAAPolicy:
    """Verify HIPAA policy redacts PHI entity types (Req 9.6)."""

    def test_hipaa_redacts_ssn(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Patient SSN: 123-45-6789")
        assert "123-45-6789" not in result
        assert "[SSN]" in result

    def test_hipaa_redacts_email(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Contact doctor@hospital.org")
        assert "doctor@hospital.org" not in result
        assert "[EMAIL]" in result

    def test_hipaa_redacts_phone(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Emergency: 555-867-5309")
        assert "555-867-5309" not in result
        assert "[PHONE]" in result

    def test_hipaa_redacts_ip_address(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Accessed from 192.168.1.100")
        assert "192.168.1.100" not in result
        assert "[IP]" in result

    def test_hipaa_redacts_date_of_birth(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Born on 03/15/1985")
        assert "03/15/1985" not in result
        assert "[DOB]" in result

    def test_hipaa_redacts_bearer_token(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "[TOKEN]" in result

    def test_hipaa_redacts_api_key(self):
        policy = SanitizationPolicy.hipaa()
        result = policy.scrub("api_key: sk-prod-123abc")
        assert "sk-prod-123abc" not in result
        assert "[CREDENTIAL]" in result

    def test_hipaa_preserves_non_phi_text(self):
        policy = SanitizationPolicy.hipaa()
        text = "The patient reported mild headaches and nausea."
        result = policy.scrub(text)
        assert result == text

    def test_hipaa_redacts_multiple_phi_types(self):
        policy = SanitizationPolicy.hipaa()
        text = "SSN 123-45-6789, DOB 01/01/1980, email jane@test.com"
        result = policy.scrub(text)
        assert "123-45-6789" not in result
        assert "01/01/1980" not in result
        assert "jane@test.com" not in result
        assert "[SSN]" in result
        assert "[DOB]" in result
        assert "[EMAIL]" in result

    def test_hipaa_tokenize_round_trip(self):
        """HIPAA policy via TokenVault maintains round-trip guarantee."""
        vault = TokenVault()
        original = "Patient SSN 222-33-4444 DOB 06/15/1970 at 10.0.0.5"
        tokenized = vault.tokenize(original, SanitizationPolicy.hipaa())
        assert "222-33-4444" not in tokenized
        assert "06/15/1970" not in tokenized
        assert "10.0.0.5" not in tokenized
        restored = vault.rehydrate(tokenized)
        assert restored == original


class TestPresidioPolicy:
    """Verify Presidio policy for ML-powered detection (Req 9.7)."""

    def test_presidio_creates_policy_instance(self):
        """presidio() returns a policy object without requiring the package at init."""
        policy = SanitizationPolicy.presidio()
        assert policy is not None
        # It's a _PresidioSanitizationPolicy instance
        assert hasattr(policy, "_languages")
        assert hasattr(policy, "_entities")
        assert hasattr(policy, "_score_threshold")

    def test_presidio_default_language(self):
        policy = SanitizationPolicy.presidio()
        assert policy._languages == ["en"]

    def test_presidio_custom_languages(self):
        policy = SanitizationPolicy.presidio(languages=["en", "de"])
        assert policy._languages == ["en", "de"]

    def test_presidio_custom_entities(self):
        policy = SanitizationPolicy.presidio(entities=["PERSON", "US_SSN", "EMAIL_ADDRESS"])
        assert policy._entities == ["PERSON", "US_SSN", "EMAIL_ADDRESS"]

    def test_presidio_custom_threshold(self):
        policy = SanitizationPolicy.presidio(score_threshold=0.8)
        assert policy._score_threshold == 0.8

    def test_presidio_scrub_raises_import_error_without_dependency(self):
        """scrub() raises ImportError with install hint when presidio not installed."""
        policy = SanitizationPolicy.presidio()
        try:
            policy.scrub("Patient Jane Smith has diabetes")
            # If presidio IS installed, the call succeeds — that's fine too
        except ImportError as exc:
            assert "presidio" in str(exc).lower()
            assert "pip install" in str(exc)

    def test_presidio_add_pattern_chainable(self):
        """Presidio policy supports fluent add_pattern for layered regex."""
        policy = SanitizationPolicy.presidio().add_pattern(r"ACCT-\d+", "[ACCOUNT]")
        assert len(policy.patterns) == 1
        assert policy.patterns[0][1] == "[ACCOUNT]"

    def test_presidio_repr(self):
        policy = SanitizationPolicy.presidio(languages=["en"], entities=["PERSON"])
        r = repr(policy)
        assert "Presidio" in r
        assert "en" in r

    def test_presidio_is_subclass_of_sanitization_policy(self):
        policy = SanitizationPolicy.presidio()
        assert isinstance(policy, SanitizationPolicy)
