"""Load an AgentSpec from a tvastar YAML config file.

Every builder traces directly to a real tvastar constructor — no invented
abstractions. MCP toolsets require async setup and are skipped at load time
(ponytail: ceiling is sync-only; upgrade path is connect_mcp_server() after
load_agent_from_yaml() returns).

Quick self-check (no API key needed):
    python src/tvastar/serving/yaml_loader.py
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import TvastarError

if TYPE_CHECKING:
    from ..agent import AgentSpec


# ── env expansion ─────────────────────────────────────────────────────────────

def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} in strings. stdlib only."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


# ── reference resolution ──────────────────────────────────────────────────────

def _resolve_ref(ref: str) -> Any:
    """Import a 'module:attr' reference."""
    if ":" not in ref:
        raise TvastarError(f"Invalid module ref {ref!r} — expected 'module:attr'")
    module_path, attr = ref.rsplit(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise TvastarError(f"Cannot import {module_path!r}: {exc}") from exc
    if not hasattr(module, attr):
        raise TvastarError(f"{module_path!r} has no attribute {attr!r}")
    return getattr(module, attr)


def _lookup(value: Any, section: dict, label: str) -> dict:
    """Resolve a string name against a named section, or pass an inline dict through."""
    if isinstance(value, str):
        if value not in section:
            raise TvastarError(
                f"Unknown {label} {value!r}. Defined: {list(section) or '(none)'}"
            )
        return section[value]
    if isinstance(value, dict):
        return value
    raise TvastarError(f"{label} must be a string name or inline dict, got {type(value).__name__}")


# ── model builder ─────────────────────────────────────────────────────────────

def _build_model(cfg: Any, named_models: dict) -> Any:
    """Build a Model from a named ref, shorthand string, or inline dict."""
    if cfg is None:
        raise TvastarError("agent.model is required")

    # Named ref → resolve from models: section
    if isinstance(cfg, str) and cfg in named_models:
        cfg = named_models[cfg]

    # Shorthand string → anthropic by default
    if isinstance(cfg, str):
        cfg = {"provider": "anthropic", "name": cfg}

    if not isinstance(cfg, dict):
        raise TvastarError(f"model must be a string or dict, got {type(cfg).__name__}")

    provider = cfg.get("provider", "anthropic").lower()
    name = cfg.get("name") or ""
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key")

    if provider == "anthropic":
        from ..model import AnthropicModel
        return AnthropicModel(name or "claude-sonnet-4-6", **({} if not api_key else {"api_key": api_key}))

    if provider == "openai":
        from ..model import OpenAIModel
        kw: dict[str, Any] = {}
        if base_url:
            kw["base_url"] = base_url
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(name or "gpt-4o", **kw)

    if provider == "litellm":
        from ..model import LiteLLMModel  # type: ignore[attr-defined]
        return LiteLLMModel(name or "gpt-4o")

    if provider == "mock":
        from ..model import MockModel
        return MockModel()

    raise TvastarError(f"Unknown provider {provider!r}. Supported: anthropic, openai, litellm, mock")


# ── sandbox builder ───────────────────────────────────────────────────────────

def _build_sandbox_factory(cfg: Any, named_sandboxes: dict, base_dir: Path) -> Any:
    """Return a zero-arg sandbox factory, or None (create_agent defaults to VirtualSandbox)."""
    if cfg is None:
        return None

    cfg = _lookup(cfg, named_sandboxes, "sandbox")
    sandbox_type = cfg.get("type", "virtual")

    sec = cfg.get("security", {})

    def _policy():
        from ..sandbox.base import SecurityPolicy
        return SecurityPolicy(
            network=sec.get("network", sandbox_type == "local"),
            timeout_seconds=sec.get("timeout_seconds", 60.0),
            max_output_bytes=sec.get("max_output_bytes", 256_000),
            denied_commands=set(sec.get("denied_commands", ["shutdown", "reboot", "mkfs", "dd"])),
            allowed_commands=set(sec.get("allowed_commands", [])),
            denied_substrings=set(sec.get("denied_substrings", ["rm -rf /", ":(){:|:&};:"])),
            allowed_mcp_tools=set(sec.get("allowed_mcp_tools", [])),
            denied_mcp_tools=set(sec.get("denied_mcp_tools", [])),
        )

    def _cred():
        cf = cfg.get("credential_filter", {})
        if not cf.get("enabled", False):
            return None
        from ..sandbox.base import CredentialFilter
        return CredentialFilter(patterns=cf.get("patterns", [
            "*_KEY", "*_TOKEN", "*_SECRET", "*_PASSWORD",
            "*_PASS", "*_CREDENTIAL", "*_CREDENTIALS",
            "*_URL", "*_URI", "*_DSN",
        ]))

    if sandbox_type == "virtual":
        allow_python = cfg.get("allow_python", True)

        def _factory_virtual():
            from ..sandbox import VirtualSandbox
            return VirtualSandbox(policy=_policy(), allow_python=allow_python, credential_filter=_cred())

        return _factory_virtual

    if sandbox_type == "local":
        root = (base_dir / cfg.get("root", ".tvastar-workspace")).resolve()
        res = cfg.get("resources", {})

        def _factory_local():
            from ..sandbox import LocalSandbox
            from ..sandbox.base import ResourcePolicy
            return LocalSandbox(
                root,
                policy=_policy(),
                resources=ResourcePolicy(
                    max_cpu_seconds=res.get("max_cpu_seconds", 30.0),
                    max_memory_mb=res.get("max_memory_mb"),
                    max_output_chars=res.get("max_output_chars", 50_000),
                    allowed_domains=res.get("allowed_domains", []),
                ),
                credential_filter=_cred(),
            )

        return _factory_local

    raise TvastarError(f"Unknown sandbox type {sandbox_type!r}. Supported: virtual, local")


# ── tools builder ─────────────────────────────────────────────────────────────

def _build_tools(cfg: Any, named_toolsets: dict) -> list:
    """Build a flat list of Tools. Resolves named refs and inline lists."""
    if cfg is None:
        return []

    if isinstance(cfg, str) and cfg in named_toolsets:
        cfg = named_toolsets[cfg]

    if isinstance(cfg, str):
        if cfg == "default":
            from ..tools import default_toolset
            return default_toolset()
        if cfg == "web":
            from ..tools import web_toolset
            return web_toolset()
        raise TvastarError(f"Unknown toolset shorthand {cfg!r}. Use 'default', 'web', a named toolset, or a list.")

    if not isinstance(cfg, list):
        raise TvastarError(f"tools must be a name or list, got {type(cfg).__name__}")

    tools: list = []
    for item in cfg:
        if item == "default":
            from ..tools import default_toolset
            tools.extend(default_toolset())
        elif item == "web":
            from ..tools import web_toolset
            tools.extend(web_toolset())
        elif isinstance(item, str) and item in named_toolsets:
            tools.extend(_build_tools(named_toolsets[item], named_toolsets))
        elif isinstance(item, dict):
            t = item.get("type")
            if t == "python":
                mod_path = item.get("module")
                if not mod_path:
                    raise TvastarError("type: python toolset requires 'module'")
                mod = importlib.import_module(mod_path)
                from ..tools.base import Tool as _Tool
                tools.extend(obj for obj in vars(mod).values() if isinstance(obj, _Tool))
            elif t == "mcp":
                # ponytail: MCP needs async setup — skipped at load time.
                # Wire after load: client = await connect_mcp_server(...); use client.tools
                import warnings
                warnings.warn(
                    "YAML type: mcp toolsets are skipped (async required). "
                    "Call connect_mcp_server() after load_agent_from_yaml().",
                    stacklevel=4,
                )
            else:
                raise TvastarError(f"Unknown toolset type {t!r}. Supported: default, web, python, mcp (skipped)")
        else:
            raise TvastarError(f"Unrecognised toolset item {item!r}")
    return tools


# ── skills builder ────────────────────────────────────────────────────────────

def _build_skills(cfg: list | None, base_dir: Path) -> list:
    if not cfg:
        return []
    from ..skills import Skill, parse_skill
    skills = []
    for item in cfg:
        if isinstance(item, str):
            path = (base_dir / item).resolve()
            if not path.exists():
                raise TvastarError(f"Skill file not found: {path}")
            skills.append(parse_skill(path.read_text(encoding="utf-8"), source=str(path)))
        elif isinstance(item, dict):
            if not item.get("name"):
                raise TvastarError("Inline skill requires 'name'")
            skills.append(Skill(
                name=item["name"],
                description=item.get("description", ""),
                instructions=item.get("content") or item.get("instructions", ""),
            ))
        else:
            raise TvastarError(f"Unrecognised skill entry {item!r}")
    return skills


# ── compaction builder ────────────────────────────────────────────────────────

def _build_compaction(cfg: dict | None, named_models: dict) -> Any:
    if not cfg:
        return None
    from ..compaction_policy import CompactionPolicy
    summary_model = _build_model(cfg["summary_model"], named_models) if cfg.get("summary_model") else None
    return CompactionPolicy(
        max_messages=cfg.get("max_messages", 60),
        max_tokens_estimate=cfg.get("max_tokens_estimate", 80_000),
        keep_last=cfg.get("keep_last", 10),
        min_messages=cfg.get("min_messages", 20),
        cooldown=cfg.get("cooldown", 30.0),
        summary_model=summary_model,
        summary_max_tokens=cfg.get("summary_max_tokens", 1024),
        summary_temperature=cfg.get("summary_temperature", 0.3),
        summary_instruction=cfg.get("summary_instruction") or (
            "Produce a concise factual summary of the conversation so far. "
            "Preserve: key decisions made, files created or edited, tool results "
            "that matter, and any open questions."
        ),
    )


# ── budget builder ────────────────────────────────────────────────────────────

def _build_budget(cfg: dict | None) -> Any:
    if not cfg:
        return None
    from ..cost import BudgetPolicy
    return BudgetPolicy(
        max_usd=float(cfg["max_usd"]),
        on_exceed=cfg.get("on_exceed", "raise"),
        warn_at=cfg.get("warn_at", 0.8),
    )


# ── governance builder ────────────────────────────────────────────────────────

def _build_governance(cfg: dict | None) -> Any:
    if not cfg:
        return None
    phases = cfg.get("phases", {})
    if not phases:
        return None
    initial_phase = cfg.get("initial_phase", "default")
    if initial_phase not in phases:
        raise TvastarError(
            f"governance.initial_phase {initial_phase!r} is not in governance.phases "
            f"{list(phases)}. Add it or fix the typo."
        )
    from ..masking import GovernancePolicy
    return GovernancePolicy(
        phases={k: set(v) for k, v in phases.items()},
        current_phase=initial_phase,
    )


# ── tool policy (readonly / whitelist) ────────────────────────────────────────

def _build_tool_policy(readonly: bool, whitelist: list | None) -> Any:
    if not readonly and not whitelist:
        return None
    from ..masking import allow_only
    if readonly:
        return allow_only("read_file", "grep", "glob", "list_files")
    return allow_only(*whitelist)  # type: ignore[arg-type]


# ── approval gate builder ─────────────────────────────────────────────────────

def _build_approval_gate(cfg: dict | None) -> Any:
    if not cfg:
        return None
    backend = cfg.get("backend", "cli")
    if backend == "none":
        return None
    from ..approval import ApprovalGate
    return ApprovalGate(
        backend=backend,
        approved_by_default=cfg.get("approved_by_default", False),
    )


# ── tool retry builder ────────────────────────────────────────────────────────

def _build_tool_retry(cfg: dict | None) -> Any:
    if not cfg:
        return None
    from ..tools.base import ToolRetryPolicy
    return ToolRetryPolicy(
        max_attempts=cfg.get("max_attempts", 3),
        backoff_base=cfg.get("backoff_base", 0.5),
        backoff_max=cfg.get("backoff_max", 10.0),
        jitter=cfg.get("jitter", 0.1),
    )


# ── detect builder ────────────────────────────────────────────────────────────

def _build_detect(cfg: Any) -> Any:
    if cfg is None or cfg is False:
        return False
    if cfg is True:
        return True
    if isinstance(cfg, list):
        return [_resolve_ref(r) for r in cfg]
    raise TvastarError(f"detect must be true, false, or a list of module:attr refs, got {cfg!r}")


# ── subagents builder ─────────────────────────────────────────────────────────

def _build_subagents(
    cfg: list | None,
    named_models: dict,
    named_toolsets: dict,
    base_dir: Path,
) -> list:
    if not cfg:
        return []
    from ..profiles import define_agent_profile
    profiles = []
    for item in cfg:
        if not item.get("name"):
            raise TvastarError("Each subagent requires 'name'")
        model = _build_model(item["model"], named_models) if item.get("model") else None
        tools = _build_tools(item["tools"], named_toolsets) if item.get("tools") else None
        detect = _build_detect(item["detect"]) if "detect" in item else None
        profiles.append(define_agent_profile(
            item["name"],
            description=item.get("description", ""),
            instructions=item.get("instructions"),
            model=model,
            tools=tools,
            max_steps=item.get("max_steps"),
            thinking_level=item.get("thinking_level"),
            detect=detect,
        ))
    return profiles


# ── system_prompt_hook composer ───────────────────────────────────────────────

def _build_system_prompt_hook(agent_cfg: dict) -> Any:
    """Compose add_date + add_environment_info + add_prompt_files + hooks.system_prompt."""
    import datetime, inspect as _inspect

    parts: list = []

    if agent_cfg.get("add_date"):
        def _date(prompt: str, **_: Any) -> str:
            return prompt + f"\n\nToday's date: {datetime.date.today()}"
        parts.append(_date)

    if agent_cfg.get("add_environment_info"):
        def _env(prompt: str, **_: Any) -> str:
            import subprocess, sys as _sys
            lines = [f"Working directory: {Path.cwd()}", f"OS: {_sys.platform}"]
            try:
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    stderr=subprocess.DEVNULL, text=True
                ).strip()
                lines.append(f"Git branch: {branch}")
            except Exception:
                pass
            return prompt + "\n\n" + "\n".join(lines)
        parts.append(_env)

    for fp in agent_cfg.get("add_prompt_files", []):
        _p = Path(fp)
        def _file(prompt: str, _path: Path = _p, **_: Any) -> str:
            return prompt + f"\n\n---\n{_path.read_text(encoding='utf-8')}" if _path.exists() else prompt
        parts.append(_file)

    hook_ref = (agent_cfg.get("hooks") or {}).get("system_prompt")
    if hook_ref:
        parts.append(_resolve_ref(hook_ref))

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]

    def _composed(prompt: str, **kwargs: Any) -> str:
        for fn in parts:
            try:
                if "last_user_text" in _inspect.signature(fn).parameters:
                    prompt = fn(prompt, **kwargs)
                else:
                    prompt = fn(prompt)
            except Exception:
                pass  # ponytail: hook failure never breaks the run
        return prompt

    return _composed


# ── main entry point ──────────────────────────────────────────────────────────

def load_agent_from_yaml(path: str) -> "AgentSpec":
    """Parse *path* (a tvastar YAML file) and return an AgentSpec.

    All existing CLI commands (run, chat, serve, info, quality, bench) work
    automatically once loader.py detects .yaml / .yml and calls this.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise TvastarError(f"YAML config not found: {path}")

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TvastarError(
            "PyYAML is required for YAML configs. "
            "Install with: pip install 'tvastar[yaml]'"
        ) from exc

    raw: dict = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)
    base_dir = p.parent

    named_models: dict = raw.get("models", {})
    named_sandboxes: dict = raw.get("sandboxes", {})
    named_toolsets: dict = raw.get("toolsets", {})

    agent_cfg: dict = raw.get("agent") or {}
    if not agent_cfg:
        raise TvastarError("YAML config must contain an 'agent:' section")

    name = agent_cfg.get("name", "").strip()
    if not name:
        raise TvastarError("agent.name is required")

    # instructions (inline or from file)
    instructions = agent_cfg.get("instructions", "")
    if not instructions and agent_cfg.get("instruction_file"):
        instr_path = (base_dir / agent_cfg["instruction_file"]).resolve()
        if not instr_path.exists():
            raise TvastarError(f"instruction_file not found: {instr_path}")
        instructions = instr_path.read_text(encoding="utf-8")

    # model + fallbacks
    model = _build_model(agent_cfg.get("model"), named_models)
    fallback_models = (
        [_build_model(m, named_models) for m in agent_cfg["fallback_models"]]
        if agent_cfg.get("fallback_models") else None
    )

    # tools + policy
    tools = _build_tools(agent_cfg.get("tools"), named_toolsets) or None
    tool_policy = _build_tool_policy(
        agent_cfg.get("readonly", False),
        agent_cfg.get("tools_whitelist") or None,
    )

    # sandbox
    sandbox = _build_sandbox_factory(agent_cfg.get("sandbox"), named_sandboxes, base_dir)

    # skills
    skill_list = _build_skills(agent_cfg.get("skills"), base_dir)
    from ..skills import SkillLibrary
    skills = SkillLibrary(skill_list) if skill_list else None

    # subagents
    subagents = _build_subagents(
        agent_cfg.get("subagents"), named_models, named_toolsets, base_dir
    ) or None

    # hooks
    hooks_cfg = agent_cfg.get("hooks") or {}
    system_prompt_hook = _build_system_prompt_hook(agent_cfg)
    pre_tool_hook = _resolve_ref(hooks_cfg["pre_tool"]) if hooks_cfg.get("pre_tool") else None
    post_tool_hook = _resolve_ref(hooks_cfg["post_tool"]) if hooks_cfg.get("post_tool") else None

    # metadata — carries description for AgentRouter
    # Guard: reserved create_agent params must not appear in metadata
    metadata: dict = dict(agent_cfg.get("metadata") or {})
    _RESERVED = {
        "name", "model", "instructions", "tools", "skills", "sandbox",
        "max_steps", "max_tokens", "temperature", "thinking_level", "detect",
        "subagents", "compaction", "tool_retry", "budget", "approval_gate",
        "tool_policy", "governance", "system_prompt_hook", "memory_cap_mb",
        "assurance", "pruner", "scrub_after_run", "structured_retries",
        "max_task_depth", "tool_concurrency", "pre_tool_hook", "post_tool_hook",
        "compress_tool_output", "memory_extraction", "fallback_models",
        "step_callback", "stop_predicate", "middleware", "tool_order_fn",
    }
    conflict = _RESERVED & metadata.keys()
    if conflict:
        raise TvastarError(
            f"agent.metadata contains reserved create_agent param(s): {sorted(conflict)}. "
            "Move these to the top-level agent: section instead."
        )
    if agent_cfg.get("description"):
        metadata.setdefault("description", agent_cfg["description"])

    from .. import create_agent
    return create_agent(
        name,
        model=model,
        instructions=instructions,
        tools=tools,
        skills=skills,
        sandbox=sandbox,
        max_steps=agent_cfg.get("max_steps", 20),
        max_tokens=agent_cfg.get("max_tokens", 4096),
        temperature=agent_cfg.get("temperature", 1.0),
        thinking_level=agent_cfg.get("thinking_level"),
        detect=_build_detect(agent_cfg.get("detect", True)),
        subagents=subagents,
        compaction=_build_compaction(agent_cfg.get("compaction"), named_models),
        tool_retry=_build_tool_retry(agent_cfg.get("tool_retry")),
        budget=_build_budget(agent_cfg.get("budget")),
        approval_gate=_build_approval_gate(agent_cfg.get("approval_gate")),
        tool_policy=tool_policy,
        governance=_build_governance(agent_cfg.get("governance")),
        system_prompt_hook=system_prompt_hook,
        memory_cap_mb=agent_cfg.get("memory_cap_mb"),
        scrub_after_run=agent_cfg.get("scrub_after_run", False),
        structured_retries=agent_cfg.get("structured_retries", 2),
        max_task_depth=agent_cfg.get("max_task_depth", 4),
        tool_concurrency=agent_cfg.get("tool_concurrency"),
        pre_tool_hook=pre_tool_hook,
        post_tool_hook=post_tool_hook,
        compress_tool_output=agent_cfg.get("compress_tool_output", True),
        memory_extraction=agent_cfg.get("memory_extraction"),
        fallback_models=fallback_models,
        **metadata,
    )


