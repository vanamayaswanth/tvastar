"""A tiny, dependency-free JSON Schema validator.

Only the subset Tvastar needs to check tool-call arguments: ``type``,
``required``, ``properties``, ``items``, ``enum``, and unions of types. It
returns a list of human-readable error strings (empty == valid) rather than
raising — detectors want to report, not crash.

This is intentionally lenient: unknown keywords are ignored, and an empty/None
schema accepts anything. Reliability over strictness.
"""

from __future__ import annotations

from typing import Any

# JSON type name -> python predicate. bool is checked before int because in
# Python `bool` is a subclass of `int` and we must not accept True for integer.
_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate(value: Any, schema: dict | None, *, path: str = "") -> list[str]:
    """Return a list of validation error messages (empty if valid)."""
    if not schema:
        return []
    errors: list[str] = []

    # enum
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{_p(path)} must be one of {schema['enum']!r}, got {value!r}")

    # type (may be a string or list of strings)
    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if not any(_CHECKS.get(t, lambda _v: True)(value) for t in types):
            errors.append(f"{_p(path)} should be {' | '.join(types)}, got {type(value).__name__}")
            return errors  # further checks assume the type matched

    # object: required + property schemas
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{_p(path)} missing required field '{req}'")
        for key, subschema in (schema.get("properties") or {}).items():
            if key in value:
                errors += validate(value[key], subschema, path=f"{path}.{key}" if path else key)

    # array: item schema
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], path=f"{_p(path)}[{i}]".lstrip("."))

    return errors


def _p(path: str) -> str:
    return path or "value"
