"""Error classification protocol and composition utility.

ErrorClassifier is a plain callable — no class hierarchy. Operators compose
via compose_classifiers() when multiple providers need recognition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tvastar.loop import FailureKind


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Return value from an ErrorClassifier."""

    failure_kind: FailureKind
    retry_after_seconds: float | None = None


# The protocol: a callable accepting Exception, returning result or None
ErrorClassifier = Callable[[Exception], ClassificationResult | None]


def compose_classifiers(*classifiers: ErrorClassifier) -> ErrorClassifier:
    """Return a single classifier that tries each in order, returning first non-None."""

    def _composed(exc: Exception) -> ClassificationResult | None:
        for clf in classifiers:
            result = clf(exc)
            if result is not None:
                return result
        return None

    return _composed


def _extract_retry_after_openai(exc: Exception) -> float | None:
    """Pull retry-after seconds from OpenAI RateLimitError response headers."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers is None:
            return None
        raw = headers.get("retry-after")
        if raw is None:
            return None
        return float(raw)
    except (ValueError, TypeError, AttributeError):
        return None


def openai_classifier(exc: Exception) -> ClassificationResult | None:
    """Classify OpenAI SDK exceptions. Raises ImportError if SDK missing."""
    try:
        import openai
    except ImportError:
        raise ImportError(
            "openai_classifier requires the 'openai' package. Install it with: pip install openai"
        )
    if isinstance(exc, openai.AuthenticationError):
        return ClassificationResult(FailureKind.AUTH_ERROR)
    if isinstance(exc, openai.APIStatusError) and getattr(exc, "status_code", None) == 403:
        return ClassificationResult(FailureKind.AUTH_ERROR)
    if isinstance(exc, openai.BadRequestError):
        msg = str(exc).lower()
        if "content_policy" in msg or "content_filter" in msg:
            return ClassificationResult(FailureKind.CONTENT_POLICY)
    # Rate limit with Retry-After
    if isinstance(exc, openai.RateLimitError):
        retry_after = _extract_retry_after_openai(exc)
        return ClassificationResult(FailureKind.MODEL_ERROR, retry_after_seconds=retry_after)
    return None


def _extract_retry_after_anthropic(exc: Exception) -> float | None:
    """Pull retry-after seconds from an Anthropic RateLimitError's response headers."""
    try:
        response = getattr(exc, "response", None)
        if response is None:
            return None
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get("retry-after")
        if value is None:
            return None
        return float(value)
    except (ValueError, TypeError, AttributeError):
        return None


def anthropic_classifier(exc: Exception) -> ClassificationResult | None:
    """Classify Anthropic SDK exceptions. Raises ImportError if SDK missing."""
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic_classifier requires the 'anthropic' package. "
            "Install it with: pip install anthropic"
        )
    if isinstance(exc, anthropic.AuthenticationError):
        return ClassificationResult(FailureKind.AUTH_ERROR)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ClassificationResult(FailureKind.AUTH_ERROR)
    # Content policy: BadRequestError with "content" + "policy" in message
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(exc).lower()
        if "content" in msg and "policy" in msg:
            return ClassificationResult(FailureKind.CONTENT_POLICY)
    # Rate limit with Retry-After
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = _extract_retry_after_anthropic(exc)
        return ClassificationResult(FailureKind.MODEL_ERROR, retry_after_seconds=retry_after)
    return None
