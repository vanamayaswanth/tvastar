"""Recordable demo of `tvastar-fix` — turn a red test suite green.

Creates a throwaway buggy project, shows the bug, runs `tvastar-fix` on it with
whatever model you've configured, and shows the result. Perfect for capturing a
short GIF/asciinema for the README and launch.

    export GROQ_API_KEY=...        # free tier — or run a local `ollama serve`
    uv run python examples/demo_fix.py

Tip for a clean recording: a terminal ~100 cols wide, then
    asciinema rec demo.cast -c "uv run python examples/demo_fix.py"
(or just screen-record the window).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUGGY = """\
def add(a, b):
    return a - b        # bug

def multiply(a, b):
    return a + b        # bug
"""

TESTS = """\
from calc import add, multiply


def test_add():
    assert add(2, 3) == 5


def test_multiply():
    assert multiply(4, 5) == 20
"""


def _type(line: str = "", pause: float = 0.4) -> None:
    print(line)
    time.sleep(pause)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    work = Path(tempfile.mkdtemp(prefix="tvastar-demo-"))
    (work / "calc.py").write_text(BUGGY, encoding="utf-8")
    (work / "test_calc.py").write_text(TESTS, encoding="utf-8")

    try:
        _type("$ cat calc.py        # two bugs: subtracts instead of adds, etc.")
        _type(BUGGY)
        _type("$ pytest -q          # red", 0.2)
        subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=work)
        _type("")
        _type("$ tvastar-fix        # let an agent fix it (verified by re-running)", 0.6)
        rc = subprocess.run(
            ["tvastar-fix", "--path", str(work), "--test-cmd", "pytest -q"],
        ).returncode
        _type("")
        _type("$ cat calc.py        # after", 0.2)
        _type((work / "calc.py").read_text(encoding="utf-8"))
        _type("$ pytest -q          # green ✅", 0.2)
        subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=work)
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
