# Threat Model

Threat model for the Tvastar agent framework. Covers all actors, trust boundaries,
security primitives, and residual risks.

---

## Actors

| Actor | Privilege Level | Data Sent to System | Data Received from System |
|-------|----------------|--------------------|-----------------------------|
| **Agent Operator** | Highest — configures agents, deploys fleet, sets security policies | Agent configurations, SecurityPolicy rules, deployment commands, HMAC keys, budget limits | Fleet status, audit logs, compliance reports, alert events |
| **Model Provider** (Anthropic, OpenAI) | External service — no direct system access | Completions (text, tool-use blocks) | Prompts (system + user messages), tool definitions, conversation history |
| **MCP Tool Server** | External — untrusted execution environment | Tool results (arbitrary text/JSON) | Tool invocations (tool name + arguments), authentication headers |
| **End User** | Lowest interactive — submits work, cannot configure agents | Prompts, task descriptions, file uploads | Agent responses, RunResult outputs |
| **Sandbox Process** | Lowest — isolated execution with resource caps | stdout/stderr, exit codes, file artifacts | Shell commands, Python code, filtered environment variables |

---

## Trust Boundaries

| # | Boundary | From → To | Direction | Transport | Data Crossing |
|---|----------|-----------|-----------|-----------|---------------|
| TB-1 | Operator → Framework | Agent Operator → Tvastar Core | Inbound | Python API / config files | Agent specs, policies, credentials |
| TB-2 | Framework → Model Provider | Tvastar Core → Model Provider | Outbound | HTTPS (TLS 1.2+) | Prompts, system messages, tool schemas |
| TB-3 | Model Provider → Framework | Model Provider → Tvastar Core | Inbound | HTTPS response | Completions, tool-use requests |
| TB-4 | Framework → MCP Server | Tvastar MCPClient → MCP Tool Server | Outbound | stdio pipe or HTTPS (Streamable HTTP) | Tool invocation (name + arguments) |
| TB-5 | MCP Server → Framework | MCP Tool Server → Tvastar MCPClient | Inbound | stdio pipe or HTTPS response | Tool results (untrusted text) |
| TB-6 | Framework → Sandbox | Tvastar Core → Sandbox Process | Outbound | Local subprocess (stdin/pipe) | Shell commands, filtered env vars |
| TB-7 | Sandbox → Framework | Sandbox Process → Tvastar Core | Inbound | Local subprocess (stdout/stderr) | Execution output, exit codes |
| TB-8 | End User → Framework | End User → Session/Harness | Inbound | Application-defined (HTTP, CLI, SDK) | Prompts, task descriptions |
| TB-9 | Framework → End User | Session/Harness → End User | Outbound | Application-defined | Agent responses, findings, alerts |
| TB-10 | Framework → Persistent Store | Tvastar → TrustLog / State Backend | Outbound | Local filesystem (JSONL) or network | Execution receipts, shared state |

---

## Security Primitives Mapping

| Primitive | Location | Trust Boundary Protected | Threat Mitigated |
|-----------|----------|--------------------------|------------------|
| **SecurityPolicy** | `src/tvastar/sandbox/base.py` | TB-6 (Framework → Sandbox) | **Arbitrary command execution** — enforces allow/deny lists, blocks dangerous commands and substrings before they reach the subprocess |
| **SecurityPolicy** (MCP fields) | `src/tvastar/sandbox/base.py` | TB-4 (Framework → MCP Server) | **Unauthorized tool invocation** — `allowed_mcp_tools` / `denied_mcp_tools` prevent the agent from calling dangerous or unapproved external tools |
| **CredentialFilter** | `src/tvastar/sandbox/base.py` | TB-6 (Framework → Sandbox) | **Credential leakage to sandbox** — strips secret-pattern env vars from the process environment so agent-executed code cannot read or exfiltrate API keys |
| **TrustLog** | `src/tvastar/assurance/log.py` | TB-10 (Framework → Persistent Store) | **Tampered audit trail** — append-only, hash-chained ledger of ExecutionReceipts; detects post-hoc modification of agent decision history |
| **TokenVault** | `src/tvastar/assurance/sanitize.py` | TB-2 (Framework → Model Provider) | **PII/PHI leakage to model provider** — tokenizes sensitive data (SSN, email, phone) before sending prompts; rehydrates on return |
| **ApprovalGate** | `src/tvastar/approval.py` | TB-6, TB-4 (Framework → Sandbox/MCP) | **Unauthorized dangerous actions** — human-in-the-loop gate pauses execution before irreversible or high-risk operations |
| **scan_for_injection** | `src/tvastar/boundary.py` | TB-5, TB-8 (MCP/User → Framework) | **Prompt injection** — pattern-based detection of instruction-override, role-reassignment, exfiltration, and fake system turn attempts in untrusted content |

---

## Residual Risks

| # | Unprotected Boundary | Threat | Impact | Mitigation Status |
|---|---------------------|--------|--------|-------------------|
| RR-1 | TB-3 (Model Provider → Framework) | **Model-originated prompt injection** — model returns completions containing injected instructions that influence downstream tool calls | Privilege escalation: agent performs unintended actions based on model output | **Partially mitigated** — `scan_for_injection` scans tool results but does not scan model completions for injection payloads targeting downstream tools. Detection-only, no prevention. |
| RR-2 | TB-2 (Framework → Model Provider) | **Prompt exfiltration via model** — sensitive system prompt or conversation context leaked through model provider's logging or training pipelines | Data leakage: proprietary instructions, business logic, or conversation history exposed to third party | **Accepted** — inherent to using hosted model APIs. Operator must evaluate provider data policies. `TokenVault` mitigates PII but not prompt content itself. |
| RR-3 | TB-5 (MCP Server → Framework) | **Malicious tool results** — compromised or rogue MCP server returns crafted output designed to manipulate agent behavior | Privilege escalation: agent follows instructions embedded in tool results | **Partially mitigated** — `wrap_untrusted` fences tool output and `scan_for_injection` flags known patterns, but neither prevents novel injection techniques. |
| RR-4 | TB-8 (End User → Framework) | **User-supplied prompt injection** — end user crafts input to override system prompt or manipulate agent into unauthorized actions | Privilege escalation: agent performs actions outside intended scope | **Partially mitigated** — `scan_for_injection` detects known patterns in user input. No enforcement/blocking mechanism exists; detection raises findings but execution continues. |
| RR-5 | TB-4 (Framework → MCP Server) | **Argument-level data leakage** — permitted MCP tool called with arguments containing sensitive data (credentials, PII) | Data leakage: sensitive data sent to external tool servers | **Planned** — `SecurityPolicy` checks tool names only; argument-level validation (`mcp_argument_validator`) is deferred to a future spec. `TokenVault` partially mitigates if active. |
| RR-6 | TB-6 (Framework → Sandbox) | **Side-channel exfiltration** — sandbox process uses DNS, timing, or filesystem artifacts to leak data despite `CredentialFilter` and `SecurityPolicy` | Data leakage: secrets or computation results exfiltrated through non-obvious channels | **Accepted** — `SecurityPolicy.network` flag exists but is not enforced at OS level. Full containment requires OS-level sandboxing (containers, seccomp) beyond current scope. |
| RR-7 | TB-10 (Framework → Persistent Store) | **TrustLog denial of write** — if backing storage is unavailable, audit entries are lost (in-memory only) | Compliance gap: audit trail incomplete during storage failures | **Accepted** — TrustLog falls back to in-memory storage. No replication or write-ahead log. Operator responsibility to ensure storage availability. |
