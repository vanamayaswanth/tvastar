# Tvastar Usage Guide

Decision trees and comparison tables for choosing the right API at every decision point.

---

## Which entry point should I use?

```
Do I need a running agent?
│
├── Just one prompt, one answer → harness.run(prompt)
│
├── Multiple prompts in the same conversation → harness.session() + sess.prompt()
│
├── The same prompt against many inputs → harness.fan_out([...])
│
├── A multi-step workflow that needs to survive crashes → @workflow
│
├── Fire-and-forget (webhook, chatbot, queue) → dispatch()
│
└── A scheduled, recurring job → Loop pattern (CISweeper / MakerChecker / ...)
```

---

## `harness.run()` vs `sess.prompt()`

| | `harness.run(prompt)` | `sess.prompt(text)` |
|---|---|---|
| Conversation memory | No — each call starts fresh | Yes — all prior turns included |
| Use when | One-shot questions, batch jobs | Chatbots, multi-step tasks, coding sessions |
| Returns | `RunResult` | `RunResult` |
| Async | Yes | Yes |

```python
# One-shot — no memory
result = await harness.run("What is 2+2?")

# Multi-turn — each prompt sees the full history
sess = harness.session()
r1 = await sess.prompt("My name is Alice.")
r2 = await sess.prompt("What is my name?")  # "Alice" — remembers
```

---

## `harness.run()` vs `harness.fan_out()`

| | `harness.run()` | `harness.fan_out()` |
|---|---|---|
| Prompts | 1 | N (list) |
| Concurrency | Sequential | Parallel (up to `concurrency` limit) |
| Use when | Single task | Batch processing, parallel research |
| Returns | `RunResult` | `list[RunResult]` |

```python
# One job
result = await harness.run("Summarise chapter 1")

# Many jobs, capped at 4 concurrent
results = await harness.fan_out(["Summarise chapter 1", "Summarise chapter 2", ...], concurrency=4)
```

---

## `sess.prompt()` vs `sess.task()`

| | `sess.prompt(text)` | `sess.task(text, agent=...)` |
|---|---|---|
| Who runs it | The current agent | A child agent (different profile) |
| Memory shared | Yes — same session history | No — child has its own context |
| Use when | Continuing the current task | Delegating to a specialist |
| Max nesting | — | 4 levels |

```python
# Continue in the same session
r1 = await sess.prompt("Read the auth module.")
r2 = await sess.prompt("Fix the bug you found.")

# Delegate to a specialist
review = await sess.task("Review the auth module for security issues", agent="security-reviewer")
```

---

## `@workflow` vs `dispatch()`

| | `@workflow` | `dispatch()` |
|---|---|---|
| Execution | Async, awaited | Fire-and-forget |
| State | Durable (survives process restart) | In-memory |
| Use when | Multi-step pipelines, ETL | Webhooks, chatbots, queues |
| Observe progress | `run.status`, `run.events` | `observe_dispatch()` callback |

```python
# Workflow — survives crashes, inspectable
@workflow
async def pipeline(ctx):
    h = await ctx.init(spec)
    s = await h.session()
    return (await s.prompt("Do the work")).text

run = await pipeline.run({"input": "data"})

# Dispatch — fire and move on
await dispatch(spec, id="user_1", text="Hello", on_complete=send_reply)
```

---

## When to use a Loop

Use a `Loop` when your agent needs to run on a schedule or in response to events — not just once.

| You want to... | Use |
|---|---|
| Poll CI every 15 min and fix failures | `CISweeper` |
| Triage new issues every morning | `DailyTriage` |
| Watch for stale PRs and nudge them | `PRBabysitter` |
| Bump patch dependencies weekly | `DependencySweeper` |
| Draft CHANGELOG from commit history | `ChangelogDrafter` |
| Run Maker and have a second agent verify | `MakerChecker` |
| Write a custom recurring agent | Subclass `Loop` |
| Run exactly once | `loop.trigger()` or `harness.run()` |

---

## When to use MakerChecker vs. a single agent

Use `MakerChecker` when:
- The output must be independently verified before being trusted
- You want a faster (cheaper) model to write and a stronger model to review
- A single wrong output has real consequences (code deployment, email send, DB write)
- You need structured rejection feedback fed back into the next attempt

