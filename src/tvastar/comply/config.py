"""Configuration file loader for tvastar.comply.

Loads and validates YAML/JSON config files specifying loops, frameworks,
alert sinks, thresholds, and retention settings. Wires config into
Auditor, WatchDaemon, AlertEngine, Dashboard, and CostTracker.

Zero runtime deps for JSON; optional PyYAML for YAML files.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .exceptions import ComplianceError


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LoopConfig:
    """Configuration for a single monitored loop."""

    name: str
    trust_log: str
    frameworks: List[str] = field(default_factory=lambda: ["EU_AI_Act"])


@dataclass
class AlertSinkConfig:
    """Configuration for a single alert sink."""

    type: str  # "stderr" | "file" | "callback"
    path: str = ""  # only used for "file" type


@dataclass
class ThresholdsConfig:
    """Numeric thresholds for compliance monitoring."""

    compliance_overhead_max: float = 0.15
    suppression_window_seconds: float = 300.0
    check_interval_seconds: float = 60.0


@dataclass
class RetentionConfig:
    """Retention policy configuration."""

    framework: str = "EU_AI_Act"
    max_age_days: int = 1825  # 5 years default


@dataclass
class ComplianceConfig:
    """Parsed compliance configuration."""

    loops: List[LoopConfig]
    alert_sinks: List[AlertSinkConfig] = field(default_factory=list)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    retention: Optional[RetentionConfig] = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: str) -> ComplianceConfig:
    """Load and validate a compliance config from YAML or JSON file.

    Raises ComplianceError on invalid config or I/O failure.

    Strategy:
    - Try json.load() first (always available)
    - If that fails and file looks like YAML, try importing PyYAML
    - If PyYAML not available, raise descriptive error
    """
    if not os.path.isfile(path):
        raise ComplianceError(f"Config file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        raise ComplianceError(f"Cannot read config file '{path}': {exc}") from exc

    data = _parse_content(content, path)
    return _validate(data, path)


def _parse_content(content: str, path: str) -> Dict[str, Any]:
    """Parse file content as JSON first, then YAML as fallback."""
    # Try JSON first
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        raise ComplianceError(
            f"Config file '{path}' must contain a JSON object (got {type(data).__name__})"
        )
    except json.JSONDecodeError:
        pass

    # JSON failed — try YAML via optional PyYAML
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise ComplianceError(
            f"Config file '{path}' is not valid JSON and PyYAML is not installed. "
            "Either use JSON format or install PyYAML: pip install pyyaml"
        )

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ComplianceError(
            f"Config file '{path}' is not valid JSON or YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ComplianceError(
            f"Config file '{path}' must contain a mapping (got {type(data).__name__})"
        )
    return data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(data: Dict[str, Any], path: str) -> ComplianceConfig:
    """Validate parsed config dict and return structured ComplianceConfig."""
    # loops is mandatory
    if "loops" not in data:
        raise ComplianceError(
            f"Config file '{path}' missing required key 'loops'"
        )

    loops_raw = data["loops"]
    if not isinstance(loops_raw, list) or not loops_raw:
        raise ComplianceError(
            f"Config file '{path}': 'loops' must be a non-empty list"
        )

    loops = _validate_loops(loops_raw, path)
    alert_sinks = _validate_alert_sinks(data.get("alert_sinks", []), path)
    thresholds = _validate_thresholds(data.get("thresholds", {}), path)
    retention = _validate_retention(data.get("retention"), path)

    return ComplianceConfig(
        loops=loops,
        alert_sinks=alert_sinks,
        thresholds=thresholds,
        retention=retention,
    )


def _validate_loops(raw: List[Any], path: str) -> List[LoopConfig]:
    """Validate and parse loop configurations."""
    loops: List[LoopConfig] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ComplianceError(
                f"Config '{path}': loops[{i}] must be a mapping, got {type(entry).__name__}"
            )
        if "name" not in entry:
            raise ComplianceError(
                f"Config '{path}': loops[{i}] missing required key 'name'"
            )
        if "trust_log" not in entry:
            raise ComplianceError(
                f"Config '{path}': loops[{i}] missing required key 'trust_log'"
            )
        frameworks = entry.get("frameworks", ["EU_AI_Act"])
        if not isinstance(frameworks, list):
            raise ComplianceError(
                f"Config '{path}': loops[{i}].frameworks must be a list"
            )
        loops.append(
            LoopConfig(
                name=str(entry["name"]),
                trust_log=str(entry["trust_log"]),
                frameworks=[str(f) for f in frameworks],
            )
        )
    return loops


def _validate_alert_sinks(raw: Any, path: str) -> List[AlertSinkConfig]:
    """Validate and parse alert sink configurations."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ComplianceError(
            f"Config '{path}': 'alert_sinks' must be a list"
        )
    sinks: List[AlertSinkConfig] = []
    valid_types = {"stderr", "file", "callback"}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ComplianceError(
                f"Config '{path}': alert_sinks[{i}] must be a mapping"
            )
        sink_type = entry.get("type")
        if sink_type not in valid_types:
            raise ComplianceError(
                f"Config '{path}': alert_sinks[{i}].type must be one of "
                f"{sorted(valid_types)}, got '{sink_type}'"
            )
        sink_path = ""
        if sink_type == "file":
            if "path" not in entry:
                raise ComplianceError(
                    f"Config '{path}': alert_sinks[{i}] with type='file' requires 'path'"
                )
            sink_path = str(entry["path"])
        sinks.append(AlertSinkConfig(type=sink_type, path=sink_path))
    return sinks


