"""Reflection — optional self-critique before returning results.

When enabled, after the agent produces its final output, a "critic" prompt
reviews it and can request a redo. This catches low-quality or incorrect
outputs before they reach the user.

Zero external dependencies. Uses the same model (or a separate critic model).

Usage:
    from tvastar.reflection import ReflectionPolicy, reflect

    # As a policy on the agent:
    agent = create_agent("assistant", model=model, reflection=ReflectionPolicy(max_rounds=2))

    # Or manually:
    result = await session.prompt("Write a function to sort a list")
    reviewed = await reflect(result, model=model, criteria="Check for correctness and edge cases")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReflectionResult:
    """Result of a reflection pass."""

    original_text: str
    final_text: str
    rounds: int  # how many critique rounds were needed
    critiques: list[str]  # the critiques generated
    improved: bool  # True if the output was revised


@dataclass
class ReflectionPolicy:
    """Configuration for automatic self-critique.

    Attributes:
        max_rounds: Maximum critique-revise cycles (default 2).
        criteria: What the critic should evaluate (default: correctness + completeness).
        critic_model: Optional separate model for critique (defaults to same model).
        threshold_word: If critic says this word, skip revision.
    """

    max_rounds: int = 2
    criteria: str = "Check for correctness, completeness, and edge cases. If the output is good, say ACCEPTABLE."
    critic_model: Any = None  # None = use same model
    threshold_word: str = "ACCEPTABLE"  # if critic response contains this, stop


async def reflect(
    text: str,
    *,
    model: Any,
    criteria: str = "Check for correctness, completeness, and edge cases. If good, say ACCEPTABLE.",
    max_rounds: int = 2,
    threshold_word: str = "ACCEPTABLE",
) -> ReflectionResult:
    """Run reflection on a text output.

    Sends the output to a critic prompt. If the critic suggests improvements,
    sends both the original + critique to get a revised version. Repeats up to
    max_rounds.

    Parameters
    ----------
    text: The agent's output to reflect on.
    model: Model to use for critique and revision.
    criteria: What to evaluate.
    max_rounds: Maximum critique-revise cycles.
    threshold_word: If critic says this, output is accepted as-is.

    Returns
    -------
    ReflectionResult with the final text and critique history.
    """
    from .types import Message

    current_text = text
    critiques: list[str] = []

    for round_num in range(max_rounds):
        # Critic pass
        critic_prompt = f"""Review the following output against these criteria:

CRITERIA: {criteria}

OUTPUT TO REVIEW:
{current_text}

If the output meets all criteria, respond with just: {threshold_word}
Otherwise, provide specific feedback on what needs improvement."""

        critic_resp = await model.generate(
            [Message("user", critic_prompt)],
            system="You are a strict quality reviewer. Be concise and specific.",
            tools=None,
            max_tokens=1024,
            temperature=0.3,
        )

        critique = critic_resp.message.text.strip()

        # Check if accepted
        if threshold_word.upper() in critique.upper():
            return ReflectionResult(
                original_text=text,
                final_text=current_text,
                rounds=round_num + 1,
                critiques=critiques,
                improved=current_text != text,
            )

        critiques.append(critique)

        # Revision pass
        revise_prompt = f"""Revise the following output based on this critique:

ORIGINAL OUTPUT:
{current_text}

CRITIQUE:
{critique}

CRITERIA: {criteria}

Produce an improved version that addresses ALL the critique points.
Output ONLY the revised content, no commentary."""

        revise_resp = await model.generate(
            [Message("user", revise_prompt)],
            system="You are a meticulous editor. Produce only the improved output.",
            tools=None,
            max_tokens=4096,
            temperature=0.3,
        )

        current_text = revise_resp.message.text.strip()

    # Max rounds reached
    return ReflectionResult(
        original_text=text,
        final_text=current_text,
        rounds=max_rounds,
        critiques=critiques,
        improved=current_text != text,
    )
