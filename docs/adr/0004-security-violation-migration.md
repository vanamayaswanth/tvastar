# ADR 0004: SecurityViolation Migration from SandboxError to PolicyError

## Status

Accepted

## Context

`SecurityViolation` currently inherits from `SandboxError`. This made sense historically — security violations were only raised inside the sandbox (blocked commands, denied substrings). But with the addition of MCP security policy enforcement (REQ-7) and fleet governance (GovernanceError), policy violations now occur outside the sandbox boundary.

Consumers catch `SandboxError` expecting sandbox execution failures (timeouts, resource limits, process crashes). Catching `SecurityViolation` via `except SandboxError` conflates two different failure modes: "the sandbox failed to execute" vs. "the action was denied by policy."

The new `PolicyError` hierarchy provides a semantic home for all policy-related violations:
- `SecurityViolation` — action blocked by security policy (sandbox or MCP)
- `GovernanceError` — action blocked by fleet governance policy
- `BudgetExhaustedError` — action blocked by budget policy

**Constraints:**
- Existing code uses `except SandboxError` to catch `SecurityViolation` — this must not break immediately
- The migration must be signaled clearly so consumers can update their catch patterns
- Python has no "on-catch" hook — we cannot warn at the `except` site itself
- One version cycle for migration before removing dual inheritance

## Decision

Use a dual-inheritance shim for one version cycle:

```python
class PolicyError(TvastarError):
    """Base for all policy-related violations."""

class SecurityViolation(PolicyError, SandboxError):
    """Dual-inheritance shim. Caught by both PolicyError and SandboxError.

    SandboxError inheritance will be removed in the next major version.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warnings.warn(
            "SecurityViolation is migrating from SandboxError to PolicyError. "
            "Use `except PolicyError` or `except SecurityViolation` instead of "
            "`except SandboxError`. See docs/migration/exception-hierarchy.md",
            DeprecationWarning,
            stacklevel=2,
        )
```

The `DeprecationWarning` is emitted at creation time (in `__init__`), directing developers to the migration guide.

**Alternatives rejected:**
- **Immediate break (remove SandboxError parent):** Too disruptive. Consumers' `except SandboxError` handlers silently stop catching security violations — a silent behavioral change in error handling is dangerous.
- **Never migrate (keep dual inheritance forever):** Perpetuates the semantic confusion. New code would never know which base class to catch.
- **Warn on `except SandboxError` usage:** Not possible in Python — there's no hook for `except` statements.
- **Two-version deprecation cycle:** Unnecessary complexity. One version cycle with a clear warning and migration guide is sufficient given the project's release cadence.

## Consequences

- **During shim period:** `except SandboxError` still catches `SecurityViolation` but emits `DeprecationWarning`. Existing code continues working.
- **After shim removal (next major version):** `SecurityViolation` inherits only from `PolicyError`. Code using `except SandboxError` no longer catches security violations.
- **New code pattern:** `except PolicyError` catches all policy violations (security, governance, budget).
- `BudgetExhaustedError` is re-exported from `tvastar.errors` and inherits from `(PolicyError, FleetError)` for the same one-cycle shim.
- Migration guide at `docs/migration/exception-hierarchy.md` includes: before/after hierarchy, code examples, deprecation timeline, and grep pattern to find affected code.
- Trade-off: the `DeprecationWarning` fires at exception creation time, not at catch time. Developers may not see it if their warning filters suppress `DeprecationWarning` (Python default in non-dev mode). The migration guide compensates for this.
