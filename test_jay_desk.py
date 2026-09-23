import json
import os
from datetime import datetime, timezone


for _key in (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "OPENWEATHER_API_KEY",
    "TODOIST_API_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_CALENDAR_ID",
    "ANTHROPIC_API_KEY",
):
    os.environ.setdefault(_key, "test")

import morning_brief as mb  # noqa: E402


def test_partial_error_is_log_only(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(mb, "send_telegram", sent.append)

    mb.send_error("캘린더 조회", RuntimeError("temporary"))

    assert sent == []
    assert "ERROR [캘린더 조회]" in capsys.readouterr().err


def test_decision_snapshot_and_fallback_render(tmp_path, monkeypatch):
    snapshot = tmp_path / "decisions.json"
    snapshot.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {"items": ["가격 정책", "채용 순서"], "count": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mb.os.path, "expanduser", lambda _path: str(snapshot))

    data = mb.get_pending_decisions_snapshot()

    assert data[0] == ["가격 정책", "채용 순서"]
    line = mb.get_pending_decisions_line(data)
    assert "대기 중 결정 2건" in line
    assert "가격 정책" in mb.render_block2("standard", [], pending_line=line)


def test_decision_snapshot_reads_current_bus_contract(tmp_path, monkeypatch):
    snapshot = tmp_path / "decisions.json"
    snapshot.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "open_decisions": [
                        {"id": "d1", "title": "대표 신규지시 입력 규칙"},
                        {"id": "d2", "title": "사업본부장 예산 권한"},
                    ],
                    "waiting_actions": [{"id": "a1", "title": "팀장 초안"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mb.os.path, "expanduser", lambda _path: str(snapshot))

    shown, count, _age = mb.get_pending_decisions_snapshot()

    assert shown == ["대표 신규지시 입력 규칙", "사업본부장 예산 권한"]
    assert count == 2


def test_ranked_calls_do_not_repeat_pending_line():
    rendered = mb.render_block2(
        "standard",
        [{"headline": "가격 정책", "points": ["오늘 확정하는 걸 권해드려요"], "level": "네 결정"}],
        pending_line="📋 대기 중 결정 1건: 가격 정책",
    )

    assert rendered.count("가격 정책") == 1
    assert "오늘 볼 것" in rendered


def test_main_build_failure_is_nonzero_and_silent(monkeypatch):
    monkeypatch.setattr(mb, "build_message", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    sent = []
    monkeypatch.setattr(mb, "send_telegram", sent.append)

    assert mb.main() == 1
    assert sent == []