Use a single agent when:
- Speed matters more than verification
- The task is low-stakes or easily reversible
- You have a silent-failure detector that catches bad outputs anyway

```python
# Single agent — fast, no verification
result = await harness.run("Summarize this article")

# MakerChecker — verified before passing
loop = MakerChecker(
    maker_model=AnthropicModel("claude-haiku-4-5-20251001"),
    checker_model=AnthropicModel("claude-sonnet-4-6"),
    goal="Write and verify the SQL migration for the new users table",
)
run = await loop.trigger()
```

---

## Choosing a model

| Need | Recommended model |
|------|------------------|
| Fast, cheap, high-volume tasks | `claude-haiku-4-5-20251001` |
| Balanced quality + cost | `claude-sonnet-4-6` |
| Maximum quality, hard problems | `claude-opus-4-8` |
| OpenAI compatible | `OpenAIModel("gpt-4o")` |
| Local, free, no API key | `OpenAIModel("llama3.2", base_url="http://localhost:11434/v1", api_key="ollama")` |
| Tests (no API calls) | `MockModel(script=["response 1", "response 2"])` |

Use a faster/cheaper model for the Maker in MakerChecker and a stronger model for the Checker.

---

## Structuring tool output

| Situation | What to do |
|---|---|
| Tool returns rich data (JSON, CSV) | Return it as a string — the model will parse it |
| Tool can fail transiently (network) | Use `@tool(retry=ToolRetryPolicy(...))` |
| Tool needs access to the session | Add `ctx: ToolContext` parameter |
| Tool result should be typed | Declare return type in signature — schema is auto-derived |
| Tool content comes from user input | Wrap with `wrap_untrusted()` to signal it as data |

```python
from tvastar.tools.base import tool, ToolRetryPolicy, ToolContext

@tool(retry=ToolRetryPolicy(max_attempts=3, backoff_base=1.0))
async def fetch_data(url: str, ctx: ToolContext) -> str:
    """Fetch data from a URL and return the body."""
    response = await http_get(url)
    ctx.memory.set("last_url", url)   # persist to session memory
    return response.text
```

---

## Handling long sessions

| Problem | Solution |
|---|---|
| Context grows past model limit | Add `CompactionPolicy` |
| Session crashes mid-way | Use `FileStore` for durable checkpointing |
| Agent needs memory across restarts | `Harness(spec, store=FileStore(path))` |

```python
from tvastar.compaction import CompactionPolicy
from tvastar.memory.store import FileStore

spec = create_agent(
    "long-runner",
    model=model,
    compaction=CompactionPolicy(max_messages=40, keep_last=10),
)
harness = Harness(spec, store=FileStore(".tvastar-state"))

# Resume after crash
sess = harness.resume("session-id") or harness.session("session-id")
```

---

## Structuring output

| Return type | How to use |
|---|---|
| Plain text | `result.text` |
| Pydantic model | `result=MyModel` in `prompt()` → `result.data` as `MyModel` |
| Plain dict | `result=dict` in `prompt()` → `result.data` as `dict` |
| Multiple fields | Use a Pydantic model |

```python
# Text
r = await sess.prompt("What year is it?")
print(r.text)

# Typed
class Answer(BaseModel):
    year: int
    confidence: float

r = await sess.prompt("What year is it?", result=Answer)
print(r.data.year)          # 2026
print(r.data.confidence)    # 0.99
```

---

## Choosing a sandbox

| Situation | Sandbox |
|---|---|
| Agent only reads/summarises (no code exec) | `VirtualSandbox` (default) |
| Agent runs code in trusted environment | `LocalSandbox` with `SecurityPolicy` |
| Agent runs untrusted model-generated code | Docker-based adapter (not yet built-in) |
| Tests — fully isolated, in-memory | `VirtualSandbox` (default) |

```python
from tvastar.sandbox.local import LocalSandbox
from tvastar.sandbox.base import SecurityPolicy

policy = SecurityPolicy(allowed_commands={"python", "pytest"}, network=False)
spec = create_agent(..., sandbox_factory=lambda: LocalSandbox("./workspace", policy=policy))
```

---

## Choosing a tracing backend

