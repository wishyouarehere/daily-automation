"""치프봇 저녁 한 통 — 매일 20:00 KST (회사맥 cron, 2026-09-23 개편).

하루를 스스로 돌아보게 하는 메시지다. 봇은 하루를 평가하지 않는다. 구성:
  오늘 있었던 일(사실 2~4줄) → 돌아볼 질문 하나 → 「다시, 여기」 문장 하나(아침과 다른 문장)
주말은 질문과 문장만.

재료: 캘린더 · 오늘 회의록 · 볼트 Daily(WorkFlowy) · 코덱스·클로드 세션 제목.
드라이런: DRY_RUN=1 python daily_review.py  (--dry-run도 같음)
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


def underlines_for_prompt(rows: list[dict]) -> str:
    return "\n".join(f"[{r['id']}] {r['text']}" for r in rows) or "없음"


def _prompt(today: date, weekend: bool, cands: list[dict], material: dict) -> str:
    head = f"""너는 Jay(장홍석, 다니엘프로젝트 부대표·CPO)의 저녁 파트너다.
오늘은 {today.isoformat()} ({J.WEEKDAY_KR[today.weekday()]}요일). 하루를 닫는 메시지 한 통의 재료를 만든다.
목적은 Jay가 스스로 하루를 돌아보게 하는 것이다. 봇이 하루를 평가·채점·조언하지 않는다.

{J.TONE}

출력은 JSON 객체 하나만. 설명·코드펜스 금지:
{{"facts": ["...", "..."], "question": "...", "quote_id": "...", "underline_id": "..." 또는 null, "underline_text": "..."}}

question: 오늘을 돌아보는 질문 하나(~60자). 감정·해석·배움·감사에 닿는 질문. 할 일·내일 계획을 묻지 않는다.
  가능하면 오늘 있었던 구체적 장면에 연결한다. 예: "브랜딩 미팅에서 가장 마음이 움직인 순간은 언제였어요?"
quote_id: [문장 후보] 중 오늘 하루를 닫기에 맞는 문장 id 하나.
underline_id: [책 밑줄 후보] 중 「다시, 여기」(마음·태도·감사·내면을 다잡는 문장 모음)에 둘 만한 것 하나의 id.
  업무 요령·정보·통계·줄거리 문장은 고르지 않는다. 마땅한 게 없으면 null.
underline_text: 고른 밑줄 원문에서 OCR 띄어쓰기·줄바꿈 오류만 고친 문장. 글자는 하나도 바꾸거나 빼지 않는다.
  밑줄이 문장 중간에서 잘려 있으면 원문 그대로 둔다.

[책 밑줄 후보]
{underlines_for_prompt(material.get('underlines') or [])}
"""
    if weekend:
        return head + f"""facts: 주말이라 반드시 빈 배열 [].

[문장 후보]
{J.quotes_for_prompt(cands)}
"""
    return head + f"""facts: 오늘 실제로 있었던 일 2~4줄(각 ~45자). 회의록의 결론·결정, 오늘 한 작업.
  - 재료에 결론이 적힌 것만. 일정 제목만 있고 내용이 없으면 "OO 미팅" 정도로만 쓴다.
  - 평가어("잘했다", "아쉽다") 금지. 재료가 빈약하면 1줄이어도 된다.

[문장 후보]
{J.quotes_for_prompt(cands)}

[오늘 일정]
{material['calendar'] or '없음'}

[오늘 회의록]
{material['meetings'] or '없음'}

[오늘 WorkFlowy 데일리]
{material['daily'] or '없음'}

[오늘 Jay가 AI와 한 작업 (코덱스·클로드 세션 제목과 첫 요청)]
{material['sessions'] or '없음'}
"""


def build(today: date) -> tuple[str, dict]:
    weekend = J.is_weekend(today)
    material = {"underlines": S.underline_candidates(J.offered_underlines())}
    if not weekend:
        start = datetime(today.year, today.month, today.day, tzinfo=J.KST)
        material |= {
            "calendar": "\n".join(J.calendar_lines(today)),
            "meetings": S.meetings(days=1),
            "daily": S.workflowy_daily(today),
            "sessions": "\n".join(S.ai_sessions(start, start + timedelta(days=1))),
        }
    cands = J.quote_candidates(exclude_texts=J.used_today())
    data = J.ask_json(_prompt(today, weekend, cands, material), job="jay_desk_evening")
    quote = J.pick_quote(cands, data.get("quote_id"))
    facts = [J.clean(f) for f in (data.get("facts") or []) if str(f).strip()][:4]
    question = J.clean(data.get("question"))

    parts = [f"🌙 <b>{J.date_label(today)} · 하루를 닫으며</b>"]
    if facts and not weekend:
        parts.append("<b>오늘 있었던 일</b>\n" + "\n".join(f"  · {f}" for f in facts))
    if question:
        parts.append(f"<b>돌아볼 질문</b>\n{question}")
    if quote:
        parts.append(J.render_quote(quote))
        J.mark_used(quote, "evening")
    meta = {"weekend": weekend, "facts": len(facts), "llm_ok": bool(data),
            "quote": quote, "candidate": _pick_underline(material.get("underlines") or [], data)}
    return "\n\n".join(parts), meta


def _pick_underline(rows: list[dict], data: dict) -> dict | None:
    """모델이 고른 밑줄. 띄어쓰기 교정본이 원문과 글자가 다르면 원문을 쓴다."""
    uid = data.get("underline_id")
    row = next((r for r in rows if r["id"] == uid), None) if uid else None
    if not row:
        return None
    fixed = str(data.get("underline_text") or "").strip()
    text = fixed if fixed and J.same_letters(fixed, row["text"]) else row["text"]
    return {"id": row["id"], "text": text, "captured": row.get("captured", "")}


def candidate_message(c: dict) -> str:
    when = ""
    if c.get("captured"):
        y, m, d = c["captured"].split("-")
        when = f" ({int(m)}/{int(d)}에 찍은 페이지)"
    return (f"📚 <b>책 밑줄에서</b>{when}\n<i>{J.clean(c['text'])}</i>\n\n"
            "「다시, 여기」에 둘까요? ❤️ 누르면 넣어요.")


def main() -> int:
    if "--dry-run" in sys.argv:
        os.environ["DRY_RUN"] = "1"
    today = _today()
    try:
        msg, meta = build(today)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR 저녁 메시지 조립 실패: {e}", file=sys.stderr)
        return 1
    q, cand = meta.pop("quote"), meta.pop("candidate")
    mid = J.send_telegram(msg)
    if q:
        J.record_message(mid, "quote", q["text"], q["section"])
    if cand:
        cmid = J.send_telegram(candidate_message(cand))
        J.record_message(cmid, "candidate", cand["text"], "책에서", ref=cand["id"])
        J.mark_offered(cand["id"])
    meta["candidate"] = bool(cand)
    J.emit_ledger("jay_desk.evening.sent", "저녁 한 통 발송", meta)
    print(f"✅ 저녁 한 통 발송 {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
