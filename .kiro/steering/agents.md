---
inclusion: auto
---

# Agent Workflow — Lifecycle, Roles, Skills & Routing

Every code-writing task follows `ponytail.md`. Every task is routed to the agent whose **leading words** match the work. Every agent reads their assigned **skill** before writing code.

## Development Lifecycle

```
1. REQUIREMENTS (pm-lead) ─── EARS + INVEST
       │
       ├── Challenge (requirements-red-team) ─── 5 Whys + Contradiction
       ├── Challenge (pm-requirements-challenger) ─── Competitor Test + Journey
       ▼
2. DESIGN (engineer-backend leads) ─── Hexagonal + Pointfree + Event-Driven
       │
       ▼
3. TASKS (pm-lead decomposes) ─── Wave-based dependency DAG
       │
       ▼
4. IMPLEMENT (routed agent + skill) ─── Ponytail + agent method + skill patterns
       │
       ▼
5. TEST (qa-engineer) ─── Hypothesis PBT + STAR + Testcontainers
       │
       ▼
6. REVIEW (ponytail-review) ─── delete/stdlib/yagni/shrink audit
       │
       ▼
7. DOCUMENT (tech-writer) ─── Diátaxis (Tutorial/How-to/Explanation/Reference)
       │
       ▼
8. DEPLOY (engineer-infra) ─── 12-Factor, build once deploy many
       │
       ▼
9. OBSERVE (engineer-infra) ─── RED metrics + OTel traces + alerts → feedback to step 1
```

## Agent → Skills Mapping

| Agent | Skills to read BEFORE writing code | Location |
|-------|-----------------------------------|----------|
| `engineer-backend` | Temporal Developer, Clean Architecture, Domain-Driven Design, Pragmatic Programmer | `.skills/temporal/`, `.skills/wondelai/clean-architecture/`, `.skills/wondelai/domain-driven-design/`, `.skills/wondelai/pragmatic-programmer/` |
| `engineer-frontend` | Next.js Skills | `.skills/nextjs/` |
| `engineer-voice` | LiveKit Agent Skills | `.skills/livekit/` |
| `engineer-ai` | Qdrant Skills (reference for future migration) | `.skills/wondelai/pragmatic-programmer/` |
| `engineer-integrations` | Release It (circuit breakers, bulkheads) | `.skills/wondelai/release-it/` |
| `engineer-infra` | System Design, DDIA Systems | `.skills/wondelai/system-design/`, `.skills/wondelai/ddia-systems/` |
| `designer-ux` | UX Heuristics, Design of Everyday Things | `.skills/wondelai/ux-heuristics/`, `.skills/wondelai/design-everyday-things/` |
| `designer-ui` | Refactoring UI, Web Typography | `.skills/wondelai/refactoring-ui/`, `.skills/wondelai/web-typography/` |
| `pm-lead` | Jobs to be Done, Inspired Product | `.skills/wondelai/jobs-to-be-done/`, `.skills/wondelai/inspired-product/` |
| `qa-engineer` | Pragmatic Programmer (testing chapters) | `.skills/wondelai/pragmatic-programmer/` |
| `tech-writer` | — (Diátaxis is self-contained in agent) | — |

## Routing Table

| Task type | Agent | Leading words | Method |
|-----------|-------|---------------|--------|
| Scope features, prioritize, write stories | `pm-lead` | outcome, journey, cut | RICE + JTBD + EARS |
| Challenge from user value lens | `pm-requirements-challenger` | competitor test, journey | Competitor Test + Journey Map |
| Destroy weak decisions | `requirements-red-team` | 5 whys, contradict | 5 Whys + Contradiction Matrix |
| Python backend: domain, services, workflows, APIs | `engineer-backend` | compose, boundary, contract | Pointfree Compositional |
| Next.js: pages, components, hooks | `engineer-frontend` | flow, atomic, snappy | Atomic Design + FSD |
| LiveKit SIP, STT/TTS, audio, warm transfer | `engineer-voice` | latency, stream, dialog | Latency Budgeting |
| smolagents, RAG, prompts, scoring | `engineer-ai` | grounded, relevant, conversational | Grounded RAG |
| CRM adapters, WhatsApp API, webhooks | `engineer-integrations` | adapter, idempotent, eventual | Adapter Pattern |
| Docker, Terraform, CI/CD, scaling | `engineer-infra` | resilient, observable, immutable | 12-Factor + IaC |
| User flows, friction, task completion | `designer-ux` | self-evident, satisfice, feedback | Krug + Norman + Nielsen |
| Colors, spacing, typography, hierarchy | `designer-ui` | hierarchy, constraint, depth | Refactoring UI + WCAG |
| Property tests, invariants, state machines | `qa-engineer` | property, invariant, shrink | Hypothesis PBT + STAR |
| API docs, runbooks, guides, ADRs | `tech-writer` | audience, task, working | Diátaxis |

