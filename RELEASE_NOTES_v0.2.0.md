# Tvastar v0.2.0

**Paste this into the GitHub Release for the `v0.2.0` tag**
(Releases → Draft a new release → choose tag `v0.2.0`).

---

## 🛠️ New: `tvastar-fix` — auto-fix failing tests, verified

Tvastar now ships a real application built on itself: a command and a GitHub
Action that **fix your failing test suite**. An agent reads the failures, edits
the source, and iterates in a no-Docker sandbox — then Tvastar **re-runs the
tests itself** and reports success from the real exit code, never the model's
claim.

```bash
pip install -U tvastar
export GROQ_API_KEY=...        # free tier — or run a local `ollama serve`
tvastar-fix                    # fixes ./ using `pytest -q`
```

As a GitHub Action (opens a PR when CI goes red):

```yaml
- uses: vanamayaswanth/tvastar/action@v0.2.0
  with:
    test-command: "pytest -q"
    groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

### Highlights
- **Verify, don't trust.** Success is decided by re-running the suite, so the
  agent can't fake a green run.
- **Free to try.** Auto-selects a model: Groq free tier → OpenAI → Anthropic →
  local Ollama, or any OpenAI-compatible endpoint via `--model/--base-url`.
- **No Docker.** The agent runs real code in Tvastar's in-memory sandbox.
- **CI-ready.** `--check` gates your pipeline; the composite Action + an example
  PR-opening workflow are included.

## Install / upgrade

```bash
pip install -U tvastar          # or: uv pip install -U tvastar
```

## Full changelog
See [CHANGELOG.md](CHANGELOG.md). Compared to v0.1.0, this release adds the
`tvastar-fix` CLI, the `action/` composite GitHub Action, the example
PR-opening workflow, and tests.

**Full diff:** https://github.com/vanamayaswanth/tvastar/compare/v0.1.0...v0.2.0
