"""비 오기 전 알림 (2026-11-03, 치프봇).

Jay 요청: 강우레이더 앱처럼 곧 올 비를 미리 알려 달라. 비 없는 날은 조용하고, 비 한 번에 한 통.

- 07~21시 10분마다 돈다. 지금 있을 곳(근무일 09~19시=회사, 그 외=집)의 앞으로 2시간을 본다.
- 데이터 1순위: 레이더 초단기강수예측(radar.py, 10분 간격·약 1시간 반 앞). 0.5mm/h 이상이면 비.
  레이더를 못 읽으면 네이버 시간대별 예보(앞 6시간=기상청 초단기예보, 1시간 단위)로 대신한다.
- 같은 비는 한 번만 알린다. 마지막으로 젖어 있던 시각(wet_until)에서 30분 넘게 갰다가 다시 오면 새 비.
  상태 ~/.local/state/jay-desk/weather/rain-YYYY-MM-DD.json.
드라이런 `DRY_RUN=1`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
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


RADAR_WET = 0.5        # mm/h — 이보다 약하면 비로 안 친다(이슬비·번짐)
NEW_RAIN_GAP_MIN = 30
ALERT_WITHIN_MIN = 60  # 레이더 예측은 멀수록 틀린다 — 1시간 안에 시작할 비만 알리고, 더 먼 비는 다음 실행이 잡는다


def alerts_path() -> Path:
    return wb.STATE / "rain_alerts.jsonl"


def state_path(d: date) -> Path:
    return wb.STATE / f"rain-{d.isoformat()}.json"


def load_state(d: date) -> dict:
    try:
        return json.loads(state_path(d).read_text(encoding="utf-8"))
    except Exception:
        return {}


def strength(mm: float) -> str:
    if mm >= 15:
        return "아주 강한 비"
    if mm >= 3:
        return "강한 비"
    if mm >= 1:
        return "보통 비"
    return "약한 비"


def radar_episode(series: list[tuple[datetime, float]]) -> tuple[datetime, datetime | None, float, datetime] | None:
    """(시작, 그치는 시각 또는 None=예측 끝까지 계속, 최대 mm/h, 마지막 젖은 시각).

    잡음 거르기: 한 프레임(10분)만 스치는 비는 1mm/h 이상일 때만 친다.
    """
    i = 0
    while i < len(series):
        if series[i][1] < RADAR_WET:
            i += 1
            continue
        i0 = i1 = i
        while i1 + 1 < len(series) and series[i1 + 1][1] >= RADAR_WET:
            i1 += 1
        if i1 > i0 or series[i0][1] >= 1.0:
            break
        i = i1 + 1
    else:
        return None
    end = series[i1 + 1][0] if i1 + 1 < len(series) else None
    peak = max(v for _, v in series[i0:i1 + 1])
    return series[i0][0], end, peak, series[i1][0]


def radar_message(start: datetime, end: datetime | None, peak: float, now: datetime, place: str,
                  later_hour: int | None = None, snow: bool = False) -> str:
    mins = int((start - now).total_seconds() // 60)
    k = "눈이" if snow else "비가"
    head = f"지금 {k} 오고 있어요" if mins <= 5 else f"{start:%H:%M}쯤({mins}분 뒤) {k} 시작돼요"
    if end:
        tail = f"{end:%H:%M}쯤 그칠 것 같아요"
    elif later_hour is not None:
        tail = f"{later_hour + 1}시까지 이어질 것 같아요"
    else:
        tail = "한동안 이어질 것 같아요"
    return "\n".join([f"☔️ <b>{escape(head)}</b>", f"{escape(tail)} · {escape(place)}",
                      f"<i>{'눈' if snow else strength(peak)} · 최대 시간당 {peak:g}mm · 레이더</i>"])


def message(start: int, end: int, peak: dict, now: datetime, place: str) -> str:
    """레이더를 못 읽었을 때(시간 단위 예보) 문구."""
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


def is_new_rain(start: datetime, st: dict) -> bool:
    last = st.get("wet_until")
    if not last:
        return True
    return (start - datetime.fromisoformat(last)).total_seconds() > NEW_RAIN_GAP_MIN * 60


def save_state(today: date, st: dict) -> None:
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        return
    wb.STATE.mkdir(parents=True, exist_ok=True)
    state_path(today).write_text(json.dumps(st), encoding="utf-8")
    for f in wb.STATE.glob("rain-*.json"):
        if f.name[5:15] < today.isoformat():
            f.unlink(missing_ok=True)


def run() -> int:
    now = jay_desk.now()
    today = now.date()
    if not 7 <= now.hour <= 21:
        return 0
    cfg = wb.load_config()
    workday = not jay_desk.is_weekend(today)
    key, loc = where(now, workday, cfg)
    place = loc.get("name", key)
    st = load_state(today)

    try:
        import radar
        series = radar.forecast({key: (loc["lat"], loc["lng"])}, now)[key]
        if not series:
            raise RuntimeError("레이더 프레임 없음")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 레이더 실패 → 시간 단위 예보로 대신: {e}", file=sys.stderr)
        try:  # 조용히 정밀도만 떨어지는 걸 막는다 — warning이라 반복될 때만 Jay에게 올라간다
            sys.path.insert(0, str(Path.home() / "ops-bot"))
            import notify_policy
            notify_policy.send(f"비 알림이 레이더를 못 읽어 1시간 단위 예보로 대신하고 있어요(rain_watch)\n{e}",
                               severity="warning", source="rain_watch")
        except Exception:  # noqa: BLE001
            pass
        return run_hourly(now, today, key, loc, st)

    verify_alerts(now)
    ep = radar_episode(series)
    if not ep:
        print(f"[ok] {now:%H:%M} {key} 레이더 비 없음")
        return 0
    start, end, peak, last_wet = ep
    if not is_new_rain(start, st):
        st["wet_until"] = max(last_wet.isoformat(), st.get("wet_until", ""))
        save_state(today, st)
        print(f"[ok] {now:%H:%M} {key} 이미 알린 비 계속")
        return 0
    if (start - now).total_seconds() > ALERT_WITHIN_MIN * 60:
        print(f"[ok] {now:%H:%M} {key} {start:%H:%M} 비 예측 — 1시간 넘게 남아 다음 실행에서 다시 확인")
        return 0
    later, snow = None, False
    try:
        hrs = upcoming(loc["code"], place, today)
        snow = "눈" in (hrs.get(start.hour) or {}).get("txt", "")
        if end is None:  # 레이더 끝까지 계속이면 시간 예보로 그치는 시각을 짐작
            h = series[-1][0].hour
            while h + 1 in hrs and is_wet(hrs[h + 1]):
                h += 1
            later = h if h > series[-1][0].hour else None
    except Exception:  # noqa: BLE001
        pass
    jay_desk.send_telegram(radar_message(start, end, peak, now, place, later, snow))
    st["wet_until"] = max(last_wet.isoformat(), st.get("wet_until", ""))
    st["sent_at"] = now.isoformat()
    save_state(today, st)
    log_alert({"sent_at": now.isoformat(), "key": key, "lat": loc["lat"], "lng": loc["lng"],
               "start": start.isoformat(), "peak": peak})
    return 0


# ── 검증 루프: 보낸 알림이 실제로 맞았나 ──────────────────────────
# 시작 예측 시각 앞뒤(−20분~+60분)의 레이더 '관측' 프레임에 그 지점 비(0.5mm/h↑)가 있었으면 적중.
# 최근 5건 중 2건 이상 빗나가면 운영 채널(notify_policy, warning — 반복 시 자동 승격)에 알린다.
VERIFY_AFTER_MIN = 100  # 관측 프레임이 ~30분 늦게 올라오므로 시작+60분 창이 다 찰 때까지 기다림
MISS_LIMIT = 2


def log_alert(row: dict) -> None:
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        return
    with open(alerts_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _alerts() -> list[dict]:
    try:
        return [json.loads(x) for x in alerts_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return []


def judge(alert: dict, observed: list[tuple[datetime, float]]) -> bool:
    start = datetime.fromisoformat(alert["start"])
    window = [v for t, v in observed if -20 * 60 <= (t - start).total_seconds() <= 60 * 60]
    return any(v >= RADAR_WET for v in window)


def verify_alerts(now: datetime) -> None:
    rows = _alerts()
    todo = [r for r in rows if "hit" not in r
            and (now - datetime.fromisoformat(r["start"])).total_seconds() >= VERIFY_AFTER_MIN * 60]
    if not todo:
        return
    import radar
    for r in todo:
        start = datetime.fromisoformat(r["start"])
        if (now - start).total_seconds() > 8 * 3600:   # 관측 프레임 보관(약 10시간) 밖 — 판정 불가로 닫기
            r["hit"] = None
            continue
        r["hit"] = judge(r, radar.observed((r["lat"], r["lng"]), start - timedelta(minutes=20),
                                           start + timedelta(minutes=60), now.tzinfo))
    if os.getenv("DRY_RUN") not in ("1", "true", "TRUE"):
        alerts_path().write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows[-200:]),
                                 encoding="utf-8")
    judged = [x for x in rows if x.get("hit") is not None][-5:]
    misses = sum(1 for x in judged if x["hit"] is False)
    if any(x in todo and x.get("hit") is False for x in rows) and misses >= MISS_LIMIT:
        try:
            sys.path.insert(0, str(Path.home() / "ops-bot"))
            import notify_policy
            notify_policy.send(f"비 알림이 최근 {len(judged)}건 중 {misses}건 빗나갔어요(rain_watch)\n"
                               "레이더 관측에 그 시각 비가 없었어요. 문턱(RADAR_WET)·좌표 점검이 필요해요.",
                               severity="warning", source="rain_watch")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 빗나감 알림 실패: {e}", file=sys.stderr)


def run_hourly(now: datetime, today: date, key: str, loc: dict, st: dict) -> int:
    hourly = upcoming(loc["code"], loc.get("name", key), today)
    if not hourly:
        raise RuntimeError("시간대별 예보가 비어 있습니다(페이지 구조 변경 의심)")
    ep = episode(hourly, now.hour)
    if not ep:
        print(f"[ok] {now:%H:%M} {key} 알릴 비 없음(시간 예보)")
        return 0
    start, end, peak = ep
    s_dt = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if not is_new_rain(s_dt, st):
        return 0
    jay_desk.send_telegram(message(start, end, peak, now, loc.get("name", key)))
    st["wet_until"] = now.replace(hour=end, minute=50, second=0, microsecond=0).isoformat()
    st["sent_at"] = now.isoformat()
    save_state(today, st)
    return 0


if __name__ == "__main__":
    sys.exit(run())
