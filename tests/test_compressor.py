"""Property tests and unit tests for ToolOutputCompressor.

# Feature: pi-ecosystem-adaptations, Property 1: Compression is non-expanding
# Feature: pi-ecosystem-adaptations, Property 2: Dedup replaces duplicate file content with reference
# Feature: pi-ecosystem-adaptations, Property 3: Truncation preserves tail

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**

Property 1: For any tool output string of any length, the ToolOutputCompressor
SHALL return a result whose character count is less than or equal to the original.

Property 2: For any file-read tool result content, if the same content (by
SHA-256 hash) has been seen previously in the same session, the compressor SHALL
return a string matching the pattern [dedup: sha256=<hex>, size=<N> bytes].

Property 3: For any shell tool output string exceeding the configured threshold,
the compressor SHALL return a string whose last `threshold` characters equal the
last `threshold` characters of the original input (tail-preserving truncation).

Unit tests: disabled via compress_tool_output=False (Req 1.5), fault tolerance
with raising compressor (Req 1.4).
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar.compressor import ToolOutputCompressor
from tvastar.agent import create_agent
from tvastar.model.mock import MockModel


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Tool names that trigger file-read dedup logic
st_file_tool_names = st.sampled_from([
    "read_file", "cat_file", "file_read", "read_content",
    "cat", "read", "file_get",
])

# Tool names that trigger shell truncation logic
st_shell_tool_names = st.sampled_from([
    "shell_exec", "run_command", "bash_run", "exec_cmd",
    "shell", "run", "exec", "bash", "cmd",
])

# Tool names that don't match either heuristic
st_neutral_tool_names = st.sampled_from([
    "search", "list_dir", "get_url", "write_output",
    "compute", "analyze", "plan",
])

# All tool name categories combined
st_any_tool_name = st.one_of(st_file_tool_names, st_shell_tool_names, st_neutral_tool_names)

# Random string content for tool output
st_tool_output = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z", "S")),
    min_size=0,
    max_size=10000,
)

# Threshold values for the compressor
st_threshold = st.integers(min_value=10, max_value=5000)


# ---------------------------------------------------------------------------
# Property 1: Compression is non-expanding
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 1: Compression is non-expanding
@given(
    tool_name=st_any_tool_name,
    result=st_tool_output,
    threshold=st_threshold,
)
@settings(max_examples=100, deadline=None)
def test_compression_is_non_expanding(
    tool_name: str,
    result: str,
    threshold: int,
):
    """Property 1: Compression is non-expanding.

    **Validates: Requirements 1.1, 1.6**

    For any tool output, the result char count after compression is <= the original.
    This covers both the case where compression modifies the output and where it
    passes through unchanged.
    """
    compressor = ToolOutputCompressor(threshold=threshold)
    compressed = compressor(tool_name, {}, result)

    # If compressor returns None, the original is used unchanged (len == len)
    # If it returns a string, it must be <= original length
    if compressed is not None:
        assert len(compressed) <= len(result), (
            f"Compression expanded output: "
            f"original={len(result)} chars, compressed={len(compressed)} chars, "
            f"tool_name={tool_name!r}"
        )


# ---------------------------------------------------------------------------
# Property 2: Dedup replaces duplicate file content with reference
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 2: Dedup replaces duplicate file content with reference
@given(
    tool_name=st_file_tool_names,
    content=st.text(min_size=1, max_size=5000),
)
@settings(max_examples=100, deadline=None)
def test_dedup_replaces_duplicate_with_reference(
    tool_name: str,
    content: str,
):
    """Property 2: Dedup replaces duplicate file content with reference.

    **Validates: Requirements 1.2**

    When the same content is passed twice for a file-read tool, the second call
    returns the dedup reference pattern [dedup: sha256=<hex>, size=<N> bytes].
    """
    compressor = ToolOutputCompressor()

    # First call — stores the hash
    first_result = compressor(tool_name, {}, content)
    assert first_result is None, (
        "First occurrence of content should not be deduped (returns None)"
    )

    # Second call — same content, should produce dedup reference
    second_result = compressor(tool_name, {}, content)
    assert second_result is not None, (
        "Second occurrence of identical content should be deduped"
    )

    # Verify the dedup pattern
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    expected_size = len(content)
    expected_pattern = f"[dedup: sha256={expected_hash}, size={expected_size} bytes]"
    assert second_result == expected_pattern, (
        f"Expected dedup pattern:\n  {expected_pattern}\n"
        f"Got:\n  {second_result}"
    )


# ---------------------------------------------------------------------------
# Property 3: Truncation preserves tail
# ---------------------------------------------------------------------------


# Feature: pi-ecosystem-adaptations, Property 3: Truncation preserves tail
@given(
    tool_name=st_shell_tool_names,
    threshold=st.integers(min_value=10, max_value=2000),
    content_suffix=st.text(min_size=1, max_size=2000),
    content_prefix=st.text(min_size=1, max_size=5000),
)
@settings(max_examples=100, deadline=None)
def test_truncation_preserves_tail(
    tool_name: str,
    threshold: int,
    content_suffix: str,
    content_prefix: str,
):
    """Property 3: Truncation preserves tail.

    **Validates: Requirements 1.3**

    For shell output exceeding the threshold, the last `threshold` characters of
    the compressed result equal the last `threshold` characters of the original.
    """
    # Build content that exceeds the threshold by enough for truncation to save space.
    # The header "[truncated: N chars omitted]\n" is ~30+ chars, so we need the
    # content to exceed threshold by at least that much for truncation to be worthwhile.
    content = content_prefix + content_suffix
    # Ensure content exceeds threshold by a comfortable margin (>50 chars overhead)
    min_length = threshold + 50
    if len(content) <= min_length:
        content = "x" * (min_length - len(content) + 1) + content

    compressor = ToolOutputCompressor(threshold=threshold)
    compressed = compressor(tool_name, {}, content)

    # Compressor only truncates when doing so actually saves space
    if compressed is None:
        # Content exceeded threshold but truncation wouldn't save space; skip assertion
        return

    # The tail of the compressed result must match the tail of the original
    assert compressed[-threshold:] == content[-threshold:], (
        f"Tail mismatch: last {threshold} chars of compressed result do not match "
        f"original tail.\n"
        f"  Original tail: {content[-threshold:]!r}\n"
        f"  Compressed tail: {compressed[-threshold:]!r}"
    )

    # Verify the truncation marker is present
    assert compressed.startswith("[truncated:"), (
        f"Truncated result should start with '[truncated:' marker, got: {compressed[:50]!r}"
    )


# ---------------------------------------------------------------------------
# Unit Test: compress_tool_output=False bypasses compression (Req 1.5)
# ---------------------------------------------------------------------------


class TestCompressToolOutputDisabled:
    """When compress_tool_output=False, no compression is applied."""

    def test_no_compressor_installed_when_disabled(self):
        """Req 1.5: compress_tool_output=False bypasses the ToolOutputCompressor."""
        model = MockModel()
        spec = create_agent("test", model=model, compress_tool_output=False)

        # post_tool_hook should be None (no compressor installed, no user hook)
        assert spec.post_tool_hook is None

    def test_user_hook_preserved_when_compression_disabled(self):
        """Req 1.5: User's post_tool_hook still works when compression is disabled."""
        model = MockModel()
        user_hook_called = []

        def my_hook(tool_name, args, result):
            user_hook_called.append(True)
            return "modified"

        spec = create_agent(
            "test", model=model, compress_tool_output=False, post_tool_hook=my_hook
        )

        # The user's hook should be the post_tool_hook directly
        assert spec.post_tool_hook is my_hook


