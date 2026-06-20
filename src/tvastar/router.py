"""AgentRouter — auto-route sess.task() to the right AgentProfile.

Uses semantic-router (embedding-based) when installed, falls back to
difflib word-overlap for zero-dep environments.

Usage::

    from tvastar import AgentRouter

    router = AgentRouter(spec.subagents.values())

    # Explicit call
    result = await sess.task("Review this SQL migration", router=router)

    # Or resolve name yourself
    name = router.route("Review this SQL migration")  # "db-reviewer"

Install semantic-router for embedding accuracy::

    pip install tvastar[router]
"""

from __future__ import annotations

import difflib
from typing import Iterable, Optional

from .profiles import AgentProfile

__all__ = ["AgentRouter"]


class AgentRouter:
    """Route a task prompt to the best-matching AgentProfile.

    Args:
        profiles:   Iterable of AgentProfile — the pool to route into.
        threshold:  Minimum match score (0–1) to accept a route.
                    Below this, ``route()`` returns ``None``.
        encoder:    Optional semantic-router encoder instance. If ``None``,
                    semantic-router is tried automatically; falls back to
                    difflib word-overlap when the package is absent.

    Example::

        router = AgentRouter([reviewer, coder, tester])
        name = router.route("Write unit tests for auth.py")  # "tester"
    """

    def __init__(
        self,
        profiles: Iterable[AgentProfile],
        *,
        threshold: float = 0.3,
        encoder=None,
    ):
        self._profiles = {p.name: p for p in profiles}
        self._threshold = threshold
        self._layer = None  # semantic-router RouteLayer, built lazily

        # Build semantic-router layer if the package is available
        try:
            from semantic_router import Route, RouteLayer  # type: ignore

            enc = encoder
            if enc is None:
                try:
                    from semantic_router.encoders import FastEmbedEncoder  # type: ignore
                    enc = FastEmbedEncoder()
                except ImportError:
                    from semantic_router.encoders import OpenAIEncoder  # type: ignore
                    enc = OpenAIEncoder()

            routes = [
                Route(name=p.name, utterances=[p.description or p.name])
                for p in self._profiles.values()
                if p.description or p.name
            ]
            if routes:
                self._layer = RouteLayer(encoder=enc, routes=routes)
        except ImportError:
            pass  # ponytail: stdlib fallback covers this

    def route(self, text: str) -> Optional[str]:
        """Return the best-matching profile name, or ``None`` if below threshold."""
        if not self._profiles:
            return None

        # semantic-router path
        if self._layer is not None:
            result = self._layer(text)
            if result.name:
                return result.name
            return None

        # ponytail: difflib word-overlap fallback — no deps
        words = set(text.lower().split())
        best_name, best_score = None, 0.0
        for name, profile in self._profiles.items():
            desc = (profile.description or "") + " " + name
            desc_words = set(desc.lower().split())
            if not desc_words:
                continue
            overlap = len(words & desc_words) / max(len(words | desc_words), 1)
            # also check subsequence similarity on the full strings
            seq = difflib.SequenceMatcher(None, text.lower(), desc.lower()).ratio()
            score = max(overlap, seq * 0.7)  # weight seq lower — it penalises length diff
            if score > best_score:
                best_score, best_name = score, name

        return best_name if best_score >= self._threshold else None

    def __repr__(self) -> str:
        backend = "semantic-router" if self._layer else "difflib"
        return f"AgentRouter({list(self._profiles)!r}, backend={backend!r})"
