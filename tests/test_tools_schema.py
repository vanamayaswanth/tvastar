from enum import Enum
from typing import Literal, Optional

from tvastar.tools import tool
from tvastar.tools.schema import schema_from_callable


def test_schema_from_primitive_signature():
    def f(a: int, b: str, c: bool = True) -> int:
        """Do a thing.

        Args:
            a: first number
            b: a label
        """
        return a

    schema = schema_from_callable(f)
    assert schema["type"] == "object"
    assert schema["properties"]["a"] == {"type": "integer", "description": "first number"}
    assert schema["properties"]["b"]["type"] == "string"
    assert schema["properties"]["c"]["type"] == "boolean"
    assert set(schema["required"]) == {"a", "b"}  # c has a default


def test_schema_optional_and_literal_and_enum():
    class Color(Enum):
        RED = "red"
        BLUE = "blue"

    def f(x: Optional[int], mode: Literal["fast", "slow"], col: Color):
        return x

    schema = schema_from_callable(f)
    assert "x" not in schema.get("required", [])  # Optional => not required
    assert schema["properties"]["mode"]["enum"] == ["fast", "slow"]
    assert schema["properties"]["col"]["enum"] == ["red", "blue"]


def test_tool_decorator_builds_spec():
    @tool
    def add(a: int, b: int) -> int:
        "Add two numbers."
        return a + b

    assert add.name == "add"
    assert add.description == "Add two numbers."
    assert add.input_schema["properties"]["a"]["type"] == "integer"


def test_ctx_param_excluded_from_schema():
    from tvastar.tools.base import ToolContext

    @tool
    def t(ctx: ToolContext, path: str) -> str:
        "Read."
        return path

    assert t.wants_ctx is True
    assert "ctx" not in t.input_schema["properties"]
    assert "path" in t.input_schema["properties"]
