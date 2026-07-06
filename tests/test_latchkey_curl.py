"""Property-based and unit tests for latchkey_curl tool.

Property 14: latchkey_curl command construction
- For any valid URL, HTTP method, list of headers, and optional body, the
  subprocess command starts with ["latchkey", "curl", "-X", method] and
  ends with URL.

**Validates: Requirements 5.2**

Property 15: latchkey_curl non-zero exit handling
- For any subprocess execution returning a non-zero exit code, the tool returns
  a string containing stderr if non-empty, or a message with the exit code if
  stderr is empty.

**Validates: Requirements 5.4**
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import hypothesis.strategies as st
from hypothesis import given, settings


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

http_methods = st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])

# Generate valid URLs (http or https with a host)
urls = st.from_regex(r"https?://[a-z][a-z0-9]{0,20}\.[a-z]{2,4}(/[a-z0-9]{1,10}){0,3}", fullmatch=True)

# Generate header strings like "Content-Type: application/json"
header_keys = st.from_regex(r"[A-Z][a-zA-Z\-]{1,20}", fullmatch=True)
header_values = st.from_regex(r"[a-zA-Z0-9/;=\-\. ]{1,30}", fullmatch=True)
headers = st.lists(
    st.tuples(header_keys, header_values).map(lambda kv: f"{kv[0]}: {kv[1]}"),
    min_size=0,
    max_size=5,
)

# Optional body content
bodies = st.one_of(st.none(), st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))))


# ---------------------------------------------------------------------------
# Property 14: latchkey_curl command construction
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    url=urls,
    method=http_methods,
    hdrs=headers,
    body=bodies,
)
def test_latchkey_curl_command_construction(url: str, method: str, hdrs: list[str], body: str | None):
    """Property 14: latchkey_curl command construction.

    For any valid URL, HTTP method, list of headers, and optional body,
    the subprocess command starts with ["latchkey", "curl", "-X", method]
    and the URL is the last element.

    **Validates: Requirements 5.2**
    """
    from tvastar.tools.latchkey import latchkey_curl

    captured_cmd = None

    def fake_subprocess_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ok"
        result.stderr = ""
        return result

    with patch("tvastar.tools.latchkey.subprocess.run", side_effect=fake_subprocess_run):
        latchkey_curl.fn(url, method=method, headers=hdrs if hdrs else None, body=body)

    assert captured_cmd is not None, "subprocess.run was not called"

    # Command must start with ["latchkey", "curl", "-X", method]
    assert captured_cmd[:4] == ["latchkey", "curl", "-X", method], (
        f"Command prefix mismatch: {captured_cmd[:4]}"
    )

    # URL must be the last element
    assert captured_cmd[-1] == url, (
        f"URL should be last element. Got {captured_cmd[-1]!r}, expected {url!r}"
    )


# ---------------------------------------------------------------------------
# Property 15: latchkey_curl non-zero exit handling
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    exit_code=st.integers(min_value=1, max_value=255),
    stderr_content=st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
)
def test_latchkey_curl_nonzero_exit_handling(exit_code: int, stderr_content: str):
    """Property 15: latchkey_curl non-zero exit handling.

    For any subprocess execution returning a non-zero exit code, the tool
    returns stderr content if non-empty, or an exit code message if stderr
    is empty.

    **Validates: Requirements 5.4**
    """
    from tvastar.tools.latchkey import latchkey_curl

    mock_result = MagicMock()
    mock_result.returncode = exit_code
    mock_result.stdout = ""
    mock_result.stderr = stderr_content

    with patch("tvastar.tools.latchkey.subprocess.run", return_value=mock_result):
        result = latchkey_curl.fn("https://example.com", method="GET")

    if stderr_content.strip():
        # Non-empty stderr → result should contain the stderr content
        assert result == stderr_content, (
            f"Expected stderr content {stderr_content!r}, got {result!r}"
        )
    else:
        # Empty stderr → result should indicate exit code
        assert str(exit_code) in result, (
            f"Expected exit code {exit_code} in result {result!r}"
        )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_latchkey_curl_binary_not_found():
    """Unit test: FileNotFoundError returns appropriate error message.

    **Validates: Requirements 5.3**
    """
    from tvastar.tools.latchkey import latchkey_curl

    with patch("tvastar.tools.latchkey.subprocess.run", side_effect=FileNotFoundError):
        result = latchkey_curl.fn("https://example.com")

    assert result == "[error] latchkey is not installed or not on PATH"


def test_latchkey_curl_timeout():
    """Unit test: TimeoutExpired returns appropriate error message.

    **Validates: Requirements 5.5**
    """
    from tvastar.tools.latchkey import latchkey_curl

    exc = subprocess.TimeoutExpired(cmd=["latchkey", "curl"], timeout=30)
    with patch("tvastar.tools.latchkey.subprocess.run", side_effect=exc):
        result = latchkey_curl.fn("https://example.com")

    assert result == "[error] request timed out after 30s"


def test_latchkey_curl_not_in_default_toolset():
    """Unit test: latchkey_curl is NOT included in default_toolset().

    **Validates: Requirements 5.7**
    """
    from tvastar import default_toolset

    tools = default_toolset()
    tool_names = [t.name for t in tools]
    assert "latchkey_curl" not in tool_names, (
        "latchkey_curl should not be in default_toolset (opt-in only)"
    )
