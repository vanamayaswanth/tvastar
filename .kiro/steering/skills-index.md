---
inclusion: manual
---

# Installed Skills Index

Official and curated skills installed at `.skills/`. Reference these when working on the corresponding technology.

## Technology Skills (Official)

| Technology | Skill location | When to read |
|-----------|---------------|--------------|
| **Temporal** | `.skills/temporal/` | Writing workflows, activities, retry policies, worker config, versioning |
| **Qdrant** | `.skills/qdrant/` | Vector indexing, search queries, collection config, performance tuning |
| **LiveKit** | `.skills/livekit/` | SIP trunks, voice agents, audio pipeline, room management |
| **Next.js** | `.skills/nextjs/` | App Router patterns, RSC, data fetching, middleware |

## Architecture & Design Skills (wondelai)

| Skill | Location | When to read |
|-------|----------|--------------|
| **Refactoring UI** | `.skills/wondelai/refactoring-ui/` | Visual hierarchy, spacing, color, shadows — `designer-ui` agent |
| **UX Heuristics** | `.skills/wondelai/ux-heuristics/` | Nielsen's 10, Don't Make Me Think — `designer-ux` agent |
| **Design of Everyday Things** | `.skills/wondelai/design-everyday-things/` | Affordances, signifiers, feedback — `designer-ux` agent |
| **Clean Architecture** | `.skills/wondelai/clean-architecture/` | Hexagonal boundaries — `engineer-backend` agent |
| **Domain-Driven Design** | `.skills/wondelai/domain-driven-design/` | Aggregates, bounded contexts — `engineer-backend` agent |
| **System Design** | `.skills/wondelai/system-design/` | Scaling, caching, queues — `engineer-infra` agent |
| **DDIA Systems** | `.skills/wondelai/ddia-systems/` | Distributed systems patterns — `engineer-infra` agent |
| **Release It** | `.skills/wondelai/release-it/` | Circuit breakers, bulkheads — `engineer-infra` + `engineer-integrations` |
| **Pragmatic Programmer** | `.skills/wondelai/pragmatic-programmer/` | General engineering principles — all agents |
| **Jobs to be Done** | `.skills/wondelai/jobs-to-be-done/` | User motivation framework — `pm-lead` agent |
| **Inspired Product** | `.skills/wondelai/inspired-product/` | Product discovery — `pm-lead` agent |
| **Web Typography** | `.skills/wondelai/web-typography/` | Font choices, sizing, line-height — `designer-ui` agent |
| **Lean Startup** | `.skills/wondelai/lean-startup/` | Build-measure-learn — `pm-lead` agent |

## Usage Rule

When an agent encounters work in their domain, check the corresponding skill's `SKILL.md` for patterns and anti-patterns BEFORE writing code. The skill has already distilled the best practices — don't reinvent them.

Example flow:
1. Task: "Implement LeadWorkflow with Temporal"
2. Agent: `engineer-backend`
3. First action: Read `.skills/temporal/` SKILL.md for workflow determinism rules, activity patterns, and retry config
4. Then: implement following those patterns
