"""Key/value stores and a scoped Memory handle.

Stores are namespaced by key prefix so one backend can hold many sessions. The
FileStore writes one JSON file per key under a root dir — simple, debuggable
(you can ``cat`` any record), and crash-safe via atomic replace.
"""

from __future__ import annotations

import abc
import base64
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional


def _make_fernet(key: Optional[str]):
    """Return a Fernet instance derived from *key*, or None if key is falsy.

    Key may be a literal string or ``"env:VAR_NAME"`` to read from the env.
    Requires ``pip install cryptography``.
    """
    if not key:
        return None
    raw = os.environ.get(key[4:]) if key.startswith("env:") else key
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
        # Derive a 32-byte Fernet key from the user-supplied string via SHA-256.
        fernet_key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(fernet_key)
    except ImportError:
        import warnings
        warnings.warn("pip install cryptography to enable encrypted FileStore", stacklevel=3)
        return None


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


@contextmanager
def _file_lock(path: Path):
    """Exclusive cross-process advisory lock via a sibling ``.lock`` file.

    Uses ``msvcrt.locking`` on Windows (detected via import) and
    ``fcntl.flock`` on POSIX. Falls back to a no-op when neither is
    importable or when the lock file cannot be opened, so callers never
    see lock-related exceptions.
    """
    lock_path = path.with_suffix(".lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError:
        yield
        return

    # Detect platform by import, not sys.platform, so static analysers
    # don't flag one branch as unreachable depending on the host OS.
    unlock: Optional[Any] = None
    try:
        try:
            import msvcrt  # Windows only

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            unlock = lambda: msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # noqa: E731
        except (ImportError, OSError):
            try:
                import fcntl  # POSIX only

                fcntl.flock(fd, fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(fd, fcntl.LOCK_UN)  # noqa: E731
            except (ImportError, OSError):
                pass  # best-effort: proceed without cross-process lock
        yield
    finally:
        if unlock is not None:
            try:
                unlock()
            except OSError:
                pass
        os.close(fd)


class FileStore(Store):
    """JSON-per-key store. Keys are sanitized into safe filenames.

    Pass ``key`` to encrypt every record with AES-256-GCM (via
    ``cryptography.fernet.Fernet``). Requires ``pip install cryptography``.
    The key can be any string; it is hashed to a 32-byte Fernet key internally.
    Set ``key="env:MY_VAR"`` to read the key from an environment variable.

    Example::

        store = FileStore(".tvastar-state", key="env:TVASTAR_STORE_KEY")
    """

    def __init__(self, root: str | Path = ".tvastar-state", key: Optional[str] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fernet = _make_fernet(key)

    def _path(self, key: str) -> Path:
        # Use percent-encoding for special characters so keys with literal "__"
        # are not confused with keys that contained "/" (fix #16).
        safe = key.replace(":", "%3A").replace("/", "%2F").replace("\\", "%5C")
        return self.root / f"{safe}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            raw = p.read_bytes()
            text = self._fernet.decrypt(raw).decode("utf-8") if self._fernet else raw.decode("utf-8")
            return json.loads(text)
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        # PID-unique temp name avoids cross-process stomping on a shared .tmp file.
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        with _file_lock(p):
            with self._lock:
                try:
                    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
                    data = self._fernet.encrypt(raw) if self._fernet else raw
                    tmp.write_bytes(data)
                    os.replace(tmp, p)  # atomic on POSIX & Windows
                except Exception:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def keys(self, prefix: str = "") -> list[str]:
        out = []
        for p in self.root.glob("*.json"):
            key = p.stem.replace("%5C", "\\").replace("%2F", "/").replace("%3A", ":")
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
