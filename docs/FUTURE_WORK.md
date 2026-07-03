# Future Work — Tvastar SaaS Products & Agent Templates

> Inspired by [Harness.io](https://developer.harness.io/docs/) pipeline templates
> and enterprise DevOps workflows. Each product is an autonomous agent loop
> powered by Tvastar's core: silent failure detection, quality scoring,
> governance, cost tracking, and durable execution.

---

## Priority 1 — Ship Next (extensions of tvastar-fix)

### 1. tvastar-ci — Autonomous CI Agent

**What it does:** Watches repos for pushes/PRs, runs builds, detects failures,
auto-fixes them (extends existing `tvastar-fix`), reports quality scores.

**Harness equivalent:** [Harness CI](https://developer.harness.io/docs/continuous-integration) —
managed build runners, test intelligence, caching.

**Tvastar advantage:** Silent failure detection on test results (agent verifies
tests actually pass, doesn't trust exit codes alone). Quality scoring per build.

**Architecture:**
```
Loop(CISweeper) → trigger on push → run tests → detect failures
    → spawn fix agent → verify fix → commit or handoff
```

**Key features to build:**
- GitHub/GitLab webhook receiver (FastAPI + `tvastar[serve]`)
- Test intelligence: skip unchanged test modules
- Parallel test fan-out across runners
- Auto-PR creation with fix + quality score
- Dashboard showing loop health (L0→L3 readiness)

**Revenue model:** Per-repo/month ($29 hobby, $99 team, $299 enterprise)

**References:**
- [Harness CI overview](https://developer.harness.io/docs/continuous-integration/get-started/overview)
- [Harness Test Intelligence](https://developer.harness.io/docs/continuous-integration/use-ci/run-tests/ti-overview)
- Existing: `tvastar-fix`, `CISweeper` loop pattern

---

### 2. tvastar-secure — Security Scanning & Auto-Remediation Agent

**What it does:** Agent runs SAST/DAST/SCA scanners, triages findings by
severity, auto-remediates low-risk vulns (dependency bumps, code fixes),
escalates high-risk to humans.

**Harness equivalent:** [Harness STO](https://developer.harness.io/docs/security-testing-orchestration) —
security test orchestration, deduplication, prioritization.

**Tvastar advantage:** Agent doesn't just report vulns — it fixes them and
*verifies the fix doesn't break anything*. Silent failure detection catches
"fixed" vulnerabilities that reappear.

**Architecture:**
```
Loop(SecuritySweeper) → schedule daily → run scanners → triage findings
    → auto-fix low-risk → verify fix passes tests → PR or handoff
```

**Key features to build:**
- Scanner orchestration (Semgrep, Bandit, npm audit, Trivy)
- Vulnerability deduplication and prioritization
- Auto-fix templates for common CWEs
- Governance: only auto-fix below severity threshold
- Compliance reporting (SOC2, HIPAA mapped to CWEs)

**Revenue model:** Per-scan or per-repo/month ($49-$199)

**References:**
- [Harness STO overview](https://developer.harness.io/docs/security-testing-orchestration/get-started/overview)
- [Harness STO pipeline](https://developer.harness.io/docs/security-testing-orchestration/use-sto/set-up-sto-pipelines)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

---

## Priority 2 — Build After Core Products Stable

### 3. tvastar-deploy — Intelligent Deployment Agent

**What it does:** Agent manages deployments with canary/blue-green strategies,
monitors metrics during rollout, auto-rolls back on anomaly detection.

**Harness equivalent:** [Harness CD](https://developer.harness.io/docs/continuous-delivery) —
deployment pipelines, canary verification, rollback.

**Tvastar advantage:** Loop quality scoring on each deployment. Agent verifies
health metrics *independently* rather than trusting deployment tool output.

**Architecture:**
```
@workflow deploy_pipeline:
    phase("plan") → determine strategy (canary/blue-green/rolling)
    phase("deploy") → execute deployment to target
    phase("verify") → monitor metrics for N minutes
    phase("promote") → full rollout or rollback
```

**Key features to build:**
- Deployment strategy selection (canary %, blue-green, rolling)
- Metric monitoring integration (Prometheus, Datadog, CloudWatch)
- Anomaly detection on latency/error-rate/CPU during canary
- Automatic rollback with governance approval gate
- Multi-environment promotion (dev → staging → prod)

**Revenue model:** Per-deployment ($0.10-$1.00) or flat monthly

**References:**
- [Harness CD overview](https://developer.harness.io/docs/continuous-delivery/get-started/cd-pipeline-basics)
- [Harness Canary deployments](https://developer.harness.io/docs/continuous-delivery/deploy-srv-diff-platforms/kubernetes/cd-k8s-ref/canary-deployment-step)
- [Harness Continuous Verification](https://developer.harness.io/docs/continuous-delivery/verify/verify-deployments-with-the-verify-step)

---

### 4. tvastar-cost — Cloud Cost Optimization Agent

**What it does:** Agent scans cloud accounts, identifies waste (idle resources,
oversized instances, unused volumes), auto-implements savings with governance
approval.

**Harness equivalent:** [Harness CCM](https://developer.harness.io/docs/cloud-cost-management) —
cloud cost management, recommendations, anomaly detection.

**Tvastar advantage:** Agent doesn't just recommend — it *acts* (with approval
gate). Verifies savings were actually achieved after changes.

**Architecture:**
```
Loop(CostOptimizer) → schedule weekly → scan accounts → identify savings
    → rank by impact → auto-implement safe ones → verify savings → report
```

**Key features to build:**
- AWS/GCP/Azure cost data ingestion (Cost Explorer APIs)
- Idle resource detection (EC2, RDS, EBS, S3)
- Right-sizing recommendations with auto-implementation
- Savings verification (compare before/after billing)
- Budget alerts and anomaly detection
- Governance: approval required above $X threshold

**Revenue model:** % of savings achieved (10-20%) or flat monthly

**References:**
- [Harness CCM overview](https://developer.harness.io/docs/cloud-cost-management/get-started/overview)
- [Harness AutoStopping](https://developer.harness.io/docs/cloud-cost-management/use-ccm-cost-optimization/optimize-cloud-costs-with-intelligent-cloud-auto-stopping-rules/create-auto-stopping-rules)
- [AWS Cost Explorer API](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-api.html)

---

### 5. tvastar-oncall — Incident Response Agent

**What it does:** Agent triages alerts, correlates with recent deploys, runs
diagnostic playbooks, auto-remediates known issues, pages humans for novel
problems.

**Harness equivalent:** [Harness SRM](https://developer.harness.io/docs/service-reliability-management) —
service reliability management, change impact analysis.

**Tvastar advantage:** MakerChecker pattern — diagnosis agent proposes fix,
verification agent independently confirms before auto-remediation.

**Architecture:**
```
Loop(IncidentResponder) → trigger on alert → correlate with deploys
    → run diagnostic tools → classify severity
    → auto-remediate (known) OR escalate (novel) → verify resolution
```

**Key features to build:**
- Alert ingestion (PagerDuty, OpsGenie, CloudWatch Alarms)
- Change correlation (which deploy caused this?)
- Runbook execution (restart service, scale up, rollback)
- Severity classification with confidence scoring
- Escalation with full context (what was tried, what failed)
- Post-incident report generation

**Revenue model:** Per-incident or monthly ($199-$999)

**References:**
- [Harness SRM overview](https://developer.harness.io/docs/service-reliability-management)
- [PagerDuty Events API](https://developer.pagerduty.com/api-reference/)
- Existing: `incident_responder.py` example

---

### 6. tvastar-rollout — Feature Flag Management Agent

**What it does:** Agent manages progressive feature rollouts, monitors error
rates per flag, auto-kills features that cause regressions, schedules rollout
progression.

**Harness equivalent:** [Harness Feature Flags](https://developer.harness.io/docs/feature-flags) —
progressive delivery, targeting rules, flag lifecycle.

**Tvastar advantage:** Agent monitors real user metrics during rollout and
auto-reverts — not just a toggle, but an intelligent rollout manager.

**Architecture:**
```
Loop(RolloutManager) → trigger on new flag → progressive rollout
    → 1% → 10% → 50% → 100% (with metric gates at each stage)
    → auto-kill on error spike → report
```

**Key features to build:**
- Flag SDK integration (LaunchDarkly, Unleash, or custom)
- Metric gates between rollout stages
- Error rate monitoring per flag cohort
- Auto-kill with instant rollback on regression
- Scheduled rollouts (business hours only)
- A/B testing with statistical significance

**Revenue model:** Per-flag/month or per-seat

**References:**
- [Harness FF overview](https://developer.harness.io/docs/feature-flags/get-started/overview)
- [Harness FF + CCM integration](https://developer.harness.io/docs/feature-flags/ff-creating-flag/using-ff-ccm)
- [OpenFeature](https://openfeature.dev/)

---

## Priority 3 — Platform Layer (enables all above as SaaS)

### 7. tvastar-portal — AI-Powered Internal Developer Portal

**What it does:** Agent indexes your docs, runbooks, and codebase. Developers
ask questions in natural language, get answers with source links. Agent can
also execute actions (create PR, trigger deploy, open ticket).

**Harness equivalent:** [Harness IDP](https://developer.harness.io/docs/internal-developer-portal) +
[AI Knowledge Agent](https://www.harness.io/blog/the-ai-knowledge-agent-making-internal-developer-portals-smarter)

**Tvastar advantage:** Not just Q&A — agent can *do things*. "Deploy service X
to staging" actually triggers the deployment loop.

**Architecture:**
```
tvastar-portal:
    Knowledge Agent (LTM + RAG over docs/code)
    Action Agent (tools: deploy, create-pr, open-ticket, run-pipeline)
    Governance (approve destructive actions)
```

**Key features to build:**
- Document ingestion (Markdown, Confluence, Notion, README)
- Code indexing with semantic search
- Natural language → action routing
- Service catalog integration
- Onboarding flows for new developers
- Governance: read-only by default, action requires approval

**Revenue model:** Per-seat/month ($19-$49)

**References:**
- [Harness IDP overview](https://developer.harness.io/docs/internal-developer-portal/get-started/overview)
- [Backstage](https://backstage.io/) (open-source IDP)
- Existing: `tvastar.contrib.ltm.LTMStore`

---

### 8. tvastar-cloud — Managed Agent Runtime

**What it does:** SaaS platform where you deploy Tvastar agents and they run
on managed infrastructure. Dashboard for monitoring loops, viewing traces,
managing budgets across all agents.

**Harness equivalent:** [Harness Platform](https://developer.harness.io/docs/platform) —
the shared foundation powering all modules.

**This is the meta-product** — the platform that hosts all the above products.

**Key features to build:**
- Agent deployment (upload spec → runs on managed infra)
- Multi-tenant loop management
- Unified dashboard (all agents, all loops, all quality scores)
- Team/org RBAC
- Usage-based billing
- API for programmatic agent management
- Webhook/event integrations

**Revenue model:** Usage-based (compute + LLM tokens + storage)

**References:**
- [Harness Platform architecture](https://developer.harness.io/docs/platform/get-started/harness-platform-architecture)
- [Harness RBAC](https://developer.harness.io/docs/platform/role-based-access-control/rbac-in-harness)
- Existing: `tvastar[serve]`, `tvastar.workflow`, `tvastar.dispatch`

---

## Implementation Order

```
Phase 1 (NOW):      tvastar-fix ✅ (shipped)
                    agent-debugger example ✅ (shipped v0.19.0)

Phase 2 (Q3 2026):  tvastar-ci (extend CISweeper → SaaS)
                    tvastar-secure (SAST/SCA auto-fix)

Phase 3 (Q4 2026):  tvastar-deploy (canary + verification)
                    tvastar-oncall (incident auto-triage)

Phase 4 (Q1 2027):  tvastar-cost (cloud optimization)
                    tvastar-rollout (feature flag agent)

Phase 5 (Q2 2027):  tvastar-portal (developer portal)
                    tvastar-cloud (managed platform)
```

---

## Competitive Positioning

| vs. | Tvastar Advantage |
|-----|-------------------|
| **Harness** | Open source, embeddable, silent failure detection, no enterprise pricing |
| **LangGraph** | Quality scoring, governance, loop engineering — LangGraph is orchestration only |
| **CrewAI** | Verification built-in, not just coordination. Catches when agents lie. |
| **AWS AgentCore** | Model-agnostic, self-hosted option, full loop lifecycle (schedule + verify + handoff) |
| **GitHub Actions AI** | Agent-native (not just CI steps), quality scoring, auto-remediation |

---

## Revenue Targets

| Product | Year 1 Target | Pricing |
|---------|--------------|---------|
| tvastar-ci | $50K ARR | $99/repo/month × 50 repos |
| tvastar-secure | $30K ARR | $149/repo/month × 20 repos |
| tvastar-deploy | $40K ARR | $0.50/deployment × 80K deploys |
| tvastar-oncall | $60K ARR | $499/month × 10 teams |
| tvastar-cost | $100K ARR | 15% of savings × $650K saved |
| tvastar-cloud | $200K ARR | Usage-based × 100 teams |
| **Total** | **$480K ARR** | |

---

*Last updated: 2026-07-04 (v0.20.0)*
