"""Property-based test for TokenVault round-trip (Property 22).

**Property 22: TokenVault round-trip**
For any valid input string containing sensitive patterns matching a
SanitizationPolicy, vault.rehydrate(vault.tokenize(text, policy)) SHALL
produce the original string.

**Validates: Requirements 9.5**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar.assurance.sanitize import SanitizationPolicy, TokenVault


# ---------------------------------------------------------------------------
# Strategies: generate text strings with embedded sensitive patterns
# ---------------------------------------------------------------------------

# SSN format: 3 digits - 2 digits - 4 digits
st_ssn = st.from_regex(r"[1-9]\d{2}-\d{2}-\d{4}", fullmatch=True)

# Email format: user@domain.tld
st_email = st.builds(
    lambda user, domain, tld: f"{user}@{domain}.{tld}",
    user=st.from_regex(r"[a-z][a-z0-9._%+]{1,10}", fullmatch=True),
    domain=st.from_regex(r"[a-z][a-z0-9]{1,8}", fullmatch=True),
    tld=st.sampled_from(["com", "org", "net", "edu", "io"]),
)

# Phone format: (xxx) xxx-xxxx or xxx-xxx-xxxx
st_phone = st.one_of(
    st.builds(
        lambda a, b, c: f"({a}) {b}-{c}",
        a=st.from_regex(r"[2-9]\d{2}", fullmatch=True),
        b=st.from_regex(r"\d{3}", fullmatch=True),
        c=st.from_regex(r"\d{4}", fullmatch=True),
    ),
    st.builds(
        lambda a, b, c: f"{a}-{b}-{c}",
        a=st.from_regex(r"[2-9]\d{2}", fullmatch=True),
        b=st.from_regex(r"\d{3}", fullmatch=True),
        c=st.from_regex(r"\d{4}", fullmatch=True),
    ),
)

# IP address format: d.d.d.d (valid-ish octet ranges)
st_ip = st.builds(
    lambda a, b, c, d: f"{a}.{b}.{c}.{d}",
    a=st.integers(min_value=1, max_value=254),
    b=st.integers(min_value=0, max_value=255),
    c=st.integers(min_value=0, max_value=255),
    d=st.integers(min_value=1, max_value=254),
)

# DOB format: MM/DD/YYYY or MM-DD-YYYY
st_dob = st.builds(
    lambda m, d, y, sep: f"{m:02d}{sep}{d:02d}{sep}{y}",
    m=st.integers(min_value=1, max_value=12),
    d=st.integers(min_value=1, max_value=28),
    y=st.integers(min_value=1950, max_value=2005),
    sep=st.sampled_from(["/", "-"]),
)

# Choose one sensitive pattern
st_sensitive_pattern = st.one_of(st_ssn, st_email, st_phone, st_ip, st_dob)

# Surrounding text — safe characters that won't accidentally form patterns
st_safe_text = st.from_regex(r"[A-Za-z ]{0,30}", fullmatch=True)


# Strategy: embed one or more sensitive patterns within safe text
@st.composite
def st_text_with_sensitive_patterns(draw):
    """Generate text containing 1-3 embedded sensitive patterns within safe text."""
    num_patterns = draw(st.integers(min_value=1, max_value=3))
    parts = []
    for i in range(num_patterns):
        prefix = draw(st_safe_text)
        if prefix:
            parts.append(prefix)
        parts.append(draw(st_sensitive_pattern))
    # Optional trailing text
    suffix = draw(st_safe_text)
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(text=st_text_with_sensitive_patterns())
@settings(max_examples=100, deadline=None)
def test_token_vault_round_trip(text: str):
    """Property 22: vault.rehydrate(vault.tokenize(text, policy)) == text.

    **Validates: Requirements 9.5**

    For any valid input string containing sensitive patterns,
    tokenizing then rehydrating produces the original string.
    """
    vault = TokenVault()
    policy = SanitizationPolicy.hipaa()

    # Tokenize should produce something different (patterns were replaced)
    tokenized = vault.tokenize(text, policy)

    # Round-trip: rehydrate must restore the original
    restored = vault.rehydrate(tokenized)
    assert restored == text, (
        f"Round-trip failed:\n"
        f"  original:  {text!r}\n"
        f"  tokenized: {tokenized!r}\n"
        f"  restored:  {restored!r}"
    )


@given(
    ssn=st_ssn,
    email=st_email,
    phone=st_phone,
)
@settings(max_examples=100, deadline=None)
def test_token_vault_round_trip_multiple_pii_types(ssn: str, email: str, phone: str):
    """Property 22 (multi-type variant): covers SSN, email, and phone in one text.

    **Validates: Requirements 9.5**

    Ensures the round-trip holds when multiple PII types coexist in a single string.
    """
    text = f"Contact info SSN {ssn} email {email} phone {phone} end"

    vault = TokenVault()
    policy = SanitizationPolicy.hipaa()

    tokenized = vault.tokenize(text, policy)

    # Verify tokenization actually replaced the sensitive data
    assert ssn not in tokenized, f"SSN {ssn!r} was not tokenized"
    assert email not in tokenized, f"Email {email!r} was not tokenized"

    # Round-trip must restore original
    restored = vault.rehydrate(tokenized)
    assert restored == text, (
        f"Round-trip failed:\n"
        f"  original:  {text!r}\n"
        f"  tokenized: {tokenized!r}\n"
        f"  restored:  {restored!r}"
    )
