"""Scripted MockModel responses for the offline demo mode.

Provides a pre-configured MockModel that exercises the full pipeline
(diagnose, fix, verify) without requiring any API keys.

Uses profile-keyed scripts so each sub-agent gets its own independent
scripted responses via ``MockModel(scripts={...})``.

Requirements: 12.1
"""

from __future__ import annotations

from tvastar.model.mock import MockModel


def create_demo_model() -> MockModel:
    """Build a MockModel that exercises the full pipeline.

    Each agent profile gets its own scripted responses:
    - diagnosis_agent: Produces a DiagnosisReport finding thrash_loop + unverified_completion
    - rewriter_agent: Generates a FixProposal that adds verification steps
    - verifier_agent: Returns a VerificationResult with quality_score=0.85
    """
    return MockModel(
        scripts={
            "diagnosis_agent": [
                (
                    "Based on my analysis of the trajectory, I identified two primary "
                    "failure modes:\n\n"
                    "1. **thrash_loop** (severity: error): The agent repeated the same "
                    "tool call 4 times without making progress. Evidence shows it was "
                    "stuck in a retry loop on lines 12-28.\n\n"
                    "2. **unverified_completion** (severity: warning): The agent claimed "
                    "task completion on line 34 without verifying the output matched the "
                    "expected result.\n\n"
                    "Root cause: The agent's instructions lack explicit exit conditions "
                    "and do not require verification before reporting success. "
                    "Confidence: 0.82"
                ),
            ],
            "rewriter_agent": [
                (
                    "I have rewritten the agent instructions to address the diagnosed "
                    "failure modes:\n\n"
                    "**Changes:**\n"
                    "1. Added a retry budget of 3 attempts before escalating or changing "
                    "strategy (addresses thrash_loop).\n"
                    "2. Added a mandatory verification step: before claiming completion, "
                    "the agent must run the task's acceptance check and confirm the output "
                    "matches expectations (addresses unverified_completion).\n\n"
                    "Rewritten instructions include explicit loop-exit conditions and "
                    "a final verification gate. All resolved."
                ),
            ],
            "verifier_agent": [
                (
                    "Re-ran the original task with the rewritten instructions. Results:\n\n"
                    "- The agent no longer enters a thrash loop (resolved after 2 attempts "
                    "instead of 4+).\n"
                    "- The agent now verifies its output before claiming success.\n"
                    "- Quality score: 0.85 — all original failure modes resolved.\n"
                    "- No new failure modes introduced.\n"
                    "- Trajectory length: 8 steps (down from 34)."
                ),
            ],
        }
    )
