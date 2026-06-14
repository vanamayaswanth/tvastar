"""
tvastar.cost — token cost tracking and budget enforcement.

Usage:
    from tvastar.cost import Cost, BudgetPolicy, cost_for_model

    cost = cost_for_model("claude-opus-4-6", input_tokens=1000, output_tokens=500)
    print(f"${cost.usd:.4f}")

    # Attach a budget to an agent — raises BudgetExceeded if exceeded
    agent = create_agent(..., budget=BudgetPolicy(max_usd=1.00))
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "Cost",
    "BudgetPolicy",
    "BudgetExceeded",
    "cost_for_model",
    "COST_TABLE",
]

# ---------------------------------------------------------------------------
# Price table — USD per million tokens (input, output)
# Prices correct as of June 2026; update as providers change pricing.
# ---------------------------------------------------------------------------

COST_TABLE: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    # Groq (approximate)
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "llama-3.1-70b-versatile": {"input": 0.59, "output": 0.79},
    "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24},
}


# ---------------------------------------------------------------------------
# Cost dataclass
# ---------------------------------------------------------------------------


@dataclass
class Cost:
    """Token cost for a single run or session."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def usd(self) -> float:
        """Total cost in US dollars."""
        rates = COST_TABLE.get(self.model)
        if rates is None:
            return 0.0
        return (
            self.input_tokens * rates["input"] / 1_000_000
            + self.output_tokens * rates["output"] / 1_000_000
        )

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            model=self.model or other.model,
        )

    def __repr__(self) -> str:
        return (
            f"Cost(input={self.input_tokens}, output={self.output_tokens}, "
            f"usd=${self.usd:.4f}, model={self.model!r})"
        )


# ---------------------------------------------------------------------------
# Budget policy
# ---------------------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    """Raised when a run exceeds its configured budget."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(f"Budget exceeded: spent ${spent:.4f}, limit ${limit:.4f}")


@dataclass
class BudgetPolicy:
    """
    Enforce a cost ceiling on an agent run or session.

    Args:
        max_usd:   Maximum spend in US dollars.
        on_exceed: What to do when the budget is exceeded.
                   "raise"  → raise BudgetExceeded (default)
                   "stop"   → stop cleanly, return partial result
        warn_at:   Fraction of budget at which to emit a WARNING finding
                   (e.g. 0.8 = warn when 80% spent). Set to None to disable.

    Example::

        agent = create_agent(
            "assistant",
            model=AnthropicModel("claude-opus-4-6"),
            budget=BudgetPolicy(max_usd=0.50, on_exceed="stop"),
        )
    """

    max_usd: float
    on_exceed: Literal["raise", "stop", "approve"] = "raise"
    warn_at: float | None = 0.8

    def check(self, cost: Cost) -> None:
        """Raise BudgetExceeded if cost exceeds the limit."""
        if cost.usd >= self.max_usd:
            if self.on_exceed == "raise":
                raise BudgetExceeded(spent=cost.usd, limit=self.max_usd)

    def should_warn(self, cost: Cost) -> bool:
        if self.warn_at is None:
            return False
        return cost.usd >= self.max_usd * self.warn_at


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def cost_for_model(model: str, *, input_tokens: int, output_tokens: int) -> Cost:
    """
    Compute cost for a specific model and token counts.

    Example::

        c = cost_for_model("claude-opus-4-6", input_tokens=1000, output_tokens=500)
        print(f"${c.usd:.4f}")   # $0.0525
    """
    return Cost(input_tokens=input_tokens, output_tokens=output_tokens, model=model)


def estimate_cost(model: str, prompt: str, *, chars_per_token: float = 4.0) -> Cost:
    """
    Rough cost estimate from a prompt string before sending.
    Assumes output ≈ input length. Useful for sanity checks, not billing.
    """
    tokens = int(len(prompt) / chars_per_token)
    return Cost(input_tokens=tokens, output_tokens=tokens, model=model)
