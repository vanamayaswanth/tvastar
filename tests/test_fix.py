"""End-to-end tests for the test-fixer app.

These create a REAL temp project with a failing pytest suite and run the actual
fixer loop (real subprocess pytest); only the model's decisions are scripted.
"""

import shutil
import sys
from pathlib import Path

import pytest

from tvastar.fix import fix_tests
from tvastar.model import MockModel

# Use sys.executable to ensure pytest is invocable on all platforms
_PYTEST_CMD = f"{sys.executable} -m pytest -q"

# Skip all tests if pytest can't be invoked via subprocess
_can_run_pytest = shutil.which("pytest") is not None or sys.executable is not None
from tvastar.types import ToolUseBlock

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _project(tmp_path: Path) -> Path:
    (tmp_path / "calc.py").write_text(BUGGY, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(TEST, encoding="utf-8")
    return tmp_path


async def test_fixes_failing_suite_and_verifies(tmp_path):
    proj = _project(tmp_path)
    # Scripted "agent": writes the correct file, then claims done.
    model = MockModel(
        [
            ToolUseBlock(name="write_file", input={"path": "calc.py", "content": FIXED}),
            "Fixed add().",
        ]
    )
    result = await fix_tests(proj, model=model, test_command=_PYTEST_CMD, max_steps=6)
    assert result.fixed is True
    assert result.already_green is False
    # Ground truth: the file on disk is actually corrected.
    assert (proj / "calc.py").read_text() == FIXED


async def test_already_green_is_a_noop(tmp_path):
    proj = _project(tmp_path)
    (proj / "calc.py").write_text(FIXED, encoding="utf-8")  # already correct
    model = MockModel(["should not be needed"])
    result = await fix_tests(proj, model=model, test_command=_PYTEST_CMD)
    assert result.already_green is True
    assert result.fixed is True
    assert result.attempts == 0


async def test_reports_unfixed_when_agent_fails(tmp_path):
    proj = _project(tmp_path)
    # Agent does nothing useful -> tests still fail -> ground truth says unfixed.
    model = MockModel(["I looked at it but changed nothing."])
    result = await fix_tests(proj, model=model, test_command=_PYTEST_CMD, max_steps=3)
    assert result.fixed is False
    assert result.status == "unfixed"
    assert (proj / "calc.py").read_text() == BUGGY  # unchanged


def test_resolve_model_errors_without_config(monkeypatch):

    from tvastar.errors import ModelError
    from tvastar.fix import resolve_model

    for var in (
        "TVASTAR_FIX_MODEL",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    # Force the Ollama probe to fail fast.
    monkeypatch.setattr("tvastar.fix.models._ollama_up", lambda host: False)
    with pytest.raises(ModelError):
        resolve_model()
