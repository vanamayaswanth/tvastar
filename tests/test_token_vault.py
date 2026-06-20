"""Tests for TokenVault — reversible PII tokenization."""

from __future__ import annotations

from tvastar.assurance import SanitizationPolicy, TokenVault


class TestTokenVault:
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
