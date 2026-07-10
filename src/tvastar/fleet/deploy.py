"""
tvastar.fleet.deploy — Canary deployment, version history/rollback, and A/B testing.

Provides the DeployManager helper that coordinates traffic splitting, quality
tracking, promotion/rollback decisions, and A/B test conclusions for agents
managed by a FleetRegistry.

All logic uses only the Python standard library (zero third-party deps).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from tvastar.fleet.registry import AgentVersion


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CanaryDeployment:
    """Tracks an active canary deployment for a single agent.

    Attributes
    ----------
    agent_name:
        The agent undergoing canary deployment.
    stable_version:
        The current stable version string.
    canary_version:
        The new version being evaluated.
    traffic_pct:
        Fraction of traffic (0.0-1.0) routed to the canary.
    canary_config:
        Configuration snapshot for the canary version.
    stable_quality_scores:
        Quality scores observed for the stable version during evaluation.
    canary_quality_scores:
        Quality scores observed for the canary version during evaluation.
    started_at:
        Timestamp (epoch seconds) when the canary was started.
    min_quality_threshold:
        Minimum acceptable quality score for the canary. Below this triggers
        automatic rollback.
    evaluation_period:
        Duration in seconds required before promotion can occur.
    """

    agent_name: str
    stable_version: str
    canary_version: str
    traffic_pct: float  # 0.0-1.0, percentage going to canary
    canary_config: dict[str, Any] = field(default_factory=dict)
    stable_quality_scores: list[float] = field(default_factory=list)
    canary_quality_scores: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    min_quality_threshold: float = 70.0
    evaluation_period: float = 3600.0  # seconds


@dataclass
class ABTest:
    """Tracks an active A/B test for a single agent.

    Attributes
    ----------
    agent_name:
        The agent under A/B test.
    variant_a_config:
        Configuration for the control variant (A).
    variant_b_config:
        Configuration for the experimental variant (B).
    traffic_ratio:
        Fraction of traffic (0.0-1.0) routed to variant B.
    variant_a_scores:
        Quality scores collected for variant A.
    variant_b_scores:
        Quality scores collected for variant B.
    variant_a_costs:
        Cost values collected for variant A.
    variant_b_costs:
        Cost values collected for variant B.
    variant_a_latencies:
        Latency values (seconds) collected for variant A.
    variant_b_latencies:
        Latency values (seconds) collected for variant B.
    started_at:
        Timestamp (epoch seconds) when the A/B test was started.
    """

    agent_name: str
    variant_a_config: dict[str, Any] = field(default_factory=dict)
    variant_b_config: dict[str, Any] = field(default_factory=dict)
    traffic_ratio: float = 0.5  # fraction going to variant B
    variant_a_scores: list[float] = field(default_factory=list)
    variant_b_scores: list[float] = field(default_factory=list)
    variant_a_costs: list[float] = field(default_factory=list)
    variant_b_costs: list[float] = field(default_factory=list)
    variant_a_latencies: list[float] = field(default_factory=list)
    variant_b_latencies: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# DeployManager
# ---------------------------------------------------------------------------


class DeployManager:
    """Manages canary deployments, A/B tests, and version history/rollback.

    Works alongside a FleetRegistry instance to coordinate traffic splitting,
    quality-based promotion/rollback, and version management.

    Parameters
    ----------
    registry:
        The FleetRegistry instance this manager operates on.
    """

    def __init__(self, registry: Any, event_bus: Any | None = None) -> None:
        self._registry = registry
        self._event_bus = event_bus

        # Active canary deployments: agent_name -> CanaryDeployment
        self._canaries: dict[str, CanaryDeployment] = {}

        # Active A/B tests: agent_name -> ABTest
        self._ab_tests: dict[str, ABTest] = {}

        # Agents halted after rollback exhaustion
        self._halted_agents: set[str] = set()

    # ------------------------------------------------------------------
    # Canary deployment methods
    # ------------------------------------------------------------------

    def start_canary(
        self,
        agent_name: str,
        new_version: str,
        traffic_pct: float,
        config: dict[str, Any],
        *,
        min_quality: float = 70.0,
        eval_period: float = 3600.0,
    ) -> CanaryDeployment:
        """Start a canary deployment for an agent.

        Creates a new AgentVersion entry and routes the configured percentage
        of traffic to the canary version.

        Parameters
        ----------
        agent_name:
            Name of the agent to deploy a canary for.
        new_version:
            Version string for the canary.
        traffic_pct:
            Fraction of traffic (0.0-1.0) to route to the canary.
        config:
            Configuration snapshot for the canary version.
        min_quality:
            Minimum quality threshold. Below this triggers rollback.
        eval_period:
            Seconds that must pass before canary can be promoted.

        Returns
        -------
        CanaryDeployment:
            The created canary deployment tracking object.

        Raises
        ------
        ValueError:
            If traffic_pct is not in [0.0, 1.0] or agent doesn't exist.
        """
        if not (0.0 <= traffic_pct <= 1.0):
            raise ValueError(f"traffic_pct must be between 0.0 and 1.0, got {traffic_pct}")

        if agent_name in self._halted_agents:
            raise ValueError(f"Agent {agent_name!r} is halted due to rollback exhaustion")

        entry = self._registry.get(agent_name)
        if entry is None:
            raise ValueError(f"Agent {agent_name!r} not found in registry")

        # Determine current stable version
        stable_version = entry.version

        # Record the new canary version in the registry's version history
        canary_version_entry = AgentVersion(
            version=new_version,
            config_snapshot=dict(config),
        )
        self._registry._versions.setdefault(agent_name, []).append(canary_version_entry)

        # Create and store the canary deployment
        deployment = CanaryDeployment(
            agent_name=agent_name,
            stable_version=stable_version,
            canary_version=new_version,
            traffic_pct=traffic_pct,
            canary_config=dict(config),
            stable_quality_scores=[],
            canary_quality_scores=[],
            started_at=time.time(),
            min_quality_threshold=min_quality,
            evaluation_period=eval_period,
        )

        self._canaries[agent_name] = deployment
        return deployment

    def record_canary_quality(self, agent_name: str, is_canary: bool, score: float) -> None:
        """Record a quality score for the canary or stable version.

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment.
        is_canary:
            True if the score is for the canary version, False for stable.
        score:
            The quality score to record.

        Raises
        ------
        KeyError:
            If no active canary exists for the agent.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            raise KeyError(f"No active canary deployment for agent {agent_name!r}")

        if is_canary:
            deployment.canary_quality_scores.append(score)
        else:
            deployment.stable_quality_scores.append(score)

    def should_promote_canary(self, agent_name: str) -> bool:
        """Check if the canary should be promoted to stable.

        Returns True if:
        - The evaluation period has elapsed since the canary was started
        - The canary has at least one quality score recorded
        - The canary's average quality exceeds the stable's average quality

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment.

        Returns
        -------
        bool:
            True if canary should be promoted.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            return False

        # Must have elapsed enough time
        elapsed = time.time() - deployment.started_at
        if elapsed < deployment.evaluation_period:
            return False

        # Need at least one score for canary
        if not deployment.canary_quality_scores:
            return False

        canary_avg = sum(deployment.canary_quality_scores) / len(deployment.canary_quality_scores)

        # If no stable scores, canary wins by default (it has scores)
        if not deployment.stable_quality_scores:
            return True

        stable_avg = sum(deployment.stable_quality_scores) / len(deployment.stable_quality_scores)

        return canary_avg > stable_avg

    def should_rollback_canary(self, agent_name: str) -> bool:
        """Check if the canary should be rolled back.

        Returns True if the canary's average quality falls below the
        minimum quality threshold.

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment.

        Returns
        -------
        bool:
            True if canary should be rolled back.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            return False

        # Need at least one canary score to evaluate
        if not deployment.canary_quality_scores:
            return False

        canary_avg = sum(deployment.canary_quality_scores) / len(deployment.canary_quality_scores)

        return canary_avg < deployment.min_quality_threshold

    def promote_canary(self, agent_name: str) -> None:
        """Promote the canary version to stable.

        Updates the agent entry to use the canary version and config,
        then removes the canary deployment tracking.

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment to promote.

        Raises
        ------
        KeyError:
            If no active canary exists for the agent.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            raise KeyError(f"No active canary deployment for agent {agent_name!r}")

        entry = self._registry.get(agent_name)
        if entry is not None:
            # Promote: update agent to canary version/config
            entry.version = deployment.canary_version
            entry.config_overrides = dict(deployment.canary_config)

            # Update quality score on the version entry
            versions = self._registry._versions.get(agent_name, [])
            for v in versions:
                if v.version == deployment.canary_version:
                    if deployment.canary_quality_scores:
                        v.quality_score = sum(deployment.canary_quality_scores) / len(
                            deployment.canary_quality_scores
                        )
                    break

        # Remove the canary tracking
        del self._canaries[agent_name]

    def rollback_canary(self, agent_name: str) -> None:
        """Roll back the canary deployment, restoring full traffic to stable.

        Removes the canary version from version history and deletes the
        canary deployment tracking.

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment to roll back.

        Raises
        ------
        KeyError:
            If no active canary exists for the agent.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            raise KeyError(f"No active canary deployment for agent {agent_name!r}")

        # Remove canary version from version history
        versions = self._registry._versions.get(agent_name, [])
        self._registry._versions[agent_name] = [
            v for v in versions if v.version != deployment.canary_version
        ]

        # Remove the canary tracking — traffic reverts to stable
        del self._canaries[agent_name]

    async def evaluate_and_rollback(self, agent_name: str) -> bool:
        """Auto-rollback if quality regression detected. Returns True if rolled back.

        Wires should_rollback_canary() → rollback_canary() with retry logic.
        On exhaustion: emits alert via EventBus and halts further deployments.

        Parameters
        ----------
        agent_name:
            Agent with an active canary deployment to evaluate.

        Returns
        -------
        bool:
            True if rollback succeeded, False if no rollback needed or retries exhausted.
        """
        if not self.should_rollback_canary(agent_name):
            return False

        deployment = self._canaries.get(agent_name)
        if deployment is None:
            return False

        last_error: Exception | None = None

        for attempt in range(3):
            try:
                self.rollback_canary(agent_name)
                return True
            except Exception as e:
                last_error = e
                await asyncio.sleep(min(1.0 * (2**attempt), 4.0))  # 1s, 2s, 4s

        # All retries exhausted — alert and halt
        if self._event_bus and last_error:
            alert = {
                "agent_name": agent_name,
                "failed_version": deployment.canary_version,
                "rollback_target_version": deployment.stable_version,
                "error_message": str(last_error),
            }
            self._event_bus.publish("fleet.alert.rollback_failed", alert, source_agent=agent_name)

        # Halt further deployments for this agent
        self._halted_agents.add(agent_name)
        return False

    def route_to_canary(self, agent_name: str) -> bool:
        """Decide whether a request should be routed to the canary.

        Uses random sampling based on the configured traffic percentage.

        Parameters
        ----------
        agent_name:
            Agent to check for canary routing.

        Returns
        -------
        bool:
            True if the request should go to the canary, False for stable.
            Returns False if no active canary exists.
        """
        deployment = self._canaries.get(agent_name)
        if deployment is None:
            return False

        return random.random() < deployment.traffic_pct

    def has_canary(self, agent_name: str) -> bool:
        """Check if an agent has an active canary deployment."""
        return agent_name in self._canaries

    def get_canary(self, agent_name: str) -> CanaryDeployment | None:
        """Return the active canary deployment for an agent, or None."""
        return self._canaries.get(agent_name)

    # ------------------------------------------------------------------
    # A/B testing methods
    # ------------------------------------------------------------------

    def start_ab_test(
        self,
        agent_name: str,
        variant_b_config: dict[str, Any],
        traffic_ratio: float = 0.5,
    ) -> ABTest:
        """Start an A/B test for an agent.

        Variant A is the current configuration; variant B uses the provided
        config. Traffic is split according to traffic_ratio.

        Parameters
        ----------
        agent_name:
            Agent to run the A/B test on.
        variant_b_config:
            Configuration for variant B (experimental).
        traffic_ratio:
            Fraction of traffic (0.0-1.0) to route to variant B.

        Returns
        -------
        ABTest:
            The created A/B test tracking object.

        Raises
        ------
        ValueError:
            If traffic_ratio is not in [0.0, 1.0] or agent doesn't exist.
        """
        if not (0.0 <= traffic_ratio <= 1.0):
            raise ValueError(f"traffic_ratio must be between 0.0 and 1.0, got {traffic_ratio}")

        entry = self._registry.get(agent_name)
        if entry is None:
            raise ValueError(f"Agent {agent_name!r} not found in registry")

        # Variant A config is the agent's current config
        variant_a_config = dict(entry.config_overrides) if entry.config_overrides else {}

        ab_test = ABTest(
            agent_name=agent_name,
            variant_a_config=variant_a_config,
            variant_b_config=dict(variant_b_config),
            traffic_ratio=traffic_ratio,
            variant_a_scores=[],
            variant_b_scores=[],
            variant_a_costs=[],
            variant_b_costs=[],
            variant_a_latencies=[],
            variant_b_latencies=[],
            started_at=time.time(),
        )

        self._ab_tests[agent_name] = ab_test
        return ab_test

    def record_ab_quality(
        self,
        agent_name: str,
        is_variant_b: bool,
        score: float,
        cost: float = 0.0,
        latency: float = 0.0,
    ) -> None:
        """Record a quality score, cost, and latency for an A/B test variant.

        Parameters
        ----------
        agent_name:
            Agent with an active A/B test.
        is_variant_b:
            True if recording for variant B, False for variant A.
        score:
            The quality score observed.
        cost:
            The cost incurred for this observation.
        latency:
            The latency in seconds for this observation.

        Raises
        ------
        KeyError:
            If no active A/B test exists for the agent.
        """
        ab_test = self._ab_tests.get(agent_name)
        if ab_test is None:
            raise KeyError(f"No active A/B test for agent {agent_name!r}")

        if is_variant_b:
            ab_test.variant_b_scores.append(score)
            ab_test.variant_b_costs.append(cost)
            ab_test.variant_b_latencies.append(latency)
        else:
            ab_test.variant_a_scores.append(score)
            ab_test.variant_a_costs.append(cost)
            ab_test.variant_a_latencies.append(latency)

    def conclude_ab_test(self, agent_name: str) -> str:
        """Conclude an A/B test and return the winning variant.

        The variant with the higher average quality score wins. If scores
        are equal, variant A (the control) wins. The winning config is
        applied to the agent and the A/B test tracking is removed.

        Parameters
        ----------
        agent_name:
            Agent with an active A/B test to conclude.

        Returns
        -------
        str:
            "A" if variant A wins (or tie), "B" if variant B wins.

        Raises
        ------
        KeyError:
            If no active A/B test exists for the agent.
        """
        ab_test = self._ab_tests.get(agent_name)
        if ab_test is None:
            raise KeyError(f"No active A/B test for agent {agent_name!r}")

        # Calculate average scores
        avg_a = (
            sum(ab_test.variant_a_scores) / len(ab_test.variant_a_scores)
            if ab_test.variant_a_scores
            else 0.0
        )
        avg_b = (
            sum(ab_test.variant_b_scores) / len(ab_test.variant_b_scores)
            if ab_test.variant_b_scores
            else 0.0
        )

        # Determine winner: A wins ties
        if avg_b > avg_a:
            winner = "B"
            winning_config = ab_test.variant_b_config
        else:
            winner = "A"
            winning_config = ab_test.variant_a_config

        # Apply winning config to the agent
        entry = self._registry.get(agent_name)
        if entry is not None:
            entry.config_overrides = dict(winning_config)

        # Clean up
        del self._ab_tests[agent_name]

        return winner

    def route_to_variant_b(self, agent_name: str) -> bool:
        """Decide whether a request should be routed to variant B.

        Uses random sampling based on the configured traffic ratio.

        Parameters
        ----------
        agent_name:
            Agent to check for A/B routing.

        Returns
        -------
        bool:
            True if the request should go to variant B, False for variant A.
            Returns False if no active A/B test exists.
        """
        ab_test = self._ab_tests.get(agent_name)
        if ab_test is None:
            return False

        return random.random() < ab_test.traffic_ratio

    def has_ab_test(self, agent_name: str) -> bool:
        """Check if an agent has an active A/B test."""
        return agent_name in self._ab_tests

    def get_ab_test(self, agent_name: str) -> ABTest | None:
        """Return the active A/B test for an agent, or None."""
        return self._ab_tests.get(agent_name)

    # ------------------------------------------------------------------
    # Version history and rollback
    # ------------------------------------------------------------------

    def get_version_history(self, agent_name: str) -> list[AgentVersion]:
        """Return the version history for an agent.

        Parameters
        ----------
        agent_name:
            The agent whose version history to retrieve.

        Returns
        -------
        list[AgentVersion]:
            All recorded versions, ordered oldest to newest.

        Raises
        ------
        ValueError:
            If the agent does not exist in the registry.
        """
        entry = self._registry.get(agent_name)
        if entry is None:
            raise ValueError(f"Agent {agent_name!r} not found in registry")

        return list(self._registry._versions.get(agent_name, []))

    def rollback_to_version(self, agent_name: str, version: str) -> None:
        """Roll back an agent to a specific previous version.

        Restores the agent's configuration from the version's config snapshot
        and updates the agent's version string.

        Parameters
        ----------
        agent_name:
            Agent to roll back.
        version:
            The version string to roll back to (must exist in history).

        Raises
        ------
        ValueError:
            If the agent doesn't exist or the version is not in history.
        """
        entry = self._registry.get(agent_name)
        if entry is None:
            raise ValueError(f"Agent {agent_name!r} not found in registry")

        versions = self._registry._versions.get(agent_name, [])
        target = None
        for v in versions:
            if v.version == version:
                target = v
                break

        if target is None:
            raise ValueError(f"Version {version!r} not found in history for agent {agent_name!r}")

        # Restore agent to the target version
        entry.version = target.version
        entry.config_overrides = dict(target.config_snapshot)
