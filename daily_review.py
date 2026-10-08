"""치프봇 저녁 한 통 — 매일 20:00 KST (회사맥 cron, 2026-10-08 재설계).

비서가 하루를 정리해 건네는 메시지다. 평가·조언·감정 질문은 하지 않는다. 구성:
  오늘 정해진 것 → 다음 근무일(일정 + 지난 회의에서 정해진 맥락) → 「다시, 여기」 문장
빈 칸은 통째로 생략한다. 미결·할 일 목록은 보여주지 않는다(10/8 Jay: 스트레스만 준다).
쉬는 날(주말·공휴일·회사 휴무·연차 — work_calendar 판정)에는 보내지 않는다.

재료: 오늘·내일 캘린더 · 최근 회의록(오늘 + 지난 10일) · 볼트 Daily(WorkFlowy) · 코덱스·클로드 세션 제목.
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


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"[{i}] {l}" for i, l in enumerate(lines)) or "없음"


def _prompt(today: date, weekend: bool, cands: list[dict], material: dict) -> str:
    tomorrow = material.get("next_day") or today + timedelta(days=1)
    return f"""너는 Jay(장홍석, 다니엘프로젝트 부대표·CPO)의 비서실장이다.
오늘은 {today.isoformat()} ({J.WEEKDAY_KR[today.weekday()]}요일) 저녁. 하루를 정리해 건네는 메시지의 재료를 만든다.
Jay가 30초 읽고 "오늘 뭐가 정해졌고, 내일 미팅이 어떤 흐름 위에 있는지" 알게 하는 게 목적이다.

{J.TONE}

출력은 JSON 객체 하나만. 설명·코드펜스 금지:
{{"decided": [{{"meeting": "...", "time": "HH:MM", "items": ["..."]}}], "tomorrow": [{{"i": 0, "note": "..."}}], "quote_id": "...", "underline_id": "..." 또는 null, "underline_text": "..."}}

decided: 오늘 정해진 것을 회의별로 묶는다. 회의 0~3개, 회의당 1~2줄(각 ~40자). 전체 최대 4줄. {'쉬는 날이라 반드시 [].' if weekend else ''}
  형식: [{{"meeting": "주승님 1on1", "time": "11:00", "items": ["정산은 데이원으로 옮기고 소희님이 맡기로 했어요", "..."]}}]
  - time은 그 회의 실제 시작 시각 HH:MM. 회의록의 "날짜: … HH:MM:SS"가 있으면 그것을 먼저 쓰고(캘린더는 같은 시각에 겹칠 수 있다), 없으면 오늘 일정의 시각. 모르면 "".
  - 각 줄은 완결된 해요체 문장으로 끝낸다("~하기로 했어요", "~로 정했어요"). "~하기로", "~함" 같은 반토막 끝맺음 금지.
  - meeting은 자리 이름. 1on1·미팅은 상대 이름까지. items에는 회의 이름을 다시 쓰지 않는다.
  - 오늘 회의록의 '확정 결정'에 적힌 것만. 핵심 요약·미결·제안·요청은 넣지 않는다.
  - 무게 있는 것(돈·사람·일정·제품 방향)부터. 비슷한 결정은 한 줄로 합친다. 사소한 운영 결정은 버린다.
tomorrow: [내일 일정]의 번호 i에 붙일 맥락 한 줄 note(~55자). 0~3개.
  - 지난 회의록에 그 미팅과 직접 이어지는 결정·약속·미결이 있을 때만. 없으면 그 일정은 넣지 않는다.
  - decided에 이미 쓴 내용은 반복하지 않는다. 오늘 회의 말고 그 전 회의에서 이어지는 게 있을 때만.
  - 지난 회의에서 정해진 것만 짚는다. "기록 없음", "미정", "아직 안 됨" 같은 밀린 일·빈칸은 쓰지 않는다.
  - 예: "10/6에 다음 스프린트부터 에픽 단위로 플래닝하기로 했어요"
  - 회의 제목만 보고 내용을 짐작하지 않는다. 스크럼처럼 매일 반복되는 일정은 특별한 게 없으면 빼라.
