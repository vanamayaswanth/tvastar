"""Lead data model — parse CSV or list[dict] into typed Lead objects."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Lead", "parse_csv", "parse_leads"]


@dataclass
class Lead:
    company: str
    name: str
    email: str
    title: str = ""
    website: str = ""
    linkedin_url: str = ""
    extra: dict = field(default_factory=dict)

    def display(self) -> str:
        parts = [f"{self.name} @ {self.company}"]
        if self.title:
            parts.append(f"({self.title})")
        if self.email:
            parts.append(f"<{self.email}>")
        return " ".join(parts)


def parse_csv(path: str | Path) -> list[Lead]:
    """Load leads from a CSV file. Column names are flexible (case-insensitive)."""
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k.lower().strip(): str(v).strip() for k, v in row.items()})
    return parse_leads(rows)


def parse_leads(data: list[dict]) -> list[Lead]:
    """Convert a list of dicts to Lead objects. Keys are case-insensitive."""
    leads: list[Lead] = []
    for row in data:
        row = {k.lower().strip(): v for k, v in row.items()}

        def _get(*keys: str) -> str:
            for k in keys:
                val = row.get(k)
                if val:
                    return str(val).strip()
            return ""

        extra = {
            k: v
            for k, v in row.items()
            if k
            not in {
                "company",
                "company_name",
                "org",
                "organization",
                "name",
                "contact",
                "contact_name",
                "first_name",
                "email",
                "email_address",
                "contact_email",
                "title",
                "job_title",
                "role",
                "position",
                "website",
                "url",
                "company_url",
                "domain",
                "linkedin_url",
                "linkedin",
                "linkedin_profile",
            }
        }

        lead = Lead(
            company=_get("company", "company_name", "org", "organization"),
            name=_get("name", "contact", "contact_name", "first_name"),
            email=_get("email", "email_address", "contact_email"),
            title=_get("title", "job_title", "role", "position"),
            website=_get("website", "url", "company_url", "domain"),
            linkedin_url=_get("linkedin_url", "linkedin", "linkedin_profile"),
            extra=extra,
        )
        if lead.company or lead.email:
            leads.append(lead)
    return leads
