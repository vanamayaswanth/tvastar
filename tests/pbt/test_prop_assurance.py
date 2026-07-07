"""Property-based tests for ExecutionReceipt and TrustLog assurance.

Property 20: Receipt sign-verify round-trip
- For any valid RunResult data and signing key K, creating an ExecutionReceipt
  and calling verify(K) returns True.
- Modifying any field after signing and calling verify(K) returns False.

**Validates: Requirements 8.3, 8.4**

Property 21: TrustLog chain integrity
- For any sequence of N receipts appended to a TrustLog, verify_chain() returns True.
- If any receipt's content is tampered, verify_chain() returns False and identifies
  the corrupted entry.

**Validates: Requirements 8.5, 8.6**
"""

from __future__ import annotations

import time

import hypothesis.strategies as st
from hypothesis import given, settings, assume

from tvastar.assurance.log import TrustLog
from tvastar.assurance.receipt import ExecutionReceipt
from tvastar.quality import LoopQualityReport
from tvastar.types import Message, TextBlock, Usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_quality(score: int = 90, grade: str = "PASS") -> LoopQualityReport:
    return LoopQualityReport(
        score=score,
        grade=grade,
        errors=[],
        warnings=[],
        findings=[],
        summary="ok" if grade == "PASS" else "fail",
    )


class _FakeResult:
    """Minimal duck-type for RunResult used by ExecutionReceipt.from_run_result."""

    def __init__(
        self,
        text: str = "All done.",
        stopped: str = "end_turn",
        findings: list | None = None,
        input_tokens: int = 100,
        output_tokens: int = 50,
        messages: list | None = None,
        score: int = 90,
        grade: str = "PASS",
    ):
        self.text = text
        self.stopped = stopped
        self.findings = findings or []
        self.usage = Usage(input_tokens, output_tokens)
        self.messages = messages or [Message("assistant", [TextBlock(text=text)])]
        self._score = score
        self._grade = grade

    @property
    def quality(self) -> LoopQualityReport:
        return _fake_quality(self._score, self._grade)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Signing keys: non-empty ASCII strings
st_signing_key = st.text(
    alphabet=st.characters(categories=("L", "N", "P")),
    min_size=1,
    max_size=64,
)

# Agent names
st_agent_name = st.from_regex(r"[a-z][a-z0-9_-]{0,19}", fullmatch=True)

# Model names
st_model_name = st.sampled_from(
    [
        "claude-sonnet-4-6",
        "gpt-4o",
        "mock-model",
        "llama-3-70b",
        "gemini-pro",
    ]
)

# Prompt text
st_prompt = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# Final text (the assistant's response)
st_final_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# Stopped reason
st_stopped = st.sampled_from(["end_turn", "max_steps", "budget", "error"])

# Quality score
st_quality_score = st.integers(min_value=0, max_value=100)

# Quality grade
st_quality_grade = st.sampled_from(["PASS", "WARN", "FAIL"])

# Token counts
st_tokens = st.integers(min_value=0, max_value=100_000)

# Fields that are part of the canonical payload and can be tampered
TAMPERABLE_FIELDS = [
    "run_id",
    "agent",
    "model_name",
    "prompt",
    "final_text",
    "quality_score",
    "quality_grade",
    "stopped",
    "usage_input",
    "usage_output",
    "prev_hash",
]

st_tamper_field = st.sampled_from(TAMPERABLE_FIELDS)


# ---------------------------------------------------------------------------
# Property 20: Fresh receipt verifies True
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    key=st_signing_key,
    agent=st_agent_name,
    model_name=st_model_name,
    prompt=st_prompt,
    final_text=st_final_text,
    stopped=st_stopped,
    score=st_quality_score,
    grade=st_quality_grade,
    input_tokens=st_tokens,
    output_tokens=st_tokens,
)
def test_fresh_receipt_verifies_true(
    key: str,
    agent: str,
    model_name: str,
    prompt: str,
    final_text: str,
    stopped: str,
    score: int,
    grade: str,
    input_tokens: int,
    output_tokens: int,
):
    """Property 20 (positive): verify(key) returns True for freshly signed receipts.

    For any valid RunResult data and signing key K, creating an ExecutionReceipt
    and calling receipt.verify(K) SHALL return True.

    **Validates: Requirements 8.3**
    """
    t = time.time()
    result = _FakeResult(
        text=final_text,
        stopped=stopped,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        score=score,
        grade=grade,
    )
    receipt = ExecutionReceipt.from_run_result(
        result,
        agent=agent,
        model_name=model_name,
        prompt=prompt,
        started_at=t,
        completed_at=t + 0.1,
        key=key,
    )
    assert receipt.verify(key=key) is True


