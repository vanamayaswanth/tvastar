# Examples

Each example is self-contained. Run with `python examples/<file>.py`.

## Flagship Examples

| Example | What It Shows | Tvastar Features Used |
|---------|--------------|----------------------|
| [`quickstart.py`](quickstart.py) | First 10 lines | Agent + Harness + tools |
| [`detect_silent_failure.py`](detect_silent_failure.py) | Core differentiator | Silent-failure detectors, quality scoring |
| [`security_remediation_agent.py`](security_remediation_agent.py) | Auto-fix CVEs | Loop, sandbox, governance, budget, receipts, TrustLog |
| [`incident_responder.py`](incident_responder.py) | Auto-triage alerts | Loop, approval gate, delegation, governance phases |
| [`compliance_audit_agent.py`](compliance_audit_agent.py) | HIPAA audit trail | Receipts, TrustLog, PII sanitization, SLA enforcement |
| [`pipeline_generator.py`](pipeline_generator.py) | Generate CI/CD pipelines | Structured output, TaskGraph DAG, governance, observability |
| [`coding_agent.py`](coding_agent.py) | Build + tools | Tools, sandbox, default toolset |
| [`self_healing_agent.py`](self_healing_agent.py) | Loop engineering | Loop, retry, backoff, circuit breaker, handoff |
| [`mcp_agent.py`](mcp_agent.py) | MCP tool servers | MCP client, tool discovery |
| [`deploy/`](deploy/) | Ship it | GitHub Action, Docker, production deploy |

## Prerequisites

```bash
pip install "tvastar[anthropic]"
export ANTHROPIC_API_KEY=sk-...
```

Most examples use `MockModel` so they run **without an API key** for demo purposes. Swap `MockModel(...)` for `AnthropicModel("claude-sonnet-4-6")` to use a real model.

## The Core Idea

```python
from tvastar import Harness, create_agent, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent(
    "my-agent",
    model=AnthropicModel("claude-sonnet-4-6"),
    tools=default_toolset(),
)
result = await Harness(agent).run("Fix the failing tests")

# Tvastar tells you when the agent lied:
if not result.ok:
    for f in result.findings:
        print(f"[{f.severity}] {f.detector}: {f.message}")
```

## Feature Map

| You need... | Look at |
|------------|---------|
| Silent-failure detection | `detect_silent_failure.py` |
| Quality scoring (0-100 + grade) | `detect_silent_failure.py` |
| Loop with retry + backoff + handoff | `self_healing_agent.py`, `incident_responder.py` |
| Phase-based tool governance | `security_remediation_agent.py`, `pipeline_generator.py` |
| Human approval before dangerous actions | `incident_responder.py` |
| Cryptographic audit trail (receipts) | `compliance_audit_agent.py` |
| PII/PHI redaction | `compliance_audit_agent.py` |
| Structured output (Pydantic models) | `pipeline_generator.py` |
| DAG parallel execution (TaskGraph) | `pipeline_generator.py` |
| Sub-agent delegation | `incident_responder.py` |
| MCP tool servers | `mcp_agent.py` |
| Budget/cost enforcement | `security_remediation_agent.py` |
| Sandbox execution | `security_remediation_agent.py`, `coding_agent.py` |
| Observability (tracing) | `pipeline_generator.py` |
| Deploy as GitHub Action | `deploy/` |
