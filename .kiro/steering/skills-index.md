---
inclusion: auto
---

# Installed Skills Index

Reference for all installed skills. Two categories: the **Internal Skill Library** (role-based archetypes) and **Technology Skills** (official docs and curated patterns).

---

## Internal Skill Library

Character-installation system prompts at `.kiro/skills/internal/`. Each is an archetype from the Hindu epics whose specific acts ground engineering principles. Every skill is independent. Composition lives in the Workflow Orchestrator.

Registry: `.kiro/skills/internal/index.yaml`

| Skill | Archetype | Location | When to read |
|-------|-----------|----------|--------------|
| **Product Manager** | Chanakya | `.kiro/skills/internal/Product Manager/` | Scoping features, defining mission, prioritizing, writing stories |
| **Business Analyst** | Vidura | `.kiro/skills/internal/Business Analyst/` | Gathering, writing, or reviewing requirements |
| **Architect** | Vishwakarma | `.kiro/skills/internal/Architect/` | Designing architecture, reviewing technical designs |
| **Developer** | Krishna | `.kiro/skills/internal/Developer/` | Implementing, changing, or reviewing code |
| **QA** | Shakuni | `.kiro/skills/internal/QA/` | Reviewing requirements, testing features, assessing release readiness |
| **Security** | Krishna + Shakuni | `.kiro/skills/internal/Security/` | Designing or reviewing anything security-sensitive |
| **Reliability** | Bhishma | `.kiro/skills/internal/Reliability/` | Building or reviewing failure behavior, SLOs, operability |
| **Performance** | Hanuman | `.kiro/skills/internal/Performance/` | Investigating latency, throughput, or scaling problems |
| **AI Engineer** | Vyasa | `.kiro/skills/internal/AI Engineer/` | Building, deploying, or reviewing model-backed features |
| **Prompt Engineer** | Narada | `.kiro/skills/internal/Prompt Engineer/` | Writing, reviewing, or debugging prompts for AI features |
| **Data Engineer** | Sahadeva | `.kiro/skills/internal/Data Engineer/` | Designing pipelines, schemas, or data models |
| **DevOps Platform** | Nala | `.kiro/skills/internal/DevOps Platform/` | Designing pipelines, deployments, or platform tooling |
| **Documentation Engineer** | Ganesha | `.kiro/skills/internal/Documentation Engineer/` | Writing or reviewing docs, starting a project |
| **Incident Responder** | Jatayu | `.kiro/skills/internal/Incident Responder/` | Something broken or degraded, declaring or reviewing incidents |
| **VC-idea-Validation** | Chanakya (Venture) | `.kiro/skills/internal/VC-idea-Validation/` | Deciding whether to build, validate, narrow, or reject an idea |
| **Workflow Orchestrator** | Yudhishthira | `.kiro/skills/internal/Workflow Orchestrator/` | A job spans more than one role and needs sequencing |

---

## Technology Skills (Official)

Installed at `.skills/`. Official documentation and patterns for specific technologies.

| Technology | Location | When to read |
|-----------|----------|--------------|
| **Temporal** | `.skills/temporal/` | Writing workflows, activities, retry policies, worker config, versioning |
| **Qdrant** | `.skills/qdrant/` | Vector indexing, search queries, collection config, performance tuning |
| **LiveKit** | `.skills/livekit/` | SIP trunks, voice agents, audio pipeline, room management |
| **Next.js** | `.skills/nextjs/` | App Router patterns, RSC, data fetching, middleware |

---

## Architecture & Design Skills (Curated)

Installed at `.skills/wondelai/`. Curated patterns from influential books.

| Skill | Location | When to read |
|-------|----------|--------------|
| **Refactoring UI** | `.skills/wondelai/refactoring-ui/` | Visual hierarchy, spacing, color, shadows |
| **UX Heuristics** | `.skills/wondelai/ux-heuristics/` | Nielsen's 10, Don't Make Me Think |
| **Design of Everyday Things** | `.skills/wondelai/design-everyday-things/` | Affordances, signifiers, feedback |
| **Clean Architecture** | `.skills/wondelai/clean-architecture/` | Hexagonal boundaries, dependency inversion |
| **Domain-Driven Design** | `.skills/wondelai/domain-driven-design/` | Aggregates, bounded contexts, ubiquitous language |
| **System Design** | `.skills/wondelai/system-design/` | Scaling, caching, queues, distributed patterns |
| **DDIA Systems** | `.skills/wondelai/ddia-systems/` | Distributed systems patterns from DDIA |
| **Release It** | `.skills/wondelai/release-it/` | Circuit breakers, bulkheads, stability patterns |
| **Pragmatic Programmer** | `.skills/wondelai/pragmatic-programmer/` | General engineering principles |
| **Jobs to be Done** | `.skills/wondelai/jobs-to-be-done/` | User motivation framework |
| **Inspired Product** | `.skills/wondelai/inspired-product/` | Product discovery patterns |
| **Web Typography** | `.skills/wondelai/web-typography/` | Font choices, sizing, line-height |
| **Lean Startup** | `.skills/wondelai/lean-startup/` | Build-measure-learn |

---

## Usage Rule

When an agent encounters work in their domain:

1. **Read the Internal Skill** — inhabit the archetype, follow the patterns and anti-patterns
2. **Read the Technology Skill** (if one exists for the specific technology being used)
3. **Then implement** — following both the character's disposition and the technology's patterns

The Internal Skill provides the *how to think*. The Technology Skill provides the *what to know*.
