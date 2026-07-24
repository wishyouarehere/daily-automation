"""
Tests for exec_events constants.
Verifies event type identifiers, domain constants, and ALL_TYPES coverage.
"""
import re

from exec_events import (
    ALL_TYPES,
    BRIEF_SENT,
    DAILY_REVIEW_SENT,
    DECISION_PENDING,
    DOMAIN_ORG,
    DOMAIN_OPS,
    EVENING_SYNC_DONE,
    EVENING_SYNC_ERROR,
    INDEX_REGEN_COMPLETE,
    WEEKLY_RETRO_SAVED,
)
from exec_emitter import VALID_DOMAINS

_EVENT_TYPE_PATTERN = re.compile(r'^daily_automation\.[a-z_]+\.[a-z_]+$')

EXPECTED_TYPES = {
    BRIEF_SENT,
    DAILY_REVIEW_SENT,
    WEEKLY_RETRO_SAVED,
    INDEX_REGEN_COMPLETE,
    EVENING_SYNC_ERROR,
    EVENING_SYNC_DONE,
    DECISION_PENDING,
}


# ── ALL_TYPES coverage ───────────────────────────────────────────────

def test_all_types_contains_all_expected():
    assert EXPECTED_TYPES == ALL_TYPES


def test_all_types_non_empty():
    assert len(ALL_TYPES) > 0


# ── Event type string format ─────────────────────────────────────────

def test_event_type_format():
    for t in ALL_TYPES:
        assert _EVENT_TYPE_PATTERN.match(t), f"Bad event type format: {t!r}"


def test_event_type_values():
    assert BRIEF_SENT == "daily_automation.morning_brief.sent"
    assert DAILY_REVIEW_SENT == "daily_automation.daily_review.sent"
    assert WEEKLY_RETRO_SAVED == "daily_automation.weekly_retro.draft_saved"
    assert INDEX_REGEN_COMPLETE == "daily_automation.index_regen.complete"
    assert EVENING_SYNC_ERROR == "daily_automation.evening_sync.error"
    assert EVENING_SYNC_DONE == "daily_automation.evening_sync.done"
    assert DECISION_PENDING == "daily_automation.decision.pending"


# ── Domain constants ─────────────────────────────────────────────────

def test_domain_ops_valid():
    assert DOMAIN_OPS in VALID_DOMAINS


def test_domain_org_valid():
    assert DOMAIN_ORG in VALID_DOMAINS


def test_domain_ops_value():
    assert DOMAIN_OPS == "ops"


def test_domain_org_value():
    assert DOMAIN_ORG == "org"


# ── No numeric levels exported ───────────────────────────────────────

def test_no_numeric_level_exports():
    import exec_events
    for name in ("L0", "L1", "L2", "L3"):
        assert not hasattr(exec_events, name), f"Numeric level {name} must not be exported"


def test_no_exec_event_class():
    import exec_events
    assert not hasattr(exec_events, "ExecEvent"), "ExecEvent class must not be exported"


# ── exec_gate_suppresses unit tests ─────────────────────────────────

from exec_events import exec_gate_suppresses


def test_gate_absent_no_suppress(monkeypatch):
    """EXEC_GATE 미설정 → 게이트 비활성 → direct send 진행."""
    monkeypatch.delenv("EXEC_GATE", raising=False)
    assert exec_gate_suppresses(emitted=True) is False


def test_gate_off_no_suppress(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "off")
    assert exec_gate_suppresses(emitted=True) is False


def test_gate_invalid_no_suppress(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "yes")
    assert exec_gate_suppresses(emitted=True) is False


def test_gate_empty_no_suppress(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "")
    assert exec_gate_suppresses(emitted=True) is False


def test_gate_on_suppresses(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "on")
    assert exec_gate_suppresses(emitted=True) is True


def test_gate_on_uppercase_suppresses(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "ON")
    assert exec_gate_suppresses(emitted=True) is True


def test_gate_on_mixed_case_suppresses(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "On")
    assert exec_gate_suppresses(emitted=True) is True


def test_gate_on_whitespace_suppresses(monkeypatch):
    monkeypatch.setenv("EXEC_GATE", "  on  ")
    assert exec_gate_suppresses(emitted=True) is True


def test_gate_emitter_failure_no_suppress(monkeypatch):
    """에미터 실패(emitted=False)면 게이트=on이어도 fail-open → direct send 진행."""
    monkeypatch.setenv("EXEC_GATE", "on")
    assert exec_gate_suppresses(emitted=False) is False


# ── Sender path behavior tests (emit→direct 순서 + 게이트 제어) ──────

def _run_sender_pattern(emitter_returns, gate, monkeypatch):
    """morning_brief/daily_review 등 proactive sender 패턴 시뮬레이션.
    emit_event와 send_telegram을 fake로 대체해 호출 순서·횟수를 검증한다.
    Returns (emit_count, send_count, call_order)."""
    if gate is None:
        monkeypatch.delenv("EXEC_GATE", raising=False)
    else:
        monkeypatch.setenv("EXEC_GATE", gate)

    emit_count = [0]
    send_count = [0]
    call_order = []

    def fake_emit(**kwargs):
        emit_count[0] += 1
        call_order.append("emit")
        return emitter_returns

    def fake_send(text):
        send_count[0] += 1
        call_order.append("send")

    # Mirror exact sender pattern: emit → gate check → conditional send
    _emitted = False
    try:
        _emitted = fake_emit(
            source="morning_brief",
            domain="ops",
            event_type="daily_automation.morning_brief.sent",
            title="아침 브리핑 전송",
        )
    except Exception:
        _emitted = False
    if not exec_gate_suppresses(_emitted):
        fake_send("브리핑 메시지")

    return emit_count[0], send_count[0], call_order


def test_sender_absent_gate_emit_then_direct(monkeypatch):
    """EXEC_GATE 없음 → emit 후 direct send."""
    emits, sends, order = _run_sender_pattern(True, None, monkeypatch)
    assert emits == 1
    assert sends == 1


def test_sender_off_gate_emit_then_direct(monkeypatch):
    """EXEC_GATE=off → emit 후 direct send."""
    emits, sends, order = _run_sender_pattern(True, "off", monkeypatch)
    assert emits == 1
    assert sends == 1


def test_sender_invalid_gate_emit_then_direct(monkeypatch):
    """EXEC_GATE=invalid → emit 후 direct send."""
    emits, sends, order = _run_sender_pattern(True, "garbage", monkeypatch)
    assert emits == 1
    assert sends == 1


def test_sender_on_gate_emit_no_direct(monkeypatch):
    """EXEC_GATE=on → emit하되 direct send 억제."""
    emits, sends, order = _run_sender_pattern(True, "on", monkeypatch)
    assert emits == 1
    assert sends == 0


def test_sender_emitter_failure_direct(monkeypatch):
    """에미터 실패(False 반환) + EXEC_GATE=on → fail-open → direct send 보존."""
    emits, sends, order = _run_sender_pattern(False, "on", monkeypatch)
    assert emits == 1
    assert sends == 1


def test_sender_emit_before_send_order(monkeypatch):
    """emit이 반드시 send보다 먼저 호출되어야 한다."""
    monkeypatch.delenv("EXEC_GATE", raising=False)
    _, _, order = _run_sender_pattern(True, None, monkeypatch)
    assert order == ["emit", "send"]
