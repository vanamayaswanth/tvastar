"""Key/value stores and a scoped Memory handle.

Stores are namespaced by key prefix so one backend can hold many sessions. The
FileStore writes one JSON file per key under a root dir — simple, debuggable
(you can ``cat`` any record), and crash-safe via atomic replace.
"""

from __future__ import annotations

import abc
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional


class Store(abc.ABC):
    @abc.abstractmethod
    def get(self, key: str) -> Optional[Any]: ...

    @abc.abstractmethod
    def set(self, key: str, value: Any) -> None: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def keys(self, prefix: str = "") -> list[str]: ...


class InMemoryStore(Store):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return sorted(k for k in self._data if k.startswith(prefix))


class FileStore(Store):
    """JSON-per-key store. Keys are sanitized into safe filenames."""

    def __init__(self, root: str | Path = ".tvastar-state"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, key: str) -> Path:
        safe = key.replace(":", "%3A").replace("/", "__").replace("\\", "__")
        return self.root / f"{safe}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, p)  # atomic on POSIX & Windows

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def keys(self, prefix: str = "") -> list[str]:
        out = []
        for p in self.root.glob("*.json"):
            key = p.stem.replace("__", "/").replace("%3A", ":")
            if key.startswith(prefix):
                out.append(key)
        return sorted(out)


class Memory:
    """A namespaced scratchpad handle handed to tools/agents.

    Backed by a Store under the ``mem:<scope>:`` prefix. Values must be
    JSON-serializable so they survive a FileStore round-trip.
    """

    def __init__(self, store: Store, scope: str):
        self._store = store
        self._prefix = f"mem:{scope}:"

    def get(self, key: str, default: Any = None) -> Any:
        v = self._store.get(self._prefix + key)
        return default if v is None else v

    def set(self, key: str, value: Any) -> None:
        self._store.set(self._prefix + key, value)

    def delete(self, key: str) -> None:
        self._store.delete(self._prefix + key)

    def all(self) -> dict[str, Any]:
        n = len(self._prefix)
        return {k[n:]: self._store.get(k) for k in self._store.keys(self._prefix)}
