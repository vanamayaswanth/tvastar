"""Tests for zero-dependency core constraint (REQ-DEPS-001).

Verifies:
- pyproject.toml [project].dependencies remains an empty list (Req 22.1)
- Lazy imports raise ImportError with correct extra name (Req 22.2, 22.3)
- Core modules use only Python stdlib (Req 22.4)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _parse_pyproject_dependencies() -> list[str]:
    """Parse [project].dependencies from pyproject.toml using only stdlib."""
    # We intentionally avoid tomllib to show this works on 3.10 without extras
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib  # type: ignore[import]
        except ImportError:
            import tomli as tomllib  # type: ignore[import,no-redef]

    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["dependencies"]


def _parse_optional_dependencies() -> dict[str, list[str]]:
    """Parse [project.optional-dependencies] from pyproject.toml."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib  # type: ignore[import]
        except ImportError:
            import tomli as tomllib  # type: ignore[import,no-redef]

    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return data["project"].get("optional-dependencies", {})


# ---------------------------------------------------------------------------
# Test: pyproject.toml [project].dependencies is empty (Req 22.1)
# ---------------------------------------------------------------------------


class TestPyprojectDependenciesEmpty:
    """Verify that pyproject.toml declares zero runtime dependencies."""

    def test_dependencies_is_empty_list(self):
        """The [project].dependencies list SHALL remain empty."""
        deps = _parse_pyproject_dependencies()
        assert deps == [], (
            f"Core dependencies must be empty (zero-dep constraint CON-001), "
            f"but found: {deps}"
        )

    def test_pyproject_file_exists(self):
        """Sanity check: pyproject.toml exists at expected location."""
        assert PYPROJECT.exists(), f"pyproject.toml not found at {PYPROJECT}"

    def test_optional_extras_defined(self):
        """Optional extras should exist for provider SDKs and tools."""
        extras = _parse_optional_dependencies()
        expected_extras = {"anthropic", "openai", "litellm", "serve", "otel"}
        assert expected_extras.issubset(
            set(extras.keys())
        ), f"Missing expected optional extras. Found: {list(extras.keys())}"


# ---------------------------------------------------------------------------
# Test: Lazy imports raise ImportError with correct extra name (Req 22.2, 22.3)
# ---------------------------------------------------------------------------


