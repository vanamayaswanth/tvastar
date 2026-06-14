# Tvastar Patterns Cookbook

Copy-paste recipes for every major Tvastar feature. Each pattern is self-contained and runnable.

---

## 1. One-Shot Run

The simplest possible use: ask a question, get an answer.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.model.anthropic import AnthropicModel

agent = Agent(
    spec=AgentSpec(name="assistant", instructions="You are helpful."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("What is the capital of France?")
print(result.text)         # "Paris"
print(result.stop_reason)  # "end_turn"
```

---

## 2. Multi-Turn Session

Keep a session alive to maintain conversation history.

```python
from tvastar import Agent, AgentSpec, Harness, Session
from tvastar.model.anthropic import AnthropicModel

agent = Agent(
    spec=AgentSpec(name="tutor", instructions="You are a math tutor."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

session = agent.session()

r1 = session.run("What is 12 × 12?")
print(r1.text)  # "144"

r2 = session.run("What about 13 × 13?")
print(r2.text)  # "169" — model remembers context

r3 = session.run("Explain why the pattern works.")
print(r3.text)  # Explanation referencing both previous answers
```

---

## 3. Tools

Register functions the agent can call during its loop.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.tools import tool
from tvastar.model.anthropic import AnthropicModel
import httpx

@tool
def get_weather(city: str) -> str:
    """Return current weather for a city."""
    # In production, call a real weather API
    return f"Sunny, 22°C in {city}"

@tool
def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between temperature units."""
    if from_unit == "celsius" and to_unit == "fahrenheit":
        return value * 9/5 + 32
    if from_unit == "fahrenheit" and to_unit == "celsius":
        return (value - 32) * 5/9
    raise ValueError(f"Unsupported conversion: {from_unit} → {to_unit}")

agent = Agent(
    spec=AgentSpec(
        name="weather",
        instructions="Help users with weather questions.",
        tools=[get_weather, convert_units],
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("What's the weather in Tokyo in Fahrenheit?")
print(result.text)
# Model calls get_weather("Tokyo"), then convert_units(22, "celsius", "fahrenheit")
# → "It's 71.6°F and sunny in Tokyo."
```

---

## 4. Structured Output

Get typed results back instead of raw text.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.model.anthropic import AnthropicModel
from pydantic import BaseModel
from typing import List

class Ingredient(BaseModel):
    name: str
    amount: str
    unit: str

class Recipe(BaseModel):
    title: str
    servings: int
    prep_minutes: int
    cook_minutes: int
    ingredients: List[Ingredient]
    steps: List[str]

agent = Agent(
    spec=AgentSpec(
        name="chef",
        instructions="You are a professional chef. Return structured recipes.",
        result_type=Recipe,
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

result = agent.run("Give me a simple pasta carbonara recipe for 2 people.")
recipe: Recipe = result.value

print(recipe.title)           # "Pasta Carbonara"
print(recipe.servings)        # 2
print(recipe.prep_minutes)    # 10
for ing in recipe.ingredients:
    print(f"  {ing.amount} {ing.unit} {ing.name}")
```

---

## 5. Parallel Fan-Out

Run many prompts concurrently. Useful for batch processing, research, or multi-perspective analysis.

```python
import asyncio
from tvastar import Agent, AgentSpec, Harness
from tvastar.model.anthropic import AnthropicModel

agent = Agent(
    spec=AgentSpec(name="analyst", instructions="Be concise."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

companies = ["Apple", "Google", "Microsoft", "Amazon", "Meta"]

async def main():
    results = await agent.harness.fan_out(
        prompts=[
            f"In one sentence, what does {co} do?"
            for co in companies
        ],
        concurrency=3,   # max 3 in-flight at once
    )
    for company, result in zip(companies, results):
        print(f"{company}: {result.text}")

asyncio.run(main())
```

Fan-out also accepts per-prompt overrides:

```python
async def main():
    results = await agent.harness.fan_out([
        {"prompt": "Write a haiku about rain.", "thinking_level": "low"},
        {"prompt": "Analyze quantum entanglement.", "thinking_level": "high", "max_steps": 10},
        {"prompt": "What is 2+2?", "cancel_after": 5.0},  # timeout in seconds
    ])
```

---

## 6. Agent Profiles and Sub-Tasks

Define reusable agent profiles, then spin up child agents from within a session.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.profiles import AgentProfile, define_agent_profile
from tvastar.model.anthropic import AnthropicModel
from tvastar.tools import tool

# Define reusable profiles
define_agent_profile(AgentProfile(
    name="researcher",
    instructions="You search and summarize information accurately.",
    max_steps=5,
))

define_agent_profile(AgentProfile(
    name="writer",
    instructions="You write clear, engaging prose from bullet points.",
    max_steps=3,
))

@tool
def web_search(query: str) -> str:
    """Search the web."""
    return f"[Search results for: {query}]"  # replace with real search

orchestrator = Agent(
    spec=AgentSpec(
        name="orchestrator",
        instructions="""
        To write a blog post:
        1. Use the 'researcher' profile to gather facts
        2. Use the 'writer' profile to turn facts into prose
        """,
        tools=[web_search],
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

session = orchestrator.session()

# session.task() spawns a child agent with the named profile
research = session.task(
    prompt="Research recent advances in fusion energy",
    agent="researcher",
)
print(research.text)

article = session.task(
    prompt=f"Write a 500-word blog post from these facts:\n{research.text}",
    agent="writer",
)
print(article.text)
```

---

## 7. Workflow — Persistent Multi-Step Pipelines

Workflows survive process restarts and record every step's output.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.workflow import workflow, WorkflowContext
from tvastar.model.anthropic import AnthropicModel

model = AnthropicModel("claude-3-5-haiku-20241022")

@workflow
async def content_pipeline(ctx: WorkflowContext, topic: str) -> str:
    harness = Harness()

    # Step 1: Research
    researcher = Agent(
        spec=AgentSpec(name="researcher", instructions="Research concisely."),
        harness=harness,
        model=model,
    )
    facts = await ctx.step("research", researcher.arun, f"Key facts about {topic}")

    # Step 2: Draft
    writer = Agent(
        spec=AgentSpec(name="writer", instructions="Write engaging prose."),
        harness=harness,
        model=model,
    )
    draft = await ctx.step("draft", writer.arun, f"Write 200 words from: {facts.text}")

    # Step 3: Edit
    editor = Agent(
        spec=AgentSpec(name="editor", instructions="Improve clarity and flow."),
        harness=harness,
        model=model,
    )
    final = await ctx.step("edit", editor.arun, f"Edit this draft: {draft.text}")

    return final.text

import asyncio

async def main():
    run = await content_pipeline.start(topic="quantum computing")
    print(f"Run ID: {run.id}")

    result = await run.wait()
    print(result)

    # Retrieve history later
    history = content_pipeline.history()
    for past_run in history:
        print(f"  {past_run.id}: {past_run.status}")

asyncio.run(main())
```

---

## 8. Dispatch — Webhook and Chatbot Patterns

`dispatch()` sends a prompt to an agent without blocking. Use for webhooks, chat UIs, async APIs.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.dispatch import dispatch, observe
from tvastar.model.anthropic import AnthropicModel

agent = Agent(
    spec=AgentSpec(name="bot", instructions="Answer helpfully."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

# Fire and forget
session_id = dispatch(agent, "Tell me a joke")

# Watch events as they arrive
for event in observe(session_id):
    if event.type == "text_delta":
        print(event.data, end="", flush=True)
    elif event.type == "done":
        break

print()  # newline after streaming output
```

Inject user input mid-run (for interactive chatbots):

```python
from tvastar.dispatch import dispatch, send_input, observe

session_id = dispatch(agent, "I need help with my order.")

for event in observe(session_id):
    if event.type == "tool_call" and event.data["name"] == "ask_user":
        # Agent asked for more info — send it
        send_input(session_id, "Order #12345, placed yesterday")
    elif event.type == "done":
        break
```

---

## 9. Auto-Compaction for Long Sessions

Automatically summarize old messages when context gets large, keeping sessions alive indefinitely.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.compaction import CompactionPolicy
from tvastar.model.anthropic import AnthropicModel

compaction = CompactionPolicy(
    max_tokens=80_000,          # summarize when context exceeds this
    target_tokens=20_000,       # compress down to this size
    keep_last_n=10,             # always keep the 10 most recent messages
    summary_model=None,         # use the same model for summarization
)

agent = Agent(
    spec=AgentSpec(
        name="analyst",
        instructions="You are a long-running data analyst.",
        compaction=compaction,
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

session = agent.session()

# Run hundreds of turns — compaction fires automatically
for i in range(200):
    result = session.run(f"Analyze data point {i}: value={i*3.14:.2f}")
    if i % 10 == 0:
        print(f"Turn {i}: {result.text[:60]}...")
```

Manual compaction on demand:

```python
from tvastar.compaction import compact_session, CompactionPolicy

policy = CompactionPolicy(max_tokens=50_000, target_tokens=10_000)
compact_session(session, policy=policy)
print(f"Messages after compaction: {len(session.messages)}")
```

---

## 10. Tool Retry with Exponential Backoff

Automatically retry flaky tools (network calls, rate-limited APIs).

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.tools import tool, ToolRetryPolicy
from tvastar.model.anthropic import AnthropicModel
import random

# Per-tool retry policy
flaky_retry = ToolRetryPolicy(
    max_attempts=4,
    backoff_base=1.0,   # start with 1s delay
    backoff_max=30.0,   # cap at 30s
    jitter=True,        # add randomness to avoid thundering herd
    retryable=(ConnectionError, TimeoutError, Exception),
)

@tool(retry=flaky_retry)
def fetch_stock_price(ticker: str) -> float:
    """Fetch real-time stock price."""
    if random.random() < 0.5:
        raise ConnectionError("API temporarily unavailable")
    return round(random.uniform(100, 500), 2)

# Harness-wide default retry (applies to all tools without their own policy)
harness_retry = ToolRetryPolicy(max_attempts=2, backoff_base=0.5, jitter=False)

agent = Agent(
    spec=AgentSpec(
        name="trader",
        instructions="Fetch prices and give investment advice.",
        tools=[fetch_stock_price],
        tool_retry=harness_retry,  # default for all tools
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("What's the current price of AAPL?")
print(result.text)
```

---

## 11. Extended Thinking

Give the model more reasoning budget for hard problems.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.model.anthropic import AnthropicModel

agent = Agent(
    spec=AgentSpec(
        name="reasoner",
        instructions="Solve problems step by step.",
        thinking_level="high",   # "low" | "medium" | "high"
        # Maps to budget_tokens: low=1024, medium=8000, high=16000
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-7-sonnet-20250219"),  # thinking-capable model
)

result = agent.run(
    "A farmer has 17 sheep. All but 9 die. How many are left? "
    "Explain your reasoning carefully."
)
print(result.text)
# Model uses extended thinking to avoid the classic trick question mistake
```

Per-prompt thinking level via fan-out:

```python
async def main():
    results = await agent.harness.fan_out([
        {"prompt": "2 + 2?", "thinking_level": "low"},
        {"prompt": "Prove P≠NP", "thinking_level": "high"},
    ])
```

---

## 12. File System Access

Use `harness.fs` to read and write files from tool code.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.tools import tool
from tvastar.model.anthropic import AnthropicModel

harness = Harness(workspace="/tmp/agent_workspace")

@tool
def read_csv(filename: str) -> str:
    """Read a CSV file from the workspace."""
    path = harness.fs.path(filename)
    return path.read_text()

@tool
def write_report(filename: str, content: str) -> str:
    """Write a report file to the workspace."""
    path = harness.fs.path(filename)
    path.write_text(content)
    return f"Written to {filename}"

agent = Agent(
    spec=AgentSpec(
        name="data_agent",
        instructions="Analyze data files and produce reports.",
        tools=[read_csv, write_report],
    ),
    harness=harness,
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("Read sales.csv, find the top 3 products, write a summary to report.txt")
print(result.text)
```

---

## 13. Shell Access

Run shell commands from a sandboxed environment.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.tools import tool
from tvastar.model.anthropic import AnthropicModel

harness = Harness()

@tool
def run_python(code: str) -> str:
    """Execute Python code and return stdout."""
    result = harness.shell.run(["python3", "-c", code], capture_output=True, timeout=10)
    return result.stdout or result.stderr

@tool
def run_shell(command: str) -> str:
    """Run a shell command."""
    result = harness.shell.run(command, shell=True, capture_output=True, timeout=10)
    return result.stdout or result.stderr

agent = Agent(
    spec=AgentSpec(
        name="devtools",
        instructions="Help with coding and system tasks.",
        tools=[run_python, run_shell],
    ),
    harness=harness,
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

result = agent.run("Count the number of Python files in /tmp recursively.")
print(result.text)
```

---

## 14. MCP Server Integration

Connect to any MCP (Model Context Protocol) server to give the agent external tools.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.mcp import MCPServer
from tvastar.model.anthropic import AnthropicModel

# Connect to a local MCP server (stdio transport)
github_mcp = MCPServer(
    name="github",
    command=["npx", "-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."},
)

# Connect to a remote MCP server (HTTP transport)
remote_mcp = MCPServer(
    name="mytools",
    url="https://my-mcp-server.example.com/mcp",
    api_key="sk-...",
)

harness = Harness(mcp_servers=[github_mcp, remote_mcp])

agent = Agent(
    spec=AgentSpec(
        name="devbot",
        instructions="Help with GitHub tasks.",
        # MCP tools are automatically available — no extra registration needed
    ),
    harness=harness,
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

result = agent.run("List open issues in my-org/my-repo labeled 'bug'")
print(result.text)
```

---

## 15. SSE Streaming from HTTP Server

Stream agent responses token-by-token over HTTP using Server-Sent Events.

```python
# server.py
from tvastar import Agent, AgentSpec, Harness
from tvastar.model.anthropic import AnthropicModel
from tvastar.serving.http import create_app
import uvicorn

agent = Agent(
    spec=AgentSpec(name="assistant", instructions="Be helpful."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

app = create_app(agent)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

```bash
# Start a session
curl -X POST http://localhost:8000/sessions \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Tell me a story"}'
# → {"session_id": "sess_abc123", "status": "running"}

# Stream the response (SSE)
curl -N "http://localhost:8000/sessions/sess_abc123/stream"
# → data: {"type": "text_delta", "data": "Once"}
# → data: {"type": "text_delta", "data": " upon"}
# → data: {"type": "text_delta", "data": " a time..."}
# → data: [DONE]
```

JavaScript client:

```javascript
const source = new EventSource(`/sessions/${sessionId}/stream`);

source.onmessage = (event) => {
  if (event.data === "[DONE]") {
    source.close();
    return;
  }
  const { type, data } = JSON.parse(event.data);
  if (type === "text_delta") {
    document.getElementById("output").textContent += data;
  }
};
```

---

## 16. Observability with Tracers

Capture every event in the agent loop for logging, debugging, or analytics.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.tracer import Tracer, TraceEvent
from tvastar.model.anthropic import AnthropicModel
import json
from datetime import datetime

class JsonlTracer(Tracer):
    def __init__(self, path: str):
        self.f = open(path, "a")

    def on_event(self, event: TraceEvent) -> None:
        self.f.write(json.dumps({
            "ts": datetime.utcnow().isoformat(),
            "type": event.type,
            "data": event.data,
        }) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()

tracer = JsonlTracer("/var/log/agent.jsonl")

agent = Agent(
    spec=AgentSpec(
        name="assistant",
        instructions="Help users.",
        tracer=tracer,
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("Explain recursion in one sentence.")
# All events written to /var/log/agent.jsonl:
# {"ts": "...", "type": "run_start", "data": {...}}
# {"ts": "...", "type": "model_request", "data": {...}}
# {"ts": "...", "type": "model_response", "data": {...}}
# {"ts": "...", "type": "run_end", "data": {...}}
```

---

## 17. Silent Failure Detection

Detect when the model gives a low-quality response and take action.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.detection import Detector, DetectionResult
from tvastar.model.anthropic import AnthropicModel

class HallucinationDetector(Detector):
    UNCERTAINTY_PHRASES = [
        "I'm not sure", "I don't know", "I cannot", "I'm unable",
        "I don't have access", "as of my knowledge cutoff",
    ]

    def detect(self, result) -> DetectionResult:
        text = result.text.lower()
        triggered = [p for p in self.UNCERTAINTY_PHRASES if p.lower() in text]
        if triggered:
            return DetectionResult(
                triggered=True,
                label="uncertainty",
                details={"phrases": triggered},
            )
        return DetectionResult(triggered=False)

class ShortResponseDetector(Detector):
    def __init__(self, min_words: int = 20):
        self.min_words = min_words

    def detect(self, result) -> DetectionResult:
        word_count = len(result.text.split())
        if word_count < self.min_words:
            return DetectionResult(
                triggered=True,
                label="too_short",
                details={"word_count": word_count, "minimum": self.min_words},
            )
        return DetectionResult(triggered=False)

agent = Agent(
    spec=AgentSpec(
        name="assistant",
        instructions="Always give thorough, confident answers.",
        detectors=[HallucinationDetector(), ShortResponseDetector(min_words=30)],
    ),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-haiku-20241022"),
)

result = agent.run("Explain photosynthesis.")
for detection in result.detections:
    if detection.triggered:
        print(f"⚠️  Detection: {detection.label} — {detection.details}")
```

---

## 18. Custom Model Adapter

Wrap any LLM in Tvastar's model interface.

```python
from tvastar.model.base import Model, ModelResponse, StreamChunk
from tvastar import Agent, AgentSpec, Harness
from typing import Iterator, Optional, List
import anthropic

class FineTunedModel(Model):
    """Adapter for a fine-tuned or custom-hosted model."""

    def __init__(self, base_url: str, api_key: str, model_id: str):
        self.client = anthropic.Anthropic(base_url=base_url, api_key=api_key)
        self.model_id = model_id

    def generate(
        self,
        messages: List[dict],
        system: Optional[str] = None,
        tools: Optional[List[dict]] = None,
        max_tokens: int = 1024,
        thinking_level: Optional[str] = None,
        **kwargs,
    ) -> ModelResponse:
        response = self.client.messages.create(
            model=self.model_id,
            messages=messages,
            system=system or "",
            tools=tools or [],
            max_tokens=max_tokens,
        )
        return ModelResponse(
            text=response.content[0].text if response.content else "",
            stop_reason=response.stop_reason,
            tool_calls=[...],  # parse tool use blocks
            usage={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        )

    def stream(self, messages, **kwargs) -> Iterator[StreamChunk]:
        with self.client.messages.stream(
            model=self.model_id,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 1024),
        ) as stream:
            for text in stream.text_stream:
                yield StreamChunk(type="text_delta", data=text)

my_model = FineTunedModel(
    base_url="https://my-llm.example.com",
    api_key="sk-...",
    model_id="my-fine-tuned-v1",
)

agent = Agent(
    spec=AgentSpec(name="custom", instructions="Use custom model."),
    harness=Harness(),
    model=my_model,
)
```

---

## 19. Durable Execution

Persist agent state so runs survive crashes and can be resumed.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.durable import DurableStore, durable_session
from tvastar.model.anthropic import AnthropicModel

store = DurableStore(path="/var/tvastar/sessions")  # persists to disk

agent = Agent(
    spec=AgentSpec(name="analyst", instructions="Analyze data step by step."),
    harness=Harness(),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

# Create a durable session — state is checkpointed after each step
with durable_session(agent, store, session_id="analysis-run-001") as session:
    r1 = session.run("Load the dataset and describe its structure.")
    r2 = session.run("Find outliers in the price column.")
    r3 = session.run("Produce a summary report.")
    print(r3.text)

# If the process crashes mid-run, restart with the same session_id:
with durable_session(agent, store, session_id="analysis-run-001") as session:
    # Resumes from last checkpoint — no repeated work
    session.run("Continue from where we left off.")
```

---

## 20. Full Production Stack

Everything together: structured output, compaction, retry, tracing, SSE serving.

```python
from tvastar import Agent, AgentSpec, Harness
from tvastar.compaction import CompactionPolicy
from tvastar.tools import tool, ToolRetryPolicy
from tvastar.tracer import Tracer, TraceEvent
from tvastar.detection import Detector, DetectionResult
from tvastar.serving.http import create_app
from tvastar.model.anthropic import AnthropicModel
from pydantic import BaseModel
from typing import List
import uvicorn, logging

log = logging.getLogger(__name__)

# --- Tracer ---
class LogTracer(Tracer):
    def on_event(self, event: TraceEvent):
        log.info("agent_event type=%s", event.type)

# --- Detector ---
class EmptyResponseDetector(Detector):
    def detect(self, result):
        triggered = not result.text.strip()
        return DetectionResult(triggered=triggered, label="empty_response")

# --- Tools ---
retry_policy = ToolRetryPolicy(max_attempts=3, backoff_base=1.0, jitter=True)

@tool(retry=retry_policy)
def search_knowledge_base(query: str) -> str:
    """Search internal knowledge base."""
    # ... real implementation
    return f"Results for: {query}"

# --- Structured Output ---
class AnalysisResult(BaseModel):
    summary: str
    key_points: List[str]
    confidence: float
    sources: List[str]

# --- Agent ---
agent = Agent(
    spec=AgentSpec(
        name="production_assistant",
        instructions="You are a thorough research assistant.",
        tools=[search_knowledge_base],
        result_type=AnalysisResult,
        compaction=CompactionPolicy(max_tokens=80_000, target_tokens=20_000),
        tool_retry=ToolRetryPolicy(max_attempts=2),
        thinking_level="medium",
        tracer=LogTracer(),
        detectors=[EmptyResponseDetector()],
    ),
    harness=Harness(workspace="/var/agent/workspace"),
    model=AnthropicModel("claude-3-5-sonnet-20241022"),
)

# --- HTTP Server with SSE ---
app = create_app(agent)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=4)
```

---

## 21. Dynamic Capability Governance

Enforce least-privilege at invocation time — after the model has already decided to
call a tool. Tamper-proof against prompt injection because it runs in Python, not
as a prompt instruction.

```python
import asyncio
from tvastar import create_agent, Harness, GovernancePolicy, default_toolset
from tvastar.model import AnthropicModel

gov = GovernancePolicy(
    phases={
        "read":  {"grep", "read_file", "glob"},
        "write": {"grep", "read_file", "glob", "write_file", "bash"},
    },
    current_phase="read",
)

# Wire both masking and governance from the same object:
agent = create_agent(
    "secure-agent",
    model=AnthropicModel(),
    tools=default_toolset(),
    governance=gov,
    tool_policy=gov.as_tool_policy(),   # masking mirrors the current phase live
)

harness = Harness(agent)
sess = harness.session()

async def run():
    # Phase "read" — write_file and bash are both masked and governance-blocked
    r1 = await sess.prompt("List all Python files")
    print(r1.text)

    # Elevate to write — all concurrent sessions are isolated (each has a copy)
    sess.spec.governance.set_phase("write")
    r2 = await sess.prompt("Add a type hint to utils.py")
    print(r2.text)

asyncio.run(run())
```

---

## 22. Transactional Sandbox

Wrap any session step in `harness.transaction()` to guarantee atomic rollback
if the step raises an exception.

```python
import asyncio
from tvastar import create_agent, Harness, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent("coder", model=AnthropicModel(), tools=default_toolset())
harness = Harness(agent)

async def run():
    sess = harness.session()

    try:
        async with harness.transaction(sess) as s:
            # Snapshot taken before entering
            result = await s.prompt("Refactor auth.py and make sure tests pass")
            if not result.ok:
                raise RuntimeError("agent reported failure")
            # Clean exit → snapshot discarded, workspace keeps the changes
    except RuntimeError:
        # Exception escaped → sandbox automatically restored to pre-prompt state
        print("Refactor failed — workspace rolled back")

asyncio.run(run())
```

Manual snapshot / restore without the context manager:

```python
from tvastar.sandbox.virtual import VirtualSandbox

sb = VirtualSandbox({"main.py": "print('hello')"})
snap = sb.snapshot()   # dict[str, str]

sb.fs.write("main.py", "CORRUPTED")
sb.restore(snap)

assert sb.fs.read("main.py") == "print('hello')"
```

---

## 23. Long-Term Memory (LTM)

Consolidate knowledge from each session into a persistent store and inject it
back into the system prompt on subsequent sessions.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.contrib.ltm import LTMStore
from tvastar.memory.store import FileStore
from tvastar.model import AnthropicModel

ltm = LTMStore(FileStore(".ltm"))
model = AnthropicModel()

agent = create_agent(
    "assistant",
    model=model,
    instructions="You are an expert Python engineer.",
    system_prompt_hook=ltm.as_hook(),   # retrieved memories injected per turn
)
harness = Harness(agent)

async def run_and_consolidate(prompt: str, session_id: str):
    result = await harness.run(prompt, session_id=session_id)
    # After session completes, extract and persist facts
    nodes = await ltm.consolidate(result, model, session_id=session_id)
    print(f"Consolidated {len(nodes)} memory nodes")
    return result

async def main():
    # First session — agent learns about the codebase
    await run_and_consolidate("Explore the auth module and fix the flaky test", "s1")

    # Second session — agent recalls relevant facts without re-reading the code
    result = await harness.run("The auth test broke again — what did we learn last time?")
    print(result.text)

asyncio.run(main())
```

Semantic retrieval (optional — install `sentence-transformers`):

```python
ltm = LTMStore(FileStore(".ltm"), semantic=True)   # cosine similarity, model cached on first load
```

---

## 24. System Prompt Hook

Augment the system prompt dynamically before each model call — inject retrieved
context, tenant configuration, or any per-call data without subclassing.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model import AnthropicModel

# Basic hook — no access to the current user message
def add_date(system_prompt: str) -> str:
    from datetime import date
    return f"{system_prompt}\n\nToday's date: {date.today()}"

# Extended hook — receives the most-recent user message for context-aware retrieval
def retrieval_hook(system_prompt: str, *, last_user_text: str = "") -> str:
    query = last_user_text or system_prompt
    docs = my_vector_search(query, k=3)   # your retrieval logic
    if not docs:
        return system_prompt
    block = "\n".join(f"- {d}" for d in docs)
    return f"{system_prompt}\n\n## Retrieved Context\n{block}"

agent = create_agent(
    "rag-agent",
    model=AnthropicModel(),
    instructions="You are a helpful assistant.",
    system_prompt_hook=retrieval_hook,
)

asyncio.run(Harness(agent).run("What are our SLA commitments?"))
```

Hook failures emit a `UserWarning` and fall back to the original prompt —
they cannot crash a live session.

---

## Pattern Quick-Reference

| Goal | Key API |
|---|---|
| One-shot answer | `harness.run(prompt)` |
| Multi-turn chat | `sess = harness.session(); sess.prompt(...)` |
| Typed output | `sess.prompt(..., result=MyModel)` |
| Parallel runs | `harness.fan_out(prompts, concurrency=8)` |
| Child agent | `sess.task(prompt, agent="profile_name")` |
| Persistent pipeline | `@workflow; ctx.init(spec); sess.prompt(...)` |
| Async fire-and-forget | `dispatch(agent, id=user_id, text=msg)` |
| Long sessions | `create_agent(..., compaction=CompactionPolicy(...))` |
| Flaky tools | `@tool(retry=ToolRetryPolicy(...))` |
| Deep reasoning | `create_agent(..., thinking_level="high")` |
| External tools (MCP) | `connect_mcp_server(command="python", args=["server.py"])` |
| Token streaming | `GET /sessions/{id}/stream` (SSE) |
| Audit trail | `Harness(agent, tracer=JSONLExporter("trace.jsonl"))` |
| Quality guard | `create_agent(..., detect=[*default_detectors(), my_detector])` |
| Crash recovery | `Harness(agent, store=FileStore(".state")); harness.resume(sid)` |
| Phase-based governance | `create_agent(..., governance=GovernancePolicy(...))` |
| Atomic rollback | `async with harness.transaction(session) as sess: ...` |
| Cross-session memory | `LTMStore(FileStore(".ltm")); ltm.as_hook()` |
| Dynamic system prompt | `create_agent(..., system_prompt_hook=my_hook)` |
| Session memory limit | `create_agent(..., memory_cap_mb=50)` |
