"""Score a researched lead against the Ideal Customer Profile."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..harness import Harness

from .research import ResearchResult

__all__ = ["ScoredLead", "score_lead"]


@dataclass
class ScoredLead:
    research: ResearchResult
    score: float  # 0.0 – 1.0
    rationale: str
    qualified: bool  # score >= threshold


async def score_lead(
    research: ResearchResult,
    *,
    icp: str,
    harness: "Harness",
    min_score: float = 0.5,
) -> ScoredLead:
    """Score a researched lead against the Ideal Customer Profile.

    Returns a ScoredLead with a 0.0–1.0 score and a short rationale.
    """
    try:
        from pydantic import BaseModel

        class _Score(BaseModel):
            score: float
            rationale: str

        use_pydantic = True
    except ImportError:
        use_pydantic = False

    prompt = (
        f"Score how well this lead matches our Ideal Customer Profile (ICP).\n\n"
        f"## ICP\n{icp}\n\n"
        f"## Lead\n{research.lead.display()}\n\n"
        f"## Research Brief\n{research.summary}\n\n"
        f"Return a score from 0.0 (terrible fit) to 1.0 (perfect fit) "
        f"and a 2-3 sentence rationale explaining the score."
    )

    sess = harness.session(f"score-{research.lead.email or research.lead.name}")
    async with sess:
        if use_pydantic:
            result = await sess.prompt(prompt, result=_Score)
            if isinstance(result.data, _Score):
                raw_score = max(0.0, min(1.0, float(result.data.score)))
                rationale = result.data.rationale
                return ScoredLead(
                    research=research,
                    score=raw_score,
                    rationale=rationale,
                    qualified=raw_score >= min_score,
                )
        else:
            result = await sess.prompt(prompt)

    # Fallback: extract first float from the text response
    m = re.search(r"\b(0\.\d+|1\.0|0|1)\b", result.text)
    raw_score = max(0.0, min(1.0, float(m.group()))) if m else 0.0
    return ScoredLead(
        research=research,
        score=raw_score,
        rationale=result.text[:400],
        qualified=raw_score >= min_score,
    )
