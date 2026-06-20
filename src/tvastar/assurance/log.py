"""TrustLog — append-only, chain-linked receipt ledger.

A TrustLog stores :class:`~tvastar.assurance.receipt.ExecutionReceipt` objects
in an append-only (WORM) sequence. Each receipt embeds the ``content_hash``
of the receipt before it, forming a tamper-evident linked list:

    receipt[0] → prev_hash=""
    receipt[1] → prev_hash=receipt[0].content_hash
    receipt[2] → prev_hash=receipt[1].content_hash
    ...

Modify any entry and ``verify_chain()`` catches it immediately.

Two backends are supported:

- **In-memory** (``TrustLog()`` — default): fast, ephemeral. Useful for tests
  and short-lived processes.
- **File-backed** (``TrustLog(".tvastar-trust.jsonl")``): JSONL file, one
  receipt per line. The file is opened in append mode — existing entries are
  never overwritten.

Usage::

    log = TrustLog(".tvastar-trust.jsonl")

    # Append receipts
    log.append(receipt)

    # Verify nothing was tampered with
    assert log.verify_chain()

    # Iterate
    for receipt in log:
        print(receipt.run_id, receipt.quality_grade)

    # Look up a specific run
    r = log.get("run_abc123")

    # Count
    print(len(log))
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterator, List, Optional

from .receipt import ExecutionReceipt

__all__ = ["TrustLog"]


class TrustLog:
    """Append-only, chain-linked ledger of ExecutionReceipts.

    Args:
        path:      Path to a JSONL file (one receipt per line). Pass ``None``
                   (default) for an in-memory-only log.
        on_breach: Callable invoked when ``verify_chain()`` detects tampering.
                   Receives the first corrupted receipt. Use this to alert,
                   quarantine, or notify — satisfying regulatory incident-
                   response requirements. Can be sync or async.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        on_breach: Optional[Callable[[ExecutionReceipt], None]] = None,
        can_read: Optional[Callable[[str], bool]] = None,
    ):
        self._path: Optional[Path] = Path(path) if path else None
        self._entries: List[ExecutionReceipt] = []
        self._on_breach = on_breach
        self._can_read = can_read  # callable(role: str) -> bool; None = open access
        if self._path and self._path.exists():
            self._load()

    # ------------------------------------------------------------------ write

    def append(self, receipt: ExecutionReceipt) -> None:
        """Append a receipt to the log. Raises ValueError on chain corruption."""
        expected_prev = self._entries[-1].content_hash if self._entries else ""
        if receipt.prev_hash != expected_prev:
            raise ValueError(
                f"Receipt chain broken: expected prev_hash={expected_prev!r}, "
                f"got {receipt.prev_hash!r}"
            )
        self._entries.append(receipt)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(receipt.to_json() + "\n")

    # ------------------------------------------------------------------ verify

    def verify_chain(self) -> bool:
        """Return True if every receipt's hash and chain link is intact.

        Walks every receipt in order, recomputing its content_hash and checking
        that its prev_hash matches the preceding receipt's content_hash.
        On the first discrepancy, fires ``on_breach(receipt)`` (if configured)
        before returning False — satisfying regulatory incident-response
        requirements.
        """
        prev_hash = ""
        for receipt in self._entries:
            if not receipt.verify() or receipt.prev_hash != prev_hash:
                self._fire_breach(receipt)
                return False
            prev_hash = receipt.content_hash
        return True

    def _fire_breach(self, receipt: ExecutionReceipt) -> None:
        """Invoke on_breach callback synchronously or schedule if async."""
        if self._on_breach is None:
            return
        import inspect

        if inspect.iscoroutinefunction(self._on_breach):
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._on_breach(receipt))
            except RuntimeError:
                # No running loop — run synchronously
                asyncio.run(self._on_breach(receipt))
        else:
            self._on_breach(receipt)

    # ------------------------------------------------------------------ read

    def _check_read(self, role: str = "") -> None:
        """Raise PermissionError if *role* is not allowed to read this log."""
        if self._can_read is not None and not self._can_read(role):
            raise PermissionError(
                f"Role {role!r} is not permitted to read this TrustLog. "
                "Configure can_read= to grant access."
            )

    def get(self, run_id: str, *, role: str = "") -> Optional[ExecutionReceipt]:
        """Return the receipt with the given run_id, or None.

        Args:
            run_id: The run identifier to look up.
            role:   Caller's role string. Checked against ``can_read`` if set.
        """
        self._check_read(role)
        for r in self._entries:
            if r.run_id == run_id:
                return r
        return None

    def iter_as(self, role: str) -> Iterator[ExecutionReceipt]:
        """Iterate entries as a specific role (access-controlled).

        Args:
            role: Caller's role string. Checked against ``can_read``.
        """
        self._check_read(role)
        return iter(list(self._entries))

    @property
    def tail_hash(self) -> str:
        """content_hash of the most recent receipt, or '' if the log is empty."""
        return self._entries[-1].content_hash if self._entries else ""

    def __iter__(self) -> Iterator[ExecutionReceipt]:
        """Iterate all entries (no access control — use iter_as() for gated access)."""
        return iter(list(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        path_str = str(self._path) if self._path else "in-memory"
        return f"TrustLog({path_str!r}, entries={len(self._entries)})"

    # ------------------------------------------------------------------ I/O

    def _load(self) -> None:
        """Load receipts from an existing JSONL file (skip corrupt lines)."""
        assert self._path is not None
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._entries.append(ExecutionReceipt.from_json(line))
                except (json.JSONDecodeError, KeyError):
                    pass  # skip malformed entries

    def to_jsonl(self) -> str:
        """Export the full log as a JSONL string."""
        return "\n".join(r.to_json() for r in self._entries)
