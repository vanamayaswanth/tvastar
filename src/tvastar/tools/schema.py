"""Generate JSON Schema from a Python callable's signature + type hints.

Kept dependency-free and small. Supports the common cases an agent tool needs:
str / int / float / bool / list / dict / Optional / Literal / Enum, with
docstring-derived descriptions. Anything unknown degrades gracefully to a
permissive schema rather than raising — reliability over strictness.
"""

from __future__ import annotations

import enum
import inspect
import typing
from typing import Any, Union, get_args, get_origin

_PRIMITIVES: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    dict: {"type": "object"},
    list: {"type": "array"},
    type(None): {"type": "null"},
}


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return (is_optional, inner_type) for Optional[X]/Union[X, None]."""
    if get_origin(tp) is Union:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) != len(get_args(tp)):
            inner = args[0] if len(args) == 1 else Union[tuple(args)]
            return True, inner
    return False, tp


def type_to_schema(tp: Any) -> dict[str, Any]:
    """Map a single annotation to a JSON Schema fragment."""
    if tp is inspect.Parameter.empty or tp is Any:
        return {}  # permissive

    _, tp = _is_optional(tp)

    # Enums
    if inspect.isclass(tp) and issubclass(tp, enum.Enum):
        return {"type": "string", "enum": [e.value for e in tp]}

    origin = get_origin(tp)

    # Literal["a", "b"]
    if origin is typing.Literal:
        vals = list(get_args(tp))
        jtype = "string" if all(isinstance(v, str) for v in vals) else None
        out: dict[str, Any] = {"enum": vals}
        if jtype:
            out["type"] = jtype
        return out

    # list[X]
    if origin in (list, set, tuple):
        args = get_args(tp)
        item = type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item}

    # dict[K, V]
    if origin is dict:
        return {"type": "object"}

    if tp in _PRIMITIVES:
        return dict(_PRIMITIVES[tp])

    # Fallback: accept anything.
    return {}


def schema_from_callable(fn: Any) -> dict[str, Any]:
    """Build an input_schema (object) from a function signature.

    Param descriptions are pulled from a simple ``name: desc`` convention in
    the docstring's ``Args:`` block when present.
    """
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    arg_docs = _parse_arg_docs(fn.__doc__ or "")

    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        # `ctx` is reserved for the ToolContext injection (see decorator).
        if pname == "ctx":
            continue
        ann = hints.get(pname, param.annotation)
        frag = type_to_schema(ann)
        if pname in arg_docs:
            frag = {**frag, "description": arg_docs[pname]}
        props[pname] = frag

        is_opt, _ = _is_optional(ann)
        if param.default is inspect.Parameter.empty and not is_opt:
            required.append(pname)

    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _parse_arg_docs(doc: str) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = doc.splitlines()
    in_args = False
    for raw in lines:
        line = raw.strip()
        low = line.lower()
        if low in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue
        if in_args:
            if not line or (low.endswith(":") and " " not in low):
                in_args = False
                continue
            if ":" in line:
                name, _, desc = line.partition(":")
                out[name.strip()] = desc.strip()
    return out
