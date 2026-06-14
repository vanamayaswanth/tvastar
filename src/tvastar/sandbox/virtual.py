"""VirtualSandbox — a tiny in-memory shell, the zero-dependency default.

No containers, no daemon, no disk by default — it interprets a small set of
POSIX-ish commands (and runs real Python) against an in-memory
:class:`VirtualFileSystem`. Perfect for tests, demos, serverless, and any place
where spinning a real container is overkill. Memory footprint is just the dict
of files.

Supported: echo, cat, ls, pwd, cd, mkdir, rm, touch, grep, find/glob, wc, head,
tail, write redirection (``>`` / ``>>``), and ``&&`` / ``;`` chaining.
Unsupported commands return a clear error rather than silently no-op'ing.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..errors import SecurityViolation
from ..filesystem.virtual import VirtualFileSystem
from .base import AuditEntry, CredentialFilter, ExecResult, Sandbox, SecurityPolicy, _truncate


class VirtualSandbox(Sandbox):
    """In-memory sandbox. Runs a small shell subset *and* real Python via the
    host interpreter — so code-executing agents work with **no Docker and no
    extra dependencies**, anywhere Python is installed.

    Set ``allow_python=False`` to forbid interpreter execution (pure shell
    subset only), e.g. for fully untrusted models.
    """

    def __init__(
        self,
        files: Optional[dict[str, str]] = None,
        *,
        policy: Optional[SecurityPolicy] = None,
        allow_python: bool = True,
        credential_filter: Optional[CredentialFilter] = None,
    ):
        self.fs = VirtualFileSystem(files)
        self.policy = policy or SecurityPolicy(network=False)
        self.cwd = ""
        self.allow_python = allow_python
        self.credential_filter = credential_filter
        self.audit: list[AuditEntry] = []

    async def exec(
        self,
        cmd: str,
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        try:
            self.policy.check(cmd)
        except SecurityViolation as exc:
            self.audit.append(AuditEntry.blocked(cmd, str(exc)))
            raise
        t0 = time.monotonic()
        # The chain may spawn a (blocking) subprocess for `python`; run the
        # whole thing off the event loop so the harness stays responsive.
        result = await asyncio.to_thread(self._exec_sync, cmd, timeout)
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        self.audit.append(AuditEntry.executed(cmd, exit_code=result.exit_code, duration_ms=elapsed))
        return result

    def _exec_sync(self, cmd: str, timeout: Optional[float]) -> ExecResult:
        out_parts: list[str] = []
        code = 0
        # Split on ; (sequential) and && (conditional).
        for segment, joiner in _split_chain(cmd):
            if joiner == "&&" and code != 0:
                break
            r = self._run_one(segment.strip(), timeout)
            if r.stdout:
                out_parts.append(r.stdout)
            if r.stderr:
                out_parts.append(r.stderr)
            code = r.exit_code
        stdout = "\n".join(p for p in out_parts if p)
        return ExecResult(
            exit_code=code,
            stdout=_truncate(stdout, self.policy.max_output_bytes),
        )

    # ---- command implementations ---------------------------------------

    #: shell names routed to the host Python interpreter
    _PY_COMMANDS = {"python", "python3", "py"}

    def _run_one(self, segment: str, timeout: Optional[float] = None) -> ExecResult:
        if not segment:
            return ExecResult(0, "")
        # Handle output redirection: cmd > file / cmd >> file
        redirect = None
        append = False
        m = re.search(r"\s(>>?)\s*(\S+)\s*$", segment)
        if m:
            append = m.group(1) == ">>"
            redirect = m.group(2)
            segment = segment[: m.start()].strip()

        try:
            argv = shlex.split(segment, posix=True)
        except ValueError as e:
            return ExecResult(2, "", f"parse error: {e}")
        if not argv:
            return ExecResult(0, "")
        name, args = argv[0], argv[1:]

        if name in self._PY_COMMANDS or name == "pytest":
            if not self.allow_python:
                return ExecResult(126, "", f"{name}: python execution disabled")
            res = self._run_python(name, args, timeout)
        else:
            handler = getattr(self, f"_cmd_{name}", None)
            if handler is None:
                return ExecResult(127, "", f"{name}: command not found (virtual sandbox)")
            res = handler(args)
        if redirect is not None and res.exit_code == 0:
            prev = self.fs.read(redirect) if (append and self.fs.exists(redirect)) else ""
            self.fs.write(redirect, prev + res.stdout + ("\n" if res.stdout else ""))
            return ExecResult(0, "")
        return res

    def _run_python(self, name: str, args: list[str], timeout: Optional[float]) -> ExecResult:
        """Run the host interpreter against a temp materialization of the
        in-memory FS, then sync any created/modified files back in.

        This keeps the "no Docker, no setup" promise (it uses the Python that is
        already running the agent) while letting the virtual sandbox execute
        real code. The temp dir is the only thing that touches disk and it is
        removed afterwards, so the sandbox leaves no trace on the host tree.
        """
        timeout = timeout or self.policy.timeout_seconds
        with tempfile.TemporaryDirectory(prefix="tvastar-vsbx-") as tmp:
            root = Path(tmp)
            before: dict[str, float] = {}
            for path, content in self.fs.snapshot().items():
                fp = root / path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content, encoding="utf-8")
                before[path] = fp.stat().st_mtime

            if name == "pytest":
                cmd = [sys.executable, "-m", "pytest", *args]
            else:
                cmd = [sys.executable, *args]

            run_env = dict(os.environ)
            if not self.policy.network:
                run_env.update({"http_proxy": "", "https_proxy": "", "no_proxy": "*"})
            if self.credential_filter is not None:
                run_env = self.credential_filter.filter_env(run_env)
            run_env["PYTHONDONTWRITEBYTECODE"] = "1"

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(root),
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return ExecResult(124, "", f"timed out after {timeout}s", timed_out=True)
            except FileNotFoundError:
                return ExecResult(127, "", f"{name}: interpreter not found")

            # Sync new / changed files back into the virtual FS.
            for fp in root.rglob("*"):
                if not fp.is_file():
                    continue
                rel = fp.relative_to(root).as_posix()
                if "__pycache__" in rel:
                    continue
                if rel not in before or fp.stat().st_mtime > before[rel]:
                    try:
                        self.fs.write(rel, fp.read_text(encoding="utf-8"))
                    except UnicodeDecodeError:
                        pass  # skip binaries

            out = proc.stdout or ""
            if proc.stderr:
                out = (out + "\n" + proc.stderr) if out else proc.stderr
            return ExecResult(proc.returncode, out.strip())

    def snapshot(self) -> dict[str, str]:
        """Snapshot the in-memory filesystem. O(n) copy of file contents."""
        return self.fs.snapshot()

    def restore(self, snap: dict[str, str]) -> None:
        """Restore the filesystem to a previous snapshot, discarding changes."""
        self.fs.restore(snap)

    def _resolve(self, p: str) -> str:
        import posixpath

        if p.startswith("/"):
            return p.lstrip("/")
        return posixpath.normpath(posixpath.join(self.cwd, p)).lstrip("./")

    def _cmd_echo(self, args: list[str]) -> ExecResult:
        return ExecResult(0, " ".join(args))

    def _cmd_pwd(self, args: list[str]) -> ExecResult:
        return ExecResult(0, "/" + self.cwd)

    def _cmd_cd(self, args: list[str]) -> ExecResult:
        self.cwd = self._resolve(args[0]) if args else ""
        return ExecResult(0, "")

    def _cmd_cat(self, args: list[str]) -> ExecResult:
        out = []
        for a in args:
            path = self._resolve(a)
            if not self.fs.exists(path):
                return ExecResult(1, "", f"cat: {a}: No such file")
            out.append(self.fs.read(path))
        return ExecResult(0, "\n".join(out))

    def _cmd_ls(self, args: list[str]) -> ExecResult:
        target = self._resolve(args[-1]) if args and not args[-1].startswith("-") else self.cwd
        entries = self.fs.listdir(target or ".")
        return ExecResult(0, "\n".join(entries))

    def _cmd_mkdir(self, args: list[str]) -> ExecResult:
        return ExecResult(0, "")  # virtual fs is path-based; dirs are implicit

    def _cmd_touch(self, args: list[str]) -> ExecResult:
        for a in args:
            p = self._resolve(a)
            if not self.fs.exists(p):
                self.fs.write(p, "")
        return ExecResult(0, "")

    def _cmd_rm(self, args: list[str]) -> ExecResult:
        for a in args:
            if a.startswith("-"):
                continue
            self.fs.delete(self._resolve(a))
        return ExecResult(0, "")

    def _cmd_grep(self, args: list[str]) -> ExecResult:
        flags = [a for a in args if a.startswith("-")]
        rest = [a for a in args if not a.startswith("-")]
        if not rest:
            return ExecResult(2, "", "grep: missing pattern")
        pattern = rest[0]
        files = rest[1:]
        out = []
        targets = files or self.fs.glob("**/*")
        for f in targets:
            p = self._resolve(f) if files else f
            if not self.fs.exists(p):
                continue
            for i, line in enumerate(self.fs.read(p).splitlines(), 1):
                if re.search(pattern, line):
                    prefix = f"{p}:" if len(targets) > 1 else ""
                    if "-n" in flags:
                        prefix += f"{i}:"
                    out.append(prefix + line)
        return ExecResult(0 if out else 1, "\n".join(out))

    def _cmd_wc(self, args: list[str]) -> ExecResult:
        rest = [a for a in args if not a.startswith("-")]
        if not rest:
            return ExecResult(2, "", "wc: missing file")
        text = self.fs.read(self._resolve(rest[0]))
        lines = len(text.splitlines())
        words = len(text.split())
        chars = len(text)
        if "-l" in args:
            return ExecResult(0, str(lines))
        return ExecResult(0, f"{lines} {words} {chars} {rest[0]}")

    def _cmd_head(self, args: list[str]) -> ExecResult:
        return self._head_tail(args, head=True)

    def _cmd_tail(self, args: list[str]) -> ExecResult:
        return self._head_tail(args, head=False)

    def _head_tail(self, args: list[str], *, head: bool) -> ExecResult:
        n = 10
        files = []
        it = iter(args)
        for a in it:
            if a == "-n":
                n = int(next(it))
            elif a.startswith("-") and a[1:].isdigit():
                n = int(a[1:])
            else:
                files.append(a)
        if not files:
            return ExecResult(2, "", "missing file")
        lines = self.fs.read(self._resolve(files[0])).splitlines()
        sel = lines[:n] if head else lines[-n:]
        return ExecResult(0, "\n".join(sel))

    def _cmd_find(self, args: list[str]) -> ExecResult:
        return ExecResult(0, "\n".join(self.fs.glob("**/*")))


def _split_chain(cmd: str) -> list[tuple[str, str]]:
    """Split a command line into (segment, preceding_joiner) pairs."""
    parts: list[tuple[str, str]] = []
    tokens = re.split(r"(\s*&&\s*|\s*;\s*)", cmd)
    joiner = ""
    for tok in tokens:
        s = tok.strip()
        if s in ("&&", ";"):
            joiner = "&&" if s == "&&" else ";"
        elif s:
            parts.append((s, joiner))
            joiner = ""
    return parts