quote_id: [문장 후보] 중 하루를 닫기에 맞는 문장 id 하나.
underline_id: [책 밑줄 후보] 중 「다시, 여기」(마음·태도·감사·내면을 다잡는 문장 모음)에 둘 만한 것 하나의 id.
  업무 요령·정보·통계·줄거리 문장은 고르지 않는다. 마땅한 게 없으면 null.
underline_text: 고른 밑줄 원문 안에서 완결된 문장 1~2개만 연속으로 잘라낸 구간. 잘린 조각·앞뒤 문맥은 버린다.
  OCR 띄어쓰기 오류만 고치고 글자는 하나도 바꾸거나 더하지 않는다. 완결된 문장이 없으면 underline_id도 null.

공통 규칙:
  - 재료에 적힌 것만. 누가 누구와 합의했는지 주어를 원문 그대로 지킨다.
  - 독자는 Jay 본인이다. "Jay"를 3인칭으로 쓰지 않는다("Jay 컨펌" → "부대표 컨펌").
  - 사람에 대한 평가·갈등·사과·인사, 건강·가족 같은 사적인 일은 쓰지 않는다.
  - 날짜는 재료에 적힌 것만. 마감을 계산해 만들지 않는다. "사실상 마지막" 같은 긴박감 해석 금지.
  - 회의록 말투를 옮기지 말고 동료에게 말하듯 쉬운 말로. 제품명은 데이원(Day1·대시온은 받아쓰기 오류).
  - 미결·할 일·밀린 일·담당 미정 같은 건 어디에도 쓰지 않는다. Jay에게 부담만 준다.
  - 확실한 게 없으면 그 칸은 빈 배열. 채우려고 약한 내용을 넣는 것보다 비우는 게 낫다.

[내일 일정] ({tomorrow.isoformat()} {J.WEEKDAY_KR[tomorrow.weekday()]})
{_numbered(material.get('tomorrow') or [])}

[오늘 일정]
{material.get('calendar') or '없음'}

[오늘 회의록]
{material.get('meetings') or '없음'}

[지난 10일 회의록 — 미결 반복·내일 맥락 확인용]
{material.get('recent') or '없음'}

[오늘 WorkFlowy 데일리]
{material.get('daily') or '없음'}

[오늘 Jay가 AI와 한 작업]
{material.get('sessions') or '없음'}

[문장 후보]
{J.quotes_for_prompt(cands)}

