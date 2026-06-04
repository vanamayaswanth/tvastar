# Launch notes

Draft copy for announcing Tvastar. Lead with the **product** (`tvastar-fix`),
not the framework — a tool that fixes a real pain spreads; "framework #47"
doesn't. Mention the framework as the thing it's built on.

Before posting: make the GitHub repo public, confirm `pip install tvastar`
works, and have the `examples/self_healing_agent.py` / `proof_groq.py` outputs
handy (or a short GIF/asciinema).

---

## Show HN

**Title:**
Show HN: A GitHub Action that fixes your failing tests – and can't lie about it

**Body:**
I built Tvastar, a small Python agent framework, and its first real app:
`tvastar-fix` — a CLI and GitHub Action that auto-fixes a failing test suite.

The twist most agent tools get wrong: an agent that "fixes tests" is worthless
if it just *says* it fixed them. So `tvastar-fix` doesn't trust the model — after
the agent edits your code, Tvastar **re-runs the suite itself** and reports
success only on the real exit code. It also flags "silent failures" (the model
claiming success over a red run, calling a tool with bad args, looping).

How it works:
- Runs your tests, feeds failures to an agent (read/edit/grep/bash tools).
- The agent iterates in an in-memory sandbox that runs **real Python with no
  Docker** — uses the interpreter you already have.
- Tvastar verifies by re-running the tests; opens a PR only if they pass.

Free to try: it auto-selects a model — Groq's free tier, a local Ollama, or any
OpenAI-compatible endpoint (also OpenAI/Anthropic).

```bash
pip install tvastar
export GROQ_API_KEY=...      # or run `ollama serve`
tvastar-fix
```

GitHub Action:
```yaml
- uses: vanamayaswanth/tvastar/action@v0.2.0
  with: { test-command: "pytest -q", groq-api-key: ${{ secrets.GROQ_API_KEY }} }
```

The framework underneath (Tvastar) is a zero-dependency "agent = model + harness"
core: pluggable models/sandboxes, Markdown skills, MCP client, durable
checkpoint/resume, workflows, sub-agents, structured output, extended thinking,
auto-compaction, tool retries, and the silent-failure detection that powers the
verify step.

Repo: https://github.com/vanamayaswanth/tvastar
PyPI: https://pypi.org/project/tvastar/

Happy to hear where it breaks — especially on real repos with slow or flaky
suites. Honest about limits: it's new, single-author, and best on small,
well-scoped failures today.

---

## Reddit (r/Python, r/devtools)

**Title:**
I made a tool that auto-fixes failing tests and verifies the fix by re-running them

**Body:**
`pip install tvastar` → `tvastar-fix`. An agent reads your test failures, edits
the source, and iterates — then it re-runs the suite and only reports success on
the real exit code (no "trust me, it's fixed"). Runs code in an in-memory
sandbox with **no Docker**, and works with free models (Groq tier / local
Ollama) or any OpenAI-compatible endpoint.

It's the first app built on Tvastar, a small zero-dependency agent framework I
wrote (pluggable models/sandboxes, MCP, workflows, sub-agents, structured
output, durable runs, silent-failure detection).

Repo + docs: https://github.com/vanamayaswanth/tvastar

Feedback welcome — particularly failure cases on real-world test suites.

---

## One-liner / social

> `pip install tvastar` → `tvastar-fix`: an agent fixes your failing tests, and
> proves it by re-running them. No Docker. Free models work.

---

## r/LocalLLaMA angle (lean into local + free)

Same as above, but lead with: works fully **local and free** via Ollama — point
`tvastar-fix` at `llama3.2` and it fixes your tests offline, no API keys, no
Docker. Then mention Groq free tier as the zero-install option.
