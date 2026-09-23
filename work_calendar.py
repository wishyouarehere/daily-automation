"""한국 근무일 판정 — 공휴일·대체공휴일·연휴를 반영한다 (2026-09-23).

정본: 구글 '대한민국 공휴일(공식)' 캘린더(기념일 제외). daily-automation .env의 캘린더 토큰으로 읽고
~/.local/state/jay-desk/holidays.json에 저장한다(7일마다 갱신).
캘린더를 못 읽으면 저장본 → 그것도 없으면 아래 내장표(2026~2027, 9/23 구글 공식 캘린더에서 복사) 순으로 쓴다.
회사 자체 휴무·개인 연차는 ~/.config/jay-workdays.json으로 더하거나 뺀다:
    {"off": {"2026-10-02": "회사 휴무"}, "work": ["2026-05-01"]}

쓰는 곳: 주간회고·타임오딧(그 주 마지막 근무일), 월요일 브리프(그 주 첫 근무일), 아침·저녁 한 통(휴일=주말 모드).
CLI: python work_calendar.py [YYYY-MM-DD]   # 그 주 근무일·판정 출력
     python work_calendar.py --gate last|first [YYYY-MM-DD]   # 해당하면 exit 0, 아니면 1 (cron 가드용)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CAL_ID = "ko.south_korea.official#holiday@group.v.calendar.google.com"
CACHE = Path.home() / ".local/state/jay-desk/holidays.json"
OVERRIDES = Path.home() / ".config/jay-workdays.json"
REFRESH_DAYS = 7
_ENV_DIRS = (Path.home() / "Documents/daily-automation", Path.home() / "daily-automation")

# (시작일, 종료일(미포함), 이름) — 구글 공식 캘린더 그대로
_BUILTIN = [
    ("2026-01-01", "2026-01-02", "새해첫날"), ("2026-02-16", "2026-02-17", "설날 연휴"),
    ("2026-02-17", "2026-02-18", "설날"), ("2026-02-18", "2026-02-19", "설날 연휴"),
    ("2026-03-01", "2026-03-02", "삼일절"), ("2026-03-02", "2026-03-03", "쉬는 날 삼일절"),
    ("2026-05-01", "2026-05-02", "노동절"), ("2026-05-05", "2026-05-06", "어린이날"),
    ("2026-05-24", "2026-05-25", "부처님오신날"), ("2026-05-25", "2026-05-26", "쉬는 날 부처님오신날"),
    ("2026-06-03", "2026-06-04", "지방선거일"), ("2026-06-06", "2026-06-07", "현충일"),
    ("2026-07-17", "2026-07-18", "제헌절"), ("2026-08-15", "2026-08-16", "광복절"),
    ("2026-08-17", "2026-08-18", "쉬는 날 광복절"), ("2026-09-24", "2026-09-25", "추석 연휴"),
    ("2026-09-25", "2026-09-26", "추석"), ("2026-09-26", "2026-09-27", "추석 연휴"),
    ("2026-10-03", "2026-10-04", "개천절"), ("2026-10-05", "2026-10-06", "쉬는 날 개천절"),
    ("2026-10-09", "2026-10-10", "한글날"), ("2026-12-25", "2026-12-26", "크리스마스"),
    ("2027-01-01", "2027-01-02", "새해첫날"), ("2027-02-07", "2027-02-08", "설날"),
    ("2027-02-08", "2027-02-09", "설날 연휴"), ("2027-02-09", "2027-02-10", "쉬는 날 설날"),
    ("2027-03-01", "2027-03-02", "삼일절"), ("2027-05-01", "2027-05-02", "노동절"),
    ("2027-05-03", "2027-05-04", "쉬는 날 노동절"), ("2027-05-05", "2027-05-06", "어린이날"),
    ("2027-05-13", "2027-05-14", "부처님오신날"), ("2027-06-06", "2027-06-07", "현충일"),
    ("2027-07-17", "2027-07-18", "제헌절"), ("2027-07-19", "2027-07-20", "쉬는 날 제헌절"),
    ("2027-08-15", "2027-08-16", "광복절"), ("2027-08-16", "2027-08-17", "쉬는 날 광복절"),
    ("2027-09-14", "2027-09-15", "추석 연휴"), ("2027-09-15", "2027-09-16", "추석"),
    ("2027-09-16", "2027-09-17", "추석 연휴"), ("2027-10-03", "2027-10-04", "개천절"),
    ("2027-10-04", "2027-10-05", "쉬는 날 개천절"), ("2027-10-09", "2027-10-10", "한글날"),
    ("2027-10-11", "2027-10-12", "쉬는 날 한글날"), ("2027-12-25", "2027-12-26", "크리스마스"),
    ("2027-12-27", "2027-12-28", "쉬는 날 크리스마스"),
]

_memo: dict | None = None


def _expand(rows) -> dict[str, str]:
    """[(start, end_exclusive, name)] → {날짜: 이름}. 여러 날짜 일정도 하루씩 편다."""
    out: dict[str, str] = {}
    for s, e, name in rows:
        d, end = date.fromisoformat(s), date.fromisoformat(e)
        while d < end:
            out.setdefault(d.isoformat(), name)
            d += timedelta(days=1)
    return out


def _fetch() -> dict[str, str]:
    """구글 공식 공휴일 캘린더(올해 1월~내년 12월). 실패하면 예외."""
    from dotenv import load_dotenv
    for d in _ENV_DIRS:
        if (d / ".env").exists():
            load_dotenv(d / ".env")
            break
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(token=None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
                        client_id=os.environ["GOOGLE_CLIENT_ID"],
                        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/calendar.readonly"])
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    y = datetime.now(KST).year
    items, token = [], None
    while True:
        r = svc.events().list(calendarId=CAL_ID, timeMin=f"{y}-01-01T00:00:00+09:00",
                              timeMax=f"{y + 2}-01-01T00:00:00+09:00", singleEvents=True,
                              orderBy="startTime", pageToken=token).execute()
        items += r.get("items", [])
        token = r.get("nextPageToken")
        if not token:
            break
    rows = [(e["start"]["date"], e["end"]["date"], e.get("summary") or "공휴일")
            for e in items if e.get("start", {}).get("date")]
    if not rows:
        raise RuntimeError("공휴일 캘린더가 비어 있음")
    return _expand(rows)


def holidays() -> dict[str, str]:
    """{YYYY-MM-DD: 이름}. 캘린더 → 저장본 → 내장표 순. 저장본은 내장표와 합쳐 빈 해를 메운다."""
    global _memo
    if _memo is not None:
        return _memo
    cached, fetched_at = {}, None
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        cached, fetched_at = c.get("days") or {}, c.get("fetched_at")
    except Exception:
        pass
    fresh = False
    if fetched_at:
        try:
            fresh = (datetime.now(KST) - datetime.fromisoformat(fetched_at)).days < REFRESH_DAYS
        except ValueError:
            fresh = False
    if not fresh:
        try:
            cached = _fetch()
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"fetched_at": datetime.now(KST).isoformat(), "days": cached},
                                        ensure_ascii=False, indent=0), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 공휴일 캘린더 갱신 실패 — {'저장본' if cached else '내장표'} 사용: {e}", file=sys.stderr)
    merged = _expand(_BUILTIN)
    merged.update(cached)
    _memo = merged
    return merged


def _overrides() -> tuple[dict[str, str], set[str]]:
    try:
        o = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        off = o.get("off") or {}
        if isinstance(off, list):
            off = {d: "휴무" for d in off}
        return {str(k): str(v) for k, v in off.items()}, {str(d) for d in (o.get("work") or [])}
    except FileNotFoundError:
        return {}, set()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {OVERRIDES} 읽기 실패(무시): {e}", file=sys.stderr)
        return {}, set()


def day_off_reason(d: date) -> str | None:
    """쉬는 날이면 이유('토요일'·'추석 연휴'·'회사 휴무'), 근무일이면 None."""
    key = d.isoformat()
    off, work = _overrides()
    if key in work:
        return None
    if key in off:
        return off[key]
    name = holidays().get(key)
    if name:
        return name
    if d.weekday() >= 5:
        return "토요일" if d.weekday() == 5 else "일요일"
    return None


def is_workday(d: date) -> bool:
    return day_off_reason(d) is None


def week_workdays(d: date) -> list[date]:
    """d가 속한 주(월~일)의 근무일."""
    monday = d - timedelta(days=d.weekday())
    return [monday + timedelta(days=i) for i in range(7) if is_workday(monday + timedelta(days=i))]


def is_last_workday_of_week(d: date) -> bool:
    days = week_workdays(d)
    return bool(days) and d == days[-1]


def is_first_workday_of_week(d: date) -> bool:
    days = week_workdays(d)
    return bool(days) and d == days[0]


def _today() -> date:
    return datetime.now(KST).date()


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--gate":
        which = argv[1]
        d = date.fromisoformat(argv[2]) if len(argv) > 2 else _today()
        ok = is_last_workday_of_week(d) if which == "last" else is_first_workday_of_week(d)
        print(f"{d} {which} workday of week: {ok}")
        return 0 if ok else 1
    d = date.fromisoformat(argv[0]) if argv else _today()
    print(f"{d}: {'근무일' if is_workday(d) else '쉬는 날 — ' + day_off_reason(d)}")
    print("이번 주 근무일:", ", ".join(x.strftime("%m/%d(%a)") for x in week_workdays(d)) or "없음")
    print("첫 근무일:", is_first_workday_of_week(d), "| 마지막 근무일:", is_last_workday_of_week(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
