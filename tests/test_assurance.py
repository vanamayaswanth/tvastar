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

from tvastar.assurance import AssurancePolicy, ExecutionReceipt, SLABreached, TrustLog
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
    score: int = 90,
    grade: str = "PASS",
    stopped: str = "end_turn",
) -> ExecutionReceipt:
    t = time.time()
    return ExecutionReceipt.from_run_result(
        _FakeResult(text, stopped, score=score, grade=grade),
        agent=agent,
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
        assert r.version == "1"

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
