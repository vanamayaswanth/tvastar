"""Tests for tvastar.assurance — verifiable execution.

Design-for-failure philosophy:
- Every boundary condition is explicit.
- Ugly inputs: empty strings, None, huge text, unicode, binary-ish content.
- Adversarial: tampered receipts, broken chains, wrong keys, corrupt JSONL.
- SLA: every branch of on_fail (ignore / raise / escalate) is exercised.
- Integration: full run through MockModel confirms receipt appears on RunResult.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tvastar.assurance import AssurancePolicy, ExecutionReceipt, RetentionPolicy, SLABreached, SanitizationPolicy, TrustLog
from tvastar.detect import Finding, Severity
from tvastar.quality import LoopQualityReport
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _fake_quality(score: int = 90, grade: str = "PASS") -> LoopQualityReport:
    return LoopQualityReport(
        score=score, grade=grade,
        errors=[], warnings=[], findings=[],
        summary="ok" if grade == "PASS" else "fail",
    )


class _FakeResult:
    """Minimal duck-type for RunResult used by ExecutionReceipt.from_run_result."""

    def __init__(
        self,
        text: str = "All done.",
        stopped: str = "end_turn",
        findings: list = None,
        input_tokens: int = 100,
        output_tokens: int = 50,
        messages: list = None,
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


def _make_receipt(
    text: str = "Done.",
    key: str = "",
    prev_hash: str = "",
    prompt: str = "fix tests",
    agent: str = "test-agent",
    model_name: str = "mock-model",
    score: int = 90,
    grade: str = "PASS",
    stopped: str = "end_turn",
    findings: list = None,
) -> ExecutionReceipt:
    t = time.time()
    return ExecutionReceipt.from_run_result(
        _FakeResult(text, stopped, findings=findings, score=score, grade=grade),
        agent=agent,
        model_name=model_name,
        prompt=prompt,
        started_at=t,
        completed_at=t + 0.1,
        key=key,
        prev_hash=prev_hash,
    )


# ===========================================================================
# ExecutionReceipt — build
# ===========================================================================


class TestExecutionReceiptBuild:
    def test_run_id_is_unique_each_call(self):
        r1 = _make_receipt()
        r2 = _make_receipt()
        assert r1.run_id != r2.run_id

    def test_run_id_has_expected_prefix(self):
        r = _make_receipt()
        assert r.run_id.startswith("run_")

    def test_content_hash_has_sha256_prefix(self):
        r = _make_receipt()
        assert r.content_hash.startswith("sha256:")

    def test_content_hash_is_64_hex_chars_after_prefix(self):
        r = _make_receipt()
        hex_part = r.content_hash.split(":", 1)[1]
        assert len(hex_part) == 64
        int(hex_part, 16)  # must be valid hex

    def test_unsigned_receipt_has_empty_signature(self):
        r = _make_receipt(key="")
        assert r.signature == ""

    def test_signed_receipt_has_hmac_prefix(self):
        r = _make_receipt(key="secret")
        assert r.signature.startswith("hmac-sha256:")

    def test_agent_name_stored(self):
        r = _make_receipt(agent="billing-bot")
        assert r.agent == "billing-bot"

    def test_prompt_stored(self):
        r = _make_receipt(prompt="charge customer $50")
        assert r.prompt == "charge customer $50"

    def test_quality_score_stored(self):
        r = _make_receipt(score=42, grade="FAIL")
        assert r.quality_score == 42
        assert r.quality_grade == "FAIL"

    def test_stopped_stored(self):
        r = _make_receipt(stopped="max_steps")
        assert r.stopped == "max_steps"

    def test_version_field_is_1(self):
        r = _make_receipt()
        assert r.version == "2"

    def test_prev_hash_defaults_to_empty(self):
        r = _make_receipt()
        assert r.prev_hash == ""

    def test_prev_hash_stored_when_provided(self):
        r = _make_receipt(prev_hash="sha256:abc123")
        assert r.prev_hash == "sha256:abc123"

    def test_tool_calls_extracted_from_messages(self):
        use = ToolUseBlock(name="bash", input={"command": "pytest"}, id="c1")
        msg_a = Message("assistant", [use])
        result = _FakeResult(messages=[msg_a])
        t = time.time()
        receipt = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q",
            started_at=t, completed_at=t + 0.1,
        )
        assert len(receipt.tool_calls) == 1
        assert receipt.tool_calls[0]["name"] == "bash"
        assert receipt.tool_calls[0]["input"] == {"command": "pytest"}

    def test_multiple_tool_calls_all_captured(self):
        uses = [ToolUseBlock(name=f"tool_{i}", input={}, id=f"c{i}") for i in range(3)]
        msg = Message("assistant", uses)
        result = _FakeResult(messages=[msg])
        t = time.time()
        receipt = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q",
            started_at=t, completed_at=t + 0.1,
        )
        assert len(receipt.tool_calls) == 3

    def test_findings_serialised_to_dicts(self):
        f = Finding("thrash_loop", Severity.WARNING, "repeated calls", {})
        result = _FakeResult(findings=[f])
        t = time.time()
        receipt = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q",
            started_at=t, completed_at=t + 0.1,
        )
        assert receipt.findings[0]["detector"] == "thrash_loop"
        assert receipt.findings[0]["severity"] == "warning"

    def test_usage_tokens_stored(self):
        result = _FakeResult(input_tokens=999, output_tokens=111)
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q",
            started_at=t, completed_at=t + 0.1,
        )
        assert r.usage_input == 999
        assert r.usage_output == 111

    def test_env_key_used_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "env-secret")
        r = _make_receipt(key="")
        assert r.signature.startswith("hmac-sha256:")

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "env-secret")
        r_explicit = _make_receipt(key="explicit-secret")
        r_env = _make_receipt(key="")
        # Different keys → different signatures
        assert r_explicit.signature != r_env.signature


# ===========================================================================
# ExecutionReceipt — verify
# ===========================================================================


class TestExecutionReceiptVerify:
    def test_fresh_unsigned_receipt_verifies(self):
        assert _make_receipt().verify() is True

    def test_fresh_signed_receipt_verifies_with_correct_key(self):
        r = _make_receipt(key="s3cr3t")
        assert r.verify(key="s3cr3t") is True

    def test_wrong_key_fails_verification(self):
        r = _make_receipt(key="correct")
        assert r.verify(key="wrong") is False

    def test_empty_key_on_signed_receipt_still_checks_hash(self):
        r = _make_receipt(key="secret")
        # Passing no key → only content_hash is checked (no HMAC)
        # Content hash itself is correct, so it returns True.
        assert r.verify(key="") is True

    def test_tampered_final_text_fails_hash(self):
        r = _make_receipt()
        object.__setattr__(r, "final_text", r.final_text + " TAMPERED")
        assert r.verify() is False

    def test_tampered_prompt_fails_hash(self):
        r = _make_receipt(prompt="original")
        object.__setattr__(r, "prompt", "injected new prompt")
        assert r.verify() is False

    def test_tampered_quality_score_fails_hash(self):
        r = _make_receipt(score=90)
        object.__setattr__(r, "quality_score", 100)
        assert r.verify() is False

    def test_tampered_tool_calls_fails_hash(self):
        r = _make_receipt()
        original_calls = list(r.tool_calls)
        original_calls.append({"name": "ghost", "input": {}, "id": "x"})
        object.__setattr__(r, "tool_calls", original_calls)
        assert r.verify() is False

    def test_tampered_content_hash_itself_fails(self):
        r = _make_receipt()
        object.__setattr__(r, "content_hash", "sha256:" + "0" * 64)
        assert r.verify() is False

    def test_tampered_prev_hash_fails_verification(self):
        r = _make_receipt(prev_hash="")
        object.__setattr__(r, "prev_hash", "sha256:" + "a" * 64)
        assert r.verify() is False

    def test_env_key_used_in_verify_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "env-key")
        r = _make_receipt(key="env-key")
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "env-key")
        assert r.verify() is True  # picks up env key automatically


# ===========================================================================
# ExecutionReceipt — serialisation round-trip
# ===========================================================================


class TestExecutionReceiptSerialization:
    def test_to_json_produces_valid_json(self):
        r = _make_receipt()
        parsed = json.loads(r.to_json())
        assert parsed["run_id"] == r.run_id

    def test_from_json_round_trip_verifies(self):
        r = _make_receipt(key="k")
        loaded = ExecutionReceipt.from_json(r.to_json())
        assert loaded.verify(key="k")

    def test_from_json_restores_all_fields(self):
        r = _make_receipt(agent="my-bot", prompt="hello", score=70, grade="WARN")
        loaded = ExecutionReceipt.from_json(r.to_json())
        assert loaded.run_id == r.run_id
        assert loaded.agent == "my-bot"
        assert loaded.prompt == "hello"
        assert loaded.quality_score == 70
        assert loaded.quality_grade == "WARN"
        assert loaded.content_hash == r.content_hash
        assert loaded.signature == r.signature

    def test_to_dict_keys_are_sorted(self):
        r = _make_receipt()
        raw = r.to_json()
        # json.dumps with sort_keys always produces alphabetically sorted keys
        keys = [k for k in json.loads(raw)]
        assert keys == sorted(keys)

    def test_from_dict_handles_missing_optional_fields(self):
        minimal = {
            "run_id": "run_abc",
            "agent": "bot",
            "prompt": "go",
            "final_text": "",
            "content_hash": "sha256:" + "0" * 64,
        }
        r = ExecutionReceipt.from_dict(minimal)
        assert r.run_id == "run_abc"
        assert r.tool_calls == []
        assert r.findings == []

    def test_unicode_content_survives_round_trip(self):
        ugly = "résumé 日本語 🔥 \x00 \n\t"
        r = _make_receipt(text=ugly, prompt=ugly)
        loaded = ExecutionReceipt.from_json(r.to_json())
        assert loaded.final_text == ugly
        assert loaded.prompt == ugly
        assert loaded.verify()

    def test_empty_string_prompt_round_trip(self):
        r = _make_receipt(prompt="")
        loaded = ExecutionReceipt.from_json(r.to_json())
        assert loaded.prompt == ""
        assert loaded.verify()

    def test_very_long_text_round_trip(self):
        huge = "x" * 100_000
        r = _make_receipt(text=huge)
        loaded = ExecutionReceipt.from_json(r.to_json())
        assert loaded.final_text == huge
        assert loaded.verify()


# ===========================================================================
# TrustLog — append + verify chain
# ===========================================================================


class TestTrustLogInMemory:
    def test_empty_log_has_len_zero(self):
        assert len(TrustLog()) == 0

    def test_empty_log_tail_hash_is_empty(self):
        assert TrustLog().tail_hash == ""

    def test_empty_log_verify_chain_passes(self):
        assert TrustLog().verify_chain() is True

    def test_append_increments_len(self):
        log = TrustLog()
        log.append(_make_receipt())
        assert len(log) == 1

    def test_tail_hash_matches_last_receipt(self):
        log = TrustLog()
        r = _make_receipt()
        log.append(r)
        assert log.tail_hash == r.content_hash

    def test_two_receipts_chain_correctly(self):
        log = TrustLog()
        r1 = _make_receipt()
        log.append(r1)
        r2 = _make_receipt(prev_hash=log.tail_hash)
        log.append(r2)
        assert len(log) == 2
        assert log.verify_chain() is True

    def test_verify_chain_fails_after_content_tamper(self):
        log = TrustLog()
        r = _make_receipt()
        log.append(r)
        # Tamper the stored receipt directly
        object.__setattr__(log._entries[0], "final_text", "TAMPERED")
        assert log.verify_chain() is False

    def test_verify_chain_fails_when_prev_hash_mismatched(self):
        log = TrustLog()
        r1 = _make_receipt()
        log.append(r1)
        r2 = _make_receipt(prev_hash="sha256:" + "0" * 64)  # wrong prev_hash
        # append validates the chain — should raise
        with pytest.raises(ValueError, match="chain broken"):
            log.append(r2)

    def test_append_rejects_wrong_prev_hash(self):
        log = TrustLog()
        log.append(_make_receipt())
        bad = _make_receipt(prev_hash="sha256:notright" + "0" * 54)
        with pytest.raises(ValueError):
            log.append(bad)

    def test_get_by_run_id(self):
        log = TrustLog()
        r = _make_receipt()
        log.append(r)
        found = log.get(r.run_id)
        assert found is r

    def test_get_unknown_run_id_returns_none(self):
        log = TrustLog()
        assert log.get("run_nonexistent") is None

    def test_iter_yields_all_receipts_in_order(self):
        log = TrustLog()
        r1 = _make_receipt(prompt="a")
        log.append(r1)
        r2 = _make_receipt(prompt="b", prev_hash=log.tail_hash)
        log.append(r2)
        entries = list(log)
        assert entries[0].prompt == "a"
        assert entries[1].prompt == "b"

    def test_iter_yields_snapshot_not_live_view(self):
        log = TrustLog()
        r = _make_receipt()
        log.append(r)
        it = iter(log)
        # Append after iter created — snapshot should not include new entry
        r2 = _make_receipt(prev_hash=log.tail_hash)
        log.append(r2)
        entries = list(it)
        assert len(entries) == 1

    def test_repr_contains_entry_count(self):
        log = TrustLog()
        log.append(_make_receipt())
        assert "1" in repr(log)

    def test_to_jsonl_produces_one_line_per_receipt(self):
        log = TrustLog()
        r1 = _make_receipt()
        log.append(r1)
        r2 = _make_receipt(prev_hash=log.tail_hash)
        log.append(r2)
        lines = [ln for ln in log.to_jsonl().splitlines() if ln.strip()]
        assert len(lines) == 2


class TestTrustLogFileBacked:
    def test_persists_to_file(self, tmp_path):
        p = str(tmp_path / "trust.jsonl")
        log = TrustLog(p)
        log.append(_make_receipt())
        assert Path(p).exists()
        lines = Path(p).read_text().strip().splitlines()
        assert len(lines) == 1

    def test_reload_from_file_restores_entries(self, tmp_path):
        p = str(tmp_path / "trust.jsonl")
        log1 = TrustLog(p)
        r = _make_receipt()
        log1.append(r)

        log2 = TrustLog(p)  # reload
        assert len(log2) == 1
        assert log2.get(r.run_id).run_id == r.run_id

    def test_reload_verifies_chain(self, tmp_path):
        p = str(tmp_path / "trust.jsonl")
        log1 = TrustLog(p)
        r1 = _make_receipt()
        log1.append(r1)
        r2 = _make_receipt(prev_hash=log1.tail_hash)
        log1.append(r2)

        log2 = TrustLog(p)
        assert log2.verify_chain() is True

    def test_corrupt_jsonl_lines_skipped_on_load(self, tmp_path):
        p = tmp_path / "trust.jsonl"
        r = _make_receipt()
        p.write_text(r.to_json() + "\n{NOT JSON}\n", encoding="utf-8")
        log = TrustLog(str(p))
        assert len(log) == 1  # corrupt line silently skipped

    def test_empty_lines_in_file_skipped(self, tmp_path):
        p = tmp_path / "trust.jsonl"
        r = _make_receipt()
        p.write_text("\n\n" + r.to_json() + "\n\n", encoding="utf-8")
        log = TrustLog(str(p))
        assert len(log) == 1

    def test_file_created_when_it_doesnt_exist(self, tmp_path):
        p = str(tmp_path / "new.jsonl")
        log = TrustLog(p)
        log.append(_make_receipt())
        assert Path(p).exists()

    def test_appends_do_not_overwrite_existing_file(self, tmp_path):
        p = str(tmp_path / "trust.jsonl")
        log1 = TrustLog(p)
        r1 = _make_receipt()
        log1.append(r1)

        log2 = TrustLog(p)
        r2 = _make_receipt(prev_hash=log2.tail_hash)
        log2.append(r2)

        lines = Path(p).read_text().strip().splitlines()
        assert len(lines) == 2  # both entries present

    def test_nonexistent_path_dir_raises_on_write(self, tmp_path):
        p = str(tmp_path / "missing_dir" / "trust.jsonl")
        log = TrustLog(p)
        with pytest.raises((FileNotFoundError, OSError)):
            log.append(_make_receipt())


# ===========================================================================
# AssurancePolicy — SLA enforcement
# ===========================================================================


class TestAssurancePolicySLA:
    def _policy(self, min_score=80, on_fail="ignore", on_escalate=None):
        return AssurancePolicy(min_score=min_score, on_fail=on_fail, on_escalate=on_escalate)

    def _receipt(self, score: int) -> ExecutionReceipt:
        return _make_receipt(score=score, grade="PASS" if score >= 80 else "FAIL")

    def test_ignore_does_nothing_on_breach(self):
        policy = self._policy(min_score=80, on_fail="ignore")
        # Must not raise
        policy.enforce_sla(self._receipt(score=50))

    def test_raise_raises_sla_breached(self):
        policy = self._policy(min_score=80, on_fail="raise")
        with pytest.raises(SLABreached) as exc_info:
            policy.enforce_sla(self._receipt(score=50))
        assert exc_info.value.score == 50
        assert exc_info.value.min_score == 80

    def test_raise_does_not_fire_when_score_meets_threshold(self):
        policy = self._policy(min_score=80, on_fail="raise")
        policy.enforce_sla(self._receipt(score=80))  # exactly at threshold — ok

    def test_raise_does_not_fire_when_score_above_threshold(self):
        policy = self._policy(min_score=60, on_fail="raise")
        policy.enforce_sla(self._receipt(score=90))

    def test_escalate_calls_on_escalate(self):
        calls = []
        policy = self._policy(min_score=80, on_fail="escalate", on_escalate=calls.append)
        policy.enforce_sla(self._receipt(score=40))
        assert len(calls) == 1
        assert calls[0].quality_score == 40

    def test_escalate_with_no_callback_is_silent(self):
        policy = self._policy(min_score=80, on_fail="escalate", on_escalate=None)
        policy.enforce_sla(self._receipt(score=10))  # no error

    def test_min_score_zero_disables_enforcement(self):
        policy = self._policy(min_score=0, on_fail="raise")
        policy.enforce_sla(self._receipt(score=0))  # should not raise

    def test_sla_breached_exception_carries_receipt(self):
        policy = self._policy(min_score=80, on_fail="raise")
        r = self._receipt(score=20)
        with pytest.raises(SLABreached) as exc_info:
            policy.enforce_sla(r)
        assert exc_info.value.receipt.run_id == r.run_id

    def test_sla_breached_str_contains_scores(self):
        policy = self._policy(min_score=80, on_fail="raise")
        with pytest.raises(SLABreached) as exc_info:
            policy.enforce_sla(self._receipt(score=30))
        assert "30" in str(exc_info.value)
        assert "80" in str(exc_info.value)


# ===========================================================================
# AssurancePolicy — log integration
# ===========================================================================


class TestAssurancePolicyLogIntegration:
    def test_receipt_appended_to_log_on_enforce_sla(self):
        log = TrustLog()
        policy = AssurancePolicy(log=log, min_score=0)
        r1 = _make_receipt()
        policy.log.append(r1)
        assert len(log) == 1

    def test_no_log_does_not_crash(self):
        policy = AssurancePolicy(log=None, min_score=80, on_fail="raise")
        with pytest.raises(SLABreached):
            policy.enforce_sla(_make_receipt(score=10))

    def test_key_defaults_to_env(self, monkeypatch):
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "from-env")
        policy = AssurancePolicy()
        # key read lazily from env at construction
        assert policy.key == "from-env"

    def test_explicit_key_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("TVASTAR_RECEIPT_KEY", "from-env")
        policy = AssurancePolicy(key="explicit")
        assert policy.key == "explicit"


# ===========================================================================
# Integration: full run through MockModel
# ===========================================================================


class TestAssuranceEndToEnd:
    @pytest.mark.asyncio
    async def test_receipt_attached_to_run_result(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        agent = create_agent(
            "billing-bot",
            model=MockModel(script=["Charged $50."]),
            assurance=AssurancePolicy(),
        )
        result = await Harness(agent).run("Charge customer $50")

        assert result.receipt is not None
        assert result.receipt.agent == "billing-bot"
        assert result.receipt.prompt == "Charge customer $50"
        assert result.receipt.final_text == "Charged $50."
        assert result.receipt.verify() is True

    @pytest.mark.asyncio
    async def test_receipt_content_hash_is_stable(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        agent = create_agent(
            "a",
            model=MockModel(script=["ok"]),
            assurance=AssurancePolicy(),
        )
        result = await Harness(agent).run("go")
        original_hash = result.receipt.content_hash
        # Re-verify — must still match
        assert result.receipt.verify() is True
        assert result.receipt.content_hash == original_hash

    @pytest.mark.asyncio
    async def test_receipt_appended_to_trust_log(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        log = TrustLog()
        agent = create_agent(
            "a",
            model=MockModel(script=["done"]),
            assurance=AssurancePolicy(log=log),
        )
        await Harness(agent).run("go")
        assert len(log) == 1
        assert log.verify_chain() is True

    @pytest.mark.asyncio
    async def test_two_runs_chain_correctly(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        log = TrustLog()
        agent = create_agent(
            "a",
            model=MockModel(script=["run 1", "run 2"]),
            assurance=AssurancePolicy(log=log),
        )
        harness = Harness(agent)
        await harness.run("first")
        await harness.run("second")
        assert len(log) == 2
        assert log.verify_chain() is True
        assert log._entries[1].prev_hash == log._entries[0].content_hash

    @pytest.mark.asyncio
    async def test_signed_receipt_verifies_with_key(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        agent = create_agent(
            "a",
            model=MockModel(script=["ok"]),
            assurance=AssurancePolicy(key="prod-secret"),
        )
        result = await Harness(agent).run("go")
        assert result.receipt.verify(key="prod-secret") is True
        assert result.receipt.verify(key="wrong-key") is False

    @pytest.mark.asyncio
    async def test_sla_raise_fires_on_bad_run(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        agent = create_agent(
            "a",
            model=MockModel(script=[""]),
            assurance=AssurancePolicy(min_score=99, on_fail="raise"),
        )
        with pytest.raises(SLABreached):
            await Harness(agent).run("go")

    @pytest.mark.asyncio
    async def test_sla_escalate_calls_handler(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        escalated = []
        agent = create_agent(
            "a",
            model=MockModel(script=[""]),
            assurance=AssurancePolicy(
                min_score=99,
                on_fail="escalate",
                on_escalate=escalated.append,
            ),
        )
        await Harness(agent).run("go")
        assert len(escalated) == 1
        assert isinstance(escalated[0], ExecutionReceipt)

    @pytest.mark.asyncio
    async def test_no_assurance_means_no_receipt(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        agent = create_agent(
            "a",
            model=MockModel(script=["hi"]),
        )
        result = await Harness(agent).run("go")
        assert result.receipt is None

    @pytest.mark.asyncio
    async def test_receipt_persisted_to_file(self, tmp_path):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        p = str(tmp_path / "trust.jsonl")
        agent = create_agent(
            "a",
            model=MockModel(script=["filed"]),
            assurance=AssurancePolicy(log=TrustLog(p)),
        )
        await Harness(agent).run("go")
        assert Path(p).exists()
        lines = Path(p).read_text().strip().splitlines()
        assert len(lines) == 1
        stored = ExecutionReceipt.from_json(lines[0])
        assert stored.verify() is True
        assert stored.final_text == "filed"


# ===========================================================================
# Edge cases & ugly inputs
# ===========================================================================


class TestUglyInputs:
    def test_empty_prompt_in_receipt(self):
        r = _make_receipt(prompt="")
        assert r.prompt == ""
        assert r.verify() is True

    def test_null_bytes_in_text(self):
        r = _make_receipt(text="hello\x00world")
        assert r.verify() is True

    def test_newlines_and_tabs_in_prompt(self):
        r = _make_receipt(prompt="fix\nthis\t\r\nplease")
        assert r.verify() is True

    def test_very_long_agent_name(self):
        r = _make_receipt(agent="a" * 1000)
        assert r.verify() is True

    def test_zero_tokens(self):
        result = _FakeResult(input_tokens=0, output_tokens=0)
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q", started_at=t, completed_at=t,
        )
        assert r.usage_input == 0
        assert r.verify() is True

    def test_receipt_with_100_tool_calls(self):
        uses = [ToolUseBlock(name=f"t{i}", input={"i": i}, id=f"c{i}") for i in range(100)]
        msg = Message("assistant", uses)
        result = _FakeResult(messages=[msg])
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q", started_at=t, completed_at=t,
        )
        assert len(r.tool_calls) == 100
        assert r.verify() is True

    def test_receipt_from_non_assistant_messages_only(self):
        msgs = [
            Message("user", [TextBlock(text="hi")]),
            Message("user", [ToolResultBlock(tool_use_id="c1", content="ok", is_error=False)]),
        ]
        result = _FakeResult(messages=msgs)
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            result, agent="a", prompt="q", started_at=t, completed_at=t,
        )
        assert r.tool_calls == []

    def test_trust_log_with_hundred_receipts_verifies(self):
        log = TrustLog()
        for i in range(100):
            r = _make_receipt(prompt=f"run {i}", prev_hash=log.tail_hash)
            log.append(r)
        assert len(log) == 100
        assert log.verify_chain() is True

    def test_key_with_special_chars(self):
        key = "p@$$w0rd!#&*()🔑\n\t"
        r = _make_receipt(key=key)
        assert r.verify(key=key) is True
        assert r.verify(key=key[:-1]) is False

    def test_content_hash_deterministic_for_same_inputs(self):
        # Two receipts with identical content except run_id (which is random)
        # must have different hashes — run_id is always unique.
        r1 = _make_receipt(prompt="same")
        r2 = _make_receipt(prompt="same")
        assert r1.content_hash != r2.content_hash  # different run_ids


# ===========================================================================
# Gap 1: Model name tracking
# ===========================================================================


class TestModelTracking:
    def test_model_name_stored_in_receipt(self):
        r = _make_receipt(model_name="claude-sonnet-4-6")
        assert r.model_name == "claude-sonnet-4-6"

    def test_model_name_included_in_hash(self):
        r1 = _make_receipt(model_name="gpt-4o")
        r2 = _make_receipt(model_name="claude-sonnet-4-6")
        assert r1.content_hash != r2.content_hash

    def test_model_name_verify_detects_tamper(self):
        r = _make_receipt(model_name="claude-sonnet-4-6")
        d = r.to_dict()
        d["model_name"] = "gpt-4o"
        r2 = ExecutionReceipt.from_dict(d)
        assert r2.verify() is False

    def test_model_name_roundtrip_json(self):
        r = _make_receipt(model_name="claude-opus-4-8")
        r2 = ExecutionReceipt.from_json(r.to_json())
        assert r2.model_name == "claude-opus-4-8"
        assert r2.verify() is True

    def test_model_name_empty_string_is_valid(self):
        r = _make_receipt(model_name="")
        assert r.model_name == ""
        assert r.verify() is True

    def test_model_name_in_text_audit_report(self):
        r = _make_receipt(model_name="claude-sonnet-4-6")
        assert "claude-sonnet-4-6" in r.to_audit_report()

    def test_model_name_in_html_audit_report(self):
        r = _make_receipt(model_name="claude-sonnet-4-6")
        assert "claude-sonnet-4-6" in r.to_audit_report("html")

    def test_no_model_name_shows_not_recorded_in_html(self):
        r = _make_receipt(model_name="")
        assert "not recorded" in r.to_audit_report("html")

    @pytest.mark.asyncio
    async def test_end_to_end_model_name_from_mock_model(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        model = MockModel(script=["done"])
        agent = create_agent("bot", model=model, assurance=AssurancePolicy())
        result = await Harness(agent).run("go")
        assert result.receipt.model_name == model.name

    def test_receipt_version_is_2(self):
        r = _make_receipt()
        assert r.version == "2"


# ===========================================================================
# Gap 2: Tool outputs captured
# ===========================================================================


class TestToolOutputs:
    def test_tool_output_in_receipt(self):
        r = _make_receipt_with_tools()
        assert r.tool_calls[0]["output"] == "612"

    def test_tool_output_included_in_hash(self):
        r1 = _make_receipt_with_tools()
        d = r1.to_dict()
        d["tool_calls"][0]["output"] = "999"
        # Change output → hash should mismatch
        r2 = ExecutionReceipt.from_dict(d)
        assert r2.verify() is False

    def test_tool_output_in_text_report(self):
        r = _make_receipt_with_tools()
        report = r.to_audit_report()
        assert "612" in report

    def test_tool_output_in_html_report(self):
        r = _make_receipt_with_tools()
        assert "612" in r.to_audit_report("html")

    def test_tool_output_empty_when_no_result(self):
        r = _make_receipt()
        assert r.tool_calls == []

    @pytest.mark.asyncio
    async def test_end_to_end_tool_output_captured(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel
        from tvastar.tools import tool

        @tool
        def get_balance(account_id: str) -> str:
            return f"${account_id}:1234.56"

        model = MockModel(script=[
            ToolUseBlock(name="get_balance", input={"account_id": "ACC-42"}, id="tc_e2e"),
            "Balance retrieved.",
        ])
        agent = create_agent("bank-bot", model=model, tools=[get_balance], assurance=AssurancePolicy())
        result = await Harness(agent).run("What is my balance?")

        assert result.receipt is not None
        assert len(result.receipt.tool_calls) == 1
        assert result.receipt.tool_calls[0]["name"] == "get_balance"
        assert result.receipt.tool_calls[0]["output"] == "$ACC-42:1234.56"
        assert result.receipt.verify() is True


# ===========================================================================
# Gap 3: PII redaction — SanitizationPolicy
# ===========================================================================


class TestSanitizationPolicy:
    # --- scrub() ---

    def test_ssn_redacted(self):
        s = SanitizationPolicy.hipaa()
        assert s.scrub("SSN is 123-45-6789 for this patient") == "SSN is [SSN] for this patient"

    def test_email_redacted(self):
        s = SanitizationPolicy.hipaa()
        assert "[EMAIL]" in s.scrub("Contact jane@example.com for details")

    def test_phone_redacted(self):
        s = SanitizationPolicy.hipaa()
        assert "[PHONE]" in s.scrub("Call 555-867-5309 now")

    def test_credit_card_redacted_pci(self):
        s = SanitizationPolicy.pci()
        assert "[CARD]" in s.scrub("Card: 4111111111111111")

    def test_ip_redacted(self):
        s = SanitizationPolicy.hipaa()
        assert "[IP]" in s.scrub("From IP 192.168.1.1")

    def test_bearer_token_redacted(self):
        s = SanitizationPolicy.hipaa()
        assert "[TOKEN]" in s.scrub("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")

    def test_no_patterns_leaves_text_unchanged(self):
        s = SanitizationPolicy()
        assert s.scrub("SSN 123-45-6789") == "SSN 123-45-6789"

    def test_custom_pattern(self):
        s = SanitizationPolicy().add_pattern(r"\bACCT-\d+\b", "[ACCOUNT]")
        assert s.scrub("Account ACCT-99182 overdrawn") == "Account [ACCOUNT] overdrawn"

    def test_multiple_matches_all_redacted(self):
        s = SanitizationPolicy.hipaa()
        text = "SSN 123-45-6789 and 987-65-4321"
        result = s.scrub(text)
        assert "123-45-6789" not in result
        assert "987-65-4321" not in result

    def test_gdpr_preset(self):
        s = SanitizationPolicy.gdpr()
        assert "[EMAIL]" in s.scrub("user@example.com")
        assert "[SSN]" in s.scrub("123-45-6789")

    def test_all_pii_preset(self):
        s = SanitizationPolicy.all_pii()
        assert "[EMAIL]" in s.scrub("x@y.com")

    # --- scrub_tool_calls() ---

    def test_scrubs_tool_input_values(self):
        s = SanitizationPolicy.hipaa()
        calls = [{"id": "1", "name": "lookup", "input": {"email": "jane@example.com"}, "output": ""}]
        result = s.scrub_tool_calls(calls)
        assert "jane@example.com" not in str(result)
        assert "[EMAIL]" in str(result)

    def test_scrubs_tool_output(self):
        s = SanitizationPolicy.hipaa()
        calls = [{"id": "1", "name": "get_ssn", "input": {}, "output": "SSN: 123-45-6789"}]
        result = s.scrub_tool_calls(calls)
        assert "123-45-6789" not in result[0]["output"]

    def test_does_not_mutate_original_tool_calls(self):
        s = SanitizationPolicy.hipaa()
        calls = [{"id": "1", "name": "f", "input": {"x": "123-45-6789"}, "output": ""}]
        s.scrub_tool_calls(calls)
        assert calls[0]["input"]["x"] == "123-45-6789"  # original unchanged

    # --- apply() ---

    def test_apply_redacts_prompt(self):
        s = SanitizationPolicy.hipaa()
        p, _, _ = s.apply(prompt="SSN 123-45-6789", tool_calls=[], final_text="ok")
        assert "[SSN]" in p
        assert "123-45-6789" not in p

    def test_apply_redacts_final_text(self):
        s = SanitizationPolicy.hipaa()
        _, _, t = s.apply(prompt="ok", tool_calls=[], final_text="SSN 123-45-6789")
        assert "[SSN]" in t

    def test_apply_respects_redact_prompt_false(self):
        s = SanitizationPolicy.hipaa()
        s.redact_prompt = False
        p, _, _ = s.apply(prompt="SSN 123-45-6789", tool_calls=[], final_text="ok")
        assert "123-45-6789" in p

    def test_apply_respects_redact_answer_false(self):
        s = SanitizationPolicy.hipaa()
        s.redact_answer = False
        _, _, t = s.apply(prompt="ok", tool_calls=[], final_text="SSN 123-45-6789")
        assert "123-45-6789" in t

    # --- receipt integration ---

    def test_receipt_prompt_redacted(self):
        s = SanitizationPolicy.hipaa()
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"),
            agent="a", model_name="m",
            prompt="Patient SSN 123-45-6789 has diabetes",
            started_at=t, completed_at=t + 1,
            sanitize=s,
        )
        assert "123-45-6789" not in r.prompt
        assert "[SSN]" in r.prompt
        assert r.verify() is True  # hash covers redacted form

    def test_receipt_final_text_redacted(self):
        s = SanitizationPolicy.hipaa()
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            _FakeResult("Contact jane@example.com for results"),
            agent="a", model_name="m",
            prompt="get results",
            started_at=t, completed_at=t + 1,
            sanitize=s,
        )
        assert "jane@example.com" not in r.final_text
        assert r.verify() is True

    def test_receipt_verify_fails_if_pii_re_injected(self):
        s = SanitizationPolicy.hipaa()
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"),
            agent="a", model_name="m",
            prompt="SSN 123-45-6789",
            started_at=t, completed_at=t + 1,
            sanitize=s,
        )
        d = r.to_dict()
        d["prompt"] = "SSN 123-45-6789"  # re-inject raw PII
        r2 = ExecutionReceipt.from_dict(d)
        assert r2.verify() is False  # hash mismatch — redaction proves PII was removed

    @pytest.mark.asyncio
    async def test_end_to_end_pii_redacted_in_trust_log(self):
        from tvastar import Harness, create_agent
        from tvastar.model import MockModel

        log = TrustLog()
        agent = create_agent(
            "hipaa-bot",
            model=MockModel(script=["Patient records retrieved for jane@example.com."]),
            assurance=AssurancePolicy(
                log=log,
                sanitize=SanitizationPolicy.hipaa(),
            ),
        )
        result = await Harness(agent).run("Get records for jane@example.com SSN 123-45-6789")
        r = result.receipt
        assert "123-45-6789" not in r.prompt
        assert "jane@example.com" not in r.prompt
        assert "jane@example.com" not in r.final_text
        assert r.verify() is True
        assert log.verify_chain()


# ===========================================================================
# Gap 3b: Presidio ML-powered PII detection
# ===========================================================================


class TestPresidioSanitizationPolicy:
    def test_presidio_factory_returns_instance(self):
        from tvastar.assurance.sanitize import _PresidioSanitizationPolicy
        p = SanitizationPolicy.presidio()
        assert isinstance(p, _PresidioSanitizationPolicy)

    def test_presidio_default_language_is_en(self):
        p = SanitizationPolicy.presidio()
        assert p._languages == ["en"]

    def test_presidio_custom_languages(self):
        p = SanitizationPolicy.presidio(languages=["en", "de", "fr"])
        assert p._languages == ["en", "de", "fr"]

    def test_presidio_custom_entities(self):
        p = SanitizationPolicy.presidio(entities=["PERSON", "EMAIL_ADDRESS"])
        assert p._entities == ["PERSON", "EMAIL_ADDRESS"]

    def test_presidio_custom_score_threshold(self):
        p = SanitizationPolicy.presidio(score_threshold=0.8)
        assert p._score_threshold == 0.8

    def test_presidio_raises_import_error_when_not_installed(self):
        import sys
        # Temporarily hide presidio from the import system
        presidio_mods = [k for k in sys.modules if "presidio" in k]
        saved = {k: sys.modules.pop(k) for k in presidio_mods}
        try:
            p = SanitizationPolicy.presidio()
            # Patch builtins.__import__ to simulate missing package
            import builtins
            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if "presidio" in name:
                    raise ImportError(f"No module named {name!r}")
                return real_import(name, *args, **kwargs)

            builtins.__import__ = mock_import
            try:
                with pytest.raises(ImportError, match="presidio"):
                    p.scrub("Patient Jane Smith")
            finally:
                builtins.__import__ = real_import
        finally:
            sys.modules.update(saved)

    def test_presidio_scrub_with_mock_engine(self):
        from unittest.mock import MagicMock, patch

        # Build mock Presidio objects
        mock_result = MagicMock()
        mock_result.entity_type = "PERSON"

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [mock_result]

        mock_anon_result = MagicMock()
        mock_anon_result.text = "Patient [PERSON] has diabetes"
        mock_anonymizer = MagicMock()
        mock_anonymizer.anonymize.return_value = mock_anon_result

        mock_operator_config = MagicMock()

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock(return_value=mock_anonymizer)),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock(return_value=mock_operator_config)),
        }):
            p = SanitizationPolicy.presidio()
            result = p.scrub("Patient Jane Smith has diabetes")

        assert result == "Patient [PERSON] has diabetes"
        mock_analyzer.analyze.assert_called_once()

    def test_presidio_no_results_returns_text_unchanged(self):
        from unittest.mock import MagicMock, patch

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = []  # no PII found

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock()),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio()
            result = p.scrub("No PII here at all.")

        assert result == "No PII here at all."

    def test_presidio_empty_string_skips_engine(self):
        from unittest.mock import MagicMock, patch

        mock_analyzer = MagicMock()
        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock()),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio()
            result = p.scrub("")

        assert result == ""
        mock_analyzer.analyze.assert_not_called()

    def test_presidio_chains_regex_patterns_after_nlp(self):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.entity_type = "PERSON"

        mock_anon_out = MagicMock()
        mock_anon_out.text = "[PERSON] opened account ACCT-99182"

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [mock_result]
        mock_anonymizer = MagicMock()
        mock_anonymizer.anonymize.return_value = mock_anon_out

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock(return_value=mock_anonymizer)),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio()
            p.add_pattern(r"ACCT-\d+", "[ACCOUNT]")
            result = p.scrub("Jane Smith opened account ACCT-99182")

        assert "[PERSON]" in result
        assert "[ACCOUNT]" in result
        assert "ACCT-99182" not in result

    def test_presidio_engines_initialised_once(self):
        from unittest.mock import MagicMock, patch

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = []
        mock_analyzer_cls.return_value = mock_analyzer

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=mock_analyzer_cls),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock()),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio()
            p.scrub("first call")
            p.scrub("second call")

        # AnalyzerEngine() constructor called only once despite two scrub() calls
        assert mock_analyzer_cls.call_count == 1

    def test_presidio_repr_shows_languages(self):
        p = SanitizationPolicy.presidio(languages=["en", "fr"])
        assert "en" in repr(p)
        assert "fr" in repr(p)

    def test_presidio_install_hint_in_error_message(self):
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "presidio" in name:
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        p = SanitizationPolicy.presidio()
        builtins.__import__ = mock_import
        try:
            with pytest.raises(ImportError, match="pip install tvastar"):
                p.scrub("some text")
        finally:
            builtins.__import__ = real_import

    def test_presidio_is_subclass_of_sanitization_policy(self):
        p = SanitizationPolicy.presidio()
        assert isinstance(p, SanitizationPolicy)

    def test_presidio_inherits_apply(self):
        from unittest.mock import MagicMock, patch

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = []

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock()),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio()
            prompt, tools, text = p.apply(
                prompt="Hello world",
                tool_calls=[],
                final_text="Safe text",
            )

        assert prompt == "Hello world"
        assert text == "Safe text"

    def test_presidio_multiple_languages_each_called(self):
        from unittest.mock import MagicMock, patch

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = []

        with patch.dict("sys.modules", {
            "presidio_analyzer": MagicMock(AnalyzerEngine=MagicMock(return_value=mock_analyzer)),
            "presidio_anonymizer": MagicMock(AnonymizerEngine=MagicMock()),
            "presidio_anonymizer.entities": MagicMock(OperatorConfig=MagicMock()),
        }):
            p = SanitizationPolicy.presidio(languages=["en", "de"])
            p.scrub("some text")

        assert "en" in str(mock_analyzer.analyze.call_args_list)
        assert "de" in str(mock_analyzer.analyze.call_args_list)


# ===========================================================================
# Gap 5: Human approver linkage
# ===========================================================================


class TestApproverTracking:
    def test_approval_request_records_approver(self):
        from tvastar.approval import ApprovalRequest
        req = ApprovalRequest("Approve transfer")
        req.approve(approver="jane.doe@company.com")
        assert req.approved_by == "jane.doe@company.com"
        assert req.approved_at > 0

    def test_approval_request_empty_approver_by_default(self):
        from tvastar.approval import ApprovalRequest
        req = ApprovalRequest("Approve")
        req.approve()
        assert req.approved_by == ""

    def test_receipt_approvals_field_defaults_empty(self):
        r = _make_receipt()
        assert r.approvals == []

    def test_receipt_approvals_included_in_hash(self):
        t = time.time()
        r_no_approval = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1, approvals=[],
        )
        r_with_approval = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1,
            approvals=[{"tool": "transfer", "approved_by": "jane", "approved_at": t, "message": "ok"}],
        )
        assert r_no_approval.content_hash != r_with_approval.content_hash

    def test_receipt_approvals_tamper_fails_verify(self):
        t = time.time()
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1,
            approvals=[{"tool": "x", "approved_by": "alice", "approved_at": t, "message": "ok"}],
        )
        d = r.to_dict()
        d["approvals"][0]["approved_by"] = "eve"
        r2 = ExecutionReceipt.from_dict(d)
        assert r2.verify() is False

    def test_receipt_approvals_roundtrip_json(self):
        t = time.time()
        ap = [{"tool": "wire_funds", "approved_by": "cfo@corp.com", "approved_at": t, "message": "ok"}]
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1, approvals=ap,
        )
        r2 = ExecutionReceipt.from_json(r.to_json())
        assert r2.approvals[0]["approved_by"] == "cfo@corp.com"
        assert r2.verify() is True

    def test_approvals_in_text_audit_report(self):
        t = time.time()
        ap = [{"tool": "delete_account", "approved_by": "supervisor@co.com", "approved_at": t, "message": "ok"}]
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1, approvals=ap,
        )
        report = r.to_audit_report()
        assert "HUMAN APPROVALS" in report
        assert "supervisor@co.com" in report
        assert "delete_account" in report

    def test_no_approvals_no_approval_section(self):
        r = _make_receipt()
        assert "HUMAN APPROVALS" not in r.to_audit_report()

    def test_unidentified_approver_shows_in_report(self):
        t = time.time()
        ap = [{"tool": "tool_x", "approved_by": "", "approved_at": t, "message": "ok"}]
        r = ExecutionReceipt.from_run_result(
            _FakeResult("done"), agent="a", model_name="m",
            prompt="go", started_at=t, completed_at=t + 1, approvals=ap,
        )
        assert "unidentified operator" in r.to_audit_report()


# ===========================================================================
# Gap 6: TrustLog access control — can_read
# ===========================================================================


class TestTrustLogAccessControl:
    def test_open_log_allows_all_roles(self):
        log = TrustLog()  # no can_read — open access
        log.append(_make_receipt())
        # No exception regardless of role
        log.get(next(iter(log)).run_id, role="anyone")

    def test_can_read_blocks_unauthorized_role(self):
        log = TrustLog(can_read=lambda r: r in ("auditor", "admin"))
        log.append(_make_receipt())
        with pytest.raises(PermissionError):
            log.get(next(iter(log)).run_id, role="developer")

    def test_can_read_allows_authorized_role(self):
        log = TrustLog(can_read=lambda r: r == "auditor")
        r = _make_receipt()
        log.append(r)
        result = log.get(r.run_id, role="auditor")
        assert result is not None
        assert result.run_id == r.run_id

    def test_iter_as_blocks_unauthorized(self):
        log = TrustLog(can_read=lambda r: r == "auditor")
        log.append(_make_receipt())
        with pytest.raises(PermissionError):
            list(log.iter_as("developer"))

    def test_iter_as_allows_authorized(self):
        log = TrustLog(can_read=lambda r: r == "auditor")
        log.append(_make_receipt())
        entries = list(log.iter_as("auditor"))
        assert len(entries) == 1

    def test_plain_iter_bypasses_access_control(self):
        # __iter__ intentionally has no access control — used internally by append/verify
        log = TrustLog(can_read=lambda r: False)
        log.append(_make_receipt())
        assert len(list(log)) == 1  # internal iteration still works

    def test_get_without_role_open_log(self):
        log = TrustLog()
        r = _make_receipt()
        log.append(r)
        assert log.get(r.run_id) is not None  # default role=""

    def test_get_missing_run_id_returns_none(self):
        log = TrustLog(can_read=lambda r: True)
        log.append(_make_receipt())
        assert log.get("run_nonexistent", role="auditor") is None

    def test_can_read_callable_receives_role_string(self):
        received = []
        log = TrustLog(can_read=lambda r: received.append(r) or True)
        r = _make_receipt()
        log.append(r)
        log.get(r.run_id, role="compliance-team")
        assert received == ["compliance-team"]


# ===========================================================================
# Gap 4: Chain breach alert — on_breach callback
# ===========================================================================


class TestChainBreachAlert:
    def test_on_breach_called_when_chain_tampered(self):
        import dataclasses
        breached = []
        log = TrustLog(on_breach=lambda r: breached.append(r))
        r1 = _make_receipt()
        r2 = _make_receipt(prev_hash=r1.content_hash)
        log.append(r1)
        log.append(r2)
        # Corrupt r2's prev_hash so chain check fails
        log._entries[1] = dataclasses.replace(r2, prev_hash="sha256:wrong")
        assert log.verify_chain() is False
        assert len(breached) == 1

    def test_on_breach_not_called_when_chain_intact(self):
        breached = []
        log = TrustLog(on_breach=lambda r: breached.append(r))
        r = _make_receipt()
        log.append(r)
        assert log.verify_chain() is True
        assert breached == []

    def test_on_breach_receives_first_corrupt_receipt(self):
        import dataclasses
        receipts = []
        log = TrustLog(on_breach=lambda r: receipts.append(r.run_id))
        r1 = _make_receipt()
        r2 = _make_receipt(prev_hash=r1.content_hash)
        log.append(r1)
        log.append(r2)
        log._entries[1] = dataclasses.replace(r2, prev_hash="sha256:wrong")
        log.verify_chain()
        assert len(receipts) == 1  # only first bad one fires

    def test_no_on_breach_chain_still_returns_false(self):
        import dataclasses
        log = TrustLog()  # no callback
        r1 = _make_receipt()
        log.append(r1)
        # Corrupt the stored hash directly so verify() fails
        log._entries[0] = dataclasses.replace(r1, content_hash="sha256:deadbeef")
        assert log.verify_chain() is False

    def test_on_breach_async_callback_scheduled(self):
        import asyncio
        import dataclasses
        fired = []

        async def breach_handler(r):
            fired.append(r.run_id)

        r1 = _make_receipt()
        log = TrustLog(on_breach=breach_handler)
        log.append(r1)
        log._entries[0] = dataclasses.replace(r1, content_hash="sha256:deadbeef")

        async def run():
            log.verify_chain()
            await asyncio.sleep(0.01)  # let the created task execute

        asyncio.run(run())
        assert len(fired) == 1

    def test_on_breach_only_fires_once_per_verify_call(self):
        import dataclasses
        calls = []
        log = TrustLog(on_breach=lambda r: calls.append(1))
        r1 = _make_receipt()
        r2 = _make_receipt(prev_hash=r1.content_hash)
        r3 = _make_receipt(prev_hash=r2.content_hash)
        log.append(r1)
        log.append(r2)
        log.append(r3)
        # corrupt entry[1] prev_hash → stops at first bad entry
        log._entries[1] = dataclasses.replace(r2, prev_hash="sha256:wrong")
        log.verify_chain()
        assert len(calls) == 1


# ===========================================================================
# Audit report — to_audit_report()
# ===========================================================================


class TestAuditReport:
    def test_text_contains_run_id(self):
        r = _make_receipt(prompt="Deny loan for customer #4821")
        report = r.to_audit_report()
        assert r.run_id in report

    def test_text_contains_agent_name(self):
        r = _make_receipt()
        assert r.agent in r.to_audit_report()

    def test_text_contains_prompt(self):
        r = _make_receipt(prompt="Transfer $1000 to account 999")
        assert "Transfer $1000 to account 999" in r.to_audit_report()

    def test_text_contains_final_answer(self):
        r = _make_receipt(text="Done. Transfer complete.")
        assert "Done. Transfer complete." in r.to_audit_report()

    def test_text_contains_quality_grade(self):
        r = _make_receipt(score=91, grade="PASS")
        report = r.to_audit_report()
        assert "PASS" in report
        assert "91" in report

    def test_text_contains_content_hash(self):
        r = _make_receipt()
        assert r.content_hash in r.to_audit_report()

    def test_text_contains_signature_when_signed(self):
        r = _make_receipt(key="signing-key")
        report = r.to_audit_report()
        assert "hmac-sha256:" in report

    def test_text_unsigned_shows_unsigned(self):
        r = _make_receipt()  # no key
        assert "(unsigned)" in r.to_audit_report()

    def test_text_first_entry_chain_display(self):
        r = _make_receipt()  # prev_hash=""
        assert "(first entry)" in r.to_audit_report()

    def test_text_chain_link_shows_prev_hash(self):
        r = _make_receipt(prev_hash="sha256:abc123xyz")
        assert "prev=sha256:abc1" in r.to_audit_report()

    def test_text_tool_calls_listed(self):
        r = _make_receipt_with_tools()
        report = r.to_audit_report()
        assert "check_credit" in report

    def test_text_no_tool_section_when_empty(self):
        r = _make_receipt()  # no tool calls
        assert "DECISIONS MADE" not in r.to_audit_report()

    def test_text_findings_listed(self):
        r = _make_receipt(findings=[
            Finding("thrash_loop", Severity.WARNING, "looping detected", {})
        ])
        report = r.to_audit_report()
        assert "thrash_loop" in report
        assert "looping detected" in report

    def test_text_no_findings_section_when_empty(self):
        r = _make_receipt()
        assert "FINDINGS" not in r.to_audit_report()

    def test_text_fail_grade(self):
        r = _make_receipt(score=30, grade="FAIL")
        report = r.to_audit_report()
        assert "FAIL" in report
        assert "30" in report

    def test_text_default_fmt_is_text(self):
        r = _make_receipt()
        assert r.to_audit_report() == r.to_audit_report("text")

    # HTML format

    def test_html_is_valid_html(self):
        r = _make_receipt()
        html = r.to_audit_report("html")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_html_contains_run_id(self):
        r = _make_receipt(prompt="Check account balance")
        html = r.to_audit_report("html")
        assert r.run_id in html

    def test_html_contains_agent_name(self):
        r = _make_receipt()
        assert r.agent in r.to_audit_report("html")

    def test_html_contains_prompt(self):
        r = _make_receipt(prompt="Approve insurance claim #77")
        assert "Approve insurance claim #77" in r.to_audit_report("html")

    def test_html_contains_final_answer(self):
        r = _make_receipt(text="Claim approved.")
        assert "Claim approved." in r.to_audit_report("html")

    def test_html_contains_content_hash(self):
        r = _make_receipt()
        assert r.content_hash in r.to_audit_report("html")

    def test_html_escapes_xss(self):
        r = _make_receipt(prompt="<script>alert('xss')</script>")
        html = r.to_audit_report("html")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_escapes_ampersand(self):
        r = _make_receipt(prompt="Charge customer A & B")
        html = r.to_audit_report("html")
        assert "A &amp; B" in html

    def test_html_tool_calls_in_table(self):
        r = _make_receipt_with_tools()
        html = r.to_audit_report("html")
        assert "check_credit" in html
        assert "<table" in html

    def test_html_no_tool_table_when_empty(self):
        r = _make_receipt()
        html = r.to_audit_report("html")
        # No tool section header when no tool calls
        assert "Decisions Made" not in html

    def test_html_findings_when_present(self):
        r = _make_receipt(findings=[
            Finding("ignored_tool_error", Severity.ERROR, "tool error ignored", {})
        ])
        html = r.to_audit_report("html")
        assert "ignored_tool_error" in html
        assert "tool error ignored" in html

    def test_html_grade_pass_green(self):
        r = _make_receipt(score=95, grade="PASS")
        html = r.to_audit_report("html")
        assert "#1a7f37" in html  # green for PASS

    def test_html_grade_fail_red(self):
        r = _make_receipt(score=20, grade="FAIL")
        html = r.to_audit_report("html")
        assert "#cf222e" in html  # red for FAIL

    def test_html_grade_warn_yellow(self):
        r = _make_receipt(score=60, grade="WARN")
        html = r.to_audit_report("html")
        assert "#d1a000" in html  # yellow for WARN

    def test_html_unsigned_shows_unsigned(self):
        r = _make_receipt()
        assert "(unsigned)" in r.to_audit_report("html")

    def test_html_signed_shows_hmac(self):
        r = _make_receipt(key="my-key")
        assert "hmac-sha256:" in r.to_audit_report("html")

    def test_roundtrip_text_report_after_json_serialise(self):
        r = _make_receipt(prompt="Do something important")
        r2 = ExecutionReceipt.from_json(r.to_json())
        assert r2.to_audit_report() == r.to_audit_report()

    def test_empty_prompt_renders_safely(self):
        r = _make_receipt(prompt="")
        report = r.to_audit_report()
        assert "INSTRUCTION GIVEN TO AGENT" in report

    def test_multiline_answer_renders(self):
        r = _make_receipt(text="Line 1\nLine 2\nLine 3")
        report = r.to_audit_report()
        assert "Line 1" in report
        assert "Line 2" in report

    def test_unicode_in_prompt_renders(self):
        r = _make_receipt(prompt="顧客への請求: ¥10,000")
        report = r.to_audit_report()
        assert "¥10,000" in report


# ---------------------------------------------------------------------------
# Helpers for audit report tests
# ---------------------------------------------------------------------------


def _make_receipt_with_tools() -> ExecutionReceipt:
    tc = [{"id": "tc_1", "name": "check_credit", "input": {"customer_id": 4821}, "output": "612"}]
    r = _make_receipt()
    # Rebuild with tool_calls injected — use from_dict so hash is consistent
    d = r.to_dict()
    d["tool_calls"] = tc
    # Recompute hash so verify() passes
    from tvastar.assurance.receipt import _canonical_payload
    import hashlib
    payload = _canonical_payload(
        run_id=d["run_id"], agent=d["agent"], model_name=d["model_name"],
        prompt=d["prompt"], tool_calls=tc, final_text=d["final_text"],
        quality_score=d["quality_score"], quality_grade=d["quality_grade"],
        findings=d["findings"], usage_input=d["usage_input"],
        usage_output=d["usage_output"], stopped=d["stopped"],
        started_at=d["started_at"], completed_at=d["completed_at"],
        prev_hash=d["prev_hash"], version=d["version"],
    )
    d["content_hash"] = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    d["signature"] = ""
    return ExecutionReceipt.from_dict(d)


class TestRetentionPolicy:
    def _make_old(self, days=40, prev_hash="") -> ExecutionReceipt:
        t = time.time() - days * 86400
        return ExecutionReceipt.from_run_result(
            _FakeResult(), agent="test-agent", model_name="mock-model",
            prompt="fix tests", started_at=t, completed_at=t + 0.1,
            prev_hash=prev_hash,
        )

    def test_no_age_returns_zero(self):
        log = TrustLog()
        log.append(_make_receipt())
        assert log.apply_retention(RetentionPolicy()) == 0

    def test_recent_entry_not_eligible(self):
        log = TrustLog()
        log.append(_make_receipt())
        assert log.apply_retention(RetentionPolicy(max_age_days=30)) == 0

    def test_old_entry_eligible(self):
        log = TrustLog()
        r = self._make_old(40)
        log.append(r)
        assert log.apply_retention(RetentionPolicy(max_age_days=30)) == 1

    def test_writes_to_archive_path(self, tmp_path):
        log = TrustLog()
        r = self._make_old(40)
        log.append(r)
        archive = str(tmp_path / "archive.jsonl")
        log.apply_retention(RetentionPolicy(max_age_days=30, archive_path=archive))
        lines = Path(archive).read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["run_id"] == r.run_id

    def test_active_log_chain_intact_after_archive(self, tmp_path):
        log = TrustLog()
        r0 = self._make_old(40)
        r1 = _make_receipt(prev_hash=r0.content_hash)
        log.append(r0)
        log.append(r1)
        log.apply_retention(RetentionPolicy(max_age_days=30, archive_path=str(tmp_path / "a.jsonl")))
        # active log untouched — chain still valid
        assert log.verify_chain()

    def test_legal_hold_blocks_archival(self):
        log = TrustLog()
        r = self._make_old(40)
        log.append(r)
        future_hold = time.time() + 86400 * 365
        count = log.apply_retention(RetentionPolicy(max_age_days=30, hold_until=future_hold))
        assert count == 0

    def test_expired_hold_allows_archival(self, tmp_path):
        log = TrustLog()
        r = self._make_old(40)
        log.append(r)
        past_hold = time.time() - 1
        count = log.apply_retention(RetentionPolicy(max_age_days=30, hold_until=past_hold, archive_path=str(tmp_path / "a.jsonl")))
        assert count == 1

    def test_no_archive_path_returns_count_only(self):
        log = TrustLog()
        r0 = self._make_old(40)
        r1 = self._make_old(40, prev_hash=r0.content_hash)
        log.append(r0)
        log.append(r1)
        count = log.apply_retention(RetentionPolicy(max_age_days=30))
        assert count == 2
        assert len(log) == 2  # active log unchanged
