"""GitHub Actions / GitLab CI entrypoint.

Used by .github/workflows/agent.yml. Loads the coding agent and runs the prompt
provided via the `prompt` workflow input (INPUT_PROMPT), writing the result to
the step outputs.

    INPUT_PROMPT="Summarize the README" python examples/deploy/github_action_entry.py
"""

from __future__ import annotations

import sys

from tvastar.deploy import run_github_action
from tvastar.serving.loader import load_agent


def main() -> int:
    # Reuse the existing agent definition — written once, deployed anywhere.
    spec = load_agent("examples/coding_agent.py:agent")
    return run_github_action(spec)


if __name__ == "__main__":
    sys.exit(main())
