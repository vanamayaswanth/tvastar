# ADR 0002: Lazy Fleet Imports

## Status

Accepted

## Context

The Fleet subsystem (`tvastar.fleet`) provides multi-agent orchestration: registry, gateway, shared state, event bus, budget governance, and observability. This is heavy machinery that most single-agent users never touch.

When a user writes `import tvastar` or `from tvastar import create_agent`, the Fleet submodule should NOT be loaded. Loading Fleet eagerly would:
- Increase import time for the common single-agent case
- Pull in Fleet-specific dataclasses, enums, and internal state even when unused
- Make the mental model heavier — users see Fleet symbols in autocomplete/introspection when they don't need them

**Constraints:**
- Core API (`create_agent`, `Harness`, `Session`, `Tool`, `Model`) must import instantly
- Fleet is an advanced feature used by a subset of adopters
- Python's import system loads all module-level code on first `import`
- The zero-deps policy (ADR 0001) means Fleet can't rely on lazy-import libraries

## Decision

Fleet submodules use lazy imports. The `tvastar.fleet` package is NOT imported at `tvastar` top-level. Users who need Fleet explicitly import it:

```python
from tvastar.fleet import Fleet, FleetRegistry, FleetGateway
```

Within Fleet itself, heavy internal modules (deploy, observer, budget) are imported lazily at point of use rather than at package `__init__` level where feasible.

**Alternatives rejected:**
- **Eager import with `__all__` filtering:** Still loads the code; `__all__` only affects `from x import *`. Doesn't solve import time.
- **Separate `tvastar-fleet` package:** Splits the install story unnecessarily; complicates versioning and cross-module type checking.
- **`importlib.util.LazyLoader`:** Stdlib solution but introduces subtle debugging issues (deferred ImportErrors surface at unexpected call sites).

## Consequences

- `import tvastar` is fast — no Fleet machinery loaded.
- Users must explicitly opt into Fleet: `from tvastar.fleet import Fleet`.
- Fleet-internal imports may use conditional or function-local imports for heavy submodules.
- Type checkers (mypy) still see the full Fleet API because `__init__.pyi` stubs can declare symbols without triggering runtime loads.
- Trade-off: slightly unusual import patterns inside Fleet code (imports inside functions) which can confuse contributors unfamiliar with the pattern.
- Testing: CI verifies that `import tvastar` does NOT trigger import of `tvastar.fleet` submodules.
