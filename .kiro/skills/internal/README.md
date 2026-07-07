# Internal Skill Library

A library of **role-based engineering skills**. Each `SKILL.md` is a system prompt for a role-agent, teaching one discipline through a specific archetype from the Indian epics — built from the archetype's *acts*, not vague character traits.

The skills are designed to work as a **team**: each owns a set of specification/notation patterns and hands off to the others through explicit seams. The machine-readable source of truth is [`index.yaml`](./index.yaml).

## Skills

| Skill | Archetype | What it does | Owns (primary) |
|---|---|---|---|
| **AI Engineer** | Vyasa | Design + evaluate model-backed systems | Model Contract, eval contract |
| **Architect** | Vishwakarma | System structure, boundaries, irreversible decisions | C4, ADR, QAS, State Machine, Event Storming, API Contract |
| **Business Analyst** | Vidura | Precise, testable requirements + risk | EARS, INCOSE, RFC 2119, IEEE 29148, Traceability, Accessibility, blended spec template |
| **Data Engineer** | Sahadeva | Queryable, traceable, trustworthy data | SQL constraint grammar |
| **Developer** | Krishna | High-leverage implementation, whole-system view | Design by Contract, Pre/Post, OCL, AI Coding Checklist |
| **DevOps Platform** | Nala | The delivery path to production | CI/CD, IaC/GitOps, release strategy, rollback, parity |
| **Documentation Engineer** | Ganesha | Docs that let the next person succeed | Record grammar, ADR capture |
| **Incident Responder** | Jatayu | Contain, communicate, hand off in incidents | Escalation grammar, incident risk |
| **Performance** | Hanuman | Fix real bottlenecks by measurement | Perf QAS, SMART targets |
| **Product Manager** | Chanakya | Mission, scope, prioritization, alignment | User/Job stories, Use Cases, Story Mapping, INVEST, SMART, MoSCoW |
| **Prompt Engineer** | Narada | Prompts as contracts | AI Prompt Contract |
| **QA** | Shakuni | Test the hidden assumptions and chains | BDD, Gherkin, decision tables, acceptance criteria, AAA, mutation |
| **Reliability** | Bhishma | Degrade, stop cleanly, recover safely | Temporal logic, safety patterns, FMEA, TLA+, resilience patterns |
| **Security** | Krishna + Shakuni | Trust abuse, authz, least privilege | Policy grammar, STRIDE, OWASP Top 10 |
| **VC-idea-Validation** | Chanakya (Venture) | Validate an idea before building | Venture decision grammar, kill criteria |
| **Workflow Orchestrator** | Yudhishthira | Compose the right skills in the right order | Workflow selection, sequencing, gating, go/no-go |

## Independence & composition

Two properties, kept separate on purpose:

- **Every skill is independent.** Its trustworthy patterns (EARS, BDD, contracts, policy grammar, FMEA…) are stated *in full inside that skill*. Load one skill and it can do its job alone. The "Cross-References" inside a skill are non-blocking *see-also* pointers, never hard dependencies.
- **Composition lives in one place.** The **Workflow Orchestrator** (Yudhishthira) is the only skill that sequences others. It carries context across the handoff seams and never makes one skill depend on another to function. The named workflows it runs are in [`index.yaml`](./index.yaml) under `workflows:`.

## How the team composes

```
PM ──▶ BA ──▶ Architect ──▶ Developer ──▶ QA ──▶ Security
                  │                 ▲          │
                  ▼                 │          ▼
              DevOps ◀── Reliability ◀───── Incident
                  ▲
            Security (secrets/least-privilege policy)
Documentation captures every artifact · Prompt/AI Engineer own the model layer
```

Each requirement gets an ID (`REQ-<AREA>-<NNN>`) in the BA, flows through design (ADRs tagged with the REQ-ids they satisfy), to code (commits reference REQ/TEST ids), to tests (`TEST-<id>` mapped back to the requirement). That chain is the traceability matrix the Business Analyst owns.

## The shared grammar

Every skill speaks a slice of one specification grammar; the Business Analyst's **blended spec template** composes them. Ownership of each notation is single-sourced — see each skill's "Grammar" and "Cross-References" sections, and [`index.yaml`](./index.yaml) `owns`.

## Using a skill

Activate the skill whose `description` (a retrieval trigger) matches the task. Each skill defines its **Output Contract** — what artifact to produce and in which notation.

## Contributing a skill

A valid skill has:

1. YAML frontmatter: `name`, `description` (a "Use when…" trigger), `version`, `owner`, `lastReviewed`.
2. The standard sections: Mission, Important Note (the archetype's specific acts), Character Disposition, Core Principle, numbered Rules (act → Ask → examples), Workflow, Output Contract, Anti-Patterns, Final Question, Motto.
3. Single ownership of any pattern it introduces, with cross-references to neighbor owners.

Run the validator before committing: `python tools/validate_skills.py` (see [`tools/`](./tools)).

## Governance

Skills carry `version` and `lastReviewed`. Review cadence: quarterly, or whenever a referenced standard changes. The registry [`index.yaml`](./index.yaml) must stay in sync with the folders — the validator enforces this.
