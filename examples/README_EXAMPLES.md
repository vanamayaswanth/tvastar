# Examples

Start here. Each example is self-contained and runs with `python examples/<file>.py`.

## Where to Begin

| You want to... | Start with |
|----------------|-----------|
| Build your first agent | [`quickstart.py`](quickstart.py) |
| Add tools to an agent | [`coding_agent.py`](coding_agent.py) |
| Use a custom model provider | [`custom_provider.py`](custom_provider.py) |
| Connect MCP tool servers | [`mcp_agent.py`](mcp_agent.py) |
| Run a self-healing loop | [`self_healing_agent.py`](self_healing_agent.py) |
| Detect silent failures | [`detect_silent_failure.py`](detect_silent_failure.py) |
| Use a non-default model (Groq) | [`proof_groq.py`](proof_groq.py) |
| Build an outbound campaign agent | [`outbound_campaign.py`](outbound_campaign.py) |
| Deploy as a GitHub Action | [`deploy/`](deploy/) |

## Prerequisites

```bash
pip install tvastar
# For Anthropic models:
pip install "tvastar[anthropic]"
# For OpenAI models:
pip install "tvastar[openai]"
```

Set your API key:
```bash
export ANTHROPIC_API_KEY=sk-...
# or
export OPENAI_API_KEY=sk-...
```

## Quickstart (10 lines)

```python
from tvastar import Harness, create_agent, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent(
    "my-agent",
    model=AnthropicModel("claude-sonnet-4-6"),
    tools=default_toolset(),
)
result = await Harness(agent).run("What files are in the current directory?")
print(result.text)
```

## What Goes Wrong

If `result.ok` is `False`, check `result.findings`:
```python
if not result.ok:
    for f in result.findings:
        print(f"[{f.severity}] {f.detector}: {f.message}")
```

Common findings:
- `unverified_completion` — agent said "done" but the tool output shows failure
- `thrash_loop` — agent called the same tool with the same args 3+ times
- `step_limit` — agent hit max_steps without completing
