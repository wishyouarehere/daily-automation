"""
Tests for canonical exec_emitter (emit_event API) and exec_events constants.
Zero network calls. Uses tmp_path for spool isolation via home= parameter.
"""
import hashlib
import json
from pathlib import Path

import pytest

import exec_emitter
from exec_emitter import emit_event, VALID_DOMAINS, VALID_DECISION_LEVELS

CANONICAL_SHA = "ce0104bdb977ac247624d10d52389c3042a7ecb4fac51f1b6a22286ebecd5f97"

# ── SHA parity ───────────────────────────────────────────────────────

def test_emitter_sha_matches_canonical():
    src = Path(__file__).with_name("exec_emitter.py").read_bytes()
    actual = hashlib.sha256(src).hexdigest()
    assert actual == CANONICAL_SHA, f"SHA mismatch: {actual}"


# ── Basic emit → pending file created ───────────────────────────────

def _pending_files(home: Path):
    return list((home / "spool" / "pending").glob("*.json"))


def test_emit_event_creates_pending_file(tmp_path):
    result = emit_event(
        source="morning_brief",
        domain="ops",
        event_type="daily_automation.morning_brief.sent",
        title="아침 브리핑 전송",
        decision_level="L1",
        home=str(tmp_path),
    )
    assert result is True
    files = _pending_files(tmp_path)
    assert len(files) == 1
    row = json.loads(files[0].read_text())
    assert row["source"] == "morning_brief"
    assert row["event_type"] == "daily_automation.morning_brief.sent"
    assert row["domain"] == "ops"
    assert row["decision_level"] == "L1"
    assert row["schema_version"] == 1


def test_emit_event_dict_style(tmp_path):
    result = emit_event(
        {
            "source": "daily_review",
            "domain": "ops",
            "event_type": "daily_automation.daily_review.sent",
            "title": "일일 리뷰 전송",
        },
        home=str(tmp_path),
    )
    assert result is True
    files = _pending_files(tmp_path)
    assert len(files) == 1


def test_emit_event_multiple_accumulate(tmp_path):
    for i in range(3):
        emit_event(
            source="evening_sync",
            domain="ops",
            event_type="daily_automation.evening_sync.done",
            title="저녁 동기화 완료",
            metadata={"task_count": i},
            home=str(tmp_path),
        )
    assert len(_pending_files(tmp_path)) == 3


# ── Atomic write: tmp dir used then removed ──────────────────────────

def test_atomic_write_no_leftover_tmp(tmp_path):
    emit_event(
        source="regenerate_index",
        domain="ops",
        event_type="daily_automation.index_regen.complete",
        title="_INDEX 재생성 완료",
        home=str(tmp_path),
    )
    tmp_dir = tmp_path / "spool" / "tmp"
    leftover = list(tmp_dir.glob("*.tmp"))
    assert leftover == []


# ── Fail-open behavior ───────────────────────────────────────────────

def test_emit_event_fail_open_returns_false_on_missing_required(tmp_path):
    result = emit_event(
        source="morning_brief",
        domain="ops",
        # event_type missing
        title="브리핑 전송",
        home=str(tmp_path),
    )
    assert result is False
    assert _pending_files(tmp_path) == []


def test_emit_event_fail_open_bad_domain(tmp_path):
    result = emit_event(
        source="morning_brief",
        domain="invalid_domain",
        event_type="daily_automation.morning_brief.sent",
        title="브리핑 전송",
        home=str(tmp_path),
    )
    assert result is False


def test_emit_event_no_args_fail_open(tmp_path):
    result = emit_event(home=str(tmp_path))
    assert result is False


def test_emit_event_non_dict_positional_fail_open(tmp_path):
    result = emit_event("not-a-dict", home=str(tmp_path))
    assert result is False


def test_emit_event_dict_and_kwargs_conflict_fail_open(tmp_path):
    result = emit_event(
        {"source": "morning_brief"},
        domain="ops",
        home=str(tmp_path),
    )
    assert result is False