| Use case | Exporter |
|---|---|
| Print to terminal during dev | `ConsoleExporter()` |
| Local JSONL file for the trace viewer | `JSONLExporter("trace.jsonl")` |
| OpenTelemetry (Braintrust, Honeycomb, Datadog) | `OTelExporter()` |
| Multiple destinations | `Tracer([ConsoleExporter(), JSONLExporter(...)])` |

```python
from tvastar.observability import Tracer, ConsoleExporter, JSONLExporter

harness = Harness(spec, tracer=Tracer([ConsoleExporter(), JSONLExporter("trace.jsonl")]))
```

---

## Self-Improving Loops (`meta_model`)

Set `meta_model` on a `LoopConfig` to enable Hyperagents-style prompt evolution. After each
FAIL the meta-agent reads the failure evidence and rewrites the loop's agent instructions.
The improved instructions are persisted and used from the very next retry.

```python
config = LoopConfig(
    name="ci-sweeper",
    goal="Keep the build green.",
    schedule="*/15 * * * *",
    cancel_after=300.0,
    meta_model=AnthropicModel("claude-sonnet-4-6"),  # stronger model improves the worker
)
```

| Question | Answer |
|----------|--------|
| When does it fire? | Asynchronously after each non-PASS run, before the retry backoff expires |
| What does it improve? | The agent's `instructions` string — never code |
| How do improvements persist? | Stored under `loop:{name}:meta_instructions` in `FileStore` |
| What if meta-improvement fails? | Silently ignored — loop continues on previous instructions |
| Which model for `meta_model`? | Stronger than the worker (e.g. Sonnet for a Haiku worker) |
| Does it affect PASS runs? | No — only fires after FAIL/RETRY/HANDOFF |

### Generational Archive

Every run is recorded as a `LoopGeneration` with a fitness score.

```python
archive = loop.generation_archive     # list[LoopGeneration], oldest first
best    = loop.best_generation()      # highest score (most recent PASS wins ties)
print(f"Best: gen {best.gen_id}, score={best.score}")
print(best.instructions_snapshot)     # instructions that produced this result
```

`LoopGeneration` fields: `gen_id`, `run_id`, `loop_name`, `state`, `score` (1.0=PASS / 0.0=FAIL),
`started_at`, `instructions_snapshot`.

---

## Loop readiness checklist (L0 → L3)

Before deploying a loop to production, check its readiness level:

```bash
tvastar loop audit .tvastar/loops/myloop.py:loop
```

| Level | Name | What you need |
|-------|------|---------------|
| L0 | MANUAL | Loop exists — you `trigger()` it yourself |
| L1 | OBSERVE | `schedule=` + `handoff=` — runs automatically, escalates |
| L2 | GATED | L1 + `cancel_after=` — safe for loops that mutate state |
| L3 | AUTONOMOUS | L2 + `detect=` + `circuit_breaker_limit=` — fully unattended |

Audit passes and gaps are printed line by line. Fix gaps to advance a level.

---

## Error handling

| Error | What it means | What to do |
|-------|---------------|-----------|
| `TvastarError` | Framework misconfiguration | Check the message — usually a bad argument |
| `result.stopped == "max_steps"` | Agent hit the step ceiling | Increase `max_steps` or simplify the task |
| `result.stopped == "error"` | Tool or model error | Check `result.findings` for details |
| `result.ok == False` | At least one warning finding | Inspect `result.warnings` |
| `LoopState.FAIL` | Loop iteration failed | Check `run.error`, inspect `run.findings` |
| `LoopState.SUSPENDED` | Circuit breaker tripped | Call `loop.reset()` after fixing the root cause |

