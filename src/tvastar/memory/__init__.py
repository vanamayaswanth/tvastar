"""Memory layer: durable key/value + session state stores.

Three store backends share one interface:
* :class:`InMemoryStore` — fast, ephemeral, zero deps (default).
* :class:`FileStore` — JSON-on-disk, survives restarts.
* :class:`SQLiteStore` — SQLite-backed with FTS5 full-text search.

The harness uses a Store to persist session transcripts (durable execution) and
exposes a scoped :class:`Memory` handle to agents/tools for scratch state.
"""

from .sqlite_store import SQLiteStore
from .store import FileStore, InMemoryStore, Memory, Store

__all__ = ["Store", "InMemoryStore", "FileStore", "SQLiteStore", "Memory"]
