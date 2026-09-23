"""치프봇 아침·저녁 한 통 + 「다시, 여기」 문장 뱅크 테스트 (2026-09-23 개편).

실행: ./venv/bin/python test_jay_desk.py   (pytest 있으면 pytest도 가능)
발송·볼트 쓰기 없이 임시 디렉터리와 가짜 소스로만 돈다.
"""
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_CLIENT_ID",
           "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID"):
    os.environ.setdefault(_k, "test")
os.environ["DRY_RUN"] = "1"

import again_bank  # noqa: E402
import daily_review  # noqa: E402
import desk_sources  # noqa: E402
import jay_desk  # noqa: E402
import morning_brief  # noqa: E402

BANK_SAMPLE = """---
title: 다시, 여기
updated: 2026-09-20
---

# 다시, 여기

## 스토아

1. 방해물이 곧 길이다.
2. 나는 언제든 내 안으로 물러날 수 있다.

## 아침 1분

(아직 없음 — 필사하다 걸리는 최애 문장을 여기로 옮긴다)
"""


class _Env:
    """임시 볼트 + 임시 이력 + 가짜 캘린더/LLM/소스."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        bank = self.dir / "다시-여기.md"
        bank.write_text(BANK_SAMPLE, encoding="utf-8")
        self.saved = (again_bank.BANK, jay_desk.USED, jay_desk.STATE,
                      jay_desk.calendar_lines, jay_desk.ask_json, jay_desk.condition_line,
                      desk_sources.ai_sessions, desk_sources.meetings,
                      desk_sources.night_chief_candidates, desk_sources.decision_reviews,
                      desk_sources.workflowy_daily, desk_sources.underline_candidates)
        again_bank.BANK = bank
        jay_desk.STATE = self.dir
        jay_desk.USED = self.dir / "used.json"
        jay_desk.calendar_lines = lambda d: ["11:00  데일리 스크럼"]
        jay_desk.condition_line = lambda: ""
        desk_sources.ai_sessions = lambda s, u, limit=12: []
        desk_sources.meetings = lambda days, total_cap=12000: ""
        desk_sources.night_chief_candidates = lambda d: ""
        desk_sources.decision_reviews = lambda d: []
        desk_sources.workflowy_daily = lambda d: ""
        desk_sources.underline_candidates = lambda ex: [
            {"id": "U1", "text": "나는 오늘 을 산다.", "captured": "2026-09-18"}]
        self.answer = {}
        jay_desk.ask_json = lambda prompt, job: self.answer
        return self

    def __exit__(self, *a):
        (again_bank.BANK, jay_desk.USED, jay_desk.STATE,
         jay_desk.calendar_lines, jay_desk.ask_json, jay_desk.condition_line,
         desk_sources.ai_sessions, desk_sources.meetings,
         desk_sources.night_chief_candidates, desk_sources.decision_reviews,
         desk_sources.workflowy_daily, desk_sources.underline_candidates) = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)


def test_bank_add_remove_roundtrip():
    with _Env():
        r = again_bank.add("「멈추고, 보고, 간다.」", "아침 1분")
        assert r == {"status": "added", "section": "아침 1분", "text": "멈추고, 보고, 간다."}
        assert again_bank.add("멈추고, 보고, 간다.")["status"] == "exists"
        text = again_bank.BANK.read_text(encoding="utf-8")
        assert "(아직 없음" not in text and "1. 멈추고, 보고, 간다." in text
        assert "updated: 2026-09-20" not in text
        assert again_bank.add("새 문장.")["section"] == "모음"
        assert again_bank.remove("없는 문장")["status"] == "not_found"
        assert again_bank.remove("다")["status"] == "ambiguous"
        assert again_bank.remove("방해물")["status"] == "removed"
        assert [l["text"] for l in again_bank.all_lines()] == [
            "나는 언제든 내 안으로 물러날 수 있다.", "멈추고, 보고, 간다.", "새 문장."]


def test_html_renders_bank_without_raw_injection():
    with _Env():
        again_bank.add("<b>태그</b>도 문장이다.", "스토아")
        html = again_bank.render_html()
        assert '"title": "스토아"' in html and "/*DATA*/" not in html
        assert "innerHTML" not in html  # 문장은 textContent로만 들어간다


def test_morning_without_llm_still_sends_quote_and_schedule():
    with _Env():
        msg, meta = morning_brief.build(date(2026, 9, 23))
        assert "11:00  데일리 스크럼" in msg
        assert "<i>" in msg and "오늘 짚을 것" not in msg
        assert meta["llm_ok"] is False


def test_focus_needs_source_and_weekend_drops_it():
    with _Env() as env:
        env.answer = {"quote_id": "스토아#1", "question": "어떤 태도로 앉고 싶어요?",
                      "focus": {"headline": "배포 범위", "line": "오늘 정해요", "source": ""}}
        msg, _ = morning_brief.build(date(2026, 9, 23))
        assert "오늘 짚을 것" not in msg  # 출처 없는 판단은 버린다
        assert "방해물이 곧 길이다." in msg and "어떤 태도로" in msg
        env.answer["focus"]["source"] = "Slack #tech-dev 9/22"
        msg, _ = morning_brief.build(date(2026, 9, 24))
        assert "근거: Slack #tech-dev 9/22" in msg
        msg, meta = morning_brief.build(date(2026, 9, 27))
        assert meta["weekend"] and "오늘 짚을 것" not in msg and "<b>오늘</b>" not in msg


def test_quote_not_reused_within_two_weeks_or_same_day():
    with _Env() as env:
        jay_desk.USED.write_text("[]")
        orig = jay_desk.mark_used
        os.environ.pop("DRY_RUN")
        try:
            env.answer = {"quote_id": "스토아#1", "question": "q"}
            morning_brief.build(date.today())
            cands = jay_desk.quote_candidates(exclude_texts=jay_desk.used_today())
            assert all(c["text"] != "방해물이 곧 길이다." for c in cands)
            msg, _ = daily_review.build(date(2026, 9, 27))
            assert "방해물이 곧 길이다." not in msg
        finally:
            os.environ["DRY_RUN"] = "1"
            jay_desk.mark_used = orig


def test_underline_candidate_keeps_letters():
    with _Env() as env:
        env.answer = {"quote_id": "스토아#1", "question": "q",
                      "underline_id": "U1", "underline_text": "나는 오늘을 산다."}
        _, meta = daily_review.build(date(2026, 9, 23))
        assert meta["candidate"]["text"] == "나는 오늘을 산다."   # 띄어쓰기만 고침
        env.answer["underline_text"] = "나는 내일을 산다."          # 글자 바뀜 → 원문 유지
        _, meta = daily_review.build(date(2026, 9, 24))
        assert meta["candidate"]["text"] == "나는 오늘 을 산다."
        env.answer["underline_id"] = None
        _, meta = daily_review.build(date(2026, 9, 25))
        assert meta["candidate"] is None
        msg = daily_review.candidate_message({"id": "U1", "text": "a<b", "captured": "2026-09-18"})
        assert "9/18에 찍은 페이지" in msg and "a&lt;b" in msg and "❤️" in msg


def test_html_escaping_of_model_output():
    assert jay_desk.clean("**<script>** #채널") == "&lt;script&gt; #채널"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
