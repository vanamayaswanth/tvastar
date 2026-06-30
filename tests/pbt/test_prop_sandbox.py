"""Property-based tests for SecurityPolicy enforcement.

Property 9: SecurityPolicy enforcement
- For any command and SecurityPolicy, if the command matches any denied_substrings
  entry, OR its first token is in denied_commands, OR (allowed_commands is non-empty
  AND first token is absent), THEN SecurityViolation SHALL be raised.
- Conversely, when none of these conditions hold, no exception is raised.

**Validates: Requirements 4.1, 4.2, 4.3**
"""

from __future__ import annotations

import shlex

import hypothesis.strategies as st
from hypothesis import given, settings, assume

import pytest

from tvastar.errors import SecurityViolation
from tvastar.sandbox import SecurityPolicy


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Simple command tokens: alphanumeric identifiers (like real command names)
st_command_token = st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True)

# Arguments: simple strings without problematic quoting
st_arg = st.from_regex(r"[a-zA-Z0-9_./-]{1,20}", fullmatch=True)


@st.composite
def st_command(draw: st.DrawFn) -> str:
    """Generate a simple command string: a command token followed by 0-3 args."""
    cmd = draw(st_command_token)
    args = draw(st.lists(st_arg, min_size=0, max_size=3))
    return " ".join([cmd] + args)


@st.composite
def st_denied_substrings(draw: st.DrawFn) -> set[str]:
    """Generate a set of denied substrings (1-3 short strings)."""
    subs = draw(
        st.lists(
            st.from_regex(r"[a-z_]{2,8}", fullmatch=True),
            min_size=1,
            max_size=3,
        )
    )
    return set(subs)


@st.composite
def st_command_set(draw: st.DrawFn) -> set[str]:
    """Generate a set of command tokens (1-5 items)."""
    cmds = draw(
        st.lists(st_command_token, min_size=1, max_size=5, unique=True)
    )
    return set(cmds)


def _first_token(cmd: str) -> str:
    """Extract the first token from a command string, matching SecurityPolicy logic."""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    return tokens[0] if tokens else ""


# ---------------------------------------------------------------------------
# Property 9: SecurityViolation raised when denied_substrings match
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    denied_sub=st.from_regex(r"[a-z_]{2,8}", fullmatch=True),
    prefix=st.from_regex(r"[a-z]{1,5}", fullmatch=True),
    suffix=st.from_regex(r"[a-z0-9 ]{0,10}", fullmatch=True),
)
def test_denied_substrings_raises_violation(denied_sub: str, prefix: str, suffix: str):
    """Property 9 (partial): SecurityViolation raised for denied_substrings match.

    For any command containing a denied_substring, SecurityPolicy.check() SHALL
    raise SecurityViolation.

    **Validates: Requirements 4.1**
    """
    # Construct a command that contains the denied substring
    command = f"{prefix} {denied_sub} {suffix}".strip()
    policy = SecurityPolicy(
        denied_substrings={denied_sub},
        allowed_commands=set(),
        denied_commands=set(),
    )
    with pytest.raises(SecurityViolation):
        policy.check(command)


# ---------------------------------------------------------------------------
# Property 9: SecurityViolation raised when allowlist violated
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    command=st_command(),
    allowed=st_command_set(),
)
def test_allowed_commands_violation_raises(command: str, allowed: set[str]):
    """Property 9 (partial): SecurityViolation raised when first token not in allowed_commands.

    For any non-empty allowed_commands set where the command's first token is NOT
    in the set, SecurityPolicy.check() SHALL raise SecurityViolation.

    **Validates: Requirements 4.2**
    """
    first = _first_token(command)
    assume(first not in allowed)
    assume(len(allowed) > 0)

    policy = SecurityPolicy(
        denied_substrings=set(),
        allowed_commands=allowed,
        denied_commands=set(),
    )
    with pytest.raises(SecurityViolation):
        policy.check(command)


# ---------------------------------------------------------------------------
# Property 9: SecurityViolation raised when denied_commands match
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    command=st_command(),
    extra_denied=st_command_set(),
)
def test_denied_commands_raises_violation(command: str, extra_denied: set[str]):
    """Property 9 (partial): SecurityViolation raised when first token in denied_commands.

    For any command whose first token is in denied_commands,
    SecurityPolicy.check() SHALL raise SecurityViolation.

    **Validates: Requirements 4.3**
    """
    first = _first_token(command)
    # Ensure the first token is in the denied set
    denied = extra_denied | {first}

    policy = SecurityPolicy(
        denied_substrings=set(),
        allowed_commands=set(),
        denied_commands=denied,
    )
    with pytest.raises(SecurityViolation):
        policy.check(command)