def test_emit_event_fail_open_false_raises(tmp_path):
    with pytest.raises(ValueError):
        emit_event(
            source="morning_brief",
            domain="ops",
            # event_type missing
            title="브리핑",
            fail_open=False,
            home=str(tmp_path),
        )


def test_emit_event_never_raises_with_fail_open(tmp_path):
    bad_home = tmp_path / "no" / "such" / "path" / ("x" * 200)
    result = emit_event(
        source="morning_brief",
        domain="ops",
        event_type="daily_automation.morning_brief.sent",
        title="브리핑",
        home=str(bad_home),
    )
    assert isinstance(result, bool)


# ── Capacity gate ────────────────────────────────────────────────────

def test_capacity_gate_drops_l0_when_spool_full(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_SPOOL_MAX_PENDING", "0")
    # Pre-create one file to push count over limit
    pending = tmp_path / "spool" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "fake.json").write_text("{}")

    result = emit_event(
        source="regenerate_index",
        domain="ops",
        event_type="daily_automation.index_regen.complete",
        title="_INDEX 재생성 완료",
        decision_level="L0",
        home=str(tmp_path),
    )
    assert result is False


def test_capacity_gate_passes_l1_under_hard_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_SPOOL_MAX_PENDING", "0")
    monkeypatch.setenv("EXEC_SPOOL_HARD_CAP", "9999")
    # L1 not dropped until hard_cap reached
    result = emit_event(
        source="morning_brief",
        domain="ops",
        event_type="daily_automation.morning_brief.sent",
        title="브리핑",
        decision_level="L1",
        home=str(tmp_path),
    )
    assert result is True


# ── Privacy: no forbidden keys in pending file ───────────────────────

FORBIDDEN_KEYS = {"token", "chat_id", "prompt", "traceback", "secret"}


def _all_keys(d: dict, acc=None):
    if acc is None:
        acc = set()
    for k, v in d.items():
        acc.add(k.lower())
        if isinstance(v, dict):
            _all_keys(v, acc)
    return acc


def test_no_forbidden_keys_in_pending_file(tmp_path):
    emit_event(
        source="morning_brief",
        domain="ops",
        event_type="daily_automation.morning_brief.sent",
        title="아침 브리핑 전송",
        decision_level="L1",
        metadata={"mode": "standard", "decision_count": 2},
        home=str(tmp_path),
    )
    files = _pending_files(tmp_path)
    row = json.loads(files[0].read_text())
    keys = _all_keys(row)
    assert not keys & FORBIDDEN_KEYS, f"Forbidden key in spool: {keys & FORBIDDEN_KEYS}"


# ── Scrubbing ────────────────────────────────────────────────────────

def test_token_in_title_is_masked(tmp_path):
    emit_event(
        source="morning_brief",
        domain="ops",
        event_type="daily_automation.morning_brief.sent",
        title="token=sk-abc123456789012345678901234567890",
        home=str(tmp_path),
    )
    files = _pending_files(tmp_path)
    row = json.loads(files[0].read_text())
    assert "sk-" not in row["title"]
    assert "[MASKED" in row["title"]


# ── Metadata: list values silently dropped ───────────────────────────

def test_list_metadata_value_dropped(tmp_path):
    emit_event(
        source="weekly_retro",
        domain="ops",
        event_type="daily_automation.weekly_retro.draft_saved",
        title="주간 회고 초안 저장",
        decision_level="L1",
        metadata={"week_n": 30, "patched_sections": ["a", "b"]},
        home=str(tmp_path),
    )
    files = _pending_files(tmp_path)
    row = json.loads(files[0].read_text())
    assert row["metadata"].get("week_n") == 30
    assert "patched_sections" not in row["metadata"]


# ── Valid domain / decision_level sets ──────────────────────────────

def test_valid_domains_set():
    assert "ops" in VALID_DOMAINS
    assert "org" in VALID_DOMAINS


def test_valid_decision_levels_set():
    for level in ("L0", "L1", "L2", "L3"):
        assert level in VALID_DECISION_LEVELS


# ── Emitter version ──────────────────────────────────────────────────

def test_emitter_version():
    assert exec_emitter.EMITTER_VERSION == "0.2.0"
