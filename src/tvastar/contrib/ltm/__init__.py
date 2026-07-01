"""tvastar.contrib.ltm — Long-Term Memory consolidation for tvastar agents.

Cross-session memory in two operations:

* **consolidate(result, model=...)** — after a session ends, call this to
  extract structured facts and procedures from the conversation via a cheap
  LLM summarisation call. Nodes are persisted to a ``Store``. Only runs when
  ``result.ok`` is True (failed runs don't pollute the memory bank).

* **as_hook()** — returns a ``system_prompt_hook`` that retrieves the most
  relevant nodes for the incoming prompt and injects them as a *Recalled
  Memory* block. Wire it into ``create_agent(system_prompt_hook=...)``.

Retrieval is keyword-based by default (zero extra deps, < 10 ms). If
``sentence-transformers`` is installed it switches automatically to cosine
similarity over node embeddings (set ``use_embeddings=True`` on the store).

No tvastar core module imports from here.

Quick start::

    from tvastar.memory import FileStore
    from tvastar.contrib.ltm import LTMStore
    from tvastar import create_agent, Harness

    ltm = LTMStore(FileStore(".ltm"))
    agent = create_agent(
        "assistant",
        model=model,
        system_prompt_hook=ltm.as_hook(),
    )
    harness = Harness(agent)

    async with harness.session() as sess:
        result = await sess.prompt("Fix the auth bug")
    await ltm.consolidate(result, model=model, session_id=sess.id)
    # Next session recalls relevant facts automatically.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from tvastar.memory.store import Store
    from tvastar.model.base import Model
    from tvastar.session import RunResult
    from tvastar.types import Message

__all__ = ["LTMNode", "LTMStore"]

# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------


@dataclass
class LTMNode:
    """A single unit of long-term memory.

    Attributes:
        id:         Short unique id (first 12 chars of a UUID4).
        type:       ``"factual"`` — a concrete fact; ``"procedural"`` — an
                    action sequence that succeeded.
        content:    The human-readable memory text (credential-redacted).
        tags:       Top-10 keywords extracted from content, used for fast
                    keyword retrieval without embeddings.
        session_id: The session that produced this node.
        created_at: Unix timestamp.
    """

    id: str
    type: str  # "factual" | "procedural"
    content: str
    tags: list[str] = field(default_factory=list)
    session_id: str = ""
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------

_REDACT_SUBS: list[tuple[str, str]] = [
    # key = value  /  key: value
    (
        r"(?i)(password|passwd|secret|token|api[_\-]?key|auth[_\-]?key"
        r"|credential|private[_\-]?key)\s*[=:]\s*\S+",
        r"\1=[REDACTED]",
    ),
    # OpenAI-style  sk-...
    (r"\bsk-[A-Za-z0-9]{20,}\b", "[REDACTED]"),
    # AWS access key
    (r"\bAKIA[A-Z0-9]{16}\b", "[REDACTED]"),
    # Long hex tokens (32+ chars)
    (r"\b[0-9a-f]{32,}\b", "[REDACTED]"),
    # Long base64-ish blobs (40+ chars)
    (r"\b[A-Za-z0-9+/]{40,}={0,2}\b", "[REDACTED]"),
]
_REDACT_RES = [(re.compile(p), r) for p, r in _REDACT_SUBS]


def _redact(text: str) -> str:
    """Strip credential-looking strings from a memory node before persisting."""
    for rx, replacement in _REDACT_RES:
        text = rx.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "have",
    "not",
    "are",
    "was",
    "were",
    "been",
    "has",
    "had",
    "but",
    "its",
    "can",
    "will",
    "when",
    "what",
    "all",
    "one",
    "our",
}


def _tokenize(text: str) -> list[str]:
    """Return lower-cased words (3+ chars, not stop words)."""
    return [w for w in re.findall(r"\b[a-z]{3,}\b", text.lower()) if w not in _STOP_WORDS]


def _keyword_score(query_tokens: set[str], node: LTMNode) -> float:
    """BM25-inspired overlap score between query tokens and a node."""
    content_tokens = set(_tokenize(node.content))
    tag_tokens = set(node.tags)
    overlap = len(query_tokens & (content_tokens | tag_tokens))
    if overlap == 0:
        return 0.0
    return overlap / (len(content_tokens) ** 0.5 + 1.0)


def _keyword_retrieve(query: str, nodes: list[LTMNode], k: int) -> list[LTMNode]:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return nodes[:k]
    scored = [(node, _keyword_score(query_tokens, node)) for node in nodes]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [n for n, s in scored[:k] if s > 0.0] or nodes[:k]


def _semantic_retrieve(
    query: str,
    nodes: list[LTMNode],
    k: int,
    *,
    _model_cache: dict = {},  # module-level cache keyed by model name
) -> list[LTMNode]:
    """Cosine-similarity retrieval via sentence-transformers (optional dep).

    The SentenceTransformer model is cached on first load so it is not
    re-instantiated (and re-loaded from disk) on every retrieval call.
    """
    try:
        import numpy as np  # type: ignore[import]
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError:
        return _keyword_retrieve(query, nodes, k)

    _model_name = "all-MiniLM-L6-v2"
    if _model_name not in _model_cache:
        _model_cache[_model_name] = SentenceTransformer(_model_name)
    st_model = _model_cache[_model_name]

    contents = [n.content for n in nodes]
    embeddings = st_model.encode(contents, show_progress_bar=False)
    query_emb = st_model.encode([query], show_progress_bar=False)[0]
    norms = np.linalg.norm(embeddings, axis=1) * np.linalg.norm(query_emb) + 1e-8
    scores = (embeddings @ query_emb) / norms
    top_idx = scores.argsort()[::-1][:k]
    return [nodes[int(i)] for i in top_idx]


# ---------------------------------------------------------------------------
# Extraction via LLM
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a memory extraction assistant. "
    "Return ONLY valid JSON — no markdown fences, no explanation."
)

_EXTRACT_PROMPT = """\
Extract reusable knowledge from the conversation below.