# ── self-check ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, textwrap, os as _os

    _yaml = textwrap.dedent("""
        agent:
          name: smoke-test
          model:
            provider: mock
          instructions: You run tests.
          max_steps: 5
          detect: true
          budget:
            max_usd: 0.10
            on_exceed: stop
          compaction:
            max_messages: 40
            keep_last: 8
          governance:
            initial_phase: read
            phases:
              read: [read_file, grep, glob, list_files]
    """)

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8") as f:
        f.write(_yaml)
        _tmp = f.name

    try:
        spec = load_agent_from_yaml(_tmp)
        assert spec.name == "smoke-test", f"name mismatch: {spec.name}"
        assert spec.max_steps == 5, f"max_steps mismatch: {spec.max_steps}"
        assert spec.budget is not None, "budget not built"
        assert spec.budget.max_usd == 0.10, f"budget.max_usd: {spec.budget.max_usd}"
        assert spec.compaction is not None, "compaction not built"
        assert spec.compaction.max_messages == 40, f"compaction.max_messages: {spec.compaction.max_messages}"
        assert spec.governance is not None, "governance not built"
        assert "read" in spec.governance.phases, "governance.phases missing 'read'"
        print("✓ load_agent_from_yaml smoke-test passed")
    finally:
        _os.unlink(_tmp)

# ponytail: assurance= and pruner= not yet wired from YAML.
# Ceiling: AssurancePolicy constructor not verified against source.
# Upgrade: add _build_assurance() and pass assurance=, pruner= to create_agent once confirmed.
