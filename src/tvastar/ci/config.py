"""Configuration for tvastar-ci."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CIConfig:
    """Configuration for the CI agent.

    Attributes:
        repo_path: Path to the repository root.
        test_command: Command to run tests (default: "pytest -q").
        model: Model name or instance for the fixing agent.
        branch: Branch to watch (default: "main").
        max_fix_attempts: Maximum fix-verify cycles per failure (default: 3).
        timeout: Timeout per fix attempt in seconds (default: 300).
        auto_pr: Whether to create PRs for fixes (default: True).
        notifications: Notification config (slack_webhook, etc.).
        schedule: Cron schedule for polling mode (default: "@manual").
        trigger_on: Event trigger (e.g., "event:ci.push") for fleet mode.
        memory_path: Path for LTM storage (past fixes, known issues).
    """

    repo_path: str = "."
    test_command: str = "pytest -q"
    model: Any = None  # Model instance or name string
    branch: str = "main"
    max_fix_attempts: int = 3
    timeout: float = 300.0
    auto_pr: bool = True
    notifications: dict[str, str] = field(default_factory=dict)  # {"slack": url, "webhook": url}
    schedule: str = "@manual"
    trigger_on: str | None = None
    memory_path: str = ".tvastar-ci/memory.db"

    @classmethod
    def from_file(cls, path: str = ".tvastar-ci.json") -> "CIConfig":
        """Load config from a JSON file."""
        import json
        from pathlib import Path

        config_path = Path(path)
        if not config_path.exists():
            return cls()

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: str = ".tvastar-ci.json") -> None:
        """Save config to a JSON file."""
        import json
        from dataclasses import asdict
        from pathlib import Path

        data = asdict(self)
        data.pop("model", None)  # Don't serialize model instance
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
