"""Tests for load_trajectories in the silent-failure benchmark module."""

import json

import pytest

from tvastar.bench.silent_failure import RawTrajectory, load_trajectories


def _valid_entry(**overrides):
    """Create a minimal valid trajectory entry."""
    entry = {
        "id": "test-001",
        "model": "GPT-5.2",
        "domain": "airline",
        "reward": 0,
        "messages": [
            {"role": "user", "content": "Change my flight"},
            {"role": "assistant", "content": "Done."},
        ],
    }
    entry.update(overrides)
    return entry


# ── FileNotFoundError ─────────────────────────────────────────────────────────


def test_raises_file_not_found_for_missing_path(tmp_path):
    missing = tmp_path / "does_not_exist.jsonl"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_trajectories(missing)


# ── JSONL format ──────────────────────────────────────────────────────────────


def test_loads_jsonl_file(tmp_path):
    entries = [_valid_entry(id=f"t{i}") for i in range(3)]
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    result = load_trajectories(path)
    assert len(result) == 3
    assert all(isinstance(t, RawTrajectory) for t in result)
    assert result[0].id == "t0"
    assert result[2].model == "GPT-5.2"


def test_jsonl_skips_malformed_lines(tmp_path, caplog):
    lines = [
        json.dumps(_valid_entry(id="good-1")),
        "this is not json {{{",
        json.dumps(_valid_entry(id="good-2")),
    ]
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = load_trajectories(path)

    assert len(result) == 2
    assert result[0].id == "good-1"
    assert result[1].id == "good-2"
    assert "Malformed JSON" in caplog.text
    assert "line 2" in caplog.text


def test_jsonl_skips_blank_lines(tmp_path):
    lines = [
        json.dumps(_valid_entry(id="t1")),
        "",
        "   ",
        json.dumps(_valid_entry(id="t2")),
    ]
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    result = load_trajectories(path)
    assert len(result) == 2


# ── Single JSON array format ──────────────────────────────────────────────────


def test_loads_json_array(tmp_path):
    entries = [_valid_entry(id=f"arr-{i}") for i in range(4)]
    path = tmp_path / "data.json"
    path.write_text(json.dumps(entries), encoding="utf-8")

    result = load_trajectories(path)
    assert len(result) == 4
    assert result[0].id == "arr-0"


def test_json_array_skips_missing_required_fields(tmp_path, caplog):
    entries = [
        _valid_entry(id="ok"),
        {"id": "bad-no-messages", "model": "X", "reward": 1},
        {"id": "bad-no-model", "messages": [], "reward": 0},
        _valid_entry(id="ok2"),
    ]
    path = tmp_path / "data.json"
    path.write_text(json.dumps(entries), encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = load_trajectories(path)

    assert len(result) == 2
    assert result[0].id == "ok"
    assert result[1].id == "ok2"
    assert "missing required fields" in caplog.text


# ── Directory of JSON files ───────────────────────────────────────────────────


def test_loads_directory_of_json_files(tmp_path):
    for name in ("a.json", "b.jsonl"):
        entries = [_valid_entry(id=f"{name}-{i}") for i in range(2)]
        content = (
            json.dumps(entries)
            if name.endswith(".json")
            else "\n".join(json.dumps(e) for e in entries)
        )
        (tmp_path / name).write_text(content, encoding="utf-8")

    result = load_trajectories(tmp_path)
    assert len(result) == 4


def test_directory_ignores_non_json_files(tmp_path):
    (tmp_path / "readme.txt").write_text("not data", encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps([_valid_entry()]), encoding="utf-8")

    result = load_trajectories(tmp_path)
    assert len(result) == 1


# ── Metadata preservation ─────────────────────────────────────────────────────


def test_preserves_metadata(tmp_path):
    entry = _valid_entry(id="meta-1", model="Claude-4", domain="retail", reward=1)
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(entry), encoding="utf-8")

    result = load_trajectories(path)
    assert result[0].id == "meta-1"
    assert result[0].model == "Claude-4"
    assert result[0].domain == "retail"
    assert result[0].reward == 1


def test_domain_defaults_to_unknown(tmp_path):
    entry = _valid_entry()
    del entry["domain"]
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(entry), encoding="utf-8")

    result = load_trajectories(path)
    assert result[0].domain == "unknown"


def test_id_auto_generated_when_missing(tmp_path):
    entry = _valid_entry()
    del entry["id"]
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(entry), encoding="utf-8")

    result = load_trajectories(path)
    assert result[0].id.startswith("auto-")