## Route by File Path

| Path | Primary agent | Skill to consult |
|------|--------------|-----------------|
| `backend/core/` | `engineer-backend` | clean-architecture, domain-driven-design |
| `backend/ports/` | `engineer-backend` | clean-architecture |
| `backend/adapters/postgres/`, `valkey/`, `nats/` | `engineer-backend` | ddia-systems |
| `backend/adapters/temporal/` | `engineer-backend` | **temporal** (official) |
| `backend/adapters/livekit/` | `engineer-voice` | **livekit** (official) |
| `backend/adapters/postgres/vectors.py` | `engineer-ai` | clean-architecture (pgvector, same DB) |
| `backend/adapters/whatsapp/`, `crm/`, `s3/`, `email/` | `engineer-integrations` | release-it |
| `backend/api/` | `engineer-backend` | clean-architecture |
| `backend/voice_agent/` | `engineer-voice` + `engineer-ai` | livekit + qdrant |
| `backend/workers/` | `engineer-backend` | **temporal** (official) |
| `backend/tests/` | `qa-engineer` | pragmatic-programmer |
| `frontend/src/shared/ui/` | `designer-ui` | refactoring-ui, web-typography |
| `frontend/src/features/` | `engineer-frontend` | **nextjs** (official) |
| `frontend/src/app/` | `engineer-frontend` | **nextjs** (official) |
| `docker-compose.yml`, `Dockerfile`, CI/CD | `engineer-infra` | system-design, ddia-systems |
| `.kiro/specs/` | `pm-lead` | jobs-to-be-done, inspired-product |

## Skill Usage Rule

**BEFORE writing any code**, the routed agent MUST:
1. Check if their assigned skill has a `SKILL.md` at the listed location
2. Read the relevant section for the pattern they're implementing
3. Follow the skill's patterns and anti-patterns
4. Only then write the implementation

This ensures every piece of code follows industry best practices from the official source.

## Collaboration Rules

- **Voice pipeline:** `engineer-voice` (livekit skill) + `engineer-ai` (pgvector/RAG) + `engineer-backend` (temporal skill)
- **Testing:** `qa-engineer` defines the property, domain agent implements satisfying code
- **Architecture decisions:** `requirements-red-team` challenges, `pm-lead` decides
- **Integrations:** `engineer-integrations` (release-it patterns) owns adapter, `engineer-backend` owns port interface

## Review Chain

| What was created | Who reviews |
|-----------------|-------------|
| Code by any engineer | `qa-engineer` writes property test |
| Requirements by `pm-lead` | `requirements-red-team` + `pm-requirements-challenger` |
| Design decisions | `pm-requirements-challenger` validates user value |
| UI components | `designer-ui` (refactoring-ui skill) + `designer-ux` (ux-heuristics skill) |
| Any diff before merge | `ponytail-review` (complexity audit: net -N lines) |

## Implementation Rules (Every Task)

1. **Ponytail first** — climb the YAGNI ladder before writing a single line
2. **Read the skill** — check assigned skill's SKILL.md for patterns
3. **Route to correct agent** — match file path or task type
4. **Agent applies their method** — leading words guide behavior
5. **Leave ONE runnable check** — non-trivial logic gets a test (ponytail minimum)

## Governing Steering Files

| File | Applies to | Purpose |
|------|-----------|---------|
| `ponytail.md` | All code | YAGNI ladder, shortest diff, deletion > addition |
| `ponytail-review.md` | All reviews | Line-level complexity audit |
| `agents.md` (this file) | All tasks | Routes work, assigns skills, enforces lifecycle |
| `graphify.md` | Architecture questions | Query knowledge graph before reading files |
| `skills-index.md` | Reference | Full index of installed skills |
