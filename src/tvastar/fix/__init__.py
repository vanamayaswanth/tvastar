"""Tvastar's flagship app: auto-fix failing test suites.

Exposes `fix_tests()` (programmatic) and the `tvastar-fix` CLI. Success is
always verified by re-running the suite — the agent can't fake a green run.
"""

from .fixer import FixResult, fix_tests
from .models import resolve_model

__all__ = ["FixResult", "fix_tests", "resolve_model"]
