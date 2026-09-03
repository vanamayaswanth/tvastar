# Tvastar examples

Each example is self-contained. Run one with `python examples/<file>.py` from the repository root.

## Choose your starting point

| You want to… | Start with | Why |
|---|---|---|
| See Tvastar verify a real outcome | [`self_healing_agent.py`](self_healing_agent.py) | **Flagship:** creates failing tests, runs `pytest`, repairs code, reruns `pytest`, and inspects the result |
| Repair a real repository from the CLI | [`tvastar-fix`](../docs/GETTING_STARTED.md#first-verified-action-ci-repair) | Runs your named test command before and after the agent edits |
| Learn the smallest harness | [`quickstart.py`](quickstart.py) | Agent + harness + tools in a minimal program |
| Inspect post-hoc failure evidence | [`detect_silent_failure.py`](detect_silent_failure.py) | Built-in detector findings and quality scoring |

## Flagship: verified CI repair

[`self_healing_agent.py`](self_healing_agent.py) is the reference demonstration for Tvastar's core contract:

1. Seed a deliberately broken `calc.py` and a real `pytest` suite.
2. Run the suite and expose the failure to the agent.
3. Repair the source file.
4. Rerun the suite and inspect the corrected file.

The test execution is real. By default, only the model decisions are scripted with `MockModel`, so the demo can run offline. Set `TVASTAR_REAL=1` and `ANTHROPIC_API_KEY` to let a real Claude model make those decisions. It runs the same agent definition against a `VirtualSandbox` and a policy-restricted `LocalSandbox`.

`VirtualSandbox` is convenient for tests, but it is not a security boundary. Do not use it to isolate untrusted model-generated code.

## Examples by capability

| Example | What it shows | Key Tvastar features |
|---|---|---|
| [`self_healing_agent.py`](self_healing_agent.py) | **Flagship: verified CI repair** | Tools, real test execution, retry, sandbox portability |
| [`quickstart.py`](quickstart.py) | First harness run | Agent, harness, tools |
| [`detect_silent_failure.py`](detect_silent_failure.py) | Post-hoc failure evidence | Findings, detector suite, quality scoring |
| [`coding_agent.py`](coding_agent.py) | Build with workspace tools | Tools, sandbox, default toolset |
| [`security_remediation_agent.py`](security_remediation_agent.py) | Bounded security remediation | Loop, governance, budget, receipts, TrustLog |
| [`incident_responder.py`](incident_responder.py) | Escalation-oriented incident handling | Loop, approval gate, delegation, governance phases |
| [`compliance_audit_agent.py`](compliance_audit_agent.py) | Audit evidence for regulated workflows | Receipts, TrustLog, PII sanitization, SLA enforcement |
| [`pipeline_generator.py`](pipeline_generator.py) | Generate a CI/CD pipeline | Structured output, TaskGraph, governance, observability |
| [`mcp_agent.py`](mcp_agent.py) | MCP tool servers | MCP client, tool discovery |
| [`deploy/`](deploy/) | Deployment material | GitHub Action, Docker, production deployment |

## Prerequisites

Many examples use `MockModel` and can run without an API key. Examples that instantiate a real provider require its optional extra and credentials:

```bash
pip install "tvastar[anthropic]"
export ANTHROPIC_API_KEY="..."
```

Replace `MockModel(...)` with the provider model shown in the example only when you intend to make real model calls.

## Evidence boundaries

A detector finding is a signal about an executed run; it does not repair the failure or prevent an action that already occurred. A named verification command proves only its named condition. Use governance, approval gates, and a suitable sandbox for operations with security or data-loss impact.

For the detector experiment's methodology and limitations, read [Benchmarks](../docs/BENCHMARKS.md).
