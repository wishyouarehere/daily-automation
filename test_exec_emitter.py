"""
Tests for exec_emitter v0.2.0 and exec_events.
Zero network calls, zero external writes — uses tmp_path for spool.
"""
import json

import pytest

from exec_events import (
    ALL_TYPES,
    BRIEF_SENT,
    DAILY_REVIEW_SENT,
    DECISION_PENDING,
    EVENING_SYNC_DONE,
    EVENING_SYNC_ERROR,
    INDEX_REGEN_COMPLETE,
    WEEKLY_RETRO_SAVED,
    ExecEvent,
    L0,
    L1,
    L2,
)
import exec_emitter


def _evt(event_type=BRIEF_SENT, level=L1, payload=None):
    return ExecEvent(event_type=event_type, level=level, payload=payload or {})


# ── ExecEvent structure ─────────────────────────────────────────────

def test_event_to_dict():
    evt = _evt(payload={"mode": "standard"})
    d = evt.to_dict()
    assert d["event_type"] == BRIEF_SENT
    assert d["level"] == L1
    assert d["payload"] == {"mode": "standard"}
    assert "ts" in d


def test_event_to_json_valid():
    obj = json.loads(_evt().to_json())
    assert obj["event_type"] == BRIEF_SENT


def test_all_event_types_registered():
    for t in [
        BRIEF_SENT, DAILY_REVIEW_SENT, WEEKLY_RETRO_SAVED,
        INDEX_REGEN_COMPLETE, EVENING_SYNC_ERROR, EVENING_SYNC_DONE,
        DECISION_PENDING,
    ]:
        assert t in ALL_TYPES


# ── Emitter version fingerprint ─────────────────────────────────────

def test_version_is_0_2_0():
    assert exec_emitter.__version__ == "0.2.0"


def test_fingerprint_format():
    assert exec_emitter._FINGERPRINT == "exec_emitter/0.2.0"


# ── Gate off → spool ────────────────────────────────────────────────

def test_gate_off_spools_event(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "0")
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt(payload={"mode": "standard", "decision_count": 2}))

    spool = tmp_path / "events.jsonl"
    assert spool.exists()
    row = json.loads(spool.read_text())
    assert row["event_type"] == BRIEF_SENT
    assert row["level"] == L1
    assert row["_emitter"] == "exec_emitter/0.2.0"


def test_gate_default_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("EXEC_GATE", raising=False)
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt())
    assert (tmp_path / "events.jsonl").exists()


def test_spool_appends_multiple(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "0")
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt(BRIEF_SENT, L1))
    exec_emitter.emit(_evt(DAILY_REVIEW_SENT, L1))

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == BRIEF_SENT
    assert json.loads(lines[1])["event_type"] == DAILY_REVIEW_SENT


# ── Gate on → dispatch (placeholder also spools) ────────────────────

def test_gate_on_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "1")
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt(INDEX_REGEN_COMPLETE, L0))
    row = json.loads((tmp_path / "events.jsonl").read_text())
    assert row["event_type"] == INDEX_REGEN_COMPLETE


# ── Fail-open: emit never raises ────────────────────────────────────

def test_emit_never_raises_on_bad_spool(tmp_path, monkeypatch):
    bad = tmp_path / "events.jsonl"
    bad.mkdir()  # directory where a file is expected → write will fail
    monkeypatch.setenv("EXEC_GATE", "0")
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt())  # must not raise


# ── Privacy: no forbidden keys in spool ─────────────────────────────

FORBIDDEN_KEYS = {"token", "chat_id", "prompt", "traceback", "content", "secret"}


def _all_keys(d: dict, acc=None):
    if acc is None:
        acc = set()
    for k, v in d.items():
        acc.add(k.lower())
        if isinstance(v, dict):
            _all_keys(v, acc)
    return acc


def test_no_forbidden_keys_in_spool(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "0")
    monkeypatch.setenv("EXEC_SPOOL_DIR", str(tmp_path))

    exec_emitter.emit(_evt(payload={"mode": "standard", "decision_count": 1}))
    row = json.loads((tmp_path / "events.jsonl").read_text())
    keys = _all_keys(row)
    assert not keys & FORBIDDEN_KEYS, f"Forbidden key found: {keys & FORBIDDEN_KEYS}"


# ── Level semantics ─────────────────────────────────────────────────

def test_level_ordering():
    assert L0 < L1 < L2


def test_l2_only_for_decisions():
    evt = _evt(DECISION_PENDING, L2, payload={"count": 3})
    assert evt.level == L2
    assert evt.event_type == DECISION_PENDING
