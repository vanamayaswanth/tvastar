"""Tests for tvastar-outbound: Lead parsing, pipeline units, and campaign orchestration."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tvastar.outbound.campaign import CampaignResult
from tvastar.outbound.email import EmailDraft
from tvastar.outbound.leads import Lead, parse_csv, parse_leads
from tvastar.outbound.send import StdoutSender

# ---------------------------------------------------------------------------
# Lead / parse_leads
# ---------------------------------------------------------------------------


def test_parse_leads_basic():
    rows = [{"company": "Acme", "name": "Alice", "email": "alice@acme.com"}]
    leads = parse_leads(rows)
    assert len(leads) == 1
    assert leads[0].company == "Acme"
    assert leads[0].name == "Alice"
    assert leads[0].email == "alice@acme.com"


def test_parse_leads_case_insensitive_keys():
    rows = [{"Company": "Beta", "Name": "Bob", "Email": "bob@beta.com", "Title": "CEO"}]
    leads = parse_leads(rows)
    assert leads[0].company == "Beta"
    assert leads[0].title == "CEO"


def test_parse_leads_alternate_column_names():
    rows = [{"organization": "Gamma", "contact_name": "Carol", "email_address": "c@g.com"}]
    leads = parse_leads(rows)
    assert leads[0].company == "Gamma"
    assert leads[0].name == "Carol"
    assert leads[0].email == "c@g.com"


def test_parse_leads_filters_empty_rows():
    rows = [
        {"company": "", "name": "Ghost", "email": ""},
        {"company": "Real", "name": "Dave", "email": "d@real.com"},
    ]
    leads = parse_leads(rows)
    assert len(leads) == 1
    assert leads[0].company == "Real"


def test_parse_leads_optional_fields_default_empty():
    rows = [{"company": "NoDetails", "name": "Eve", "email": "e@nd.com"}]
    lead = parse_leads(rows)[0]
    assert lead.title == ""
    assert lead.website == ""
    assert lead.linkedin_url == ""


def test_parse_leads_extra_columns_in_extra():
    rows = [{"company": "X", "name": "F", "email": "f@x.com", "industry": "SaaS"}]
    lead = parse_leads(rows)[0]
    assert lead.extra.get("industry") == "SaaS"


def test_parse_csv(tmp_path: Path):
    csv_file = tmp_path / "leads.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "name", "email", "website"])
        writer.writeheader()
        writer.writerow(
            {
                "company": "Tvastar",
                "name": "Vara",
                "email": "v@tvastar.ai",
                "website": "https://tvastar.ai",
            }
        )
    leads = parse_csv(csv_file)
    assert len(leads) == 1
    assert leads[0].website == "https://tvastar.ai"


def test_lead_display():
    lead = Lead(company="Acme", name="Alice", email="alice@acme.com", title="CTO")
    s = lead.display()
    assert "Alice" in s
    assert "Acme" in s
    assert "CTO" in s
    assert "alice@acme.com" in s


def test_lead_display_no_title():
    lead = Lead(company="X", name="Bob", email="b@x.com")
    s = lead.display()
    assert "Bob @ X" in s


# ---------------------------------------------------------------------------
# EmailDraft
# ---------------------------------------------------------------------------


def test_email_draft_fields():
    draft = EmailDraft(
        lead_name="Alice",
        lead_email="alice@acme.com",
        company="Acme",
        subject="Quick idea",
        body="Hi Alice...",
        score=0.8,
        research_summary="Acme is a SaaS company.",
    )
    assert draft.score == 0.8
    assert draft.subject == "Quick idea"


# ---------------------------------------------------------------------------
# StdoutSender
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_sender_returns_ok(capsys):
    draft = EmailDraft(
        lead_name="Alice",
        lead_email="alice@acme.com",
        company="Acme",
        subject="Test",
        body="Body text.",
        score=0.9,
        research_summary="",
    )
    sender = StdoutSender()
    result = await sender.send_one(draft)
    assert result.ok
    assert result.sent
    out = capsys.readouterr().out
    assert "Test" in out


@pytest.mark.asyncio
async def test_stdout_sender_send_all(capsys):
    drafts = [
        EmailDraft("A", "a@x.com", "X", "S1", "B1", 0.7, ""),
        EmailDraft("B", "b@y.com", "Y", "S2", "B2", 0.8, ""),
    ]
    results = await StdoutSender().send_all(drafts)
    assert len(results) == 2
    assert all(r.ok for r in results)


# ---------------------------------------------------------------------------
# CampaignResult
# ---------------------------------------------------------------------------


def test_campaign_result_ok_when_approved_and_sent():
    r = CampaignResult(
        leads_total=3,
        leads_researched=3,
        leads_qualified=2,
        leads_drafted=2,
        approved=True,
        sent=2,
    )
    assert r.ok


def test_campaign_result_not_ok_when_not_approved():
    r = CampaignResult(
        leads_total=3,
        leads_researched=3,
        leads_qualified=2,
        leads_drafted=2,
        approved=False,
        sent=0,
    )
    assert not r.ok


def test_campaign_result_not_ok_when_zero_sent():
    r = CampaignResult(
        leads_total=3,
        leads_researched=3,
        leads_qualified=2,
        leads_drafted=2,
        approved=True,
        sent=0,
    )
    assert not r.ok


# ---------------------------------------------------------------------------
# run_campaign integration (MockModel, auto-approve gate, StdoutSender)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_campaign_full_pipeline(capsys):
    """End-to-end campaign with MockModel — no real HTTP calls."""
    from tvastar.approval import ApprovalGate
    from tvastar.model import MockModel
    from tvastar.outbound import run_campaign

    # MockModel falls back to the echo response when script is exhausted,
    # which is a valid text reply for every research/score/email session.
    # For scoring, the mock response won't parse as a Pydantic score, so the
    # regex fallback picks 0.0 — below the default min_score of 0.5.
    # We set min_score=0.0 to ensure the lead qualifies regardless.
    model = MockModel(script=["0.8 — great ICP fit", "Subject: Test\nBody text here."])

    gate = ApprovalGate(backend="event", on_request=lambda req: req.approve())
    sender = StdoutSender()

    leads = [Lead(company="Acme", name="Alice", email="alice@acme.com")]

    result = await run_campaign(
        leads,
        model=model,
        icp="B2B SaaS companies with 50+ employees",
        sender_name="Jane",
        sender_company="Tvastar",
        sender_email="jane@tvastar.ai",
        min_score=0.0,  # qualify all leads regardless of mock score
        approval_gate=gate,
        sender=sender,
    )

    assert result.leads_total == 1
    assert result.leads_researched == 1
    assert result.leads_qualified == 1
    assert result.leads_drafted == 1
    assert result.approved
    assert result.sent == 1
    assert result.ok


@pytest.mark.asyncio
async def test_run_campaign_no_qualified_leads(capsys):
    """If no leads score above min_score, campaign exits early without sending."""
    from tvastar.approval import ApprovalGate
    from tvastar.model import MockModel
    from tvastar.outbound import run_campaign

    model = MockModel()  # all echo responses — no numeric score → 0.0
    gate = ApprovalGate(backend="event", on_request=lambda req: req.approve())

    leads = [Lead(company="Acme", name="Alice", email="alice@acme.com")]

    result = await run_campaign(
        leads,
        model=model,
        icp="Only unicorn companies with billion-dollar ARR",
        sender_name="Jane",
        sender_company="Tvastar",
        sender_email="jane@tvastar.ai",
        min_score=0.99,  # impossibly high — nothing qualifies
        approval_gate=gate,
    )

    assert result.leads_qualified == 0
    assert result.leads_drafted == 0
    assert not result.approved
    assert result.sent == 0


@pytest.mark.asyncio
async def test_run_campaign_denial_stops_sending():
    """ApprovalGate denial → approved=False, sent=0."""
    from tvastar.approval import ApprovalGate
    from tvastar.model import MockModel
    from tvastar.outbound import run_campaign

    model = MockModel()
    gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())

    leads = [Lead(company="Acme", name="Alice", email="alice@acme.com")]

    result = await run_campaign(
        leads,
        model=model,
        icp="Any company",
        sender_name="Jane",
        sender_company="Tvastar",
        sender_email="jane@tvastar.ai",
        min_score=0.0,
        approval_gate=gate,
    )

    assert not result.approved
    assert result.sent == 0


@pytest.mark.asyncio
async def test_run_campaign_from_dict_list():
    """Accept list[dict] as the leads argument."""
    from tvastar.approval import ApprovalGate
    from tvastar.model import MockModel
    from tvastar.outbound import run_campaign

    model = MockModel()
    gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())

    result = await run_campaign(
        [{"company": "Beta", "name": "Bob", "email": "bob@beta.com"}],
        model=model,
        icp="Any company",
        sender_name="Jane",
        sender_company="Tvastar",
        sender_email="jane@tvastar.ai",
        min_score=0.0,
        approval_gate=gate,
    )
    # Just verify it parsed the dict and ran through research/score
    assert result.leads_total == 1
    assert result.leads_researched == 1


@pytest.mark.asyncio
async def test_run_campaign_from_csv(tmp_path: Path):
    """Accept a CSV path as the leads argument."""
    from tvastar.approval import ApprovalGate
    from tvastar.model import MockModel
    from tvastar.outbound import run_campaign

    csv_file = tmp_path / "leads.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "name", "email"])
        writer.writeheader()
        writer.writerow({"company": "Gamma", "name": "Carol", "email": "carol@gamma.com"})

    model = MockModel()
    gate = ApprovalGate(backend="event", on_request=lambda req: req.deny())

    result = await run_campaign(
        str(csv_file),
        model=model,
        icp="Any",
        sender_name="Jane",
        sender_company="Tvastar",
        sender_email="jane@tvastar.ai",
        min_score=0.0,
        approval_gate=gate,
    )
    assert result.leads_total == 1


# ---------------------------------------------------------------------------
# Top-level exports
# ---------------------------------------------------------------------------


def test_top_level_outbound_exports():
    import tvastar

    assert hasattr(tvastar, "run_campaign")
    assert hasattr(tvastar, "CampaignResult")
    assert hasattr(tvastar, "Lead")
    assert hasattr(tvastar, "parse_csv")
    assert hasattr(tvastar, "parse_leads")
    assert hasattr(tvastar, "ResearchResult")
    assert hasattr(tvastar, "ScoredLead")
    assert hasattr(tvastar, "EmailDraft")
    assert hasattr(tvastar, "StdoutSender")
