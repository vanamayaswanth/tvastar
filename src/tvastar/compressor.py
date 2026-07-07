"""Tool output compression — dedup file reads, truncate shell output.

A post_tool_hook interceptor that reduces tool result size before it enters
the session message history. Uses SHA-256 dedup for file-read tools and
tail-preserving truncation for shell tools. Never breaks a run — exceptions
are swallowed by the hook wrapper in create_agent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class ToolOutputCompressor:
    """Post-tool-hook that compresses tool output to conserve context.

    Dedup logic: if a file-read tool returns content whose SHA-256 matches a
    previously seen result, replaces it with a short reference.

    Truncation logic: if a shell tool returns output exceeding *threshold*
    characters, retains only the tail (last *threshold* chars) with a marker.
    """

    threshold: int = 4000
    _seen_hashes: dict[str, int] = field(default_factory=dict)

    # Tool-name heuristics
    _FILE_KEYWORDS: tuple[str, ...] = ("read", "cat", "file")
    _SHELL_KEYWORDS: tuple[str, ...] = ("shell", "exec", "run", "bash", "cmd")

    def __call__(self, tool_name: str, args: dict, result: str) -> str | None:
        """post_tool_hook signature. Returns compressed string or None (no change)."""
        name_lower = tool_name.lower()

        # Dedup for file-read tools
        if any(kw in name_lower for kw in self._FILE_KEYWORDS):
            hex_digest = hashlib.sha256(result.encode()).hexdigest()
            if hex_digest in self._seen_hashes:
                size = self._seen_hashes[hex_digest]
                return f"[dedup: sha256={hex_digest}, size={size} bytes]"
            self._seen_hashes[hex_digest] = len(result)
            return None

        # Truncation for shell tools
        if any(kw in name_lower for kw in self._SHELL_KEYWORDS):
            if len(result) > self.threshold:
                omitted = len(result) - self.threshold
                compressed = f"[truncated: {omitted} chars omitted]\n{result[-self.threshold :]}"
                # Only return compressed if it actually saves space
                if len(compressed) <= len(result):
                    return compressed
            return None

        return None
