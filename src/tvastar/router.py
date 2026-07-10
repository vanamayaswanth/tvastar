"""AgentRouter — auto-route sess.task() to the right AgentProfile.

Uses word-set overlap — zero dependencies, works everywhere.

Usage::

    from tvastar import AgentRouter

    router = AgentRouter(spec.subagents.values())

    # Explicit call
    result = await sess.task("Review this SQL migration", router=router)

    # Or resolve name yourself
    name = router.route("Review this SQL migration")  # "db-reviewer"
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from .profiles import AgentProfile

__all__ = ["AgentRouter", "AgentPruner"]


class AgentRouter:
    """Route a task prompt to the best-matching AgentProfile.

    Args:
        profiles:   Iterable of AgentProfile — the pool to route into.
        threshold:  Minimum match score (0–1) to accept a route.
                    Below this, ``route()`` returns ``None``.
        scoring_fn: Optional custom scoring function. If provided, it is called
                    with ``(text, profile)`` for each profile and must return a
                    float score. The profile with the highest score (at or above
                    *threshold*) wins. When ``None``, the built-in word-overlap
                    heuristic is used.

    Example::

        router = AgentRouter([reviewer, coder, tester])
        name = router.route("Write unit tests for auth.py")  # "tester"

        # Custom scoring:
        router = AgentRouter(profiles, scoring_fn=my_scorer)
    """

    def __init__(
        self,
        profiles: Iterable[AgentProfile],
        *,
        threshold: float = 0.3,
        scoring_fn: Optional[Callable[[str, AgentProfile], float]] = None,
    ):
        self._profiles = {p.name: p for p in profiles}
        self._threshold = threshold
        self._scoring_fn = scoring_fn

    def route(self, text: str) -> Optional[str]:
        """Return the best-matching profile name, or ``None`` if below threshold."""
        if not self._profiles:
            return None

        if self._scoring_fn:
            # Use custom scoring function
            best_name, best_score = None, 0.0
            for name, profile in self._profiles.items():
                score = self._scoring_fn(text, profile)
                if score > best_score:
                    best_score, best_name = score, name
            return best_name if best_score >= self._threshold else None

        # word-overlap — no deps
        words = set(text.lower().split())
        best_name, best_score = None, 0.0
        for name, profile in self._profiles.items():
            desc = (profile.description or "") + " " + name
            desc_words = set(desc.lower().split())
            if not desc_words:
                continue
            overlap = len(words & desc_words) / max(len(words | desc_words), 1)
            # ponytail: word-overlap alone is O(w). SequenceMatcher was O(n×m) for a marginal tiebreaker. Deletion wins.
            score = overlap
            if score > best_score or (score == best_score and best_name is None):
                best_score, best_name = score, name

        return best_name if best_score >= self._threshold else None

    def __repr__(self) -> str:
        backend = "custom" if self._scoring_fn else "word-overlap"
        return f"AgentRouter({list(self._profiles)!r}, backend={backend!r})"


_MAX_SCORE_HISTORY = 100


class AgentPruner:
    """Drop underperforming AgentProfiles based on observed run quality.

    Inspired by AgentDropout: after each task result is recorded, profiles
    whose rolling average quality score falls below *threshold* are removed
    from the active pool, concentrating compute on the better specialists.

    Works standalone or paired with AgentRouter::

        pruner = AgentPruner(threshold=60.0)
        router = AgentRouter(pruner.active(all_profiles))

        result = await sess.task("...", router=router)
        pruner.update("coder", result)

        # Rebuild router with pruned pool (drops any profile that fell below 60)
        router = AgentRouter(pruner.active(all_profiles))

    Args:
        threshold: Minimum average quality score (0–100) to keep a profile.
                   Profiles with no recorded runs are always kept (not yet observed).
        min_runs:  Minimum number of runs before a profile is eligible for pruning.
    """

    def __init__(self, threshold: float = 50.0, *, min_runs: int = 1) -> None:
        self._threshold = threshold
        self._min_runs = min_runs
        self._scores: dict[str, list[float]] = {}

    def update(self, profile_name: str, result: "Any") -> None:
        """Record a RunResult against *profile_name*.

        The quality score is derived from the result's findings and stop state
        via :func:`tvastar.quality.score_run`. Call this after each task() that
        used a specific profile so the pruner can track per-profile performance.
        """
        from .quality import score_run

        score = score_run(result).score
        scores = self._scores.setdefault(profile_name, [])
        scores.append(score)
        # Cap history to prevent unbounded growth
        if len(scores) > _MAX_SCORE_HISTORY:
            self._scores[profile_name] = scores[-_MAX_SCORE_HISTORY:]

    def avg_score(self, profile_name: str) -> Optional[float]:
        """Return the rolling average score for *profile_name*, or None if unseen."""
        scores = self._scores.get(profile_name)
        return sum(scores) / len(scores) if scores else None

    def should_prune(self, profile_name: str) -> bool:
        """Return True if the profile has enough runs and falls below threshold."""
        scores = self._scores.get(profile_name)
        if not scores or len(scores) < self._min_runs:
            return False
        return (sum(scores) / len(scores)) < self._threshold

    def active(self, profiles: Iterable[AgentProfile]) -> list[AgentProfile]:
        """Return profiles that have not been pruned.

        Profiles with no recorded runs are always included — they haven't had
        a chance to prove themselves yet.
        """
        return [p for p in profiles if not self.should_prune(p.name)]

    def pruned(self, profiles: Iterable[AgentProfile]) -> list[AgentProfile]:
        """Return only the profiles that would be dropped."""
        return [p for p in profiles if self.should_prune(p.name)]

    def __repr__(self) -> str:
        counts = {name: len(s) for name, s in self._scores.items()}
        return f"AgentPruner(threshold={self._threshold}, runs={counts})"
