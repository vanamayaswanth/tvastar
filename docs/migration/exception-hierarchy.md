# Exception Hierarchy Migration Guide

## What Changed

`SecurityViolation` is moving from the `SandboxError` branch to a new `PolicyError` branch.

### Before (≤ v0.22)

```
TvastarError
├── SandboxError
│   └── SecurityViolation   ← lived here
├── ModelError
├── ToolError
│   └── ToolNotFound
├── SkillError
└── DurableError
```

### After (v0.23+)

```
TvastarError
├── SandboxError              (sandbox execution failures only)
├── PolicyError               ← NEW base for policy violations
│   ├── SecurityViolation     ← moved here
│   ├── GovernanceError       ← NEW (fleet governance)
│   └── BudgetExhaustedError  (re-exported from tvastar.fleet)
├── ModelError
├── ToolError
│   └── ToolNotFound
├── SkillError
└── DurableError
```

During the shim period (v0.23), `SecurityViolation` inherits from **both** `PolicyError` and `SandboxError` so existing `except SandboxError` handlers still catch it — but emit a `DeprecationWarning`.

---

## Why

`SandboxError` represents sandbox *execution* failures (process crashes, timeouts, filesystem errors). `SecurityViolation` represents *policy* decisions (blocked actions). These are fundamentally different:

- A `SandboxError` means "something broke while running in the sandbox."
- A `SecurityViolation` means "the policy said no."

Mixing them in one hierarchy made it impossible to catch policy violations without also catching unrelated sandbox crashes.

---

## How to Update

### Old pattern (catches SecurityViolation via SandboxError)

```python
from tvastar.errors import SandboxError

try:
    result = await sandbox.execute(command)
except SandboxError as e:
    # This used to catch both sandbox crashes AND policy blocks
    handle_error(e)
```

### New pattern — catch policy violations specifically

```python
from tvastar.errors import PolicyError, SandboxError

try:
    result = await sandbox.execute(command)
except PolicyError as e:
    # Catches SecurityViolation, GovernanceError, BudgetExhaustedError
    handle_policy_block(e)
except SandboxError as e:
    # Catches only sandbox execution failures
    handle_sandbox_crash(e)
```

### New pattern — catch SecurityViolation directly

```python
from tvastar.errors import SecurityViolation

try:
    result = await mcp_client.call_tool("dangerous_tool", {})
except SecurityViolation as e:
    # Explicit — works in both shim period and after
    log_blocked_action(e)
```

### If you only care about "any Tvastar error"

```python
from tvastar.errors import TvastarError

try:
    result = await sandbox.execute(command)
except TvastarError as e:
    # Still catches everything — no change needed
    handle_error(e)
```

---

## Finding Affected Code

Run this grep to find `except` blocks that catch `SandboxError` (which may be relying on it to catch `SecurityViolation`):

```bash
grep -rn "except.*SandboxError" src/ tests/
```

For each match, determine whether the handler expects to receive policy violations. If yes, switch to `except PolicyError` or `except SecurityViolation`.

---

## Timeline

| Version | Behavior |
|---------|----------|
| **v0.23** (current) | `SecurityViolation(PolicyError, SandboxError)` — dual inheritance. `DeprecationWarning` emitted on every `SecurityViolation` creation. |
| **v1.0** (next major) | `SecurityViolation(PolicyError)` only. Code catching `SandboxError` will **no longer** receive `SecurityViolation` instances. |

### What the warning looks like

```
DeprecationWarning: SecurityViolation is migrating from SandboxError to PolicyError.
Use `except PolicyError` or `except SecurityViolation` instead of `except SandboxError`.
This will stop working in v1.0.
See docs/migration/exception-hierarchy.md
```

### Action required

Update all `except SandboxError` handlers that expect to catch security policy violations **before upgrading to v1.0**.