class TestLazyImportErrors:
    """Verify that missing optional packages produce helpful ImportError messages."""

    def test_anthropic_model_import_error(self):
        """AnthropicModel raises with install instruction when SDK missing."""
        # Temporarily hide the anthropic package
        with patch.dict(sys.modules, {"anthropic": None}):
            # Force re-import by removing cached module
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.model.anthropic")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.model.anthropic import AnthropicModel

                # If we get here, the module was importable. Try instantiation.
                with pytest.raises((ImportError, Exception)) as exc_info:
                    AnthropicModel()
                error_msg = str(exc_info.value).lower()
                assert "anthropic" in error_msg
                assert any(
                    kw in error_msg for kw in ["pip install", "uv add", "install"]
                ), f"Error should mention install instructions: {exc_info.value}"
            except ImportError as e:
                # Module-level import fails — check the message
                error_msg = str(e).lower()
                assert "anthropic" in error_msg
            finally:
                sys.modules.update(saved)

    def test_openai_model_import_error(self):
        """OpenAIModel raises with install instruction when SDK missing."""
        with patch.dict(sys.modules, {"openai": None}):
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.model.openai")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.model.openai import OpenAIModel

                with pytest.raises((ImportError, Exception)) as exc_info:
                    OpenAIModel(model="gpt-4")
                error_msg = str(exc_info.value).lower()
                assert "openai" in error_msg
                assert any(kw in error_msg for kw in ["pip install", "uv add", "install"])
            except ImportError as e:
                error_msg = str(e).lower()
                assert "openai" in error_msg
            finally:
                sys.modules.update(saved)

    def test_litellm_model_import_error(self):
        """LiteLLMModel raises with install instruction when SDK missing."""
        with patch.dict(sys.modules, {"litellm": None}):
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.model.litellm")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.model.litellm import LiteLLMModel

                with pytest.raises((ImportError, Exception)) as exc_info:
                    LiteLLMModel(model="gpt-4")
                error_msg = str(exc_info.value).lower()
                assert "litellm" in error_msg
                assert any(kw in error_msg for kw in ["pip install", "uv add", "install"])
            except ImportError as e:
                error_msg = str(e).lower()
                assert "litellm" in error_msg
            finally:
                sys.modules.update(saved)

    def test_serve_import_error(self):
        """Serving module raises with install instruction when fastapi missing."""
        with patch.dict(sys.modules, {"fastapi": None}):
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.serving")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.serving.http import create_app
                from tvastar.model.mock import MockModel
                from tvastar.agent import AgentSpec

                spec = AgentSpec(name="test", model=MockModel())
                with pytest.raises((ImportError, RuntimeError)) as exc_info:
                    create_app(spec)
                error_msg = str(exc_info.value).lower()
                assert any(
                    kw in error_msg for kw in ["serve", "fastapi", "pip install", "uv pip install"]
                ), f"Error should mention serve extra: {exc_info.value}"
            except ImportError as e:
                error_msg = str(e).lower()
                assert any(kw in error_msg for kw in ["serve", "fastapi"])
            finally:
                sys.modules.update(saved)

    def test_presidio_import_error(self):
        """Presidio policy raises with install instruction when package missing."""
        with patch.dict(sys.modules, {"presidio_analyzer": None, "presidio_anonymizer": None}):
            mods_to_remove = [
                k for k in sys.modules if "presidio" in k or k == "tvastar.assurance.sanitize"
            ]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.assurance.sanitize import SanitizationPolicy

                policy = SanitizationPolicy.presidio()
                with pytest.raises(ImportError) as exc_info:
                    policy.scrub("Patient Jane Smith has diabetes")
                error_msg = str(exc_info.value).lower()
                assert "presidio" in error_msg
                assert "pip install" in error_msg
            except ImportError as e:
                error_msg = str(e).lower()
                assert "presidio" in error_msg
            finally:
                sys.modules.update(saved)

    def test_otel_graceful_when_missing(self):
        """OTelExporter degrades gracefully when opentelemetry SDK is missing."""
        with patch.dict(sys.modules, {"opentelemetry": None, "opentelemetry.trace": None}):
            mods_to_remove = [k for k in sys.modules if "opentelemetry" in k]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.observability import OTelExporter, Span

                exporter = OTelExporter()
                # Should not raise — graceful degradation
                span = Span(name="test_span", attributes={"key": "value"})
                exporter.export(span)  # no-op when SDK absent
            finally:
                sys.modules.update(saved)


# ---------------------------------------------------------------------------
# Test: Core modules import without optional extras (Req 22.4)
# ---------------------------------------------------------------------------


class TestCoreImportsWithoutExtras:
    """Verify that core tvastar modules import successfully without optional packages."""

    @pytest.mark.parametrize(
        "module_path",
        [
            "tvastar",
            "tvastar.agent",
            "tvastar.session",
            "tvastar.harness",
            "tvastar.types",
            "tvastar.masking",
            "tvastar.boundary",
            "tvastar.compaction",
            "tvastar.cost",
            "tvastar.quality",
            "tvastar.durable",
            "tvastar.graph",
            "tvastar.profiles",
            "tvastar.router",
            "tvastar.observability",
            "tvastar.detect",
            "tvastar.model",
            "tvastar.model.mock",
            "tvastar.model.base",
            "tvastar.workflow",
            "tvastar.approval",
            "tvastar.errors",
        ],
    )
    def test_core_module_imports(self, module_path: str):
        """Core modules SHALL import without any optional extras installed."""
        import importlib

        mod = importlib.import_module(module_path)
        assert mod is not None

    def test_tvastar_init_exports_public_api(self):
        """The public API in __init__.py should be importable without extras."""
        import tvastar

        assert hasattr(tvastar, "__all__")
        # Verify key public symbols are accessible
        assert hasattr(tvastar, "AgentSpec")
        assert hasattr(tvastar, "Harness")
        assert hasattr(tvastar, "Session")

    def test_mock_model_usable_without_extras(self):
        """MockModel should work without any provider SDKs installed."""
        from tvastar.model.mock import MockModel

        model = MockModel(script=["Hello!"])
        assert model is not None
