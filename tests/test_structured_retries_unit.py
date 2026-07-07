"""Unit tests verifying structured_retries spec field controls retry behavior."""

from pydantic import BaseModel

from tvastar import Harness, create_agent, default_toolset
from tvastar.model import MockModel


class User(BaseModel):
    name: str
    age: int


class TestStructuredRetriesConfig:
    """Verify spec.structured_retries controls retry count."""

    async def test_zero_retries_no_retry_on_failure(self):
        """When structured_retries=0, no retry on parse failure (1 total call)."""
        model = MockModel(["bad json"])
        agent = create_agent(
            "test",
            model=model,
            instructions="test",
            tools=default_toolset(),
            structured_retries=0,
        )
        r = await Harness(agent).run("get user", result=User)
        assert len(model.calls) == 1
        assert isinstance(r.data, str)  # Falls back to raw text

    async def test_zero_retries_finding_mentions_1_attempt(self):
        """When structured_retries=0, the finding should say '1 attempt(s)'."""
        model = MockModel(["bad json"])
        agent = create_agent(
            "test",
            model=model,
            instructions="test",
            tools=default_toolset(),
            structured_retries=0,
        )
        r = await Harness(agent).run("get user", result=User)
        fallback = [f for f in r.findings if f.detector == "structured_parse_failure"]
        assert len(fallback) == 1
        assert "1 attempt" in fallback[0].message

    async def test_three_retries_makes_four_calls(self):
        """When structured_retries=3, makes 4 total calls (1 initial + 3 retries)."""
        model = MockModel(["bad"] * 4)
        agent = create_agent(
            "test",
            model=model,
            instructions="test",
            tools=default_toolset(),
            structured_retries=3,
        )
        r = await Harness(agent).run("get user", result=User)
        assert len(model.calls) == 4
        assert isinstance(r.data, str)  # All failed, falls back

    async def test_one_retry_succeeds_on_second_attempt(self):
        """When structured_retries=1, succeed on the retry attempt."""
        model = MockModel(["garbage", '{"name": "Alice", "age": 30}'])
        agent = create_agent(
            "test",
            model=model,
            instructions="test",
            tools=default_toolset(),
            structured_retries=1,
        )
        r = await Harness(agent).run("get user", result=User)
        assert isinstance(r.data, User)
        assert r.data.name == "Alice"
        assert len(model.calls) == 2

    async def test_default_retries_is_two(self):
        """Default structured_retries is 2 (backward compatible with _STRUCTURED_RETRIES)."""
        model = MockModel(["bad"] * 3)
        agent = create_agent(
            "test",
            model=model,
            instructions="test",
            tools=default_toolset(),
        )
        await Harness(agent).run("get user", result=User)
        assert len(model.calls) == 3  # 1 initial + 2 retries