```python
result = await harness.run("Fix the test suite")

if not result.ok:
    for finding in result.warnings:
        print(f"[{finding.severity.value}] {finding.detector}: {finding.message}")

if result.stopped == "max_steps":
    print(f"Hit step limit ({result.steps} steps). Increase max_steps or split the task.")

---

## Assurance — when and what to use

`tvastar.assurance` gives you cryptographically-signed receipts, a tamper-evident
audit log, PII redaction, retention scheduling, and quality SLA enforcement. It is
designed for production agents that operate in regulated environments or that need
an immutable record of every decision they make.

---

### When to attach an `AssurancePolicy`

Use `AssurancePolicy` whenever you need an external, verifiable record that a run
happened exactly as described. Concrete triggers:

| Situation | Reason |
|---|---|
| Agent acts on patient, financial, or legal data | Regulatory audit trail (HIPAA, PCI-DSS, SOX) |
| Agent modifies production state (DB writes, emails, deploys) | Prove exactly what was done and when |
| SOC 2 / ISO 27001 audit requirement | Evidence that every model call is logged |
| Agent quality SLA (e.g. min 80/100) | Catch and escalate low-quality runs automatically |
| Any agent running in prod | Default-on posture for regulated sectors |

Attach with:

```python
agent = create_agent("name", model=m, assurance=AssurancePolicy(...))
```

The receipt is available on `result.receipt` after every run.

---

### Which `SanitizationPolicy` preset to use

PII redaction runs before the receipt is hashed. Pick the preset that matches
your compliance regime:

| Regulation | Preset | What it redacts |
|---|---|---|
| HIPAA (US healthcare) | `SanitizationPolicy.hipaa()` | SSN, DOB, phone, email, IP, bearer/API key |
| PCI-DSS (card payments) | `SanitizationPolicy.pci()` | Credit card numbers, CVV, bearer/API key |
| GDPR (EU personal data) | `SanitizationPolicy.gdpr()` | Email, phone, IP, DOB, bearer |
| All of the above | `SanitizationPolicy.all_pii()` | Union of all three presets |
| Custom / ML-based | `SanitizationPolicy.presidio(...)` | 50+ entity types via Microsoft Presidio ML models |

Combine presets with `add_pattern()` to cover domain-specific identifiers
(account numbers, patient IDs, internal codes):

```python
policy = SanitizationPolicy.hipaa().add_pattern(r"PAT-\d{6}", "[PATIENT_ID]")
```

`presidio()` requires `pip install tvastar[presidio]` and a spaCy language model.
Use it when regex presets miss entity types (names, organisation names, locations)
or when you need multi-language coverage.

---

### `TrustLog`: file-backed vs in-memory

| Mode | How | When to use |
|---|---|---|
| In-memory | `TrustLog()` (no path) | Tests, ephemeral jobs, local dev |
| File-backed | `TrustLog(".tvastar-trust.jsonl")` | Any production agent — survives restarts, auditable |

Always use a file-backed log in production. The JSONL file is the audit artefact
regulators or auditors will inspect. Keep it on durable storage (network volume,
object store-mounted path, or equivalent).

`verify_chain()` checks the entire chain on demand. Run it after every deployment
or on a schedule:

```python
ok = log.verify_chain()  # False + on_breach callback if tampered
```

---

### When to use `RetentionPolicy`

Compliance frameworks specify minimum retention periods. Wire `RetentionPolicy`
into a scheduled job (cron, `Loop`, or a background task) rather than running it
inline during agent calls.

| Framework | Minimum retention | Suggested config |
|---|---|---|
| SOX (US public companies) | 7 years | `max_age_days=365*7` |
| HIPAA (US healthcare) | 6 years | `max_age_days=365*6` |
| PCI-DSS | 1 year online, 3 year total | `max_age_days=365` (online); archive rest |
| GDPR | "no longer than necessary" | Domain-specific; `max_age_days` set per data type |

Set `hold_until` to an epoch timestamp whenever a legal hold is active
(litigation, regulatory inquiry). `apply_retention()` returns 0 and makes no
changes while the hold is in effect, regardless of age.

```python
# Nothing archived while hold is active
log.apply_retention(RetentionPolicy(max_age_days=30, hold_until=1800000000.0))
```

---

### `on_fail` options

`AssurancePolicy.on_fail` controls what happens when `quality_score < min_score`:

| Value | Effect | Use when |
|---|---|---|
| `"ignore"` | Receipt is logged; run continues normally | Monitoring only — you want telemetry without breaking the run |
| `"raise"` | `SLABreached(receipt)` is raised in the calling code | You want the caller to decide how to handle failures |
| `"escalate"` | `on_escalate(receipt)` is called | You have an async notification path (PagerDuty, Slack, compliance officer) |

`"escalate"` falls back to `"raise"` if `on_escalate` is `None`. Prefer
`"escalate"` in production so failures are routed to an on-call channel without
crashing the main application path.
```
