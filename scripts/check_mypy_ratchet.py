#!/usr/bin/env python3
"""Mypy ratchet: run mypy, compare error count against baseline, fail on regression.

- Runs mypy with --disallow-untyped-defs against src/tvastar/
- Compares error count against mypy_baseline.txt (single integer)
- Fails if new count > baseline
- When baseline reaches 0, switches to --strict and deletes baseline
- 300-second timeout
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "mypy_baseline.txt"
TARGET = "src/tvastar/"
TIMEOUT_SECONDS = 300


def read_baseline() -> int | None:
    """Read baseline error count. Returns None if file doesn't exist (strict mode)."""
    if not BASELINE_FILE.exists():
        return None
    text = BASELINE_FILE.read_text().strip()
    if not text:
        return 0
    return int(text)


def count_errors(output: str) -> int:
    """Count mypy error lines from output."""
    count = 0
    for line in output.splitlines():
        if ": error:" in line:
            count += 1
    return count


def run_mypy(strict: bool) -> tuple[int, str]:
    """Run mypy and return (exit_code, combined_output)."""
    cmd = [sys.executable, "-m", "mypy"]
    if strict:
        cmd.append("--strict")
    else:
        cmd.append("--disallow-untyped-defs")
    cmd.append(TARGET)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        print(f"FAIL: mypy timed out after {TIMEOUT_SECONDS} seconds", file=sys.stderr)
        sys.exit(1)

    output = result.stdout + result.stderr
    return result.returncode, output


def main() -> int:
    baseline = read_baseline()

    if baseline is None:
        # No baseline file = strict mode
        print("Baseline file not found — running mypy in --strict mode")
        exit_code, output = run_mypy(strict=True)
        print(output)
        if exit_code == 0:
            print("PASS: mypy --strict passed with 0 errors")
            return 0
        else:
            error_count = count_errors(output)
            print(f"FAIL: mypy --strict found {error_count} errors")
            return 1

    if baseline == 0:
        # Baseline is 0 — switch to strict mode and delete baseline
        print("Baseline is 0 — switching to --strict mode")
        BASELINE_FILE.unlink()
        exit_code, output = run_mypy(strict=True)
        print(output)
        if exit_code == 0:
            print("PASS: mypy --strict passed with 0 errors (baseline deleted)")
            return 0
        else:
            error_count = count_errors(output)
            print(f"FAIL: mypy --strict found {error_count} errors")
            return 1

    # Normal ratchet mode
    print(f"Baseline: {baseline} errors")
    _, output = run_mypy(strict=False)
    print(output)
    error_count = count_errors(output)
    print(f"Current errors: {error_count} (baseline: {baseline})")

    if error_count > baseline:
        print(f"FAIL: error count regressed ({error_count} > {baseline})")
        return 1
    elif error_count < baseline:
        print(f"PASS: error count improved! Updating baseline {baseline} → {error_count}")
        BASELINE_FILE.write_text(f"{error_count}\n")
        return 0
    else:
        print("PASS: error count unchanged")
        return 0


if __name__ == "__main__":
    sys.exit(main())
