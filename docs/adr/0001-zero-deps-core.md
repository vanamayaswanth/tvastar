# ADR 0001: Zero Runtime Dependencies in Core

## Status

Accepted

## Context

Agent frameworks accumulate transitive dependencies quickly. Each dependency introduces supply-chain attack vectors, version conflict sources, and install-time surprises. Tvastar targets regulated industries (fintech, healthtech) where dependency audits are expensive and every transitive package must be reviewed.

The core package (`src/tvastar/`) needs to remain minimal and auditable. Contributors naturally want to reach for third-party libraries (httpx for HTTP, pydantic for validation, structlog for logging), but each addition increases the install footprint and audit burden for downstream consumers.

**Constraints:**
- Regulated-industry adopters require explicit approval for each dependency
- Supply-chain attacks (typosquatting, compromised maintainers) scale with dependency count
- Version conflicts between tvastar and user applications must be minimized
- `pip install tvastar` should be fast and predictable

## Decision

The core package (`src/tvastar/`) SHALL have zero entries in `pyproject.toml [project].dependencies`. All third-party packages (provider SDKs, FastAPI, OpenTelemetry, Presidio) are optional extras loaded lazily behind `try/except ImportError`.

**Alternatives rejected:**
- **Minimal dependencies (e.g., httpx only):** Still introduces transitive deps and version pinning issues. Even one dependency sets a precedent.
- **Vendoring key libraries:** Maintenance burden of keeping vendored code updated; obscures audit trail.
- **Separate core/extras packages:** Over-engineering for the current project size; confuses the install story.

## Consequences

- `pip install tvastar` installs nothing but tvastar and the Python stdlib. Zero install footprint.
- Every optional import must produce a clear error: `ImportError: pip install tvastar[anthropic]`.
- Contributors must resist adding "just one small dependency" — the policy is absolute for core.
- Some patterns (e.g., async HTTP) use stdlib `asyncio.subprocess` or `urllib.request` instead of `httpx`.
- Validation uses `dataclasses` + `__post_init__` instead of pydantic (see ADR 0003).
- Logging uses stdlib `json` + `sys.stderr` instead of structlog.
- Test: `tests/test_zero_deps.py` verifies the constraint automatically in CI.
- Trade-off: slightly more verbose code in places where a library would be more ergonomic.
