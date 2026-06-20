# Tvastar Patterns Cookbook

Copy-paste recipes for every major Tvastar feature. Each pattern is self-contained and runnable.

---

## 1. One-Shot Run

The simplest possible use: ask a question, get an answer.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "assistant",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="You are helpful.",
)

result = asyncio.run(Harness(spec).run("What is the capital of France?"))
print(result.text)     # "Paris"
print(result.stopped)  # "end_turn"
print(result.ok)       # True
```

---

## 2. Multi-Turn Session

Keep a session alive to maintain conversation history.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "tutor",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a math tutor.",
)

async def main():
    sess = Harness(spec).session()

    r1 = await sess.prompt("What is 12 × 12?")
    print(r1.text)  # "144"

    r2 = await sess.prompt("What about 13 × 13?")
    print(r2.text)  # "169" — model remembers context

    r3 = await sess.prompt("Explain why the pattern works.")
    print(r3.text)

asyncio.run(main())
```

Use `async with sess:` to automatically close the session on exit:

```python
async def main():
    async with Harness(spec).session() as sess:
        r1 = await sess.prompt("step 1")
        r2 = await sess.prompt("step 2")
```

---

## 3. Tools

Register functions the agent can call during its loop.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.tools.base import tool
from tvastar.model.anthropic import AnthropicModel

@tool
def get_weather(city: str) -> str:
    """Return current weather for a city."""
    return f"Sunny, 22°C in {city}"

