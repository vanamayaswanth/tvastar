# Architecture Decision Records

Key design decisions in Tvastar, recorded at the time they were made.
Format: Context → Decision → Consequences.

---

## ADR-001: Zero Runtime Dependencies in Core

**Date:** 2024-12 (project inception)

**Context:** Agent frameworks accumulate transitive dependencies fast. Each dependency is a supply-chain attack vector, a version conflict source, and an install-time surprise. Tvastar targets regulated industries (fintech, healthtech) where dependency audits are expensive.

**Decision:** The core package (`src/tvastar/`) SHALL have zero entries in `pyproject.toml [project].dependencies`. All third-party packages (provider SDKs, FastAPI, OpenTelemetry, Presidio) are optional extras loaded lazily behind `try/except ImportError`.

**Consequences:**
- `pip install tvastar` installs nothing but tvastar and stdlib. Install footprint is zero.
- Every optional import must produce a clear error: `ImportError: pip install tvastar[anthropic]`.
- Contributors must resist the temptation to add "just one small dependency."
- Some patterns (e.g., async HTTP) use stdlib `asyncio.subprocess` instead of `httpx`.
- Test: `tests/test_zero_deps.py` verifies the constraint automatically.

---

## ADR-002: Fail-Open Masking, Fail-Closed Governance

**Date:** 2025-02

**Context:** Two layers control which tools the model can use:
1. **ToolPolicy (masking)** — hides tools from discovery (model doesn't know they exist)
2. **GovernancePolicy (enforcement)** — blocks invocation (model knows the tool but can't call it)

What happens when either layer errors?

**Decision:**
- ToolPolicy errors → fall back to exposing ALL tools (fail-open). Masking must never break a run (CON-004).
- GovernancePolicy unknown phase → deny ALL tools (fail-closed). Safety enforcement must not silently permit.

**Consequences:**
- A crashing ToolPolicy means the model sees more tools than intended — but the run continues.
- A misconfigured GovernancePolicy phase means the model can't do anything — but no unauthorized action executes.
- This asymmetry is intentional: availability (run completes) vs. security (nothing unauthorized happens).
- Tests: `test_prop_masking.py` Property 12 (fail-open), Property 13 (fail-closed).

---

## ADR-003: Post-Hoc Detection, Not Prevention

**Date:** 2025-01

**Context:** Silent-failure detection is Tvastar's core differentiator. Should detectors run inline (during the loop, potentially halting execution) or post-hoc (after the loop completes)?

**Decision:** Detectors are pure functions that inspect a completed `RunResult`. They never modify messages, never halt execution, and never inject content into the conversation.

**Consequences:**
- Detectors cannot prevent a bad action — they can only report it after the fact.
- The agent loop stays simple: prompt → model → tools → repeat → END_TURN → detect → score.
- Adding a new detector never risks breaking the agent loop.
- Detectors compose trivially: `create_agent(..., detect=[*default_detectors(), my_detector])`.
- Limitation: real-time intervention requires GovernancePolicy, not detectors.

---

## ADR-004: VirtualSandbox Is Not a Security Boundary

**Date:** 2025-03

**Context:** Tvastar offers two sandbox implementations:
- `LocalSandbox` — runs commands in a subprocess with SecurityPolicy checks
- `VirtualSandbox` — runs Python code in-memory (no subprocess, faster for tests)

Should VirtualSandbox be documented as secure?

**Decision:** VirtualSandbox is explicitly documented as **convenience-only, not an isolation boundary**. It executes in the host process namespace. Escape is trivial.

**Consequences:**
- README and docs include a warning: "VirtualSandbox is not a security boundary."
- For untrusted model-generated code, users must use LocalSandbox + SecurityPolicy or a container.
- VirtualSandbox is useful for: unit tests, trusted internal tools, development iteration.
- Risk RSK-001 in the Vidura BA analysis documents this explicitly.

---

## ADR-005: HMAC-SHA256 for Execution Receipts

**Date:** 2025-04

**Context:** ExecutionReceipts need cryptographic integrity verification. Options:
1. RSA/ECDSA signatures (asymmetric — verifier doesn't need signing key)
2. HMAC-SHA256 (symmetric — verifier needs the same key)
3. Post-quantum (PQC) signatures via liboqs

**Decision:** Use HMAC-SHA256 as the default. Provide an `encrypted` optional extra for future PQC support.

**Consequences:**
- Simple, fast, stdlib-only (hashlib + hmac). No cryptography library required for basic receipts.
- The signing key must be shared between signer and verifier (fine for single-organization use).
- For cross-organization verification (auditor doesn't trust the agent operator), asymmetric signatures would be needed — not yet implemented.
- The `pqc` extra was removed in v0.18.0 because `oqs-python` isn't available on PyPI.

---

## ADR-006: Prompt Injection Is Detection/Mitigation, Never "Protection"

**Date:** 2025-01

**Context:** Many frameworks claim to "protect" against prompt injection. No solution exists that guarantees safety.

**Decision:** Tvastar uses the word "detection" and "mitigation" — never "protection" or "prevention." The documentation explicitly states: "cannot guarantee safety."

**Consequences:**
- Brand honesty: we don't oversell (CON-006).
- `wrap_untrusted()` reduces injection risk but doesn't eliminate it.
- `scan_for_injection()` detects patterns but has false negatives.
- GovernancePolicy provides real enforcement (Python-level, not prompt-level).
- The layered defense (masking + governance + detection + boundary) is documented as "mitigation" not "solution."

---

## ADR-007: Loop State Machine with Checkpointed Transitions

**Date:** 2025-05

**Context:** Production agent loops need: retry with backoff, circuit breaking, human handoff, and crash recovery. Where does the state live?

**Decision:** Every Loop state transition is checkpointed to a Store. On restart, the Loop reads its last checkpointed state and recovers (e.g., orphaned RUNNING → INTERRUPTED).

**Consequences:**
- Process crashes at any point are recoverable.
- State machine: IDLE → TRIGGERED → RUNNING → VERIFYING → PASS/FAIL → RETRY/HANDOFF/SUSPENDED.
- The Store abstraction (FileStore, InMemoryStore) enables both local and distributed deployments.
- Backoff formula is deterministic: `base * 2^(iteration-1)` — tested via Property 18.
- Circuit breaker: `consecutive_failures >= limit` → SUSPENDED. Requires manual `loop.reset()`.
