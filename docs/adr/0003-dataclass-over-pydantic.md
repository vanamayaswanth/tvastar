# ADR 0003: Dataclass Over Pydantic for Configuration Objects

## Status

Accepted

## Context

Tvastar has multiple configuration objects (`FleetBudgetConfig`, `SecurityPolicy`, `LoopConfig`, `ModelRetryPolicy`) that need runtime validation — rejecting invalid values like negative budgets or thresholds outside [0, 1].

The Python ecosystem offers several validation approaches:
- **Pydantic v2:** Rich validation, serialization, JSON Schema generation. But it's a runtime dependency (~5MB installed, C extensions, version churn between v1/v2).
- **attrs + cattrs:** Lighter than pydantic, good validation story. Still a dependency.
- **stdlib dataclasses + `__post_init__`:** Zero dependencies, part of Python since 3.7, well-understood.

**Constraints:**
- Zero runtime dependencies in core (ADR 0001) — pydantic and attrs are both ruled out
- Validation must produce clear error messages naming the field, actual value, and violated constraint
- Configuration objects are constructed once at startup, not in hot paths — performance is irrelevant
- Type checkers (mypy) must understand the resulting classes without plugins

## Decision

All configuration dataclasses use stdlib `@dataclass` with `__post_init__` for validation. Each `__post_init__` method validates constraints and raises `ValueError` with a message that names the field, states the actual value, and describes the violated constraint.

```python
@dataclass
class FleetBudgetConfig:
    max_fleet_usd: float
    warn_threshold: float = 0.8

    def __post_init__(self) -> None:
        if self.max_fleet_usd <= 0:
            raise ValueError(
                f"max_fleet_usd must be > 0, got {self.max_fleet_usd}"
            )
```

**Alternatives rejected:**
- **Pydantic v2:** Violates ADR 0001 (zero-deps). Also introduces model vs dataclass confusion and validator decorator complexity.
- **attrs:** Violates ADR 0001. While lighter than pydantic, still an external dependency with its own validator API to learn.
- **No validation (trust the caller):** Leads to cryptic runtime errors deep in business logic when invalid config values propagate. Fails-late instead of fails-fast.
- **Property setters with validation:** More code, less idiomatic for frozen/immutable config objects.

## Consequences

- Zero additional dependencies for validation — stays consistent with ADR 0001.
- Validation errors are immediate at construction time (fail-fast), not deferred to usage.
- Error messages are human-readable and debuggable: field name + actual value + constraint.
- Trade-off: validation logic is imperative (`if/raise`) rather than declarative (annotations/decorators). More verbose for complex validators.
- Trade-off: no automatic JSON Schema generation. If needed later, it can be built from type hints without pydantic.
- Contributors must remember to add `__post_init__` validation when adding new config fields — enforced by code review and property-based tests.
- Breaking change acknowledged: adding validation to existing dataclasses may reject previously-accepted (but invalid) configurations (e.g., `warn_threshold == throttle_threshold`).
