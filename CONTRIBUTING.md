# Contributing to Tvastar

Thanks for your interest in improving Tvastar! This guide gets you set up and
explains the conventions that keep the project small, fast, and reliable.

## Quick start

Tvastar uses [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-fork-url>
cd tvastar
uv venv
uv pip install -e ".[all,dev]"   # all optional features + test tooling
```

Run the test suite and linter:

```bash
uv run pytest -q          # tests (offline, no API keys needed)
uv run ruff check .       # lint
uv run ruff format .      # auto-format
```

Everything must be green before you open a pull request.

## Project principles

These are the rules the codebase is built on. Please keep to them:

1. **The core has zero third-party dependencies.** Anything that needs an
   external package (a model SDK, a web server, OpenTelemetry) goes behind an
   optional extra and is imported **lazily, inside a `try/except ImportError`**
   with a helpful message. `import tvastar` must always succeed.
2. **Nothing observability- or detection-related may break a run.** Tracing and
   failure detectors run in isolation; if they raise, the error is captured, not
   propagated.
3. **Reliability over strictness.** Helpers degrade gracefully (e.g. the schema
   generator falls back to a permissive schema rather than raising).
4. **Tests are offline and deterministic.** Use `MockModel` (scripted) and the
   in-memory `VirtualSandbox`. Don't require network or API keys in tests.
5. **Small, readable, typed.** Match the surrounding style; add type hints and a
   one-line docstring to public functions.

## Adding things

- **A tool:** decorate a function with `@tool`; the JSON schema is derived from
  its type hints. See `tvastar/tools/builtin.py`.
- **A model provider:** if it has an OpenAI-compatible endpoint, no code is
  needed — use `OpenAIModel(base_url=...)`. Otherwise subclass `Model` and
  implement `generate()`. See `examples/custom_provider.py`.
- **A sandbox backend:** implement the `Sandbox` interface. See
  `tvastar/sandbox/`.
- **A failure detector:** write a function `(RunContext) -> list[Finding]` and
  add it to `default_detectors()` (or document it as opt-in). Keep it
  high-precision — a noisy detector is worse than none. See `tvastar/detect/`.
- **A memory store:** subclass the `Store` ABC (`get`, `set`, `delete`, `keys`).
  See `tvastar/memory/store.py` for the interface, `sqlite_store.py` for a
  reference implementation with FTS5 search.
- **An approval gate:** implement `async request(message, *, timeout, metadata)`
  returning `True` or raising `ApprovalDenied`. See `ModelVerifier` in
  `tvastar/approval.py` for a model-based example.
- **A post-tool-hook interceptor:** write a callable matching
  `(tool_name: str, args: dict, result: str) -> str | None`. See
  `ToolOutputCompressor` in `tvastar/compressor.py`.

## Pull requests

- Keep PRs focused; one logical change per PR.
- Add or update tests for any behavior change.
- Update the README if you change public API or add a feature.
- Describe the motivation, not just the diff.

## Reporting bugs

Open an issue with a minimal reproduction (ideally using `MockModel` +
`VirtualSandbox` so it runs anywhere) and what you expected to happen.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
