OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "don't message", "opt out", "opt-out"}


def detect_opt_out(message: str, extra_keywords: set[str] | None = None) -> bool:
    """Return True if message contains an opt-out keyword (case-insensitive)."""
    keywords = OPT_OUT_KEYWORDS | (extra_keywords or set())
    lower = message.lower()
    return any(kw in lower for kw in keywords)