# ---------------------------------------------------------------------------
# Property 9: No exception when all checks pass
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    command=st_command(),
    extra_allowed=st_command_set(),
    denied_subs=st.just(set()),
)
def test_no_violation_when_all_checks_pass(
    command: str, extra_allowed: set[str], denied_subs: set[str]
):
    """Property 9 (converse): No SecurityViolation when all conditions pass.

    For any command where:
    - No denied_substrings match the command
    - allowed_commands is empty OR first token IS in allowed_commands
    - first token is NOT in denied_commands
    THEN SecurityPolicy.check() SHALL NOT raise.

    **Validates: Requirements 4.1, 4.2, 4.3**
    """
    first = _first_token(command)
    assume(first != "")

    # Build an allowlist that includes the first token (or leave empty)
    allowed = extra_allowed | {first}

    # Ensure denied_commands does NOT include the first token
    denied_commands: set[str] = set()

    # Ensure no denied_substrings match the command
    denied_substrings: set[str] = set()

    policy = SecurityPolicy(
        denied_substrings=denied_substrings,
        allowed_commands=allowed,
        denied_commands=denied_commands,
    )
    # Should not raise
    policy.check(command)


# ---------------------------------------------------------------------------
# Property 9: Combined — any single violation condition triggers raise
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    command=st_command(),
    violation_type=st.sampled_from(["denied_substring", "allowlist", "denylist"]),
    extra_allowed=st_command_set(),
)
def test_any_violation_condition_raises(
    command: str, violation_type: str, extra_allowed: set[str]
):
    """Property 9 (combined): ANY single violation condition triggers SecurityViolation.

    For any command and violation type, if the matching condition is met,
    SecurityViolation is raised regardless of other policy settings.

    **Validates: Requirements 4.1, 4.2, 4.3**
    """
    first = _first_token(command)
    assume(first != "")

    if violation_type == "denied_substring":
        # Pick a substring of the command that's at least 2 chars
        assume(len(command) >= 2)
        # Use the first token as a denied substring (it's guaranteed in the command)
        denied_sub = first
        policy = SecurityPolicy(
            denied_substrings={denied_sub},
            allowed_commands=set(),
            denied_commands=set(),
        )
    elif violation_type == "allowlist":
        # Build an allowlist that does NOT include the first token
        allowed = extra_allowed - {first}
        assume(len(allowed) > 0)
        policy = SecurityPolicy(
            denied_substrings=set(),
            allowed_commands=allowed,
            denied_commands=set(),
        )
    else:  # denylist
        policy = SecurityPolicy(
            denied_substrings=set(),
            allowed_commands=set(),
            denied_commands={first},
        )

    with pytest.raises(SecurityViolation):
        policy.check(command)


# ---------------------------------------------------------------------------
# Property 10: CredentialFilter completeness
# ---------------------------------------------------------------------------

import fnmatch

from tvastar.sandbox.base import CredentialFilter


# Env var names: uppercase letters, digits, and underscores (realistic env var names)
st_env_var_name = st.from_regex(r"[A-Z][A-Z0-9_]{0,29}", fullmatch=True)

# Env var values: arbitrary short text
st_env_var_value = st.text(min_size=0, max_size=50)

# Glob patterns that are realistic for credential filtering
st_glob_pattern = st.one_of(
    # Suffix patterns: *_SUFFIX
    st.sampled_from(["*_KEY", "*_SECRET", "*_TOKEN", "*_PASSWORD", "*_PASS"]),
    # Prefix patterns: PREFIX_*
    st.sampled_from(["AWS_*", "GCP_*", "AZURE_*", "DB_*", "REDIS_*"]),
    # Connection patterns
    st.sampled_from(["*_URL", "*_URI", "*_DSN"]),
    # Exact-match patterns (no wildcards)
    st.sampled_from(["PGPASSWORD", "PGPASSFILE", "DATABASE_URL"]),
)


