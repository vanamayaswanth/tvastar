"""Tests for lazy fleet import in tvastar.__init__.py.

Validates:
- Requirement 30.1: __init__.py lazy-imports fleet behind __getattr__
- Requirement 30.2: Accessing a fleet symbol triggers the import
- Requirement 30.3: Fleet module not imported during `import tvastar`

Uses subprocess to run checks in a clean Python process, avoiding cached
imports from the test runner.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run_snippet(code: str) -> subprocess.CompletedProcess:
    """Run a Python snippet in a fresh process and return the result."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestLazyFleetNotLoadedOnImport:
    """Requirement 30.3: `import tvastar` does not load the fleet module."""

    def test_fleet_not_in_sys_modules_after_import(self):
        result = _run_snippet("""\
            import sys
            import tvastar
            # Check that the fleet module is NOT in sys.modules
            assert "tvastar.fleet" not in sys.modules, (
                f"tvastar.fleet should not be in sys.modules after bare import, "
                f"but it was found"
            )
            print("PASS")
        """)
        assert result.returncode == 0, (
            f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout


class TestLazyFleetLoadedOnAccess:
    """Requirements 30.1, 30.2: Accessing a fleet symbol triggers the import."""

    def test_accessing_fleet_symbol_triggers_import(self):
        result = _run_snippet("""\
            import sys
            import tvastar

            # Confirm fleet not loaded yet
            assert "tvastar.fleet" not in sys.modules, "fleet loaded too early"

            # Access a fleet symbol — this should trigger lazy import
            Fleet = tvastar.Fleet
            assert Fleet is not None, "Fleet symbol should be accessible"

            # Now fleet module should be in sys.modules
            assert "tvastar.fleet" in sys.modules, (
                "tvastar.fleet should be in sys.modules after accessing Fleet"
            )
            print("PASS")
        """)
        assert result.returncode == 0, (
            f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout

    def test_accessing_fleet_submodule_triggers_import(self):
        result = _run_snippet("""\
            import sys
            import tvastar

            # Confirm fleet not loaded yet
            assert "tvastar.fleet" not in sys.modules, "fleet loaded too early"

            # Access the fleet module attribute directly
            fleet_mod = tvastar.fleet
            assert fleet_mod is not None, "fleet module should be accessible"

            # Confirm it's now loaded
            assert "tvastar.fleet" in sys.modules, (
                "tvastar.fleet should be in sys.modules after accessing tvastar.fleet"
            )
            print("PASS")
        """)
        assert result.returncode == 0, (
            f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout

    def test_fleet_symbol_is_correct_class(self):
        """Verify that the lazily-loaded Fleet is the real Fleet class."""
        result = _run_snippet("""\
            import tvastar
            from tvastar.fleet import Fleet as DirectFleet

            # Access via lazy path
            LazyFleet = tvastar.Fleet

            # They should be the same class
            assert LazyFleet is DirectFleet, (
                f"tvastar.Fleet ({LazyFleet}) should be the same as "
                f"tvastar.fleet.Fleet ({DirectFleet})"
            )
            print("PASS")
        """)
        assert result.returncode == 0, (
            f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout

    def test_multiple_fleet_symbols_accessible(self):
        """Verify several fleet symbols are accessible via lazy import."""
        result = _run_snippet("""\
            import sys
            import tvastar

            assert "tvastar.fleet" not in sys.modules, "fleet loaded too early"

            # Access multiple fleet symbols
            symbols = [
                tvastar.Fleet,
                tvastar.FleetConfig,
                tvastar.FleetRegistry,
                tvastar.FleetGateway,
                tvastar.EventBus,
                tvastar.FleetBudget,
                tvastar.FleetObserver,
                tvastar.FleetDefaults,
                tvastar.FleetError,
            ]
            assert all(s is not None for s in symbols), "All fleet symbols should resolve"

            # Fleet module is now loaded
            assert "tvastar.fleet" in sys.modules
            print("PASS")
        """)
        assert result.returncode == 0, (
            f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout
