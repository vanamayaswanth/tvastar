# Requirements Document

## Introduction

Vidura BA Analysis of Tvastar — a programmable agent harness for Python.

This document formalises the requirements for **Tvastar** using the **Vidura Business Analyst** methodology. The analysis applies the nine Vidura BA principles to surface risks before decisions, document consequences alongside requirements, and ensure every requirement passes the INCOSE quality bar.

**Scope:** Tvastar v0.17.x — core harness, loop engineering, silent-failure detection, sandbox execution, compliance/assurance, observability, and deployment layers.

**Methodology:** IEEE 29148 requirement attributes, EARS patterns, RFC 2119 obligation levels, INCOSE quality rules, and the Vidura BA risk grammar.

---

## Glossary

- **Harness**: The stateful runtime that executes an AgentSpec across sessions
- **AgentSpec**: An immutable declaration of what an agent is (model, tools, instructions, policies)
- **Session**: One conversation thread with its own message history
- **RunResult**: The outcome of a single prompt execution including text, findings, usage, and quality score
- **Loop**: An agent on a schedule with verify + handoff built in
- **Finding**: A typed signal from a silent-failure detector (severity + message + evidence)
- **Detector**: A function that inspects a finished run's transcript and emits Findings
- **Sandbox**: An execution environment where agent-produced code runs
- **GovernancePolicy**: Phase-based tool enforcement at invocation time
- **ToolPolicy**: A masking function that controls which tools the model sees per turn
- **CompactionPolicy**: Rules for summarising conversation history when it exceeds thresholds
- **ExecutionReceipt**: A cryptographically signed, chain-linked record of an agent run
- **TrustLog**: An append-only, tamper-evident audit trail of ExecutionReceipts
- **SanitizationPolicy**: PII/PHI redaction rules applied before model calls and audit logging
- **TokenVault**: A mechanism that replaces sensitive tokens with opaque placeholders before model inference
- **BudgetPolicy**: A cost ceiling per run or across loop iterations
- **HandoffPolicy**: The escalation strategy when a loop exhausts its retries
- **MCP**: Model Context Protocol — a standard for connecting external tool servers
- **OTel**: OpenTelemetry — observability framework for distributed tracing
- **WCAG**: Web Content Accessibility Guidelines
- **PII**: Personally Identifiable Information
- **PHI**: Protected Health Information

---

## Stakeholder Analysis (Vidura Principle: Represent All Users)

| Stakeholder | Needs | Risk if Unserved |
|---|---|---|
| Agent Developer | Declarative spec, zero-boilerplate loop, clear errors | Abandonment to competing frameworks |
| Platform/Ops Engineer | Deploy-anywhere, observability, cost controls | Shadow deployments, unmonitored spend |
| Security/Compliance Officer | Audit trail, PII redaction, tamper-evident logs | Regulatory fines, failed audits |
| End User (of the agent) | Correct results, no silent failures, timely responses | Trust erosion, undetected data corruption |
| Accessibility User | WCAG 2.2 AA–compliant trace viewer UI | Exclusion, legal liability |
| Open-Source Contributor | Clear API surface, zero-dep core, documented patterns | Fragmented contributions, breaking changes |

---

## Constraints

| ID | Constraint | Rationale |
|---|---|---|
| CON-001 | Core package (src/tvastar/) SHALL have zero runtime third-party dependencies | Minimises supply-chain attack surface; keeps install footprint at zero |
| CON-002 | Optional extras SHALL use lazy imports behind try/except ImportError | Prevents ImportError for users who did not install the extra |
| CON-003 | Python version support SHALL span 3.10 through 3.13 inclusive | Matches CI matrix and pyproject.toml requires-python |
| CON-004 | Tracing, masking, and compaction SHALL never raise into user code | These wrap user logic; a failure here must not kill the agent run |
| CON-005 | Public API surface SHALL be defined exclusively in src/tvastar/__init__.py | Single source of truth for what is importable |
| CON-006 | All features documented in README SHALL be backed by code and a test | "Don't oversell" brand principle |

---

## Assumptions

| ID | Assumption | Impact if Wrong |
|---|---|---|
| ASM-001 | Provider SDKs (anthropic, openai) maintain backward-compatible response shapes | Model adapters break silently on SDK upgrades |
| ASM-002 | Users run agents in environments with filesystem access | FileStore, LocalSandbox, and checkpoint/resume fail on read-only filesystems |
| ASM-003 | Model providers enforce rate limits externally; Tvastar need not duplicate them | Overflow errors surface as uncaught exceptions without compaction fallback |
| ASM-004 | VirtualSandbox users run only trusted code (not adversarial model output) | In-memory sandbox is not an isolation boundary; escape is trivial |
| ASM-005 | Clock monotonicity holds for checkpoint ordering | Non-monotonic clocks (VM migration, NTP jumps) corrupt durable state ordering |
| ASM-006 | Users configure at most one AssurancePolicy per AgentSpec | Multiple policies would produce conflicting SLA enforcement |

---

## Risks (Vidura Principle: Surface Risks Before Decisions)

### Risk Grammar: IF → THEN → IMPACT → MITIGATION

| ID | IF | THEN | IMPACT | MITIGATION |
|---|---|---|---|---|
| RSK-001 | VirtualSandbox executes adversarial model-generated code | Arbitrary code runs in the host process namespace | Full host compromise, data exfiltration | Document VirtualSandbox as convenience-only; recommend LocalSandbox+SecurityPolicy or container for untrusted code |
| RSK-002 | CredentialFilter patterns miss a secret-bearing env var (non-standard naming) | Agent subprocess inherits the secret | Credential leakage to model context or logs | Allow user-defined patterns; warn on env vars that match heuristic (high entropy, base64) |
| RSK-003 | Prompt-injection detector has false negatives (novel attack) | Injected instructions execute without a Finding | Silent data corruption, privilege escalation via tools | Layer defences: wrap_untrusted + detector + GovernancePolicy; document as detection/mitigation, not prevention |
| RSK-004 | CompactionPolicy summarises away critical early context | Model hallucinates or contradicts prior instructions | Incorrect agent behaviour with no visible cause | Keep system prompt and first N user messages immune from compaction; surface compaction events in trace |
| RSK-005 | Loop meta-improvement rewrites instructions to a degenerate state | Subsequent runs fail or violate safety constraints | Runaway degradation, safety bypass | Persist generation archive; add rollback-to-best-generation API; bound improvement attempts |
| RSK-006 | TrustLog append fails (disk full, permission error) | Receipt chain breaks; integrity verification fails for all subsequent entries | Audit gap, compliance failure | Return error to caller; do not swallow; provide health-check API for log integrity |
| RSK-007 | Budget enforcement races with concurrent tool executions | Actual spend exceeds max_usd before the check fires | Unexpected cost overrun | Check budget AFTER each model.generate (tokens are known), not after tool execution |
| RSK-008 | Structured-output retry loop produces an infinite correction cycle | Agent oscillates between malformed JSON and correction prompts | Wasted tokens, timeout | Cap at _STRUCTURED_RETRIES (currently 2); fall back to raw text with WARNING finding |
| RSK-009 | Governance phase transition races with in-flight tool execution | Tool executes under wrong phase permissions | Unauthorized tool call succeeds | GovernancePolicy.copy() per session isolates state; document that set_phase is not atomic with in-flight calls |
| RSK-010 | Trace viewer UI (index.html) renders unsanitised tool output | Stored XSS via agent-produced content | Browser compromise for anyone viewing traces | HTML-escape all dynamic content in trace viewer; add CSP headers |
| RSK-011 | Model adapter silently drops thinking_level for unsupported providers | User expects extended reasoning but gets standard inference | Lower quality results with no signal | Emit INFO-level Finding or tracer event when thinking_level is requested but not supported |
| RSK-012 | Checkpoint store corruption (partial write, encoding error) | harness.resume() returns None or raises | Lost session state, user repeats work | Write-then-rename pattern; validate on read; surface corruption as typed error |

---

## Requirements

### Requirement 1: Agent Loop Execution

**User Story:** As an agent developer, I want a deterministic agent loop that executes model calls and tool invocations in sequence, so that I can reason about agent behaviour and debug failures.

**ID:** REQ-LOOP-001  
**Priority:** P0 — Critical  
**Source:** Core architecture (session.py)  
**Status:** Implemented  
**Verification Method:** Property-based test + unit test  
**Rationale:** The agent loop is the central execution primitive; correctness here is foundational.  
**RFC 2119:** MUST  
**Requires:** A valid AgentSpec with a configured Model and ToolRegistry  
**Ensures:** A RunResult with text, messages, usage, steps, stopped reason, and findings  
**Invariant:** len(result.messages) >= 1; result.steps >= 1; result.usage.input_tokens >= 0

#### Acceptance Criteria

1. WHEN a user prompt is submitted to a Session, THE Harness SHALL invoke model.generate with the accumulated messages, system prompt, and visible tool specs
2. WHILE the ModelResponse stop_reason is TOOL_USE, THE Session SHALL execute all requested tools concurrently and append results to the message history
3. WHEN the ModelResponse stop_reason is END_TURN, THE Session SHALL return a RunResult with stopped="end_turn"
4. WHEN the step count reaches AgentSpec.max_steps without END_TURN, THE Session SHALL return a RunResult with stopped="max_steps"
5. IF model.generate raises an exception that is not a context overflow, THEN THE Session SHALL propagate the exception to the caller
6. IF model.generate raises a context overflow error AND a CompactionPolicy is configured, THEN THE Session SHALL compact the history and retry the model call once

### Requirement 2: Silent-Failure Detection

**User Story:** As an agent developer, I want the harness to detect when an agent claims success but actually failed, so that I do not ship broken results to users.

**ID:** REQ-DETECT-001  
**Priority:** P0 — Critical  
**Source:** Brand principle ("Verify, don't trust")  
**Status:** Implemented  
**Verification Method:** Property-based test (invariant: success claim + failure evidence → Finding)  
**Rationale:** This is Tvastar's core differentiator. Without it, the framework has no unique value.  
**RFC 2119:** MUST  
**Requires:** A completed RunResult with messages and tool call history  
**Ensures:** result.findings contains all detected failure modes  
**Invariant:** Detectors are pure functions; they never modify RunResult.messages

#### Acceptance Criteria

1. WHEN the final assistant text contains a success claim AND the last tool result contains a failure signal, THE unverified_completion Detector SHALL emit a Finding with Severity.ERROR
2. WHEN the same tool is called with identical arguments more than threshold times, THE thrash_loop Detector SHALL emit a Finding with Severity.WARNING
3. WHEN the model calls a tool not in the ToolRegistry, THE unknown_tool Detector SHALL emit a Finding with Severity.ERROR
4. WHEN a tool call's arguments violate the tool's declared input schema, THE schema_mismatch Detector SHALL emit a Finding with Severity.ERROR
5. WHEN a tool result contains content matching injection patterns, THE prompt_injection Detector SHALL emit a Finding with Severity.WARNING
6. WHEN the run ends at END_TURN after a tool error without recovery, THE ignored_tool_error Detector SHALL emit a Finding with Severity.WARNING
7. WHEN the run ends with an empty final answer, THE empty_answer Detector SHALL emit a Finding with Severity.WARNING
8. THE Harness SHALL execute all configured detectors after the loop completes but before returning RunResult to the caller

### Requirement 3: Quality Scoring

**User Story:** As a platform engineer, I want every agent run to produce a 0–100 quality score and a PASS/WARN/FAIL grade, so that I can set SLA thresholds and alert on degradation.

**ID:** REQ-QUALITY-001  
**Priority:** P1 — High  
**Source:** Loop quality layer (quality.py)  
**Status:** Implemented  
**Verification Method:** Unit test + example-based test  
**Rationale:** Quantified quality enables automated SLA enforcement and trend monitoring.  
**RFC 2119:** MUST  
**Requires:** A RunResult with findings and stopped reason  
**Ensures:** A LoopQualityReport with score in [0, 100] and grade in {PASS, WARN, FAIL}  
**Invariant:** score_run is a pure function; same input always produces same output

#### Acceptance Criteria

1. THE score_run function SHALL start at 100 and deduct 30 points per ERROR finding, 10 per WARNING finding, 20 for max_steps stop, and 50 for error stop
2. THE score_run function SHALL clamp the score to a minimum of 0
3. WHEN score >= 80, THE LoopQualityReport SHALL assign grade "PASS"
4. WHEN 60 <= score < 80, THE LoopQualityReport SHALL assign grade "WARN"
5. WHEN score < 60, THE LoopQualityReport SHALL assign grade "FAIL"
6. THE RunResult.quality property SHALL compute the LoopQualityReport lazily on first access

### Requirement 4: Sandbox Execution Safety

**User Story:** As a security engineer, I want agent-produced code to execute within a policy-controlled sandbox, so that malicious or buggy code cannot compromise the host system.

**ID:** REQ-SANDBOX-001  
**Priority:** P0 — Critical  
**Source:** Execution safety layer (sandbox/)  
**Status:** Implemented  
**Verification Method:** Unit test + adversarial test cases  
**Rationale:** Agents produce code that may be incorrect or hostile; the sandbox is the isolation boundary.  
**RFC 2119:** MUST  
**Requires:** A Sandbox instance with a SecurityPolicy  
**Ensures:** Commands violating policy raise SecurityViolation; allowed commands return ExecResult  
**Invariant:** SecurityPolicy.check() is called before every exec(); no command bypasses it

#### Acceptance Criteria

1. WHEN a command matches a denied_substrings entry, THE SecurityPolicy SHALL raise SecurityViolation before execution
2. WHEN allowed_commands is non-empty AND the command's first token is not in the set, THE SecurityPolicy SHALL raise SecurityViolation
3. WHEN a command's first token matches denied_commands, THE SecurityPolicy SHALL raise SecurityViolation
4. THE CredentialFilter SHALL remove all environment variables matching its glob patterns before subprocess spawn
5. WHEN a command exceeds timeout_seconds, THE Sandbox SHALL terminate the process and return ExecResult with timed_out=True
6. IF VirtualSandbox is used for untrusted model-generated code, THEN THE documentation SHALL warn that VirtualSandbox is not an isolation boundary

### Requirement 5: Tool Masking and Governance

**User Story:** As a security engineer, I want to control which tools the model can see and invoke at each execution phase, so that privilege escalation via prompt injection is mitigated.

