"""Rule-based escalation matcher for Swarm coordination.

Coordinator watches SignalBus for worker escalations and responds with
pre-configured directives. Uses the same match-input-select-output pattern
as AgentRouter — deterministic, fast, no LLM in the critical path.

Zero runtime dependencies (stdlib only). Python 3.10+.
"""

from __future__ import annotations

import asyncio
import logging
import time

from tvastar.fleet.models import Escalation, EscalationRule, Goal
from tvastar.fleet.signal_bus import SignalBus

__all__ = ["Coordinator", "DEFAULT_RULES"]

logger = logging.getLogger(__name__)


DEFAULT_RULES: list[EscalationRule] = [
    EscalationRule(
        match_reason="retries_exhausted",
        match_error_type="rate_limit",
        directive={"action": "wait_and_retry", "wait_seconds": 30},
    ),
    EscalationRule(
        match_reason="retries_exhausted",
        match_error_type="timeout",
        directive={"action": "skip_and_continue"},
    ),
    EscalationRule(
        match_reason="retries_exhausted",
        match_error_type=None,
        directive={"action": "proceed_autonomously"},
    ),
]


class Coordinator:
    """Rule-based escalation matcher using AgentRouter's match pattern.

    Watches SignalBus for worker escalations (prefix ``"worker_"``), matches
    each escalation against a rule table (first-match semantics), and writes
    the resulting directive back to the escalating worker's namespace.

    Parameters
    ----------
    signal_bus:
        The shared SignalBus instance for reading escalations and writing
        directives/goals.
    rules:
        Ordered list of EscalationRule objects. First match wins. When None,
        DEFAULT_RULES are used.
    escalation_response_timeout:
        Maximum seconds to spend matching a rule before falling back to the
        default directive. Protects against pathological rule tables.
    """

    def __init__(
        self,
        signal_bus: SignalBus,
        rules: list[EscalationRule] | None = None,
        *,
        escalation_response_timeout: float = 5.0,
    ) -> None:
        self._signal_bus = signal_bus
        self._rules = rules if rules is not None else list(DEFAULT_RULES)
        self._escalation_response_timeout = escalation_response_timeout
        self._watch_task: asyncio.Task[None] | None = None

    def match_rule(self, reason: str, error_type: str) -> dict:
        """Match an escalation to a directive via first-match semantics.

        Pure function — no side effects, no I/O, deterministic. Same input
        always produces the same output (like AgentRouter's scoring_fn).

        Parameters
        ----------
        reason:
            The escalation reason (e.g. ``"retries_exhausted"``).
        error_type:
            The error type that caused the escalation (e.g. ``"rate_limit"``).

        Returns
        -------
        The directive dict from the first matching rule, or
        ``{"action": "proceed_autonomously"}`` if no rule matches.
        """
        for rule in self._rules:
            reason_matches = rule.match_reason is None or rule.match_reason == reason
            error_matches = rule.match_error_type is None or rule.match_error_type == error_type
            if reason_matches and error_matches:
                return rule.directive
        return {"action": "proceed_autonomously"}

    async def publish_goal(self, goal: str, priority: int = 5) -> None:
        """Write a goal entry to SignalBus for workers to read.

        Parameters
        ----------
        goal:
            The goal description string.
        priority:
            Goal priority (default 5). Lower number = higher priority.
        """
        goal_entry = Goal(goal=goal, priority=priority, timestamp=time.time())
        await self._signal_bus.write("coordinator", "goal", goal_entry)

    async def watch_and_respond(self) -> None:
        """Main coordination loop — watch for escalations and write directives.

        Watches SignalBus for entries in namespaces starting with ``"worker_"``
        where the key is ``"escalation"``. For each escalation, matches a rule
        and writes the resulting directive to the worker's namespace.

        Uses ``asyncio.wait_for`` to enforce ``escalation_response_timeout``.
        If matching takes too long, the default directive is written.

        This method runs indefinitely until cancelled (via ``stop()``).
        """
        async for entry in self._signal_bus.watch("worker_"):
            if entry.key != "escalation":
                continue

            # Extract reason and error_type from escalation value.
            value = entry.value
            if isinstance(value, Escalation):
                reason = value.reason
                error_type = value.error_type
            elif isinstance(value, dict):
                reason = value.get("reason", "")
                error_type = value.get("error_type", "")
            else:
                # Unrecognized format — use default directive.
                reason = ""
                error_type = ""

            # Match rule with timeout protection.
            try:
                directive = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self.match_rule, reason, error_type
                    ),
                    timeout=self._escalation_response_timeout,
                )
            except (asyncio.TimeoutError, Exception):
                directive = {"action": "proceed_autonomously"}

            # Write directive to the escalating worker's namespace.
            await self._signal_bus.write(entry.namespace, "directive", directive)

    async def stop(self) -> None:
        """Cancel the watch loop if running."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
