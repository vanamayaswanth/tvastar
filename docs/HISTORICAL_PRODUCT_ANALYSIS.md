# Historical product analysis

> [!WARNING]
> **Historical archive — not a current product contract.** This record consolidates dated product assessment and forward-analysis material. It preserves hypotheses, scores, market observations, and recommendations as they were considered; it does **not** define supported behavior, security guidance, release commitments, or roadmap commitments. Start with the [documentation map](README.md) and [root README](../README.md) for maintained information.

## Sources retained

- `PRODUCT_ASSESSMENT.md` — product diagnostic and discovery recommendations.
- `FUTURE_ANALYSIS.md` — July 2026 forward-engineering scenario analysis.

## Product assessment snapshot

### Diagnostic and thesis

The archived assessment rated product practice **4/7**: a strong problem thesis and engineering ownership, but no systematic discovery evidence or short validated delivery increments. Its central thesis was that operating production agents requires a runtime, tools, loops, coordination, quality, and governance; it warned that the public story and the feature surface had become misaligned.

The assessment classified the four risks as follows:

| Risk | Historical conclusion |
|---|---|
| Value | Strong hypothesis, based on the silent-failure problem and recorded detector experiment |
| Usability | Moderate concern because the feature surface created high cognitive load |
| Feasibility | Proven at that time by the tested implementation |
| Viability | Unknown: no validated pricing or distribution strategy |

### Scope-sprawl analysis

The source grouped runtime/session/tooling, quality/detection, loops, durable state, and model support as core or necessary. It treated Fleet, outbound, visual tooling, workflows, deployment, compliance, and repair as adjacent or insufficiently validated. That classification was a discovery prompt, not a decision to remove any component.

### Historical recommendations

1. Choose one initial customer segment; the assessment favored startups building AI products over an immediate enterprise sales motion.
2. Interview five active users before extending adjacent capabilities; examine import and command usage, and retire features with no evidence of use.
3. Deliver smaller observable increments instead of large batches, with a concrete learning question per increment.
4. Keep the public introduction short: problem, evidence, first run, then links to deeper material.
5. Write a one-page strategy with a customer sequence, principles, and usage-based kill criteria.

The proposed path from 4/7 to 7/7 was five customer conversations, a weekly validated increment, and explicit kill criteria for unvalidated functionality. These remain historical process hypotheses.

## Forward engineering analysis — July 2026 scenario

> External market statements, competitor observations, percentages, forecasts, and cost figures in this section were scenario inputs, not independently maintained facts.

### Architecture interpretation

The source described `Message`, `Harness`, `Session`, `RunResult`, `AgentSpec`, `FleetBudget`, `FleetRegistry`, and `Loop` as high-connectivity nodes. It argued that many graph communities without human-readable names signalled that implementation had outpaced architectural narration. This was an analysis snapshot, not a current coupling score.

### Five-lens observations

| Lens | Historical concern | Proposed response |
|---|---|---|
| Boundary | A fixed lifecycle-operation timeout could reject expensive hibernation | Allow a per-operation timeout override |
| Boundary | Budgeting tracked model calls but not idle compute | Track compute cost and hibernate idle environments |
| Regime shift | Agents may need multi-day state, scheduling, and heartbeat control | Add lifecycle scheduling and session/sandbox state coordination |
| Regime shift | External callers could not use agents as tools | Consider a server adapter for agent capabilities |
| Silent failure | Checkpoint metadata and lifecycle latency might not survive or be observed across restarts | Persist metadata, trace lifecycle operations, alert near timeout |
| Inversion | Deployment backends and cost accounting might need to be broader than one container lifecycle | Keep the lifecycle abstraction while considering other backends and unified cost policy |
| Interaction | Retry, hibernation, compaction, and durable replay can fail at seams | Classify hibernation as non-retryable where appropriate and persist compaction state |

### Historical market and risk matrix

The source ranked unified model-plus-compute cost visibility, agent-as-a-service exposure, and scheduled lifecycle work as high-priority hypotheses. Kubernetes-native execution, multi-tenant isolation, deterministic replay, and operational compliance visibility were listed as later or conditional work. It also recorded six premortem scenarios: timeout misconfiguration, unseen compute cost, lack of external agent access, orphaned checkpoints, a restricted deployment environment, and session replay re-expanding compacted history.

### Historical 90-day sequence

1. Address checkpoint persistence, operation-specific timeouts, retry classification, and lifecycle tracing.
2. Add compute-cost tracking, cost anomaly visibility, and idle hibernation policies.
3. Add scheduled wake and hibernation.
4. Explore external agent exposure.
5. Explore a Kubernetes lifecycle backend.

The argument concluded that durable lifecycle primitives would require an operational layer for scheduling, cost accounting, service exposure, and metadata recovery. That is preserved as a dated product and technical hypothesis, not a current delivery plan.
