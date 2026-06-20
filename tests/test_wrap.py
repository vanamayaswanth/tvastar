"""Tests for tvastar.wrap — the generic agent loop quality wrapper."""

from __future__ import annotations

import pytest

from tvastar.detect import Finding, Severity
from tvastar.wrap import WrappedResult, _default_extract_text, wrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _echo(text: str) -> str:
    return text


async def _return_dict(text: str) -> dict:
    return {"output": text}


async def _raise(_: str) -> str:
    raise ValueError("loop exploded")


def _sync_echo(text: str) -> str:
    return text


# ---------------------------------------------------------------------------
# WrappedResult
# ---------------------------------------------------------------------------


class TestWrappedResult:
    def _make(self, grade: str, findings=None) -> WrappedResult:
        from tvastar.quality import LoopQualityReport

        q = LoopQualityReport(
            score=90 if grade == "PASS" else 50,
            grade=grade,
            errors=[f for f in (findings or []) if f.severity == Severity.ERROR],
            warnings=[f for f in (findings or []) if f.severity == Severity.WARNING],
            findings=findings or [],
            summary="ok",
        )
        return WrappedResult(text="hi", quality=q, findings=findings or [], duration=0.1)

    def test_ok_true_on_pass(self):
        assert self._make("PASS").ok is True

    def test_ok_false_on_warn(self):
        assert self._make("WARN").ok is False

    def test_ok_false_on_fail(self):
        assert self._make("FAIL").ok is False

    def test_warnings_filters_severity(self):
        w = Finding("det", Severity.WARNING, "warn", {})
        e = Finding("det", Severity.ERROR, "err", {})
        r = self._make("FAIL", [w, e])
        assert len(r.warnings) == 2  # both WARNING and ERROR
        assert len(r.errors) == 1

    def test_raw_not_shown_in_repr(self):
        r = self._make("PASS")
        r.raw = "some big payload"
        assert "some big payload" not in repr(r)


# ---------------------------------------------------------------------------
# _default_extract_text
# ---------------------------------------------------------------------------


class TestDefaultExtractText:
    def test_str_passthrough(self):
        assert _default_extract_text("hello") == "hello"

    def test_dict_output_key(self):
        assert _default_extract_text({"output": "done"}) == "done"

    def test_dict_text_key(self):
        assert _default_extract_text({"text": "answer"}) == "answer"

    def test_dict_result_key(self):
        assert _default_extract_text({"result": "42"}) == "42"

    def test_object_with_text_attr(self):
        class R:
            text = "from attr"

        assert _default_extract_text(R()) == "from attr"

    def test_object_with_content_attr(self):
        class R:
            content = "from content"

        assert _default_extract_text(R()) == "from content"

    def test_none_returns_empty(self):
        assert _default_extract_text(None) == ""

    def test_unknown_type_falls_back_to_str(self):
        assert _default_extract_text(42) == "42"


# ---------------------------------------------------------------------------
# wrap() — decorator forms
# ---------------------------------------------------------------------------


class TestWrapDecorator:
    @pytest.mark.asyncio
    async def test_plain_decorator_returns_wrapped_result(self):
        wrapped = wrap(_echo)
        result = await wrapped("hello world")
        assert isinstance(result, WrappedResult)
        assert result.text == "hello world"

    @pytest.mark.asyncio
    async def test_factory_decorator_accepts_custom_detectors(self):
        def _always_warn(ctx):
            return [Finding("test", Severity.WARNING, "forced", {})]

        @wrap(detectors=[_always_warn])
        async def my_loop(prompt: str) -> str:
            return prompt

        result = await my_loop("hi")
        assert any(f.severity == Severity.WARNING for f in result.findings)

    @pytest.mark.asyncio
    async def test_preserves_function_name(self):
        wrapped = wrap(_echo)
        assert wrapped.__name__ == "_echo"

    @pytest.mark.asyncio
    async def test_wrapped_attribute_points_to_original(self):
        wrapped = wrap(_echo)
        assert wrapped.__wrapped__ is _echo

    @pytest.mark.asyncio
    async def test_sync_function_wrapped_as_async(self):
        wrapped = wrap(_sync_echo)
        result = await wrapped("sync input")
        assert result.text == "sync input"

    @pytest.mark.asyncio
    async def test_dict_return_extracted(self):
        wrapped = wrap(_return_dict)
        result = await wrapped("from dict")
        assert result.text == "from dict"

    @pytest.mark.asyncio
    async def test_custom_extract_text_used(self):
        @wrap(extract_text=lambda r: r.get("custom_key", ""))
        async def my_loop(_: str) -> dict:
            return {"custom_key": "special answer"}

        result = await my_loop("q")
        assert result.text == "special answer"


# ---------------------------------------------------------------------------
# wrap() — quality scoring
# ---------------------------------------------------------------------------


class TestWrapQualityScoring:
    @pytest.mark.asyncio
    async def test_clean_text_result_scores_high(self):
        wrapped = wrap(_echo)
        result = await wrapped("The analysis is complete. All tests pass.")
        assert result.quality.score >= 80
        assert result.quality.grade == "PASS"

    @pytest.mark.asyncio
    async def test_exception_stops_on_error(self):
        wrapped = wrap(_raise)
        result = await wrapped("x")
        assert result.quality.grade == "FAIL"
        assert "error" in result.text

    @pytest.mark.asyncio
    async def test_duration_is_positive(self):
        wrapped = wrap(_echo)
        result = await wrapped("hi")
        assert result.duration >= 0

    @pytest.mark.asyncio
    async def test_findings_is_list(self):
        wrapped = wrap(_echo)
        result = await wrapped("answer")
        assert isinstance(result.findings, list)


# ---------------------------------------------------------------------------
# wrap() — empty answer detector fires
# ---------------------------------------------------------------------------


class TestWrapEmptyAnswer:
    @pytest.mark.asyncio
    async def test_empty_string_triggers_finding(self):
        async def _empty(_: str) -> str:
            return ""

        result = await wrap(_empty)("prompt")
        # empty_answer fires as WARNING — one warning keeps score at 90 (PASS),
        # but the finding itself must be present and .ok must reflect no errors.
        assert any(f.detector == "empty_answer" for f in result.findings)
        assert len(result.warnings) >= 1
