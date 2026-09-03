# Tvastar

[![PyPI](https://img.shields.io/pypi/v/tvastar.svg)](https://pypi.org/project/tvastar/)
[![Python](https://img.shields.io/pypi/pyversions/tvastar.svg)](https://pypi.org/project/tvastar/)
[![CI](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml/badge.svg)](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**A durable Python harness for agents that act on real systems.**

Tvastar gives an agent a controlled runtime for tools, state, sandboxes, sessions, and recovery—then lets you attach task-specific checks, governance, and evidence to the work it performs. Its core is deliberately small: declare an agent, run it through a harness, and add control or assurance layers only when the operating need requires them.

The first reference workflow is **verified CI repair**: run a failing test command, let an agent repair the workspace, and accept success only after Tvastar reruns that same command. The harness is the product; CI repair is the clearest way to see its contract in action.

```
Agent     = Model + Harness
Loop      = Agent + Schedule + Verification + Handoff
Assurance = Evidence + Policy + Receipts
```

## Tvastar product architecture

```text
┌────────────────────────────────────────────────────────────────────┐
│ TVASTAR CORE                                                       │
│ Agent declaration · Harness · Session · Tools · Sandboxes          │
│ Storage · Memory · Models                                          │
│                                                                    │
│ Build and run one capable agent.                                   │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│ TVASTAR CONTROL                                                    │
│ Workflows · Dispatch · Loops · Subagents · Fleet                   │
│ Scheduling · Retry · Handoff · Routing · Coordination              │
│                                                                    │
│ Turn individual runs into managed operational work.                │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│ TVASTAR ASSURANCE                                                  │
│ Findings · Verification · Governance · Approvals · Receipts        │
│ Audit trail · Cost controls · Reliability · Observability          │
│                                                                    │
│ Decide what is allowed, what counts as success, and what occurred. │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│ TVASTAR REFERENCE SOLUTIONS                                        │
│ Verified CI repair · Incident response · Compliance                │
│ Security remediation · Outbound · Other built examples             │
│                                                                    │
│ Concrete applications built from the same Core, Control, and       │
│ Assurance layers.                                                  │
└────────────────────────────────────────────────────────────────────┘
```

| Rack | What it gives you | Start here when… |
|---|---|---|
| **Tvastar Core** | The runtime for defining and running an agent with models, tools, sessions, sandboxes, and state. | You need one agent to complete one task or conversation. |
| **Tvastar Control** | The operational layer for recurring, asynchronous, multi-step, or multi-agent work. | A single harness run needs a workflow, background dispatch, retry, handoff, or routing. |
| **Tvastar Assurance** | The evidence and policy layer around agent action and acceptance. | The work needs verification, approvals, governance, receipts, cost limits, or operational visibility. |
| **Tvastar Reference Solutions** | Inspectable applications that demonstrate the architecture under real workflows. | You want a concrete starting point, especially verified CI repair. |

Start with **Core**. Add **Control** only when work must be operated over time or across agents. Add **Assurance** when an action needs policy, evidence, or an explicit acceptance condition. The reference solutions prove how the same layers combine in real workflows.

For each component, its responsibilities, source location, data flow, and selection guidance, see the [Architecture Map](docs/ARCHITECTURE_MAP.md).

A quality finding can surface suspicious behavior, and a passing named check can validate a defined task. Neither is a universal proof of correctness, security, or safety. See [Benchmarks](docs/BENCHMARKS.md) for the scope and limitations of the repository's detector evaluation.

## Start with a verified repair

Install Tvastar with the model provider you intend to use:

```bash
pip install "tvastar[anthropic]"
export ANTHROPIC_API_KEY="..."
```

Run it from a project with a failing test suite:

```bash
tvastar-fix --path . --test-cmd "pytest -q" --check
```

`tvastar-fix` runs the test command before editing, gives the agent access to the failure, and reruns the same command afterward. `--check` makes the command exit non-zero if the suite is still failing. It reports `already-green`, `fixed`, or `unfixed`; it does **not** push changes or create a pull request for you.

The model resolver also supports `GROQ_API_KEY`, `OPENAI_API_KEY`, a running local Ollama instance, or an explicit OpenAI-compatible endpoint. See the [first-run CI repair guide](docs/GETTING_STARTED.md#first-verified-action-ci-repair) for the supported setup paths.

## Embed the harness in Python

```python
import asyncio

from tvastar import Harness, create_agent, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent(
    "test-fixer",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Inspect the workspace, fix the failing tests, and report what changed.",
    tools=default_toolset(),
)

async def main() -> None:
    result = await Harness(agent).run("Run the tests and fix the underlying defect.")
    print(result.text)
    print(result.ok)  # Runtime/quality outcome; add a named check for task acceptance.

asyncio.run(main())
```

Use a persistent store when a session must survive a process restart:

```python
from tvastar import Harness
from tvastar.memory.store import FileStore

harness = Harness(agent, store=FileStore(".tvastar-state"))
result = await harness.run("Continue the repair.", session_id="ci-repair-42")
resumed = harness.resume("ci-repair-42")
```

`InMemoryStore` is the default, so it does not provide restart recovery. Persistent state is a configuration choice, not an unconditional guarantee.

## Safety and verification boundaries

- **Use a task-specific verifier for a task-specific claim.** Loop verification accepts a `VerificationContract`; a missing, failed, or malformed required verifier fails the loop run. The CI repair workflow's verifier is the independently rerun test command.
- **Detection is post-hoc evidence, not prevention.** Built-in detectors can report failure signals after a run; use governance, approval gates, and an appropriate execution boundary to limit actions before they happen.
- **`VirtualSandbox` is not a security boundary.** It is the convenient default for tests and trusted development. For untrusted model-generated code, use `LocalSandbox` with a tight `SecurityPolicy` or a container/remote sandbox appropriate to your threat model.
- **Receipts are integrity evidence, not third-party attestation.** See the [threat model](docs/threat-model.md) for trust boundaries and remaining risks.

## Documentation

The full documentation map is in **[docs/README.md](docs/README.md)**.

| Start here | Build and extend | Operate and govern |
|---|---|---|
| [Getting Started](docs/GETTING_STARTED.md) | [Usage Guide](docs/USAGE.md) | [Threat Model](docs/threat-model.md) |
| [Examples](examples/README.md) | [API Reference](docs/API.md) | [SLOs](docs/slo.md) |
| [Cookbook](docs/COOKBOOK.md) | [Cookbook recipes](docs/COOKBOOK.md#core-concepts) | [Failure Modes](docs/failure-modes.md) and [Runbooks](docs/runbooks/) |
| [Benchmarks and limitations](docs/BENCHMARKS.md) | [Architecture map](docs/ARCHITECTURE_MAP.md) and [decisions](docs/ARCHITECTURE.md) | [Security Policy](SECURITY.md) |

Advanced control-plane features—loops, workflows, dispatch, multi-agent Fleet, and MCP—are documented as optional compositions. Start with the harness and a concrete success condition first.

## Install extras

```bash
pip install "tvastar[anthropic]"  # Anthropic models
pip install "tvastar[openai]"     # OpenAI-compatible providers and Ollama
pip install "tvastar[litellm]"    # LiteLLM provider integration
pip install "tvastar[serve]"      # HTTP/WebSocket serving
pip install "tvastar[all]"        # Common optional integrations
```

Tvastar requires **Python 3.11+**. The core package has no runtime dependencies; integrations are optional extras.

## CLI

```bash
tvastar run agent.py:agent "summarize this report"  # one-shot prompt
tvastar chat agent.py:agent                          # interactive session
tvastar serve agent.py:agent --port 8000             # HTTP/WebSocket server
tvastar quality agent.py:agent "review this change" # run and inspect quality
tvastar-fix --test-cmd "pytest -q" --check           # verified test repair
tvastar-ci run                                        # configured local CI cycle
tvastar loop --help                                   # scheduled/retrying loop tools
```

## Contributing and security

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

## License

[Apache 2.0](LICENSE)