**ID:** REQ-MASK-001  
**Priority:** P0 — Critical  
**Source:** Masking and governance layer (masking.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (masking never grants tools not in available set)  
**Rationale:** GovernancePolicy provides injection-proof enforcement because it runs in Python, not as a prompt instruction.  
**RFC 2119:** MUST  
**Requires:** A configured ToolPolicy or GovernancePolicy on the AgentSpec  
**Ensures:** Model sees only allowed tools; invocations outside current phase are blocked  
**Invariant:** A ToolPolicy can only HIDE available tools, never GRANT new ones

#### Acceptance Criteria

1. THE ToolPolicy SHALL receive a MaskContext and return a subset of available tool names
2. IF a ToolPolicy raises an exception, THEN THE Session SHALL fall back to exposing all available tools (masking must never break a run)
3. WHEN GovernancePolicy.is_allowed returns False for a tool call, THE Session SHALL return an error ToolResultBlock to the model without executing the tool
4. WHEN GovernancePolicy.is_allowed returns False AND an ApprovalGate is configured, THE Session SHALL request human approval before blocking
5. WHEN an unknown phase is queried, THE GovernancePolicy SHALL deny the tool (fail closed)
6. THE GovernancePolicy.copy() method SHALL produce an independent instance so concurrent sessions do not share phase state

### Requirement 6: Prompt-Injection Detection and Content Boundary

**User Story:** As a security engineer, I want the harness to detect prompt-injection attempts in tool output and fence untrusted content, so that injection attacks are surfaced rather than silently executed.

**ID:** REQ-INJECT-001  
**Priority:** P0 — Critical  
**Source:** Boundary layer (boundary.py)  
**Status:** Implemented  
**Verification Method:** Unit test + pattern coverage test  
**Rationale:** Prompt injection is unsolved; honest detection/mitigation is the only defensible claim.  
**RFC 2119:** MUST  
**Requires:** Tool output text to scan; untrusted content to fence  
**Ensures:** scan_for_injection returns pattern names; wrap_untrusted returns fenced text  
**Invariant:** scan_for_injection is a pure function with no side effects

#### Acceptance Criteria

1. WHEN tool output contains text matching the override_instructions pattern, THE scan_for_injection function SHALL return ["override_instructions"]
2. WHEN tool output contains text matching the role_reassignment pattern, THE scan_for_injection function SHALL return ["role_reassignment"]
3. WHEN tool output contains text matching the exfiltration pattern, THE scan_for_injection function SHALL return ["exfiltration"]
4. WHEN tool output contains text matching the fake_system_turn pattern, THE scan_for_injection function SHALL return ["fake_system_turn"]
5. WHEN tool output contains text matching the reveal_system_prompt pattern, THE scan_for_injection function SHALL return ["reveal_system_prompt"]
6. WHEN wrap_untrusted is called with content, THE function SHALL return text containing the TVASTAR_UNTRUSTED_CONTENT sentinel delimiters and a do-not-follow-instructions notice
7. THE documentation SHALL state that injection detection is mitigation, not prevention, and cannot guarantee safety

### Requirement 7: Loop Engineering

**User Story:** As a platform engineer, I want to run agents on a schedule with automatic retry, exponential backoff, circuit breaking, and human handoff, so that autonomous loops self-heal without manual intervention.

**ID:** REQ-LOOP-ENG-001  
**Priority:** P1 — High  
**Source:** Loop layer (loop/__init__.py)  
**Status:** Implemented  
**Verification Method:** Unit test + state-machine property test  
**Rationale:** Production agents must be resilient to transient failures without human babysitting.  
**RFC 2119:** MUST  
**Requires:** An AgentSpec, a LoopConfig, and a Store for checkpointing  
**Ensures:** The Loop transitions through defined states and escalates on exhausted retries  
**Invariant:** Every state transition is checkpointed; a crash at any point is recoverable

#### Acceptance Criteria

1. WHEN a Loop is triggered, THE Loop SHALL transition: IDLE → TRIGGERED → RUNNING → VERIFYING → PASS or FAIL
2. WHEN a run FAILs and iteration < max_iterations, THE Loop SHALL transition to RETRY with exponential backoff (base * 2^(iteration-1) seconds)
3. WHEN retries are exhausted, THE Loop SHALL transition to HANDOFF and invoke the configured HandoffPolicy
4. WHEN consecutive_failures >= circuit_breaker_limit, THE Loop SHALL transition to SUSPENDED
5. WHEN a Loop starts and detects an orphaned RUNNING state from a prior process, THE Loop SHALL mark it INTERRUPTED (crash recovery)
6. IF the HandoffPolicy.escalate raises an exception, THEN THE Loop SHALL transition to HANDOFF_FAILED and retry the handoff up to 3 times
7. THE Loop SHALL checkpoint every state transition to the Store so that process restarts can recover
8. WHEN a LoopConfig.schedule is not "@manual", THE Loop SHALL validate the cron expression at construction time

### Requirement 8: Verifiable Execution and Audit Trail

**User Story:** As a compliance officer, I want every agent run to produce a cryptographically signed, chain-linked receipt with tamper-evident logging, so that I can prove to regulators exactly what the AI did.

**ID:** REQ-ASSURE-001  
**Priority:** P1 — High  
**Source:** Assurance layer (assurance/)  
**Status:** Implemented  
**Verification Method:** Unit test + round-trip property (sign → verify → True)  
**Rationale:** Regulated industries (finance, healthcare) require auditable AI decision trails.  
**RFC 2119:** MUST  
**Requires:** An AssurancePolicy attached to an AgentSpec  
**Ensures:** result.receipt is a signed ExecutionReceipt; TrustLog is append-only and chain-linked  
**Invariant:** receipt.verify() returns True for all unmodified receipts; chain breaks are detectable

#### Acceptance Criteria

1. WHEN an AssurancePolicy is configured, THE Session SHALL produce an ExecutionReceipt for every completed run
2. THE ExecutionReceipt SHALL contain: run_id, agent name, model name, prompt hash, tool_calls with outputs, quality grade, content_hash (SHA-256), and HMAC signature
3. WHEN receipt.verify() is called with the original key, THE method SHALL return True for unmodified receipts
4. WHEN any field of the receipt is modified after signing, THE receipt.verify() method SHALL return False
5. THE TrustLog SHALL link each receipt to the previous via prev_hash, forming a tamper-evident chain
6. WHEN TrustLog.verify_chain() detects a broken link, THE method SHALL return False and identify the corrupted entry
7. IF a TrustLog on_breach callback is configured, THEN THE TrustLog SHALL invoke it immediately when corruption is detected

### Requirement 9: PII/PHI Sanitization

**User Story:** As a compliance officer, I want PII and PHI to be redacted before reaching the model and before being stored in audit logs, so that the system meets HIPAA, PCI-DSS, and GDPR requirements.

**ID:** REQ-SANITIZE-001  
**Priority:** P1 — High  
**Source:** Assurance sanitize layer (assurance/sanitize.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (tokenize → rehydrate round-trip)  
**Rationale:** Regulatory fines for PII in AI training data or logs can exceed $10M.  
**RFC 2119:** MUST  
**Requires:** A SanitizationPolicy and/or TokenVault configured on the AssurancePolicy  
**Ensures:** Sensitive data is replaced with opaque tokens before model inference; rehydrated after  
**Invariant:** vault.rehydrate(vault.tokenize(text)) == text (round-trip)

#### Acceptance Criteria

1. WHEN a TokenVault is configured, THE Session SHALL tokenize the user prompt before sending to model.generate
2. WHEN the run completes, THE Session SHALL rehydrate tokens in RunResult.text
3. THE TokenVault.tokenize function SHALL replace all sensitive patterns with opaque placeholder tokens
4. THE TokenVault.rehydrate function SHALL restore original values from placeholder tokens
5. FOR ALL valid input strings, tokenizing then rehydrating SHALL produce the original string (round-trip property)
6. WHEN SanitizationPolicy.hipaa() is used, THE policy SHALL redact PHI entity types before audit logging
7. WHEN SanitizationPolicy.presidio() is used, THE policy SHALL use ML-powered entity detection for 50+ entity types

### Requirement 10: Context Compaction

**User Story:** As an agent developer, I want conversation history to be automatically summarised when it approaches the model's context limit, so that long-running sessions do not fail with overflow errors.

**ID:** REQ-COMPACT-001  
**Priority:** P1 — High  
**Source:** Compaction layer (compaction.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (compaction preserves last N messages)  
**Rationale:** Without compaction, multi-turn sessions inevitably hit context limits and crash.  
**RFC 2119:** MUST  
**Requires:** A CompactionPolicy attached to the AgentSpec  
**Ensures:** Message history stays within policy bounds; recent messages are preserved  
**Invariant:** After compaction, len(messages) >= policy.keep_last; system prompt is never compacted

#### Acceptance Criteria

1. WHEN message count exceeds CompactionPolicy.max_messages, THE Session SHALL trigger compaction after the current tool turn
2. THE compaction process SHALL preserve the most recent keep_last messages unchanged
3. THE compaction process SHALL summarise earlier messages into a compact notice
4. THE compaction process SHALL never reduce messages below CompactionPolicy.min_messages
5. WHEN a context overflow error is caught, THE Session SHALL force-compact once and retry the model call
6. IF compaction itself raises, THEN THE Session SHALL continue the run without compaction (compaction must never break a run)
7. THE Session SHALL enforce a cooldown of 30 seconds between forced compaction attempts to prevent hammering

### Requirement 11: Durable Execution and Crash Recovery

**User Story:** As a platform engineer, I want agent runs to checkpoint after every tool turn, so that a crash at step 47 of 50 resumes from step 47 rather than restarting.

**ID:** REQ-DURABLE-001  
**Priority:** P1 — High  
**Source:** Durable layer (durable.py)  
**Status:** Implemented  
**Verification Method:** Unit test + crash-resume integration test  
**Rationale:** Long-running agent tasks (10+ minutes) are expensive to repeat from scratch.  
**RFC 2119:** MUST  
**Requires:** A Store (FileStore or InMemoryStore) configured on the Harness  
**Ensures:** Session state is persisted after each tool turn; harness.resume() restores it  
**Invariant:** Checkpointed state is sufficient to continue the loop from the saved point

#### Acceptance Criteria

1. WHEN a Store is configured, THE Session SHALL checkpoint the message history after every completed tool turn
2. WHEN harness.resume(session_id) is called, THE Harness SHALL restore the Session with its full message history
3. IF the checkpoint store is corrupted or unreadable, THEN THE Harness SHALL return None from resume() rather than raising
4. THE checkpoint format SHALL include session ID, message history, and sandbox state (where supported)
5. WHEN a resumed Session receives a new prompt, THE Session SHALL continue the loop from the restored state

### Requirement 12: Cost Tracking and Budget Enforcement

**User Story:** As a platform engineer, I want per-run cost tracking and a hard budget ceiling, so that a runaway agent loop does not produce unbounded API spend.

**ID:** REQ-COST-001  
**Priority:** P1 — High  
**Source:** Cost layer (cost.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (spend never exceeds limit when on_exceed="stop")  
**Rationale:** A single misconfigured loop can accumulate thousands of dollars in model API costs.  
**RFC 2119:** MUST  
**Requires:** A BudgetPolicy attached to the AgentSpec  
**Ensures:** RunResult.cost reflects actual token usage; budget violations halt or raise  
**Invariant:** cost.usd is monotonically non-decreasing within a run

#### Acceptance Criteria

1. THE Session SHALL compute Cost from input_tokens, output_tokens, and model name after each model.generate call
2. WHEN cumulative cost reaches BudgetPolicy.max_usd AND on_exceed="raise", THE Session SHALL raise BudgetExceeded
3. WHEN cumulative cost reaches BudgetPolicy.max_usd AND on_exceed="stop", THE Session SHALL return RunResult with stopped="budget"
4. WHEN cumulative cost reaches BudgetPolicy.max_usd AND on_exceed="approve", THE Session SHALL request approval via the configured ApprovalGate
5. IF approval is denied or times out, THEN THE Session SHALL return RunResult with stopped="budget"
6. THE RunResult.cost field SHALL reflect the total token cost for that run

### Requirement 13: Observability and Tracing

**User Story:** As a platform engineer, I want every model call, tool invocation, and lifecycle event to emit structured spans, so that I can debug agent behaviour in Datadog, Honeycomb, or locally.

**ID:** REQ-OTEL-001  
**Priority:** P2 — Medium  
**Source:** Observability layer (observability.py)  
**Status:** Implemented  
**Verification Method:** Unit test  
**Rationale:** Agents are opaque without tracing; operational debugging requires structured spans.  
**RFC 2119:** SHOULD  
**Requires:** A Tracer with one or more Exporters configured on the Harness  
**Ensures:** Spans emitted for model.generate, tool.invoke, session.prompt, session.task, and lifecycle events  
**Invariant:** Tracer failures are swallowed; observability never breaks a run

#### Acceptance Criteria

1. WHEN a Tracer is configured, THE Session SHALL emit a span for each model.generate call with GenAI semantic attributes
2. WHEN a tool is invoked, THE Session SHALL emit a tool.invoke span with the tool name
3. WHEN a session.prompt, session.skill, or session.task is called, THE Session SHALL emit a wrapping span
4. IF the Tracer or an Exporter raises an exception, THEN THE Session SHALL swallow the error and continue the run
5. THE OTelExporter SHALL emit spans conforming to OpenTelemetry GenAI semantic conventions
6. THE JSONLExporter SHALL append span records as newline-delimited JSON to a file

### Requirement 14: Trace Viewer UI Accessibility

**User Story:** As a user with disabilities, I want the trace viewer UI to meet WCAG 2.2 AA, so that I can inspect agent runs using assistive technology.

**ID:** REQ-A11Y-001  
**Priority:** P2 — Medium  
**Source:** WCAG 2.2 AA compliance obligation  
**Status:** Not assessed  
**Verification Method:** Manual accessibility audit + automated axe-core scan  
**Rationale:** Accessibility is a legal requirement in many jurisdictions (ADA, EN 301 549) and an ethical obligation.  
**RFC 2119:** MUST  
**Requires:** The trace viewer HTML/JS application (src/tvastar/ui/index.html)  
**Ensures:** All WCAG 2.2 AA success criteria are met  
**Invariant:** No new UI change may introduce WCAG 2.2 AA regressions

#### Acceptance Criteria

1. THE Trace_Viewer_UI SHALL provide sufficient colour contrast (minimum 4.5:1 for text, 3:1 for large text) per WCAG 1.4.3
2. THE Trace_Viewer_UI SHALL be fully operable via keyboard navigation per WCAG 2.1.1
3. THE Trace_Viewer_UI SHALL provide visible focus indicators per WCAG 2.4.7
4. THE Trace_Viewer_UI SHALL use semantic HTML elements and ARIA attributes so screen readers can interpret the trace structure per WCAG 4.1.2
5. THE Trace_Viewer_UI SHALL not use colour as the sole means of conveying information (severity levels) per WCAG 1.4.1
6. THE Trace_Viewer_UI SHALL HTML-escape all dynamic content rendered from tool output to prevent stored XSS (also RSK-010)
7. THE Trace_Viewer_UI SHALL provide text alternatives for non-text content per WCAG 1.1.1

### Requirement 15: DAG Task Execution

**User Story:** As an agent developer, I want to define task dependencies as a directed acyclic graph, so that independent tasks run concurrently and the critical path determines wall-clock time.

**ID:** REQ-DAG-001  
**Priority:** P1 — High  
**Source:** Graph layer (graph.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (DAG topological order is preserved)  
**Rationale:** Sequential execution of independent tasks wastes time proportional to the number of tasks.  
**RFC 2119:** MUST  
**Requires:** A TaskGraph with named tasks and explicit depends_on edges  
**Ensures:** Tasks execute in topological order; independent tasks run concurrently  
**Invariant:** No task starts before all its dependencies have completed; cycles are rejected at construction

#### Acceptance Criteria

1. WHEN TaskGraph.run() is called, THE TaskGraph SHALL execute tasks with no dependencies immediately in parallel
2. WHEN all dependencies of a task complete, THE TaskGraph SHALL start that task immediately
3. IF a cycle is detected in the dependency graph, THEN THE TaskGraph SHALL raise ValueError at construction time
4. WHEN all tasks complete, THE GraphResult SHALL contain results keyed by task name with ok=True only if all tasks succeeded
5. THE TaskGraph SHALL inject dependency results into a task's prompt context automatically
6. WHEN a task specifies result=Schema, THE TaskGraph SHALL parse structured output for that task

### Requirement 16: Model Adapter Abstraction

**User Story:** As an agent developer, I want to swap between Anthropic, OpenAI, Ollama, Groq, and 100+ providers with a single interface change, so that I am not locked to any vendor.

**ID:** REQ-MODEL-001  
**Priority:** P1 — High  
**Source:** Model layer (model/)  
**Status:** Implemented  
**Verification Method:** Unit test + adapter conformance test  
**Rationale:** Vendor lock-in increases cost and reduces resilience.  
**RFC 2119:** MUST  
**Requires:** A Model implementation conforming to the Model ABC  
**Ensures:** model.generate() returns a ModelResponse regardless of provider  
**Invariant:** All Model implementations accept and return the same types (Message, ModelResponse, Usage)

#### Acceptance Criteria

1. THE Model ABC SHALL define a single required method: generate(messages, system, tools, max_tokens, temperature, stop_sequences, thinking_level) → ModelResponse
2. THE AnthropicModel SHALL map thinking_level to budget_tokens (low=1024, medium=8000, high=16000)
3. THE OpenAIModel SHALL pass thinking_level as reasoning_effort parameter
4. THE MockModel SHALL accept any generate() call and return scripted responses for testing
5. WHEN a provider SDK is not installed, THE corresponding Model import SHALL raise ImportError with a clear install instruction
6. THE LiteLLMModel SHALL support model_list routing with fallback configuration

### Requirement 17: Human-in-the-Loop Approval

**User Story:** As a platform engineer, I want to gate dangerous tool calls behind human approval, so that irreversible actions require explicit consent.

**ID:** REQ-APPROVE-001  
**Priority:** P1 — High  
**Source:** Approval layer (approval.py)  
**Status:** Implemented  
**Verification Method:** Unit test  
**Rationale:** Autonomous agents making irreversible decisions (delete, charge, deploy) without consent is an unacceptable risk.  
**RFC 2119:** MUST  
**Requires:** An ApprovalGate configured on the AgentSpec or GovernancePolicy  
**Ensures:** Dangerous tool calls pause execution until a human approves or denies  
**Invariant:** Denied approvals never execute the tool; approval records are preserved in the receipt

#### Acceptance Criteria

1. WHEN require_approval() is called within a tool, THE Session SHALL pause execution and request human approval
2. IF the human denies the approval, THEN THE tool SHALL raise ApprovalDenied
3. IF the approval times out, THEN THE tool SHALL raise ApprovalTimeout
4. WHEN an approval is granted, THE Session SHALL record the approval in the run's receipt (who, when, what)
5. THE ApprovalGate SHALL support multiple backends (CLI, webhook, event)
6. WHEN GovernancePolicy blocks a tool AND an ApprovalGate is configured, THE Session SHALL request approval instead of immediately blocking

### Requirement 18: Task Delegation and Depth Control

**User Story:** As an agent developer, I want to delegate sub-tasks to specialist profiles with a hard depth limit, so that agents can collaborate without runaway recursion.

**ID:** REQ-DELEGATE-001  
**Priority:** P1 — High  
**Source:** Profiles and session.task() (profiles.py, session.py)  
**Status:** Implemented  
**Verification Method:** Unit test + property test (depth never exceeds MAX_TASK_DEPTH)  
**Rationale:** Unbounded delegation creates cost explosions and stack overflows.  
**RFC 2119:** MUST  
**Requires:** AgentProfiles registered on the parent AgentSpec  
**Ensures:** Child sessions inherit parent config with profile overrides; depth is bounded  
**Invariant:** task_depth never exceeds MAX_TASK_DEPTH (4); child spec resolution follows: task override > profile > parent

#### Acceptance Criteria

1. WHEN session.task() is called with agent="name", THE Session SHALL create a child session using the named AgentProfile
2. WHEN task_depth reaches MAX_TASK_DEPTH, THE Session SHALL raise RuntimeError
3. THE child spec resolution SHALL follow precedence: task parameter override > profile field > parent spec field
4. WHEN a router is provided and no agent is specified, THE router SHALL select the best-matching profile
5. WHEN cancel_after is specified, THE Session SHALL raise asyncio.TimeoutError if the child exceeds the timeout
6. IF the named profile does not exist, THEN THE Session SHALL raise ValueError listing available profiles

### Requirement 19: Structured Output

**User Story:** As an agent developer, I want to pass a Pydantic model or schema and get back a typed object, so that I can integrate agent output into typed Python code without manual parsing.

**ID:** REQ-STRUCT-001  
**Priority:** P2 — Medium  
**Source:** Session structured-output support (session.py)  
**Status:** Implemented  
**Verification Method:** Unit test + round-trip property test (schema → inject → parse → validate)  
**Rationale:** Agents produce text; typed output eliminates a category of integration bugs.  
**RFC 2119:** SHOULD  
**Requires:** A Pydantic model, dataclass, dict, or callable validator passed as result= parameter  
**Ensures:** RunResult.data is a validated instance of the schema on success  
**Invariant:** On parse failure after retries, data falls back to raw text with a WARNING finding

#### Acceptance Criteria

1. WHEN result= is specified, THE Session SHALL inject a JSON schema instruction into the prompt
2. WHEN the model's response parses successfully against the schema, THE RunResult.data SHALL contain the validated instance
3. IF parsing fails, THEN THE Session SHALL append a correction message and retry up to _STRUCTURED_RETRIES times
4. IF all retries fail, THEN THE Session SHALL set data to raw text and append a structured_output_fallback WARNING finding
5. THE structured output mechanism SHALL support Pydantic v2, Pydantic v1, dataclasses, plain dict, and callable validators

### Requirement 20: MCP Tool Server Integration

**User Story:** As an agent developer, I want to connect any MCP-compliant tool server and have its tools transparently available to the model, so that I can extend capabilities without writing custom tool functions.

**ID:** REQ-MCP-001  
**Priority:** P2 — Medium  
**Source:** MCP layer (mcp/)  
**Status:** Implemented  
**Verification Method:** Unit test + integration test with echo server  
**Rationale:** MCP is the emerging standard for agent tooling; first-class support reduces integration friction.  
**RFC 2119:** SHOULD  
**Requires:** An MCP server accessible via subprocess (command) or HTTP (url)  
**Ensures:** client.tools returns Tool objects wrapping each MCP tool; tools are callable by the model  
**Invariant:** MCP tools are indistinguishable from native @tool functions in the model's view

#### Acceptance Criteria

1. WHEN connect_mcp_server(command=...) is called, THE MCPClient SHALL start the server subprocess and discover available tools
2. WHEN connect_mcp_server(url=...) is called, THE MCPClient SHALL connect via HTTP and discover available tools
3. THE MCPClient.tools property SHALL return a list of Tool objects with name, description, and input_schema derived from the MCP server
4. WHEN the model invokes an MCP tool, THE MCPClient SHALL forward the call to the server and return the result
5. WHEN client.close() is called, THE MCPClient SHALL terminate the server subprocess or close the HTTP connection
6. THE MCP tools SHALL be addable to an AgentSpec's tool list alongside native tools

### Requirement 21: Workflow and Durable Pipelines

**User Story:** As an agent developer, I want to define multi-step pipelines with run history and status tracking, so that complex agent workflows are inspectable and resumable.

**ID:** REQ-WORKFLOW-001  
**Priority:** P2 — Medium  
**Source:** Workflow layer (workflow.py)  
**Status:** Implemented  
**Verification Method:** Unit test  
**Rationale:** Complex agent orchestration needs pipeline-level visibility beyond individual runs.  
**RFC 2119:** SHOULD  
**Requires:** A function decorated with @workflow  
**Ensures:** WorkflowRun with run_id, status, output, error, and events  
**Invariant:** WorkflowRun.status is always one of PENDING, RUNNING, COMPLETED, FAILED

#### Acceptance Criteria

1. WHEN a @workflow function is called via .run(), THE system SHALL create a WorkflowRun with status RUNNING
2. WHEN the workflow function completes, THE WorkflowRun status SHALL transition to COMPLETED with output set to the return value
3. IF the workflow function raises, THEN THE WorkflowRun status SHALL transition to FAILED with error set to the exception message
4. THE RunRegistry SHALL persist completed runs so they can be listed and retrieved by run_id
5. THE WorkflowContext SHALL provide init(spec) → WorkflowHarness for creating agent sessions within the workflow

### Requirement 22: Zero-Dependency Core

**User Story:** As an open-source maintainer, I want the core package to have zero runtime dependencies, so that the install footprint is minimal and supply-chain risk is near zero.

**ID:** REQ-DEPS-001  
**Priority:** P0 — Critical  
**Source:** CON-001, AGENTS.md house rules  
**Status:** Implemented  
**Verification Method:** Automated check (pyproject.toml dependencies = [])  
**Rationale:** Every dependency is a supply-chain attack vector; zero deps means zero surface.  
**RFC 2119:** MUST  
**Requires:** All core modules use only Python stdlib  
**Ensures:** `pip install tvastar` installs nothing beyond tvastar itself  
**Invariant:** pyproject.toml [project].dependencies remains an empty list

#### Acceptance Criteria

1. THE pyproject.toml [project].dependencies list SHALL remain empty
2. WHEN a feature requires a third-party package, THE import SHALL be lazy (inside function body, behind try/except ImportError)
3. IF a lazy import fails, THEN THE code SHALL raise ImportError with a message naming the required extra (e.g., 'pip install "tvastar[anthropic]"')
4. THE CI pipeline SHALL verify that the core test suite passes with zero optional extras installed

### Requirement 23: Tool Retry with Backoff

**User Story:** As an agent developer, I want flaky tool calls (network APIs, rate-limited services) to retry automatically with exponential backoff, so that transient failures do not abort the entire run.

**ID:** REQ-RETRY-001  
**Priority:** P2 — Medium  
**Source:** Tool layer (tools/base.py)  
**Status:** Implemented  
**Verification Method:** Unit test  
**Rationale:** Network calls fail transiently; automatic retry prevents unnecessary run failures.  
**RFC 2119:** SHOULD  
**Requires:** A ToolRetryPolicy on the tool or the AgentSpec  
**Ensures:** Failed tool calls are retried up to max_attempts with exponential backoff + jitter  
**Invariant:** Tool-level retry policy takes precedence over harness-wide policy

#### Acceptance Criteria

1. WHEN a tool raises and a ToolRetryPolicy is configured, THE Harness SHALL retry the call up to max_attempts times
2. THE backoff between retries SHALL be: backoff_base * 2^attempt + random jitter up to jitter seconds
3. THE backoff SHALL be capped at backoff_max seconds
4. WHEN a retryable predicate is configured, THE Harness SHALL only retry exceptions for which retryable returns True
5. WHEN a tool has its own ToolRetryPolicy, THE tool-level policy SHALL override the harness-wide default
6. IF all retries are exhausted, THEN THE Harness SHALL return the final exception as a ToolResultBlock with is_error=True

### Requirement 24: HTTP Serving

**User Story:** As a platform engineer, I want to serve an agent over HTTP with REST and WebSocket endpoints, so that the agent can be consumed by web applications and microservices.

**ID:** REQ-SERVE-001  
**Priority:** P2 — Medium  
**Source:** Serving layer (serving/http.py)  
**Status:** Implemented  
**Verification Method:** Integration test  
**Rationale:** Most production agents are consumed as network services.  
**RFC 2119:** SHOULD  
**Requires:** pip install "tvastar[serve]" (FastAPI + Uvicorn)  
**Ensures:** Agent is accessible via REST POST, WebSocket streaming, and SSE  
**Invariant:** HTTP layer is optional; core never imports fastapi

#### Acceptance Criteria

1. WHEN "tvastar[serve]" is installed, THE CLI SHALL expose a `tvastar serve` command
2. THE HTTP server SHALL expose POST /sessions/{id}/prompt accepting {text} and returning {text, usage, steps, stopped}
3. THE HTTP server SHALL expose WS /sessions/{id}/stream for bidirectional streaming
4. THE HTTP server SHALL expose GET /sessions/{id}/stream?text=... for SSE-based streaming
5. THE HTTP server SHALL expose GET / returning agent health and info
6. WHEN "tvastar[serve]" is NOT installed, THE import of serving modules SHALL raise ImportError with install instructions

---

## Non-Functional Requirements (NFRs)

### NFR-PERF-001: Agent Loop Latency Overhead

**ID:** NFR-PERF-001  
**Priority:** P2 — Medium  
**Category:** Performance  
**Verification Method:** Benchmark  

THE Harness overhead (excluding model API latency) SHALL add no more than 5ms per loop iteration for tool dispatch, masking, and governance checks, measured on a single-core Python 3.12 environment.

### NFR-PERF-002: Memory Efficiency Under Compaction

**ID:** NFR-PERF-002  
**Priority:** P2 — Medium  
**Category:** Performance  
**Verification Method:** Load test  

WHEN CompactionPolicy is active, THE Session memory consumption SHALL remain below memory_cap_mb at steady state during multi-turn conversations exceeding 200 messages.

### NFR-SEC-001: Supply-Chain Integrity

**ID:** NFR-SEC-001  
**Priority:** P0 — Critical  
**Category:** Security  
**Verification Method:** CI pipeline check  

THE project SHALL use PyPI Trusted Publishing (OIDC) with no stored tokens, and the CI pipeline SHALL validate the package with `twine check` before publishing.

### NFR-SEC-002: Credential Isolation

**ID:** NFR-SEC-002  
**Priority:** P0 — Critical  
**Category:** Security  
**Verification Method:** Unit test  

THE CredentialFilter SHALL remove environment variables matching secret patterns before any subprocess spawn, with zero false negatives for the default pattern set.

### NFR-COMPAT-001: Python Version Support

**ID:** NFR-COMPAT-001  
**Priority:** P1 — High  
**Category:** Compatibility  
**Verification Method:** CI matrix  

THE project SHALL pass all tests on Python 3.10, 3.11, 3.12, and 3.13 as verified by the CI pipeline.

### NFR-MAINT-001: Test Coverage

**ID:** NFR-MAINT-001  
**Priority:** P1 — High  
**Category:** Maintainability  
**Verification Method:** Coverage report  

THE test suite SHALL exercise all public API functions exported in __all__ with at least one positive and one negative test case each.

### NFR-AVAIL-001: Graceful Degradation

**ID:** NFR-AVAIL-001  
**Priority:** P1 — High  
**Category:** Availability  
**Verification Method:** Fault-injection test  

WHEN any observability, masking, or compaction subsystem fails, THE Session SHALL continue the agent run by swallowing the error and logging it to the tracer if available.

### NFR-A11Y-001: WCAG 2.2 AA Compliance

**ID:** NFR-A11Y-001  
**Priority:** P2 — Medium  
**Category:** Accessibility  
**Verification Method:** Manual audit + axe-core automated scan  

THE Trace_Viewer_UI SHALL meet all WCAG 2.2 Level AA success criteria including keyboard navigation, colour contrast, focus management, and screen reader compatibility.

---

## Edge Cases (Vidura Principle: Document Consequences, Not Just Requirements)

| ID | Edge Case | Expected Behaviour | Consequence if Mishandled |
|---|---|---|---|
| EDGE-001 | Model returns TOOL_USE with zero tool calls in the list | Session treats as END_TURN | Infinite loop if treated as TOOL_USE without calls |
| EDGE-002 | Tool returns empty string | ToolResultBlock with content="" is valid | Model may re-invoke tool thinking it failed |
| EDGE-003 | Concurrent session.task() calls on same harness | Each gets independent child session | Shared mutable state corrupts both |
| EDGE-004 | CompactionPolicy.keep_last > len(messages) | No compaction occurs (below threshold) | ValueError or empty history if not guarded |
| EDGE-005 | Budget check fires between gather() of concurrent tools | Budget reflects only model tokens, not tool cost | Tools complete even after budget exceeded |
| EDGE-006 | GovernancePolicy.set_phase() called during in-flight tool execution | Phase change affects next tool call, not current | Inconsistent enforcement if not documented |
| EDGE-007 | Model generates stop_reason=MAX_TOKENS mid-sentence | RunResult.text is truncated | User receives incomplete answer without warning |
| EDGE-008 | TrustLog file is deleted between append and verify_chain | verify_chain returns False or raises IOError | Silent compliance gap if not alerted |
| EDGE-009 | resume() called with a session_id from a different AgentSpec | Messages restored but tool registry mismatches | Tool calls fail with ToolNotFound |
| EDGE-010 | Loop trigger() called while SUSPENDED | RuntimeError raised | User confusion if error message is unclear |
| EDGE-011 | MCP server crashes mid-tool-call | MCPClient returns error ToolResultBlock | Hang if no timeout on MCP communication |
| EDGE-012 | auto_topology generates a graph with unreachable nodes | Unreachable tasks never execute | GraphResult.ok=False without clear explanation |

---

## Traceability Matrix

| Requirement ID | Risk Addressed | Constraint | Test Type | Stakeholder |
|---|---|---|---|---|
| REQ-LOOP-001 | RSK-008 | CON-004 | Property + Unit | Agent Developer |
| REQ-DETECT-001 | RSK-003 | CON-006 | Property + Unit | Agent Developer, End User |
| REQ-QUALITY-001 | — | CON-006 | Unit | Platform Engineer |
| REQ-SANDBOX-001 | RSK-001, RSK-002 | CON-001 | Unit + Adversarial | Security Engineer |
| REQ-MASK-001 | RSK-003, RSK-009 | CON-004 | Property + Unit | Security Engineer |
| REQ-INJECT-001 | RSK-003, RSK-010 | CON-006 | Unit + Pattern | Security Engineer |
| REQ-LOOP-ENG-001 | RSK-005 | CON-004 | State-machine Property | Platform Engineer |
| REQ-ASSURE-001 | RSK-006 | CON-002 | Round-trip Property | Compliance Officer |
| REQ-SANITIZE-001 | RSK-006 | CON-002 | Round-trip Property | Compliance Officer |
| REQ-COMPACT-001 | RSK-004 | CON-004 | Property + Unit | Agent Developer |
| REQ-DURABLE-001 | RSK-012 | — | Integration | Platform Engineer |
| REQ-COST-001 | RSK-007 | — | Property + Unit | Platform Engineer |
| REQ-OTEL-001 | — | CON-004 | Unit | Platform Engineer |
| REQ-A11Y-001 | RSK-010 | — | Manual + axe-core | Accessibility User |
| REQ-DAG-001 | — | — | Property + Unit | Agent Developer |
| REQ-MODEL-001 | RSK-011 | CON-001, CON-002 | Conformance | Agent Developer |
| REQ-APPROVE-001 | RSK-009 | — | Unit | Platform Engineer |
| REQ-DELEGATE-001 | — | — | Property + Unit | Agent Developer |
| REQ-STRUCT-001 | RSK-008 | — | Round-trip Property | Agent Developer |
| REQ-MCP-001 | RSK-011 | CON-002 | Integration | Agent Developer |
| REQ-WORKFLOW-001 | — | — | Unit | Agent Developer |
| REQ-DEPS-001 | — | CON-001 | Automated CI | Maintainer |
| REQ-RETRY-001 | — | CON-004 | Unit | Agent Developer |
| REQ-SERVE-001 | — | CON-002 | Integration | Platform Engineer |

---

## Vidura BA Principles Application Summary

| Principle | How Applied |
|---|---|
| **Surface risks before decisions** | 12 risks identified with IF→THEN→IMPACT→MITIGATION grammar before any design decision |
| **Act on analysis even when nobody asked** | Identified RSK-010 (XSS in trace viewer) and RSK-005 (meta-improvement runaway) proactively |
| **Document consequences, not just requirements** | Edge cases table documents what happens if each case is mishandled |
| **Analysis quality over title** | Every requirement passes INCOSE bar: singular, atomic, verifiable, unambiguous |
| **Know when to stop** | Scoped to v0.17.x implemented features; did not invent requirements for unbuilt features |
| **Represent all users** | Six stakeholders identified including accessibility users and OSS contributors |
| **Ask the right questions** | ASM-001 through ASM-006 surface assumptions that, if wrong, break the system |
| **Deliver at the right moment** | Requirements are layered (stakeholder → system → component) for staged implementation |
| **Compliance is not optional** | WCAG 2.2 AA, HIPAA, PCI-DSS, and GDPR requirements are explicit, not aspirational |