def _validate_thresholds(raw: Any, path: str) -> ThresholdsConfig:
    """Validate and parse thresholds configuration."""
    if not raw:
        return ThresholdsConfig()
    if not isinstance(raw, dict):
        raise ComplianceError(
            f"Config '{path}': 'thresholds' must be a mapping"
        )
    kwargs: Dict[str, float] = {}
    for key in ("compliance_overhead_max", "suppression_window_seconds", "check_interval_seconds"):
        if key in raw:
            try:
                kwargs[key] = float(raw[key])
            except (TypeError, ValueError):
                raise ComplianceError(
                    f"Config '{path}': thresholds.{key} must be a number, "
                    f"got '{raw[key]}'"
                )
    return ThresholdsConfig(**kwargs)


def _validate_retention(raw: Any, path: str) -> Optional[RetentionConfig]:
    """Validate and parse retention configuration."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ComplianceError(
            f"Config '{path}': 'retention' must be a mapping"
        )
    framework = str(raw.get("framework", "EU_AI_Act"))
    max_age = raw.get("max_age_days", 1825)
    try:
        max_age = int(max_age)
    except (TypeError, ValueError):
        raise ComplianceError(
            f"Config '{path}': retention.max_age_days must be an integer, "
            f"got '{max_age}'"
        )
    if max_age <= 0:
        raise ComplianceError(
            f"Config '{path}': retention.max_age_days must be positive, got {max_age}"
        )
    return RetentionConfig(framework=framework, max_age_days=max_age)


# ---------------------------------------------------------------------------
# Wiring — build runtime objects from config
# ---------------------------------------------------------------------------


def build_from_config(config: ComplianceConfig) -> Dict[str, Any]:
    """Wire a ComplianceConfig into runtime components.

    Returns a dict with keys:
        alert_engine: AlertEngine
        dashboard: ComplianceDashboard
        cost_tracker: CostTracker
        loops: list of LoopConfig (for WatchDaemon to consume)
        retention_config: RetentionConfig | None
        framework: str | None (first loop's first framework, or None)

    ponytail: no Loop objects created here — that requires actual
    Tvastar Loop instantiation which depends on the caller's context.
    This builds the supporting infrastructure from config values.
    """
    from .alert import AlertEngine, FileSink, StderrSink
    from .cost import CostTracker
    from .dashboard import ComplianceDashboard

    # Build alert sinks
    sinks = _build_sinks(config.alert_sinks)

    alert_engine = AlertEngine(
        sinks=sinks or None,  # None → default StderrSink
        suppression_window=config.thresholds.suppression_window_seconds,
    )

    dashboard = ComplianceDashboard(
        check_interval=config.thresholds.check_interval_seconds,
    )

    cost_tracker = CostTracker(
        alert_engine=alert_engine,
        threshold=config.thresholds.compliance_overhead_max,
    )

    return {
        "alert_engine": alert_engine,
        "dashboard": dashboard,
        "cost_tracker": cost_tracker,
        "loops": config.loops,
        "retention_config": config.retention,
        "thresholds": config.thresholds,
    }


def _build_sinks(sink_configs: List[AlertSinkConfig]) -> list:
    """Instantiate AlertSink objects from config."""
    from .alert import FileSink, StderrSink

    sinks = []
    for sc in sink_configs:
        if sc.type == "stderr":
            sinks.append(StderrSink())
        elif sc.type == "file":
            sinks.append(FileSink(sc.path))
        # ponytail: "callback" type can't be loaded from config file —
        # requires programmatic usage. Skip silently in file-based config.
    return sinks
