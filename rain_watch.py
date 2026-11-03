"""비 오기 전 알림 (2026-11-03, 치프봇).

Jay 요청: 강우레이더 앱처럼 곧 올 비를 미리 알려 달라. 비 없는 날은 조용하고, 비 한 번에 한 통.

- 07~21시 10분마다 돈다. 지금 있을 곳(근무일 09~19시=회사, 그 외=집)의 앞으로 2시간을 본다.
- 데이터: 네이버 날씨 시간대별 예보. 앞 6시간은 기상청 초단기예보(레이더 반영, 1시간 단위)다.
  기상청 API허브 키(~/.config/kma.env)가 들어오면 레이더 초단기강수예측(10분 단위)으로 바꿀 자리 = upcoming().
- 판정: 그 시각 강수량>0 또는 강수확률 60% 이상 또는 날씨에 비·눈.
- 같은 비(연속된 젖은 시간 묶음)는 한 번만 알린다. 상태 ~/.local/state/jay-desk/weather/rain-YYYY-MM-DD.json.
드라이런 `DRY_RUN=1`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from html import escape
from pathlib import Path

import jay_desk
import weather_brief as wb

LOOKAHEAD_H = 2
OFFICE_HOURS = range(9, 19)


def is_wet(v: dict) -> bool:
    return v["amt"] > 0 or v["rain"] >= 60 or "비" in v["txt"] or "눈" in v["txt"]


def where(now: datetime, workday: bool, cfg: dict) -> tuple[str, dict]:
    if workday and now.hour in OFFICE_HOURS and (cfg.get("office") or {}).get("code"):
        return "office", cfg["office"]
    return "home", cfg["home"]


def upcoming(code: str, name: str, today: date) -> dict[int, dict]:
    """오늘 시각 → 예보. 키가 생기면 여기서 레이더 초단기강수예측을 우선한다."""
    return wb.parse(wb.fetch_raw(code), today, name).hourly


def episode(hourly: dict[int, dict], now_h: int) -> tuple[int, int, dict] | None:
    """지금~2시간 안에 시작하는 첫 젖은 묶음 (시작, 끝, 최대 예보)."""
    start = next((h for h in range(now_h, now_h + LOOKAHEAD_H + 1)
                  if h in hourly and is_wet(hourly[h])), None)
    if start is None:
        return None
    end = start
    while end + 1 in hourly and is_wet(hourly[end + 1]):
        end += 1
    peak = max((hourly[h] for h in range(start, end + 1)), key=lambda v: (v["amt"], v["rain"]))
    return start, end, peak


def state_path(d: date) -> Path:
    return wb.STATE / f"rain-{d.isoformat()}.json"


def load_state(d: date) -> dict:
    try:
        return json.loads(state_path(d).read_text(encoding="utf-8"))
    except Exception:
        return {"until": -1}


def message(start: int, end: int, peak: dict, now: datetime, place: str) -> str:
    kind = "눈" if "눈" in peak["txt"] else "비"
    if start <= now.hour:
        when = f"지금부터 {kind}가 와요" if kind == "비" else "지금부터 눈이 와요"
    else:
        mins = (start - now.hour) * 60 - now.minute
        when = f"{start}시쯤({mins}분 뒤) {kind}{'가' if kind == '비' else '이'} 시작돼요"
    lines = [f"☔️ <b>{escape(when)}</b>", f"{end + 1}시까지 이어질 것 같아요 · {escape(place)}"]
    detail = []
    if peak["amt"] > 0:
        detail.append(f"시간당 {peak['amt']:g}mm")
    if peak["rain"]:
        detail.append(f"확률 {peak['rain']:.0f}%")
    if detail:
        lines.append(f"<i>{escape(' · '.join(detail))}</i>")
    return "\n".join(lines)


def run() -> int:
    now = jay_desk.now()
    today = now.date()
    if not 7 <= now.hour <= 21:
        return 0
    cfg = wb.load_config()
    workday = not jay_desk.is_weekend(today)
    key, loc = where(now, workday, cfg)
    hourly = upcoming(loc["code"], loc.get("name", key), today)
    if not hourly:
        raise RuntimeError("시간대별 예보가 비어 있습니다(페이지 구조 변경 의심)")
    ep = episode(hourly, now.hour)
    st = load_state(today)
    if not ep or ep[0] <= st.get("until", -1):
        print(f"[ok] {now:%H:%M} {key} 알릴 비 없음")
        return 0
    start, end, peak = ep
    jay_desk.send_telegram(message(start, end, peak, now, loc.get("name", key)))
    if os.getenv("DRY_RUN") not in ("1", "true", "TRUE"):
        wb.STATE.mkdir(parents=True, exist_ok=True)
        state_path(today).write_text(json.dumps({"until": end, "sent_at": now.isoformat()}), encoding="utf-8")
        for f in wb.STATE.glob("rain-*.json"):
            if f.name[5:15] < today.isoformat():
                f.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
