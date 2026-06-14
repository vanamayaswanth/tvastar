"""Per-lead research using a parallel TaskGraph (company site + news + contact)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..harness import Harness

from .leads import Lead

__all__ = ["ResearchResult", "research_lead"]


@dataclass
class ResearchResult:
    lead: Lead
    summary: str
    ok: bool = True


async def research_lead(lead: Lead, harness: "Harness") -> ResearchResult:
    """Research a single lead in parallel using TaskGraph.

    Three concurrent tasks run first (company site, news, contact), then a
    synthesis task depends on all three and writes the final brief.
    """
    from ..graph import TaskGraph

    graph = TaskGraph(harness)
    depends: list[str] = []

    if lead.website:
        graph.task(
            "company_site",
            f"Browse {lead.website} and summarize:\n"
            f"- What {lead.company} does and who they serve\n"
            f"- Their core products/services\n"
            f"- Any signals about company size or stage\n"
            f"- Recent announcements visible on the site",
        )
        depends.append("company_site")

    graph.task(
        "company_news",
        f"Search for recent news about {lead.company!r} (last 12 months). Look for:\n"
        f"- Funding rounds or investor activity\n"
        f"- New product launches or major releases\n"
        f"- Hiring trends (growing fast? hiring freeze?)\n"
        f"- Leadership changes or pivots\n"
        f"- Awards, partnerships, or press coverage",
    )
    depends.append("company_news")

    contact_prompt = (
        f"Search for professional information about {lead.name!r} at {lead.company!r}.\n"
    )
    if lead.linkedin_url:
        contact_prompt += f"LinkedIn: {lead.linkedin_url}\n"
    contact_prompt += (
        "Find their current role, area of focus, career background, "
        "and any recent public posts or activity relevant to a sales conversation."
    )
    graph.task("contact_research", contact_prompt)
    depends.append("contact_research")

    graph.task(
        "synthesis",
        f"Synthesize all research into a structured brief for {lead.name} at {lead.company}:\n\n"
        f"**Company** — what they do, target market, size/stage\n"
        f"**Recent signals** — funding, launches, hiring, challenges\n"
        f"**Contact** — {lead.name}'s role, focus, background\n"
        f"**Opportunity** — key pain points or triggers that make this a good moment to reach out\n\n"
        f"Be concise. Prioritise specifics over generalities.",
        depends_on=depends,
    )

    try:
        graph_result = await graph.run()
        return ResearchResult(lead=lead, summary=graph_result["synthesis"].text, ok=True)
    except Exception as exc:
        return ResearchResult(lead=lead, summary=f"[research failed: {exc}]", ok=False)
