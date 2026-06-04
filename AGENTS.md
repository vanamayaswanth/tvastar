# AGENTS.md

Guidance for AI coding agents (and humans) working **in this repository**. This
is the contributor-facing companion to `CLAUDE.md` (which maps the codebase).
The `AGENTS.md` convention is the emerging cross-tool standard for agent
instructions; this repo keeps both in sync.

## What this project is

Tvastar is a programmable agent harness for Python: **`Agent = Model + Harness`**.
An `AgentSpec` is an immutable declaration; a `Harness` runs it across sessions.
The core has **zero third-party dependencies** — provider SDKs, the server, and
OpenTelemetry are optional extras, lazy-imported behind `try/except ImportError`.

## Setup, build, test

```bash
uv sync --extra dev            # install dev deps (pytest, ruff)
uv run pytest -q               # run the suite (must stay green)
uv run ruff check .            # lint
uv run ruff format .           # format
uv build && uv run twine check dist/*   # validate the package + README render
```

CI runs lint + format + tests on Python 3.10–3.13. Match it before you push.

## House rules for changes

- **Keep the core dependency-free.** Anything needing a third-party package goes
  behind an optional extra and a lazy import. Never add to `dependencies`.
- **Don't oversell.** Features are documented for what they *actually do*. We
  removed "semantic memory" because TF-IDF didn't earn the word "semantic", and
  injection support is called **detection/mitigation**, never "protection". If a
  claim isn't backed by code and a test, it doesn't ship.
- **Verify, don't trust.** The whole brand is catching silent failures. Code
  that reports success must check a real signal (an exit code, a parsed result),
  not a model's say-so. `tvastar-fix` re-runs the suite itself for exactly this.
- **Tracing/masking/compaction must never break a run.** These wrap user code;
  if they raise, swallow it and continue (see the `try/except` + fallback in
  `observability.py`, `masking.py`, `session._maybe_compact`).
- **Public API is `tvastar/__init__.py`.** New public symbols go in both the
  imports and `__all__`. Add a test and a CHANGELOG entry.
- **Tests are async-mode pytest** (`asyncio_mode = "auto"`). Use `MockModel` so
  the suite runs with no API keys. Set `model.name`/`model.system` on a mock when
  a test depends on pricing or provider attribution.

## Where things live

See `CLAUDE.md` for the full map. Quick pointers:

| Area | File |
| --- | --- |
| Core types | `tvastar/types.py` |
| Agent spec + factory | `tvastar/agent.py` |
| Agent loop | `tvastar/session.py` |
| Tool masking | `tvastar/masking.py` |
| Injection scan / content boundary | `tvastar/boundary.py` |
| Failure detectors | `tvastar/detect/` |
| Observability (OTel GenAI) | `tvastar/observability.py`, `session._genai_*` |

## Release flow

Token-free via PyPI Trusted Publishing (OIDC). Bump `version` in
`pyproject.toml` + `__version__`, update `CHANGELOG.md`, commit, tag `vX.Y.Z`,
create a GitHub Release — `publish.yml` does the rest. No tokens, no manual
upload. See `docs/twelve-factor-agents.md` for how Tvastar maps to the
"12-Factor Agents" production checklist.
