"""Load an AgentSpec from a ``module/path.py:attribute`` reference.

Used by both the CLI and the server so an agent defined in a plain Python file
can be served without any registration boilerplate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..agent import AgentSpec
from ..errors import TvastarError


def load_agent(ref: str) -> AgentSpec:
    """Resolve ``"path/to/file.py:agent"`` or ``"package.module:agent"``.

    The attribute must be an AgentSpec (or a zero-arg callable returning one).
    Defaults the attribute name to ``agent`` when ``:name`` is omitted.
    """
    target, _, attr = ref.partition(":")
    attr = attr or "agent"

    if target.endswith(".py") or "/" in target or "\\" in target:
        module = _load_from_path(target)
    else:
        import importlib

        module = importlib.import_module(target)

    if not hasattr(module, attr):
        raise TvastarError(f"'{target}' has no attribute '{attr}'")
    obj = getattr(module, attr)
    if callable(obj) and not isinstance(obj, AgentSpec):
        obj = obj()
    if not isinstance(obj, AgentSpec):
        raise TvastarError(
            f"'{ref}' resolved to {type(obj).__name__}, expected AgentSpec (use create_agent)."
        )
    return obj


def _load_from_path(path: str):
    p = Path(path).resolve()
    if not p.exists():
        raise TvastarError(f"Agent file not found: {path}")
    spec = importlib.util.spec_from_file_location(p.stem, p)
    if spec is None or spec.loader is None:
        raise TvastarError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = module
    spec.loader.exec_module(module)
    return module
