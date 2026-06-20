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
from typing import Iterator, List, Optional

from .receipt import ExecutionReceipt

__all__ = ["TrustLog"]


class TrustLog:
    """Append-only, chain-linked ledger of ExecutionReceipts.

    Args:
        path: Path to a JSONL file (one receipt per line). Pass ``None``
              (default) for an in-memory-only log.
    """

    def __init__(self, path: Optional[str] = None):
        self._path: Optional[Path] = Path(path) if path else None
        self._entries: List[ExecutionReceipt] = []
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
        Returns False on the first discrepancy.
        """
        prev_hash = ""
        for receipt in self._entries:
            if not receipt.verify():
                return False
            if receipt.prev_hash != prev_hash:
                return False
            prev_hash = receipt.content_hash
        return True

    # ------------------------------------------------------------------ read

    def get(self, run_id: str) -> Optional[ExecutionReceipt]:
        """Return the receipt with the given run_id, or None."""
        for r in self._entries:
            if r.run_id == run_id:
                return r
        return None

    @property
    def tail_hash(self) -> str:
        """content_hash of the most recent receipt, or '' if the log is empty."""
        return self._entries[-1].content_hash if self._entries else ""

    def __iter__(self) -> Iterator[ExecutionReceipt]:
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