@tool
def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between temperature units (celsius/fahrenheit)."""
    if from_unit == "celsius" and to_unit == "fahrenheit":
        return value * 9 / 5 + 32
    if from_unit == "fahrenheit" and to_unit == "celsius":
        return (value - 32) * 5 / 9
    raise ValueError(f"Unsupported: {from_unit} → {to_unit}")

spec = create_agent(
    "weather",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Help users with weather questions.",
    tools=[get_weather, convert_units],
)

result = asyncio.run(Harness(spec).run("What's the weather in Tokyo in Fahrenheit?"))
print(result.text)
# Model calls get_weather("Tokyo"), then convert_units(22, "celsius", "fahrenheit")
# → "It's 71.6°F and sunny in Tokyo."
```

---

## 4. Structured Output

Get typed results back instead of raw text.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel
from pydantic import BaseModel

class Ingredient(BaseModel):
    name: str
    amount: str
    unit: str

class Recipe(BaseModel):
    title: str
    servings: int
    prep_minutes: int
    cook_minutes: int
    ingredients: list[Ingredient]
    steps: list[str]

spec = create_agent(
    "chef",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a professional chef. Return structured recipes.",
)

async def main():
    sess = Harness(spec).session()
    result = await sess.prompt(
        "Give me a simple pasta carbonara recipe for 2 people.",
        result=Recipe,
    )
    recipe: Recipe = result.data

    print(recipe.title)    # "Pasta Carbonara"
    print(recipe.servings) # 2
    for ing in recipe.ingredients:
        print(f"  {ing.amount} {ing.unit} {ing.name}")

asyncio.run(main())
```

Plain dicts work too:

```python
result = await sess.prompt("Return the capital of France as JSON", result=dict)
print(result.data)  # {"capital": "Paris"}
```

---

## 5. Parallel Fan-Out

Run many prompts concurrently. Useful for batch processing, research, or multi-perspective analysis.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "analyst",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Be concise.",
)

companies = ["Apple", "Google", "Microsoft", "Amazon", "Meta"]

async def main():
    harness = Harness(spec)
    results = await harness.fan_out(
        [f"In one sentence, what does {co} do?" for co in companies],
        concurrency=3,  # max 3 in-flight at once
    )
    for company, result in zip(companies, results):
        print(f"{company}: {result.text}")

asyncio.run(main())
```

Fan-out also accepts per-prompt overrides:

```python
results = await harness.fan_out([
    {"prompt": "Write a haiku about rain.", "thinking_level": "low"},
    {"prompt": "Analyze quantum entanglement.", "thinking_level": "high", "max_steps": 10},
    {"prompt": "What is 2+2?", "cancel_after": 5.0},
])
```

---

## 6. Agent Profiles and Sub-Tasks

Define reusable agent profiles, then delegate to specialist child agents from within a session.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.profiles import AgentProfile
from tvastar.tools.base import tool
from tvastar.model.anthropic import AnthropicModel

@tool
def web_search(query: str) -> str:
    """Search the web."""
    return f"[Search results for: {query}]"  # replace with real search

spec = create_agent(
    "orchestrator",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Coordinate research and writing tasks.",
    tools=[web_search],
    subagents={
        "researcher": AgentProfile(
            name="researcher",
            instructions="Search and summarise information accurately.",
            max_steps=5,
        ),
        "writer": AgentProfile(
            name="writer",
            instructions="Write clear, engaging prose from bullet points.",
            max_steps=3,
        ),
    },
)

async def main():
    sess = Harness(spec).session()

    # session.task() spawns a child agent with the named profile
    research = await sess.task(
        "Research recent advances in fusion energy",
        agent="researcher",
    )
    print(research.text)

    article = await sess.task(
        f"Write a 500-word blog post from these facts:\n{research.text}",
        agent="writer",
    )
    print(article.text)

asyncio.run(main())
```

---

## 7. Workflow — Persistent Multi-Step Pipelines

Workflows record each step's output and can survive process restarts.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.workflow import workflow, WorkflowContext
from tvastar.model.anthropic import AnthropicModel

model = AnthropicModel("claude-haiku-4-5-20251001")

researcher_spec = create_agent("researcher", model=model, instructions="Research concisely.")
writer_spec     = create_agent("writer",     model=model, instructions="Write engaging prose.")
editor_spec     = create_agent("editor",     model=model, instructions="Improve clarity and flow.")

@workflow
async def content_pipeline(ctx: WorkflowContext) -> str:
    topic = ctx.payload.get("topic", "AI")

    researcher = await ctx.init(researcher_spec)
    facts = await (await researcher.session()).prompt(f"Key facts about {topic}")

    writer = await ctx.init(writer_spec)
    draft = await (await writer.session()).prompt(f"Write 200 words from: {facts.text}")

    editor = await ctx.init(editor_spec)
    final = await (await editor.session()).prompt(f"Edit this draft: {draft.text}")

    return final.text

async def main():
    run = await content_pipeline.run(payload={"topic": "quantum computing"})
    print(f"Run ID: {run.run_id}")
    print(f"Status: {run.status}")
    print(f"Output: {run.output}")

    for past_run in content_pipeline.list_runs():
        print(f"  {past_run.run_id}: {past_run.status}")

asyncio.run(main())
```

---

## 8. Dispatch — Webhook and Chatbot Patterns

`dispatch()` sends a prompt to an agent without blocking. Use for webhooks, chat UIs, and async APIs.

```python
import asyncio
from tvastar import create_agent
from tvastar.dispatch import dispatch, dispatch_and_wait, observe_dispatch
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "bot",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Answer helpfully.",
)

async def main():
    # Observe all dispatches globally (hook fires before main() runs dispatches)
    observe_dispatch(lambda event: print(f"[{event.type}] {event.dispatch_id}"))

    # Fire and forget — returns immediately
    dispatch_id = await dispatch(
        spec,
        id="user_123",
        text="Tell me a joke",
        on_complete=lambda r: print("Done:", r.text),
        on_error=lambda e: print("Error:", e),
        cancel_after=30.0,
    )
    print(f"Dispatched: {dispatch_id}")

    # Block until done — simpler for scripts
    result = await dispatch_and_wait(spec, id="user_456", text="What is 2+2?")
    print(result.text)

asyncio.run(main())
```

---

## 9. Auto-Compaction for Long Sessions

Automatically summarize old messages when context gets large, keeping sessions alive indefinitely.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.compaction import CompactionPolicy
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "analyst",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a long-running data analyst.",
    compaction=CompactionPolicy(
        max_messages=60,           # compact when history exceeds 60 messages
        max_tokens_estimate=80_000,
        keep_last=10,              # always preserve 10 most-recent messages
        min_messages=20,           # don't compact below this floor
    ),
)

async def main():
    sess = Harness(spec).session()
    for i in range(200):
        result = await sess.prompt(f"Analyze data point {i}: value={i * 3.14:.2f}")
        if i % 10 == 0:
            print(f"Turn {i}: {result.text[:60]}...")

asyncio.run(main())
```

Manual compaction on demand:

```python
from tvastar.compaction import compact_session, CompactionPolicy

policy = CompactionPolicy(max_messages=40, keep_last=5)
await compact_session(sess, policy=policy)
print(f"Messages after compaction: {len(sess.messages)}")
```

---

## 10. Tool Retry with Exponential Backoff

Automatically retry flaky tools (network calls, rate-limited APIs).

```python
import asyncio, random
from tvastar import create_agent, Harness
from tvastar.tools.base import tool, ToolRetryPolicy
from tvastar.model.anthropic import AnthropicModel

# Per-tool retry — overrides the harness default
flaky_retry = ToolRetryPolicy(
    max_attempts=4,
    backoff_base=1.0,   # sleep = base * 2^attempt + jitter
    backoff_max=30.0,
    jitter=0.1,         # seconds of random noise added to each sleep
)

@tool(retry=flaky_retry)
def fetch_stock_price(ticker: str) -> float:
    """Fetch real-time stock price."""
    if random.random() < 0.5:
        raise ConnectionError("API temporarily unavailable")
    return round(random.uniform(100, 500), 2)

spec = create_agent(
    "trader",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Fetch prices and give investment advice.",
    tools=[fetch_stock_price],
    tool_retry=ToolRetryPolicy(max_attempts=2, backoff_base=0.5),  # harness-wide default
)

result = asyncio.run(Harness(spec).run("What's the current price of AAPL?"))
print(result.text)
```

---

## 11. Extended Thinking

Give the model more reasoning budget for hard problems.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "reasoner",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Solve problems step by step.",
    thinking_level="high",  # "low" (1 024 tokens) | "medium" (8 000) | "high" (16 000)
)

result = asyncio.run(Harness(spec).run(
    "A farmer has 17 sheep. All but 9 die. How many are left? Explain your reasoning."
))
print(result.text)
# Extended thinking prevents the classic trick-question mistake
```

Per-prompt thinking level via fan-out:

```python
results = await harness.fan_out([
    {"prompt": "2 + 2?", "thinking_level": "low"},
    {"prompt": "Prove P≠NP", "thinking_level": "high"},
])
```

---

## 12. App-Level File Staging (HarnessFS)

Stage input files before an agent run and retrieve output files after.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel
from tvastar.tools.builtin import default_toolset

spec = create_agent(
    "data_agent",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Analyze data files and produce reports.",
    tools=default_toolset(),
)

async def main():
    harness = Harness(spec)

    # Stage input before running
    await harness.fs.write_file("sales.csv", "product,revenue\nA,1000\nB,2000\nC,500\n")

    result = await harness.run("Read sales.csv, find the top product, write report.txt")
    print(result.text)

    # Retrieve agent-written output
    report = await harness.fs.read_file("report.txt")
    print(report)

asyncio.run(main())
```

---

## 13. App-Level Shell

Run host shell commands from harness context (outside the agent sandbox).

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "devtools",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Help with coding and system tasks.",
)

async def main():
    harness = Harness(spec)

    # App-level shell — runs in the host environment, not the sandbox
    py_count = harness.shell("find /tmp -name '*.py' | wc -l")
    print(f"Python files in /tmp: {py_count.strip()}")

    result = await harness.run("Summarize what Python files exist in /tmp.")
    print(result.text)

asyncio.run(main())
```

---

## 14. MCP Server Integration

Connect to any MCP (Model Context Protocol) server to give the agent external tools.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.mcp import connect_mcp_server
from tvastar.model.anthropic import AnthropicModel

async def main():
    # Stdio transport (local process)
    client = await connect_mcp_server(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."},
    )

    spec = create_agent(
        "devbot",
        model=AnthropicModel("claude-sonnet-4-6"),
        instructions="Help with GitHub tasks.",
        tools=client.tools,  # MCP tools injected as regular Tool objects
    )

    result = await Harness(spec).run("List open issues in my-org/my-repo labeled 'bug'")
    print(result.text)

    await client.close()

asyncio.run(main())
```

Remote MCP (HTTP transport):

```python
client = await connect_mcp_server(
    url="https://my-mcp-server.example.com/mcp",
    headers={"Authorization": "Bearer sk-..."},
)
```

---

## 15. SSE Streaming from HTTP Server

Stream agent responses token-by-token over HTTP using Server-Sent Events.

```python
# server.py — needs: pip install tvastar[serve]
import uvicorn
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel
from tvastar.serving.http import create_app

spec = create_agent(
    "assistant",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Be helpful.",
)
harness = Harness(spec)
app = create_app(harness)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

```bash
# Create a session
curl -X POST http://localhost:8000/sessions
# → {"session_id": "sess_abc123"}

# Send a prompt and stream the response (SSE)
curl -N "http://localhost:8000/sessions/sess_abc123/stream?text=Tell+me+a+story"
# data: {"type":"text_delta","data":"Once"}
# data: {"type":"text_delta","data":" upon"}
# data: {"type":"turn_end","data":{...}}
# data: [DONE]

# Or POST to /sessions/{id}/prompt for a blocking response
curl -X POST http://localhost:8000/sessions/sess_abc123/prompt \
     -H "Content-Type: application/json" \
     -d '{"text": "What is 2+2?"}'
```

JavaScript client:

```javascript
const source = new EventSource(`/sessions/${sessionId}/stream?text=Hello`);

source.onmessage = (event) => {
  if (event.data === "[DONE]") { source.close(); return; }
  const { type, data } = JSON.parse(event.data);
  if (type === "text_delta") document.getElementById("out").textContent += data;
};
```

---

## 16. Token Streaming (Python)

Stream tokens directly in Python without the HTTP server.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "assistant",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Be helpful.",
)

async def main():
    sess = Harness(spec).session()
    async for event in sess.stream("Tell me a short story about a robot."):
        if event.type == "text_delta":
            print(event.data.get("text", ""), end="", flush=True)
        elif event.type == "turn_end":
            print()  # newline at end

asyncio.run(main())
```

---

## 17. Observability with Tracers

Capture every event in the agent loop for logging, debugging, or analytics.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.observability import Tracer, ConsoleExporter, JSONLExporter
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "assistant",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Help users.",
)

tracer = Tracer([ConsoleExporter(), JSONLExporter("/var/log/agent.jsonl")])
harness = Harness(spec, tracer=tracer)

result = asyncio.run(harness.run("Explain recursion in one sentence."))
print(result.text)
# Spans auto-emitted: model.generate, session.prompt, tool.invoke,
# context_compacted, event.*, etc.
```

Export to OpenTelemetry:

```python
from tvastar.observability import OTelExporter

tracer = Tracer([OTelExporter(endpoint="http://localhost:4317")])
harness = Harness(spec, tracer=tracer)
```

---

## 18. Silent Failure Detection

Detect when the model gives a low-quality response and surface it as a Finding.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.detect import Finding, Severity, RunContext
from tvastar.model.anthropic import AnthropicModel

UNCERTAINTY_PHRASES = ["I'm not sure", "I don't know", "I cannot", "I'm unable"]

def hallucination_detector(ctx: RunContext) -> list[Finding]:
    text = ctx.final_text.lower()
    triggered = [p for p in UNCERTAINTY_PHRASES if p.lower() in text]
    if triggered:
        return [Finding(
            detector="hallucination",
            severity=Severity.WARNING,
            message="Model expressed uncertainty",
            context={"phrases": triggered},
        )]
    return []

def short_response_detector(ctx: RunContext) -> list[Finding]:
    words = len(ctx.final_text.split())
    if words < 20:
        return [Finding(
            detector="too_short",
            severity=Severity.WARNING,
            message=f"Response only {words} words (minimum 20)",
            context={"word_count": words},
        )]
    return []

spec = create_agent(
    "assistant",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Always give thorough, confident answers.",
    detect=[hallucination_detector, short_response_detector],
)

result = asyncio.run(Harness(spec).run("Explain photosynthesis."))
for finding in result.findings:
    print(f"[{finding.severity.value.upper()}] {finding.message}")
```

---

## 19. Custom Model Adapter

Wrap any LLM in Tvastar's model interface.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.base import Model, ModelResponse
from tvastar.types import Message, Usage, StopReason

class FineTunedModel(Model):
    """Adapter for a fine-tuned or custom-hosted Anthropic-compatible model."""

    def __init__(self, base_url: str, api_key: str, model_id: str):
        import anthropic
        self.client = anthropic.AsyncAnthropic(base_url=base_url, api_key=api_key)
        self._model_id = model_id

    @property
    def name(self) -> str:
        return self._model_id

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None,
        tools,
        max_tokens: int,
        temperature: float,
        stop_sequences=None,
        thinking_level=None,
    ) -> ModelResponse:
        resp = await self.client.messages.create(
            model=self._model_id,
            messages=[{"role": m.role, "content": m.text} for m in messages],
            system=system or "",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        msg = Message(role="assistant", content=resp.content[0].text)
        return ModelResponse(
            message=msg,
            stop_reason=StopReason.END_TURN,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            ),
            raw=resp,
        )

my_model = FineTunedModel(
    base_url="https://my-llm.example.com",
    api_key="sk-...",
    model_id="my-fine-tuned-v1",
)

spec = create_agent("custom", model=my_model, instructions="Use custom model.")
result = asyncio.run(Harness(spec).run("Hello!"))
print(result.text)
```

---

## 20. Durable Execution

Persist agent state so runs survive crashes and can be resumed.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.memory.store import FileStore
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "analyst",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Analyze data step by step.",
)

store = FileStore("/var/tvastar/sessions")

async def first_run():
    harness = Harness(spec, store=store)
    sess = harness.session("analysis-run-001")
    r1 = await sess.prompt("Load the dataset and describe its structure.")
    r2 = await sess.prompt("Find outliers in the price column.")
    r3 = await sess.prompt("Produce a summary report.")
    print(r3.text)

async def resume():
    harness = Harness(spec, store=store)
    # Resume from the last checkpoint — no work is repeated
    sess = harness.resume("analysis-run-001") or harness.session("analysis-run-001")
    result = await sess.prompt("Continue from where we left off.")
    print(result.text)

asyncio.run(first_run())
# asyncio.run(resume())
```

---

## 21. Full Production Stack

Structured output, compaction, retry, tracing, and SSE serving — all together.

```python
import asyncio, logging
import uvicorn
from pydantic import BaseModel
from tvastar import create_agent, Harness
from tvastar.compaction import CompactionPolicy
from tvastar.tools.base import tool, ToolRetryPolicy
from tvastar.observability import Tracer, ConsoleExporter, JSONLExporter
from tvastar.model.anthropic import AnthropicModel
from tvastar.serving.http import create_app

log = logging.getLogger(__name__)

@tool(retry=ToolRetryPolicy(max_attempts=3, backoff_base=1.0))
def search_knowledge_base(query: str) -> str:
    """Search internal knowledge base."""
    return f"Results for: {query}"

class AnalysisResult(BaseModel):
    summary: str
    key_points: list[str]
    confidence: float

spec = create_agent(
    "production_assistant",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a thorough research assistant.",
    tools=[search_knowledge_base],
    compaction=CompactionPolicy(max_messages=60, keep_last=10),
    tool_retry=ToolRetryPolicy(max_attempts=2),
    thinking_level="medium",
)

tracer = Tracer([ConsoleExporter(), JSONLExporter("trace.jsonl")])
harness = Harness(spec, tracer=tracer)
app = create_app(harness)

async def main():
    result = await harness.run("Analyse the latest trends in LLM tooling")
    print(result.text)
    for f in result.findings:
        log.warning("finding: %s %s", f.severity, f.message)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## 22. Transactional Sandbox

Use `VirtualSandbox.snapshot()` / `restore()` for atomic file rollback.

```python
from tvastar.sandbox.virtual import VirtualSandbox

sb = VirtualSandbox({"main.py": "print('hello')"})
snap = sb.snapshot()  # dict[str, str]

sb.fs.write("main.py", "CORRUPTED")
sb.restore(snap)

assert sb.fs.read("main.py") == "print('hello')"
```

---

## 23. Loop — Recurring Automation

`Loop = Agent + Schedule + Verify + Handoff`. Use for CI sweeps, daily triage, PR babysitters, and any scheduled AI job.

```python
import asyncio
from tvastar.loop.patterns import CISweeper
from tvastar.model.anthropic import AnthropicModel

loop = CISweeper(
    model=AnthropicModel("claude-sonnet-4-6"),
    schedule="*/15 * * * *",  # every 15 minutes
    cancel_after=300.0,         # fail-safe timeout
)

# One-shot trigger (blocking)
run = asyncio.run(loop.trigger())
print(run.state)        # LoopState.PASS or LoopState.FAIL
print(run.result_text)
```

CLI equivalent:

```bash
tvastar loop init CISweeper --name my-ci
tvastar loop audit .tvastar/loops/my_ci.py:loop
tvastar loop run   .tvastar/loops/my_ci.py:loop
```

Available patterns: `CISweeper`, `PRBabysitter`, `DailyTriage`, `DependencySweeper`,
`PostMergeCleanup`, `ChangelogDrafter`, `MakerChecker`.

---

## 24. MakerChecker — Two-Agent Verification

Maker proposes; Checker independently verifies before PASS is declared. REJECTED feeds feedback back to Maker for another round.

```python
import asyncio
from tvastar.loop.patterns import MakerChecker
from tvastar.model.anthropic import AnthropicModel

loop = MakerChecker(
    maker_model=AnthropicModel("claude-haiku-4-5-20251001"),  # fast + cheap
    checker_model=AnthropicModel("claude-sonnet-4-6"),         # strong + thorough
    goal="Review the diff in /tmp/changes.patch for security vulnerabilities",
    max_rounds=3,       # Maker+Checker cycles before HANDOFF
    cancel_after=600.0,
)

run = asyncio.run(loop.trigger())
if run.state.value == "pass":
    print("Checker approved:", run.result_text)
else:
    print("Rejected after", run.iterations, "rounds:", run.error or run.result_text)
```

Key design properties:
- Checker error → counted against round limit (fail-safe, not silent-pass)
- No verdict from Checker → REJECTED (fail-safe default)
- `retry_backoff_base=0.0` — feedback is addressed immediately, no sleep between rounds

---

## 25. Loop Readiness Audit (L0 → L3)

Score a loop's production readiness before deploying it.

```python
from tvastar.loop.audit import audit_loop
from tvastar.loop.patterns import CISweeper
from tvastar.model.anthropic import AnthropicModel

loop = CISweeper(
    model=AnthropicModel("claude-sonnet-4-6"),
    schedule="*/15 * * * *",
    cancel_after=120.0,
)

report = audit_loop(loop)
print(f"Level: L{report.level} — {report.name}")  # e.g. "L2 — GATED"
print("Passes:", report.passes)
print("Gaps:  ", report.gaps)                      # must fix to advance
print("Warnings:", report.warnings)               # advisory only
print("Production-ready:", report.is_production_ready)
```

| Level | Name | Requirement |
|-------|------|-------------|
| L0 | MANUAL | Loop exists |
| L1 | OBSERVE | Schedule + handoff configured |
| L2 | GATED | cancel_after set (human can stop) |
| L3 | AUTONOMOUS | Detectors + circuit_breaker_limit (safe to run unattended) |

CI gate (exits 0 only at L3):

```bash
tvastar loop audit .tvastar/loops/ci.py:loop || exit 1
```

---

## 26. Self-Improving Loop (meta_model)

Inspired by Hyperagents: set `meta_model` on a `LoopConfig` and the loop rewrites its own
agent instructions after each FAIL. Improvements are persisted to `FileStore` and applied
to every subsequent run — the loop gets better without human intervention.

```python
import asyncio
from tvastar.loop import Loop, LoopConfig
from tvastar.loop.patterns import _make_agent
from tvastar.memory.store import FileStore
from tvastar.model.anthropic import AnthropicModel

worker_model = AnthropicModel("claude-haiku-4-5-20251001")  # fast, runs the actual task
meta_model   = AnthropicModel("claude-sonnet-4-6")           # stronger, improves instructions

spec = _make_agent(
    "self-improving-ci",
    worker_model,
    instructions=(
        "You are a CI agent. Fix failing tests. Commit and push when green. "
        "Report FAILURE if you cannot fix within 5 minutes."
    ),
    tools=None,
)

config = LoopConfig(
    name="self-improving-ci",
    goal="Keep the build green.",
    schedule="*/15 * * * *",
    cancel_after=300.0,
    meta_model=meta_model,   # ← enables self-improvement
)

store = FileStore(".tvastar-state")   # persist improvements across restarts
loop = Loop(spec, config, store=store)

# After each FAIL, meta_model rewrites `spec.instructions` and the next
# retry uses the improved version automatically.
run = asyncio.run(loop.trigger())
print(run.state)          # LoopState.PASS or LoopState.FAIL

# Inspect the generational archive
for gen in loop.generation_archive:
    print(f"Gen {gen.gen_id}: {gen.state} (score={gen.score})")

best = loop.best_generation()
if best:
    print(f"Best generation: gen {best.gen_id}, score {best.score}")
    print("Instructions that produced it:")
    print(best.instructions_snapshot[:200])
```

The meta-improvement fires as a background task immediately after the FAIL is recorded.
The next retry (after backoff) runs with the improved instructions. A meta-improvement
failure is silently ignored — it must never crash the loop.

---

## 27. MakerChecker with Persistent Rejection Memory

Standard `MakerChecker` feeds checker feedback back within a session. This pattern shows
how persistent rejection memory across sessions prevents the Maker from repeating the
same class of mistakes run after run.

```python
import asyncio
from tvastar.loop.patterns import MakerChecker
from tvastar.memory.store import FileStore
from tvastar.model.anthropic import AnthropicModel

# FileStore makes rejection history survive across process restarts
store = FileStore(".tvastar-state")

loop = MakerChecker(
    maker_model=AnthropicModel("claude-haiku-4-5-20251001"),
    checker_model=AnthropicModel("claude-sonnet-4-6"),
    goal="Write a SQL migration that adds a non-null column to the users table safely",
    max_rounds=3,
    cancel_after=600.0,
    store=store,  # ← enables cross-run persistence
)

# Run 1 — Checker rejects: "missing default value for existing rows"
run1 = asyncio.run(loop.trigger())
print(run1.state)  # LoopState.FAIL or PASS

# Run 2 (next day / next trigger) — Maker now sees the rejection from Run 1
# in its "Cross-Run Rejection History" and avoids the same mistake
run2 = asyncio.run(loop.trigger())
print(run2.state)

# Inspect stored rejection history
import json
raw = store.get(f"loop:{loop.name}:rejection_history")
if raw:
    for entry in json.loads(raw):
        print("Past rejection:", entry[:120])
```

Key properties:
- Last 5 REJECTED checker verdicts are stored (truncated to 500 chars each to avoid context bloat)
- History is scoped by `loop.name` — different loops never share history
- APPROVED runs do not write to rejection history
- History is prepended to the Maker prompt as a "Cross-Run Rejection History" block
- Combine with `meta_model` for both instruction evolution AND rejection memory

---

## 28. HIPAA-Compliant Agent

Sign every receipt with an HMAC key, log to a tamper-evident file, require a
quality score of 80 or above, and escalate low-quality runs to a compliance
officer. PII is redacted before hashing using the HIPAA preset.

```python
import asyncio, os
from tvastar import create_agent, Harness
from tvastar.assurance import AssurancePolicy, TrustLog, SanitizationPolicy
from tvastar.model.anthropic import AnthropicModel

def alert_security_team(r):
    print(f"[SECURITY BREACH] Chain tampered at run {r.run_id}")

def notify_compliance_officer(r):
    print(f"[COMPLIANCE] Quality below SLA: score={r.quality_score} run={r.run_id}")

model = AnthropicModel("claude-sonnet-4-6")

policy = AssurancePolicy(
    key=os.environ["RECEIPT_KEY"],
    log=TrustLog(".tvastar-trust.jsonl", on_breach=lambda r: alert_security_team(r)),
    min_score=80,
    on_fail="escalate",
    on_escalate=lambda r: notify_compliance_officer(r),
    sanitize=SanitizationPolicy.hipaa(),
)
agent = create_agent("clinical-assistant", model=model, assurance=policy)

result = asyncio.run(Harness(agent).run("Summarise patient intake notes."))
print(result.receipt.quality_grade)     # "PASS" | "WARN" | "FAIL"
print(result.receipt.content_hash)      # "sha256:..."
print(result.receipt.verify(os.environ["RECEIPT_KEY"]))  # True
```

---

## 29. SOX 7-Year Retention with Legal Hold

Archive receipts older than seven years as part of a daily compliance job.
A `hold_until` timestamp freezes all archival during active litigation.

```python
from tvastar.assurance import TrustLog, RetentionPolicy

log = TrustLog(".tvastar-trust.jsonl")

# Daily scheduled job — archive anything older than 7 years
count = log.apply_retention(RetentionPolicy(
    max_age_days=365 * 7,
    archive_path=".tvastar-trust-archive.jsonl",
))
print(f"{count} entries archived")

# Freeze everything during litigation (hold_until = epoch of hold expiry)
count = log.apply_retention(RetentionPolicy(
    max_age_days=30,
    hold_until=1800000000.0,   # nothing archived while time.time() < hold_until
))
print(f"{count} entries eligible (hold active, none archived)")
```

`apply_retention()` appends eligible entries to `archive_path` and removes them
from the live log. Pass `archive_path=None` to count eligible entries without
moving them (dry-run mode).

---

## 30. Role-Based TrustLog Access

Restrict who can read receipts by providing a `can_read` predicate. Useful when
the log file is shared across teams with different clearance levels.

```python
from tvastar.assurance import TrustLog

ALLOWED_ROLES = {"auditor", "compliance", "admin"}

log = TrustLog(
    ".tvastar-trust.jsonl",
    can_read=lambda role: role in ALLOWED_ROLES,
    on_breach=lambda r: quarantine(r),
)

# Permitted reads
receipt = log.get("run_abc123", role="auditor")       # OK
entries = list(log.iter_as("compliance"))             # OK

# Denied reads
log.get("run_abc123", role="developer")               # raises PermissionError
list(log.iter_as("intern"))                           # raises PermissionError

# Internal iteration bypasses access control (for verify_chain(), apply_retention())
for r in log:
    print(r.run_id)
```

---

## 31. ML-Powered PII Detection (Presidio)

Use Microsoft Presidio for entity-aware redaction covering 50+ types — names,
organisations, locations, and more — with optional multi-language support.

```python
# pip install tvastar[presidio]
# python -m spacy download en_core_web_lg
from tvastar.assurance import SanitizationPolicy

policy = SanitizationPolicy.presidio(
    languages=["en", "de"],
    # entities=["PERSON", "EMAIL_ADDRESS"],  # omit to cover all 50+ types
    score_threshold=0.5,                     # lower = more aggressive
)

# Extend with custom regex on top of the ML detectors
policy.add_pattern(r"ACCT-\d+", "[ACCOUNT]")
policy.add_pattern(r"MRN-\d{8}", "[MRN]")

print(policy.scrub("Patient John Smith (MRN-00123456) called from 555-867-5309"))
# "Patient [PERSON] ([MRN]) called from [PHONE_NUMBER]"
```

Use `all_pii()` when you need broad regex-only coverage without the Presidio
dependency. Use `presidio()` when you need entity types that regex cannot reliably
detect (free-text names, organisations, addresses).

---

## 32. Generate a Regulator-Ready Audit Report

`ExecutionReceipt.to_audit_report()` renders a human-readable or HTML report
suitable for sharing with auditors or regulators without exposing raw JSONL.

```python
from tvastar.assurance import TrustLog

log = TrustLog(".tvastar-trust.jsonl")

# Fetch a specific receipt (role-gated if can_read is configured)
r = log.get("run_abc123")

# Plain-text report (default)
print(r.to_audit_report())

# HTML report — save to file for email or document archive
html = r.to_audit_report(fmt="html")
from pathlib import Path
Path("audit.html").write_text(html)
```

The report includes: run metadata (agent, model, timestamps, token usage),
a table of tool calls with inputs and outputs, all findings with severity,
quality score and grade, hash chain verification status, and the HMAC signature.

---

## Pattern Quick-Reference

| Goal | Key API |
|---|---|
| One-shot answer | `asyncio.run(Harness(spec).run(prompt))` |
| Multi-turn chat | `sess = Harness(spec).session(); await sess.prompt(...)` |
| Typed output | `await sess.prompt(..., result=MyModel)` → `result.data` |
| Parallel runs | `await harness.fan_out(prompts, concurrency=8)` |
| Child agent | `await sess.task(prompt, agent="profile_name")` |
| Persistent pipeline | `@workflow; ctx.init(spec); (await h.session()).prompt(...)` |
| Async fire-and-forget | `await dispatch(spec, id=user_id, text=msg)` |
| Long sessions | `create_agent(..., compaction=CompactionPolicy(...))` |
| Flaky tools | `@tool(retry=ToolRetryPolicy(...))` |
| Deep reasoning | `create_agent(..., thinking_level="high")` |
| External tools (MCP) | `connect_mcp_server(command="npx", args=[...])` |
| Token streaming (Python) | `async for event in sess.stream(text)` |
| Token streaming (HTTP) | `GET /sessions/{id}/stream?text=...` (SSE) |
| Audit trail | `Harness(spec, tracer=Tracer([JSONLExporter(...)]))` |
| Quality guard | `create_agent(..., detect=[my_detector, ...])` |
| Crash recovery | `Harness(spec, store=FileStore(...)); harness.resume(sid)` |
| File staging | `await harness.fs.write_file(...); harness.run(...)` |
| Recurring automation | `CISweeper(model=..., schedule="...", cancel_after=...)` |
| Two-agent verification | `MakerChecker(maker_model=..., checker_model=..., goal=...)` |
| Readiness gate | `audit_loop(loop).is_production_ready` |
| Self-improving loop | `LoopConfig(..., meta_model=AnthropicModel(...))` |
| Cross-run MakerChecker | `MakerChecker(..., store=FileStore(...))` — rejection history persists |
| Generation archive | `loop.generation_archive` / `loop.best_generation()` |
| Verifiable receipts | `create_agent(..., assurance=AssurancePolicy(key=..., log=TrustLog(...)))` |
| HIPAA-safe receipts | `AssurancePolicy(sanitize=SanitizationPolicy.hipaa(), min_score=80, on_fail="escalate")` |
| SOX archival | `log.apply_retention(RetentionPolicy(max_age_days=365*7, archive_path=...))` |
| Legal hold freeze | `RetentionPolicy(max_age_days=30, hold_until=<epoch>)` — nothing archived |
| Role-gated log reads | `TrustLog(..., can_read=lambda role: role in ALLOWED)` |
| ML PII detection | `SanitizationPolicy.presidio(languages=["en"])` + `pip install tvastar[presidio]` |
| Audit report (text/HTML) | `receipt.to_audit_report()` / `receipt.to_audit_report(fmt="html")` |
| Quality SLA enforcement | `AssurancePolicy(min_score=80, on_fail="raise")` → raises `SLABreached` |