def _names_matching_pattern(pattern: str) -> list[str]:
    """Return example env var names that definitely match a given glob pattern."""
    p_upper = pattern.upper()

    if p_upper == "*_KEY":
        return ["API_KEY", "AWS_ACCESS_KEY", "SECRET_KEY", "MY_KEY"]
    elif p_upper == "*_SECRET":
        return ["CLIENT_SECRET", "APP_SECRET", "MY_SECRET"]
    elif p_upper == "*_TOKEN":
        return ["AUTH_TOKEN", "ACCESS_TOKEN", "REFRESH_TOKEN"]
    elif p_upper == "*_PASSWORD":
        return ["DB_PASSWORD", "ADMIN_PASSWORD", "ROOT_PASSWORD"]
    elif p_upper == "*_PASS":
        return ["DB_PASS", "ADMIN_PASS", "ROOT_PASS"]
    elif p_upper == "*_URL":
        return ["DATABASE_URL", "REDIS_URL", "API_URL"]
    elif p_upper == "*_URI":
        return ["MONGO_URI", "CONNECTION_URI", "SERVICE_URI"]
    elif p_upper == "*_DSN":
        return ["SENTRY_DSN", "DATABASE_DSN", "LOGGING_DSN"]
    elif p_upper.startswith("AWS_"):
        return ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]
    elif p_upper.startswith("GCP_"):
        return ["GCP_PROJECT", "GCP_KEY", "GCP_TOKEN"]
    elif p_upper.startswith("AZURE_"):
        return ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SECRET"]
    elif p_upper.startswith("DB_"):
        return ["DB_HOST", "DB_PASSWORD", "DB_USER"]
    elif p_upper.startswith("REDIS_"):
        return ["REDIS_URL", "REDIS_PASSWORD", "REDIS_HOST"]
    elif p_upper == "PGPASSWORD":
        return ["PGPASSWORD"]
    elif p_upper == "PGPASSFILE":
        return ["PGPASSFILE"]
    elif p_upper == "DATABASE_URL":
        return ["DATABASE_URL"]
    else:
        # For patterns with wildcards, replace * with filler
        if "*" in p_upper:
            return [p_upper.replace("*", "MY")]
        return [p_upper]


@st.composite
def st_env_and_patterns(draw: st.DrawFn) -> tuple[dict[str, str], list[str]]:
    """Generate a random env dict and a set of glob patterns.

    Ensures at least some env var names will match the patterns by
    constructing names that definitely match alongside random ones.
    """
    patterns = draw(st.lists(st_glob_pattern, min_size=1, max_size=5))

    # Generate some env vars that definitely match the patterns
    matching_names: list[str] = []
    for pat in patterns:
        name = draw(st.sampled_from(_names_matching_pattern(pat)))
        matching_names.append(name)

    # Generate additional random env var names (may or may not match)
    random_names = draw(st.lists(st_env_var_name, min_size=0, max_size=10))

    all_names = list(set(matching_names + random_names))
    env = {name: draw(st_env_var_value) for name in all_names}

    return env, patterns


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_credential_filter_completeness(data: st.DataObject) -> None:
    """Property 10: After filtering, no remaining key matches any configured pattern.

    **Validates: Requirements 4.4**

    For any environment variable set and CredentialFilter with glob patterns,
    no variable whose name matches any pattern shall remain after filtering.
    """
    env, patterns = data.draw(st_env_and_patterns())

    cred_filter = CredentialFilter(patterns=patterns)
    filtered = cred_filter.filter_env(env)

    # Verify completeness: no remaining key matches any pattern
    upper_patterns = [p.upper() for p in patterns]
    for key in filtered:
        for pat in upper_patterns:
            assert not fnmatch.fnmatch(key.upper(), pat), (
                f"Key {key!r} matches pattern {pat!r} but was not removed by filter"
            )


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_credential_filter_preserves_non_matching(data: st.DataObject) -> None:
    """Property 10 (preservation): Non-matching keys are preserved after filtering.

    **Validates: Requirements 4.4**

    The CredentialFilter must not remove keys that do NOT match any of its
    configured glob patterns. This ensures the filter doesn't over-remove.
    """
    env, patterns = data.draw(st_env_and_patterns())

    cred_filter = CredentialFilter(patterns=patterns)
    filtered = cred_filter.filter_env(env)

    # Verify preservation: every non-matching key from original is in the result
    upper_patterns = [p.upper() for p in patterns]
    for key, value in env.items():
        matches_any = any(fnmatch.fnmatch(key.upper(), pat) for pat in upper_patterns)
        if not matches_any:
            assert key in filtered, (
                f"Key {key!r} does not match any pattern but was removed by filter"
            )
            assert filtered[key] == value, (
                f"Key {key!r} value changed from {value!r} to {filtered[key]!r}"
            )
