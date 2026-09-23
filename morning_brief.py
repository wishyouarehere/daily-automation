"""치프봇 아침 한 통 — 매일 06:30 KST (회사맥 cron, 2026-09-23 개편).

마음을 세우는 메시지다. 구성:
  「다시, 여기」 문장 하나(원문 그대로) → 오늘 일정(사실) → 오늘 짚을 것(출처 있을 때만 1건) → 오늘의 질문
주말은 문장과 질문만.

재료: 캘린더 · 볼트 Daily(WorkFlowy) · 회의록 · 야간참모 결정 후보(Slack·Notion 반영)
     · 결정 노트 리뷰 도래 · 코덱스·클로드 세션 제목. 전부 desk_sources.

직접 실행: python morning_brief.py
드라이런(발송·이력 기록 안 함): DRY_RUN=1 python morning_brief.py
요일 강제: FORCE_DATE=2026-09-27 DRY_RUN=1 python morning_brief.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import desk_sources as S
import jay_desk as J


def _today() -> date:
    forced = os.getenv("FORCE_DATE")
    return date.fromisoformat(forced) if forced else J.now().date()


def _prompt(today: date, weekend: bool, cands: list[dict], material: dict) -> str:
    head = f"""너는 Jay(장홍석, 다니엘프로젝트 부대표·CPO)의 아침 파트너다.
오늘은 {today.isoformat()} ({J.WEEKDAY_KR[today.weekday()]}요일).
아침 메시지 한 통의 재료를 고른다. 이 메시지의 목적은 Jay가 하루를 시작하며 마음을 세우는 것이다.
회사 일은 배경으로 얇게 깔고, 판단을 밀어붙이지 않는다.

{J.TONE}

출력은 JSON 객체 하나만. 설명·코드펜스 금지:
{{"quote_id": "...", "focus": {{"headline": "...", "line": "...", "source": "..."}} 또는 null, "question": "..."}}

quote_id: 아래 [문장 후보] 중 오늘 Jay에게 가장 맞는 문장의 id 하나. 오늘 일정·어제 흐름과 닿는 문장을 고른다.
  딱 맞는 게 없으면 담백하게 하루를 여는 문장을 고른다.
question: 오늘 하루를 시작하며 스스로에게 던질 질문 하나(~50자). 할 일 목록이 아니라 태도·관점·마음에 대한 질문.
  가능하면 오늘의 구체적 일정이나 고른 문장과 연결한다. 예: "15시 자리에 어떤 태도로 앉고 싶어요?"
"""
    if weekend:
        return head + f"""focus: 주말이라 반드시 null.

[문장 후보]
{J.quotes_for_prompt(cands)}

[오늘 일정]
{material['calendar'] or '없음'}
"""
    return head + f"""focus: 오늘 짚을 것 최대 1건. 아래 재료에 명시된 근거가 있을 때만. 없거나 애매하면 null.
  - 오늘 날짜에 걸린 결정·마감·회의 준비처럼 오늘 행동이 달라지는 것만. 진행 보고·FYI는 버린다.
  - headline: 무엇인지 명사구(~20자).
  - line: 사실 한 줄 + 필요하면 부드러운 제안 한 마디(~60자). 짐작·가능성 서술 금지.
  - source: 근거 출처를 구체적으로(예: "9/22 리더 주간 회의록", "Slack #tech-dev 9/22", "결정 노트 8/27").
  - [야간참모 결정 후보]는 Slack·Notion을 읽고 새벽에 만든 후보다. 다른 재료와 맞으면 쓰고, 근거가 약하면 버린다.

[문장 후보]
{J.quotes_for_prompt(cands)}

[오늘 일정]
{material['calendar'] or '없음'}

[야간참모 결정 후보]
{material['night'] or '없음'}

[결정 노트 리뷰 도래]
{material['reviews'] or '없음'}

[어제·오늘 WorkFlowy 데일리]
{material['daily'] or '없음'}

[최근 회의록 (2일)]
{material['meetings'] or '없음'}

[어제 Jay가 AI와 한 작업 (코덱스·클로드 세션 제목과 첫 요청)]
{material['sessions'] or '없음'}
"""


def build(today: date) -> tuple[str, dict]:
    weekend = J.is_weekend(today)
    cal = J.calendar_lines(today)
    material = {"calendar": "\n".join(cal)}
    if not weekend:
        start = datetime(today.year, today.month, today.day, tzinfo=J.KST)
        material.update({
            "night": S.night_chief_candidates(today),
            "reviews": "\n".join(S.decision_reviews(today)),
            "daily": "\n\n".join(filter(None, [S.workflowy_daily(today - timedelta(days=1)),
                                               S.workflowy_daily(today)])),
            "meetings": S.meetings(days=2),
            "sessions": "\n".join(S.ai_sessions(start - timedelta(days=1), start)),
        })

    cands = J.quote_candidates()
    data = J.ask_json(_prompt(today, weekend, cands, material), job="jay_desk_morning")
    quote = J.pick_quote(cands, data.get("quote_id"))
    focus = data.get("focus") if isinstance(data.get("focus"), dict) and not weekend else None
    question = J.clean(data.get("question"))

    parts = [f"🌅 <b>{J.date_label(today)}</b>"]
    cond = J.condition_line()
    if cond and not weekend:
        parts[0] += f"\n{J.clean(cond)}"
    if quote:
        parts.append(J.render_quote(quote))
    if not weekend:
        sched = "\n".join(f"  {J.clean(l)}" for l in cal) if cal else "  일정 없음"
        parts.append(f"<b>오늘</b>\n{sched}")
        if focus and focus.get("headline") and focus.get("source"):
            body = f"<b>오늘 짚을 것</b>\n{J.clean(focus['headline'])}"
            if focus.get("line"):
                body += f"\n{J.clean(focus['line'])}"
            body += f"\n<i>근거: {J.clean(focus['source'])}</i>"
            parts.append(body)
    if question:
        parts.append(f"<b>오늘의 질문</b>\n{question}")

    meta = {"weekend": weekend, "focus": bool(focus), "llm_ok": bool(data),
            "quote": quote["text"] if quote else "", "section": quote["section"] if quote else ""}
    if quote:
        J.mark_used(quote, "morning")
    return "\n\n".join(parts), meta


def main() -> int:
    today = _today()
    try:
        msg, meta = build(today)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR 아침 메시지 조립 실패: {e}", file=sys.stderr)
        return 1
    mid = J.send_telegram(msg)
    if meta.get("quote"):
        J.record_message(mid, "quote", meta["quote"], meta.get("section", ""))
    J.emit_ledger("jay_desk.morning.sent", "아침 한 통 발송", meta)
    print(f"✅ 아침 한 통 발송 {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