<conversation>
{conversation}
</conversation>

Return JSON with exactly this structure:
{{
  "factual": ["fact 1", "fact 2"],
  "procedural": ["procedure 1", "procedure 2"]
}}

Factual: concrete, specific facts (file paths, config values, bug root-causes, \
system details, dependency versions).
Procedural: successful multi-step sequences worth repeating (how a bug was fixed, \
what command chain worked, what approach succeeded).

Rules:
- Skip pleasantries, failed attempts, and opinions.
- Keep each entry under 200 characters.
- Return empty lists if nothing reusable was found.
"""


_INJECTION_BLOCK = re.compile(
    r"(?i)(ignore\s+(previous|prior|above|all)\s+instructions?|"
    r"disregard\s+(the\s+)?(above|previous|prior)|"
    r"system\s*prompt|you\s+are\s+now|new\s+instructions?|"
    r"forget\s+(everything|all)|override\s+(your\s+)?instructions?)"
)


def _sanitize_for_extraction(text: str, max_chars: int = 400) -> str:
    """Truncate and redact instruction-injection patterns from a message snippet.

    This prevents adversarial user messages from hijacking the cheap extraction
    LLM into persisting attacker-controlled strings as LTM facts.
    """
    truncated = text[:max_chars]
    return _INJECTION_BLOCK.sub("[FILTERED]", truncated)


def _format_conversation(messages: "list[Message]", char_limit: int = 6000) -> str:
    parts: list[str] = []
    for m in messages:
        text = m.text
        if text:
            safe = _sanitize_for_extraction(text)
            parts.append(f"{m.role}: {safe}")
    return "\n".join(parts)[:char_limit]


async def _extract_nodes(
    messages: "list[Message]",
    model: "Model",
    session_id: str,
) -> list[LTMNode]:
    """Call `model` with an extraction prompt; parse JSON → LTMNode list."""
    from tvastar.types import Message as Msg  # avoid circular at module level

    conversation = _format_conversation(messages)
    if not conversation.strip():
        return []

    prompt_text = _EXTRACT_PROMPT.format(conversation=conversation)
    resp = None
    for _attempt in range(2):  # one retry on transient model failure
        try:
            resp = await model.generate(
                [Msg("user", prompt_text)],
                system=_EXTRACT_SYSTEM,
                max_tokens=1024,
                temperature=0.0,
            )
            break
        except Exception:
            resp = None
    if resp is None:
        return []

    raw = resp.message.text.strip()
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []

    nodes: list[LTMNode] = []
    for node_type in ("factual", "procedural"):
        for content in data.get(node_type, []):
            if not isinstance(content, str) or not content.strip():
                continue
            content = content.strip()
            nodes.append(
                LTMNode(
                    id=uuid.uuid4().hex[:12],
                    type=node_type,
                    content=content,
                    tags=_tokenize(content)[:10],
                    session_id=session_id,
                    created_at=time.time(),
                )
            )
    return nodes


# ---------------------------------------------------------------------------
# LTMStore — the public API
# ---------------------------------------------------------------------------


class LTMStore:
    """Persistent long-term memory store for tvastar agents.

    Args:
        store:          A tvastar ``Store`` backend (``FileStore`` for
                        cross-process persistence, ``InMemoryStore`` for tests).
        namespace:      Key prefix used inside the store (default ``"ltm"``).
        max_inject:     Maximum nodes injected into the system prompt per turn.
        use_embeddings: Set ``True`` to use ``sentence-transformers`` for
                        semantic retrieval. Falls back to keyword retrieval
                        if the package is not installed.
    """

    def __init__(
        self,
        store: "Store",
        *,
        namespace: str = "ltm",
        max_inject: int = 5,
        use_embeddings: bool = False,
    ) -> None:
        self._store = store
        self._ns = namespace
        self._max_inject = max_inject
        self._use_embeddings = use_embeddings

    # ---- consolidation ------------------------------------------------------

    async def consolidate(
        self,
        result: "RunResult",
        *,
        model: "Model",
        session_id: str = "",
    ) -> list[LTMNode]:
        """Extract and persist LTM nodes from a completed run.

        No-op and returns ``[]`` when ``result.ok`` is ``False`` — failed runs
        must not pollute the memory bank (§7 risk mitigation).

        Args:
            result:     The ``RunResult`` returned by ``session.prompt()``.
            model:      A cheap model (e.g. Haiku, GPT-4o-mini) to run the
                        extraction prompt. Does not have to be the same model
                        used for the agent.
            session_id: Label attached to each extracted node for traceability.

        Returns:
            The list of ``LTMNode`` objects that were persisted.
        """
        # Gate on whether the session ran to completion, not result.ok.
        # result.ok is False whenever any WARNING finding is present (including
        # structured_parse_failure), but those runs still produced valid
        # conversation knowledge worth preserving.
        if result.stopped != "end_turn":
            return []

        nodes = await _extract_nodes(result.messages, model, session_id)
        for node in nodes:
            node.content = _redact(node.content)
            self._save(node)
        return nodes

    # ---- retrieval ----------------------------------------------------------

    def retrieve(self, query: str, *, k: Optional[int] = None) -> list[LTMNode]:
        """Return the top-k most relevant nodes for the given query string.

        Uses keyword overlap by default. If ``use_embeddings=True`` and
        ``sentence-transformers`` is installed, switches to cosine similarity.
        """
        k = k if k is not None else self._max_inject
        nodes = self._load_all()
        if not nodes:
            return []
        if self._use_embeddings:
            return _semantic_retrieve(query, nodes, k)
        return _keyword_retrieve(query, nodes, k)

    # ---- hook ---------------------------------------------------------------

    def as_hook(self) -> Callable[..., str]:
        """Return a ``system_prompt_hook`` that injects recalled memory.

        Wire into ``create_agent(system_prompt_hook=ltm.as_hook())``.

        The hook accepts an optional ``last_user_text`` keyword argument
        (forwarded by the session when the extended hook signature is detected).
        When present, retrieval is keyed on the actual user intent rather than
        the static agent instructions string, making recalled memories
        contextually relevant to each turn.

        Extended signature (auto-detected by AgentSpec.build_system_prompt)::

            hook(system_prompt: str, *, last_user_text: str = "") -> str
        """

        def hook(system_prompt: str, *, last_user_text: str = "") -> str:
            # Use the user's actual message as the retrieval query when available;
            # fall back to the system prompt for the very first turn.
            query = last_user_text.strip() or system_prompt
            nodes = self.retrieve(query)
            if not nodes:
                return system_prompt
            lines = [f"[{n.type}] {n.content}" for n in nodes]
            block = "\n".join(lines)
            return f"{system_prompt}\n\n## Recalled Memory\n{block}"

        return hook

    # ---- storage helpers ----------------------------------------------------

    def _save(self, node: LTMNode) -> None:
        key = f"{self._ns}:node:{node.id}"
        self._store.set(
            key,
            {
                "id": node.id,
                "type": node.type,
                "content": node.content,
                "tags": node.tags,
                "session_id": node.session_id,
                "created_at": node.created_at,
            },
        )

    def _load_all(self) -> list[LTMNode]:
        prefix = f"{self._ns}:node:"
        nodes: list[LTMNode] = []
        for key in self._store.keys(prefix):
            raw = self._store.get(key)
            if isinstance(raw, dict):
                try:
                    nodes.append(LTMNode(**raw))
                except TypeError:
                    pass
        return nodes

    def all_nodes(self) -> list[LTMNode]:
        """Return all persisted nodes (useful for inspection / debugging)."""
        return self._load_all()

    def clear(self) -> None:
        """Delete all nodes in this store's namespace."""
        prefix = f"{self._ns}:node:"
        for key in self._store.keys(prefix):
            self._store.delete(key)