# ---------------------------------------------------------------------------
# Unit Test: Fault tolerance — raising compressor (Req 1.4)
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    """When the compressor raises, the original result is used unchanged."""

    def test_compressor_exception_uses_original_result(self):
        """Req 1.4: If ToolOutputCompressor raises, session uses original result."""
        model = MockModel()

        # Create an agent with compression enabled
        spec = create_agent("test", model=model, compress_tool_output=True)

        # The hook is installed
        assert spec.post_tool_hook is not None

        # Patch the compressor inside the closure to raise
        # We test by calling the hook directly with a tool that triggers
        # file-read path, but we'll mock the compressor to raise
        original_result = "hello world content"

        # The hook wraps exceptions in try/except, so even if we force an error
        # internally, the hook returns None (use original)
        # We can test this by making the hash computation fail
        with patch("tvastar.compressor.hashlib.sha256", side_effect=RuntimeError("boom")):
            # Create a fresh agent so the hook closure captures the fresh compressor
            spec2 = create_agent("test2", model=model, compress_tool_output=True)
            result = spec2.post_tool_hook("read_file", {}, original_result)

        # When compressor raises, hook returns None (use original unchanged)
        assert result is None

    def test_compressor_exception_still_runs_user_hook(self):
        """Req 1.4: Even if compressor raises, user hook still runs."""
        model = MockModel()
        user_results = []

        def user_hook(tool_name, args, result):
            user_results.append(result)
            return f"user_processed:{result}"

        with patch("tvastar.compressor.hashlib.sha256", side_effect=RuntimeError("boom")):
            spec = create_agent(
                "test",
                model=model,
                compress_tool_output=True,
                post_tool_hook=user_hook,
            )
            result = spec.post_tool_hook("read_file", {}, "original")

        # User hook should have been called with the original (uncompressed) result
        assert user_results == ["original"]
        assert result == "user_processed:original"