# ---------------------------------------------------------------------------
# Property 20: Tampering any field makes verify return False
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    key=st_signing_key,
    agent=st_agent_name,
    model_name=st_model_name,
    prompt=st_prompt,
    final_text=st_final_text,
    stopped=st_stopped,
    score=st_quality_score,
    grade=st_quality_grade,
    input_tokens=st_tokens,
    output_tokens=st_tokens,
    tamper_field=st_tamper_field,
)
def test_tampered_receipt_fails_verify(
    key: str,
    agent: str,
    model_name: str,
    prompt: str,
    final_text: str,
    stopped: str,
    score: int,
    grade: str,
    input_tokens: int,
    output_tokens: int,
    tamper_field: str,
):
    """Property 20 (negative): verify(key) returns False after tampering any field.

    For any valid RunResult data and signing key K, creating an ExecutionReceipt,
    modifying any field, and calling verify(K) SHALL return False.

    **Validates: Requirements 8.4**
    """
    t = time.time()
    result = _FakeResult(
        text=final_text,
        stopped=stopped,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        score=score,
        grade=grade,
    )
    receipt = ExecutionReceipt.from_run_result(
        result,
        agent=agent,
        model_name=model_name,
        prompt=prompt,
        started_at=t,
        completed_at=t + 0.1,
        key=key,
    )

    # Sanity check: original receipt verifies
    assert receipt.verify(key=key) is True

    # Tamper the selected field with a different value
    if tamper_field == "run_id":
        new_value = receipt.run_id + "_tampered"
    elif tamper_field == "agent":
        new_value = receipt.agent + "_evil"
    elif tamper_field == "model_name":
        new_value = "tampered-model"
    elif tamper_field == "prompt":
        new_value = receipt.prompt + " INJECTED"
    elif tamper_field == "final_text":
        new_value = receipt.final_text + " TAMPERED"
    elif tamper_field == "quality_score":
        new_value = (receipt.quality_score + 1) % 101
    elif tamper_field == "quality_grade":
        new_value = "FAIL" if receipt.quality_grade != "FAIL" else "PASS"
    elif tamper_field == "stopped":
        new_value = "error" if receipt.stopped != "error" else "end_turn"
    elif tamper_field == "usage_input":
        new_value = receipt.usage_input + 1
    elif tamper_field == "usage_output":
        new_value = receipt.usage_output + 1
    elif tamper_field == "prev_hash":
        new_value = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    else:
        raise AssertionError(f"Unknown tamper field: {tamper_field}")

    # Ensure the new value is actually different from the original
    original_value = getattr(receipt, tamper_field)
    assume(new_value != original_value)

    # Modify the field (dataclass is mutable)
    setattr(receipt, tamper_field, new_value)

    # Verify should now fail
    assert receipt.verify(key=key) is False


# ---------------------------------------------------------------------------
# Property 20: Unsigned receipt verifies with content hash only
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    agent=st_agent_name,
    prompt=st_prompt,
    final_text=st_final_text,
)
def test_unsigned_receipt_verifies_with_hash_only(
    agent: str,
    prompt: str,
    final_text: str,
):
    """Property 20 (unsigned): verify() returns True for unsigned receipts via content hash.

    When no signing key is used, verify() still validates the content hash,
    returning True for unmodified receipts.

    **Validates: Requirements 8.3**
    """
    t = time.time()
    result = _FakeResult(text=final_text)
    receipt = ExecutionReceipt.from_run_result(
        result,
        agent=agent,
        model_name="mock-model",
        prompt=prompt,
        started_at=t,
        completed_at=t + 0.1,
        key="",  # No signing key
    )
    assert receipt.signature == ""
    assert receipt.verify() is True


# ---------------------------------------------------------------------------
# Property 21: TrustLog chain integrity — intact chain verifies True
# ---------------------------------------------------------------------------


def _build_receipt_chain(
    n: int,
    key: str,
    agent: str,
    model_name: str,
    prompts: list[str],
    final_texts: list[str],
) -> list[ExecutionReceipt]:
    """Build a chain of N correctly linked receipts."""
    receipts: list[ExecutionReceipt] = []
    prev_hash = ""
    for i in range(n):
        t = 1000.0 + i
        result = _FakeResult(
            text=final_texts[i],
            stopped="end_turn",
            input_tokens=100 + i,
            output_tokens=50 + i,
        )
        receipt = ExecutionReceipt.from_run_result(
            result,
            agent=agent,
            model_name=model_name,
            prompt=prompts[i],
            started_at=t,
            completed_at=t + 0.1,
            key=key,
            prev_hash=prev_hash,
        )
        receipts.append(receipt)
        prev_hash = receipt.content_hash
    return receipts