[책 밑줄 후보]
{underlines_for_prompt(material.get('underlines') or [])}
"""


def _polite(line: str) -> str:
    """'~하기로'·'~기로'처럼 반토막으로 끝나면 '~했어요'로 맺는다(10/8 Jay: 건방져 보인다)."""
    t = line.rstrip(" .")
    return t + " 했어요" if t.endswith("기로") else line


def build(today: date) -> tuple[str, dict]:
    weekend = J.is_weekend(today)
    # 다음 근무일(금요일·연휴 전날이면 월요일·연휴 뒤 첫날). 4일 안에 없으면 생략.
    tomorrow = next((today + timedelta(days=k) for k in range(1, 5)
                     if not J.is_weekend(today + timedelta(days=k))), None)
    tomorrow_lines = J.calendar_lines(tomorrow) if tomorrow else []
    material = {"underlines": S.underline_candidates(J.offered_underlines()),
                "tomorrow": tomorrow_lines, "next_day": tomorrow}
    if tomorrow_lines:
        material["recent"] = S.meetings(days=10, total_cap=14000)
    if not weekend:
        start = datetime(today.year, today.month, today.day, tzinfo=J.KST)
        material |= {
            "calendar": "\n".join(J.calendar_lines(today)),
            "meetings": S.meetings(days=1),
            "daily": S.workflowy_daily(today),
            "sessions": "\n".join(S.ai_sessions(start, start + timedelta(days=1))),
        }
    cands = J.quote_candidates(exclude_texts=J.used_today())
    data = J.ask_json(_prompt(today, weekend, cands, material), job="jay_desk_evening",
                      model="opus")  # 하루 1회 — sonnet은 결정 나열에 그쳤다(10/8)
    quote = J.pick_quote(cands, data.get("quote_id"))

    decided: list[tuple[str, list[str]]] = []
    if not weekend:
        left = 4
        groups = [g for g in (data.get("decided") or [])[:3] if isinstance(g, dict)]
        # 일정 순서(시작 시각)로 — 시각을 모르면 뒤로
        groups.sort(key=lambda g: (str(g.get("time") or "").strip() or "99:99").zfill(5))
        for g in groups:
            if not isinstance(g, dict) or left <= 0:
                continue
            items = [_polite(J.clean(S.normalize_terms(x))) for x in (g.get("items") or []) if str(x).strip()][:min(2, left)]
            if items:
                decided.append((J.clean(S.normalize_terms(g.get("meeting") or "")), items))
                left -= len(items)
    notes = {}
    for t in data.get("tomorrow") or []:
        try:
            i = int(t.get("i"))
        except Exception:  # noqa: BLE001
            continue
        if 0 <= i < len(tomorrow_lines) and str(t.get("note") or "").strip():
            notes[i] = J.clean(S.normalize_terms(t["note"]))

    parts = [f"🌙 <b>{J.date_label(today)} · 오늘 정리</b>"]
    if decided:
        rows = []
        for meeting, items in decided:
            if meeting:
                rows.append(meeting)
            rows += [f"  · {x}" for x in items]
        parts.append("<b>정해진 것</b>\n" + "\n".join(rows))
    if tomorrow_lines:
        rows = []
        for i, l in enumerate(tomorrow_lines):
            rows.append(J.clean(l))
            if i in notes:
                rows.append(f"   ↳ {notes[i]}")
        head = "내일" if tomorrow == today + timedelta(days=1) else "다음 근무일"
        parts.append(f"<b>{head} {J.date_label(tomorrow)}</b>\n" + "\n".join(rows))
    if quote:
        parts.append(J.render_quote(quote))
        J.mark_used(quote, "evening")
    meta = {"weekend": weekend, "decided": sum(len(i) for _, i in decided), "tomorrow_notes": len(notes), "llm_ok": bool(data),
            "quote": quote, "candidate": _pick_underline(material.get("underlines") or [], data)}
    return "\n\n".join(parts), meta


def _pick_underline(rows: list[dict], data: dict) -> dict | None:
    """모델이 고른 밑줄 구간. 공백을 뺀 글자가 원문의 연속 구간일 때만 쓴다."""
    uid = data.get("underline_id")
    row = next((r for r in rows if r["id"] == uid), None) if uid else None
    if not row:
        return None
    fixed = str(data.get("underline_text") or "").strip()
    if not fixed or not J.letters_within(fixed, row["text"]):
        return None   # 원문에 없는 글자가 섞이면 제안하지 않는다
    text = fixed
    return {"id": row["id"], "text": text, "captured": row.get("captured", "")}


def candidate_message(c: dict) -> str:
    when = ""
    if c.get("captured"):
        y, m, d = c["captured"].split("-")
        # 옛 사진이 대부분이라 올해가 아니면 연도를 붙인다(2024-02-29가 2/29로만 나가던 문제).
        if int(y) == date.today().year:
            when = f" ({int(m)}/{int(d)}에 찍은 페이지)"
        else:
            when = f" ({y}년 {int(m)}월 {int(d)}일에 찍은 페이지)"
    return (f"📚 <b>책 밑줄에서</b>{when}\n<i>{J.clean(c['text'])}</i>\n\n"
            "「다시, 여기」에 둘까요? ❤️ 누르면 넣어요.")


def main() -> int:
    if "--dry-run" in sys.argv:
        os.environ["DRY_RUN"] = "1"
    today = _today()
    if J.is_weekend(today):   # 주말·공휴일·휴무·연차(캘린더 기준)는 보내지 않는다(10/8 Jay)
        print(f"쉬는 날 — 저녁 한 통 안 보냄 ({J.date_label(today)})")
        return 0
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
