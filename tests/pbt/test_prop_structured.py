"""Property-based tests for structured output schema injection.

Property 34: Structured output schema injection
- For any schema passed as result= parameter, the Session SHALL inject a JSON
  schema instruction into the model prompt.
- Valid model JSON matching the schema SHALL be parsed into RunResult.data.

**Validates: Requirements 19.1, 19.2**
"""

from __future__ import annotations

import json

import hypothesis.strategies as st
from hypothesis import given, settings
from pydantic import BaseModel

from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel
from tvastar.session import _inject_schema_instruction


# ---------------------------------------------------------------------------
# Dynamic Pydantic v2 model generation strategies
# ---------------------------------------------------------------------------


class SimpleUser(BaseModel):
    """Simple schema for testing."""

    name: str
    age: int


class Address(BaseModel):
    """Nested schema for testing."""

    street: str
    city: str
    zip_code: str


class Product(BaseModel):
    """Schema with numeric fields."""

    title: str
    price: float
    in_stock: bool


# Strategy: generate valid instances of our test schemas
st_simple_user = st.builds(
    SimpleUser,
    name=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N", "Z"))),
    age=st.integers(min_value=0, max_value=150),
)

st_address = st.builds(
    Address,
    street=st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N", "Z", "P"))),
    city=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "Z"))),
    zip_code=st.from_regex(r"[0-9]{5}", fullmatch=True),
)

st_product = st.builds(
    Product,
    title=st.text(min_size=1, max_size=80, alphabet=st.characters(categories=("L", "N", "Z"))),
    price=st.floats(min_value=0.01, max_value=99999.99, allow_nan=False, allow_infinity=False),
    in_stock=st.booleans(),
)

# Combined strategy: pick one of the schema/instance pairs
st_schema_and_instance = st.one_of(
    st_simple_user.map(lambda inst: (SimpleUser, inst)),
    st_address.map(lambda inst: (Address, inst)),
    st_product.map(lambda inst: (Product, inst)),
)

# Strategy for generating user prompts
st_prompt = st.text(min_size=1, max_size=200, alphabet=st.characters(categories=("L", "N", "Z", "P")))


# ---------------------------------------------------------------------------
# Property 34: Schema instruction injection
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    schema_and_instance=st_schema_and_instance,
    prompt=st_prompt,
)
def test_schema_instruction_injected_into_prompt(
    schema_and_instance: tuple[type[BaseModel], BaseModel],
    prompt: str,
):
    """Property 34 (part 1): Schema instruction injection.

    For any schema passed as result=, _inject_schema_instruction SHALL inject
    a JSON schema instruction containing "Respond with valid JSON only" and the
    schema field information into the prompt text.

    **Validates: Requirements 19.1**
    """
    schema_cls, _instance = schema_and_instance

    injected = _inject_schema_instruction(prompt, schema_cls)

    # Original prompt is preserved
    assert prompt in injected

    # JSON instruction is injected
    assert "Respond with valid JSON only" in injected
    assert "no markdown fences" in injected
    assert "no explanation" in injected

    # Schema fields are visible in the injected text
    schema_info = schema_cls.model_json_schema()
    for field_name in schema_info.get("properties", {}).keys():
        assert field_name in injected, (
            f"Field '{field_name}' from schema not found in injected prompt"
        )


@settings(max_examples=100, deadline=None)
@given(
    schema_and_instance=st_schema_and_instance,
)
async def test_run_result_data_populated_from_valid_json(
    schema_and_instance: tuple[type[BaseModel], BaseModel],
):
    """Property 34 (part 2): RunResult.data populated from valid model JSON.

    For any schema and valid instance of that schema, when the model returns
    JSON matching the schema, RunResult.data SHALL be populated with the
    deserialized instance.

    **Validates: Requirements 19.2**
    """
    schema_cls, instance = schema_and_instance

    # Serialize the instance to JSON (what the mock model will return)
    json_response = instance.model_dump_json()

    # Create an agent with MockModel scripted to return valid JSON
    model = MockModel([json_response])
    spec = create_agent(
        "structured-prop-test",
        model=model,
        instructions="Return structured data",
        detect=False,
    )

    # Run with result= schema
    harness = Harness(spec)
    run_result = await harness.run("get data", result=schema_cls)

    # RunResult.data should be populated with a validated instance
    assert run_result.data is not None
    assert isinstance(run_result.data, schema_cls)

    # The deserialized instance should match the original data
    assert run_result.data.model_dump() == instance.model_dump()

    # No fallback findings should be present
    fallback_findings = [
        f for f in run_result.findings if f.detector == "structured_parse_failure"
    ]
    assert len(fallback_findings) == 0