# Number of receipts in the chain (2 to 10)
st_chain_length = st.integers(min_value=2, max_value=10)


@settings(max_examples=100, deadline=None)
@given(
    key=st_signing_key,
    agent=st_agent_name,
    model_name=st_model_name,
    n=st_chain_length,
    data=st.data(),
)
def test_trustlog_intact_chain_verifies_true(
    key: str,
    agent: str,
    model_name: str,
    n: int,
    data,
):
    """Property 21 (positive): verify_chain() returns True for intact chains.

    For any sequence of N receipts appended to a TrustLog, verify_chain()
    SHALL return True.

    **Validates: Requirements 8.5**
    """
    prompts = data.draw(
        st.lists(st_prompt, min_size=n, max_size=n),
        label="prompts",
    )
    final_texts = data.draw(
        st.lists(st_final_text, min_size=n, max_size=n),
        label="final_texts",
    )

    receipts = _build_receipt_chain(n, key, agent, model_name, prompts, final_texts)

    log = TrustLog()
    for receipt in receipts:
        log.append(receipt)

    assert len(log) == n
    assert log.verify_chain() is True


# ---------------------------------------------------------------------------
# Property 21: TrustLog chain integrity — tampering makes verify_chain False
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    key=st_signing_key,
    agent=st_agent_name,
    model_name=st_model_name,
    n=st_chain_length,
    data=st.data(),
)
def test_trustlog_tampered_chain_fails_verify(
    key: str,
    agent: str,
    model_name: str,
    n: int,
    data,
):
    """Property 21 (negative): tampering makes verify_chain() return False.

    For any sequence of N receipts appended to a TrustLog, if any receipt's
    content is tampered, verify_chain() SHALL return False and identify the
    corrupted entry.

    **Validates: Requirements 8.5, 8.6**
    """
    prompts = data.draw(
        st.lists(st_prompt, min_size=n, max_size=n),
        label="prompts",
    )
    final_texts = data.draw(
        st.lists(st_final_text, min_size=n, max_size=n),
        label="final_texts",
    )

    receipts = _build_receipt_chain(n, key, agent, model_name, prompts, final_texts)

    log = TrustLog()
    for receipt in receipts:
        log.append(receipt)

    # Sanity: intact chain verifies
    assert log.verify_chain() is True

    # Pick a random receipt index to tamper
    tamper_idx = data.draw(
        st.integers(min_value=0, max_value=n - 1),
        label="tamper_idx",
    )

    # Tamper the receipt's final_text (changes content_hash validity)
    log._entries[tamper_idx].final_text += " TAMPERED"

    # Verify chain should now fail
    assert log.verify_chain() is False


# ---------------------------------------------------------------------------
# Property 21: TrustLog on_breach callback fires on tampered chain
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    key=st_signing_key,
    agent=st_agent_name,
    model_name=st_model_name,
    n=st_chain_length,
    data=st.data(),
)
def test_trustlog_on_breach_called_on_tampered_chain(
    key: str,
    agent: str,
    model_name: str,
    n: int,
    data,
):
    """Property 21 (on_breach): on_breach callback identifies the corrupted entry.

    When verify_chain() detects a broken link, the on_breach callback SHALL be
    invoked with the first corrupted receipt.

    **Validates: Requirements 8.6**
    """
    prompts = data.draw(
        st.lists(st_prompt, min_size=n, max_size=n),
        label="prompts",
    )
    final_texts = data.draw(
        st.lists(st_final_text, min_size=n, max_size=n),
        label="final_texts",
    )

    receipts = _build_receipt_chain(n, key, agent, model_name, prompts, final_texts)

    # Track breach callback invocations
    breached_receipts: list[ExecutionReceipt] = []

    def on_breach(receipt: ExecutionReceipt) -> None:
        breached_receipts.append(receipt)

    log = TrustLog(on_breach=on_breach)
    for receipt in receipts:
        log.append(receipt)

    # Pick a random receipt index to tamper
    tamper_idx = data.draw(
        st.integers(min_value=0, max_value=n - 1),
        label="tamper_idx",
    )

    # Tamper the receipt
    log._entries[tamper_idx].final_text += " TAMPERED"

    # Verify chain — should fail and call on_breach
    assert log.verify_chain() is False
    assert len(breached_receipts) == 1
    assert breached_receipts[0].run_id == log._entries[tamper_idx].run_id
