"""아침 날씨 한 통 (2026-11-03, 치프봇).

Jay 요청: 절대 기온은 감이 없으니 "어제보다 얼마나 춥냐·덥냐"를 먼저,
그리고 오늘 하루 변동을 미리 보고 챙길 것을 알려준다.

- 데이터: 네이버 날씨 페이지(weather.naver.com/today/<지역코드>)에 박힌 blockApiResult JSON.
  시간대별 예보·오늘 최저/최고·현재 기온과 어제 같은 시각 차이(ytmpr)·어제 실측 최저/최고(pastWetr).
  어제 실측이 아직 없으면 어제 이 잡이 저장한 스냅샷으로 비교한다.
- 지역: ~/.config/jay-weather.json {"home": {"name", "code"}, "office": {"name", "code"}}.
  코드는 `weather_brief.py find 역삼동`으로 찾는다. 근무일엔 아침=집·낮과 저녁=회사, 쉬는 날은 집만.
- 발송 시각: Jay 기상 기록(2026-09~10, 평일 대개 06:40~08:00·주말 07:00~09:00) 기준
  근무일 06:50·쉬는 날 07:50. cron은 두 칸 다 돌고 오늘이 어느 칸인지는 work_calendar가 정한다.
- LLM 없음. 문장은 규칙으로 조립한다. 드라이런 `DRY_RUN=1`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import requests

import jay_desk

CONFIG = Path(os.getenv("JAY_WEATHER_CONFIG") or Path.home() / ".config/jay-weather.json")
STATE = jay_desk.STATE / "weather"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
MORNING = range(7, 10)       # 출근·외출 준비
EVENING_HOUR = 19            # 퇴근길
ACTIVE_WORK = range(7, 22)   # 근무일에 밖에 있을 수 있는 시간
ACTIVE_OFF = range(9, 23)


# ── 네이버 날씨 ──────────────────────────────────────────────
def fetch_raw(code: str) -> dict:
    r = requests.get(f"https://weather.naver.com/today/{code}", headers=UA, timeout=20)
    r.raise_for_status()
    h = r.text
    key = "var blockApiResult = "
    i = h.index(key) + len(key)
    obj, _ = json.JSONDecoder().raw_decode(h[i:])
    return obj["results"]["choiceResult"]


def find_region(q: str) -> list[tuple[str, str]]:
    r = requests.get("https://ac.weather.naver.com/ac", headers=UA, timeout=10,
                     params={"q_enc": "utf-8", "r_format": "json", "r_enc": "utf-8",
                             "r_lt": "1", "st": "1", "q": q})
    r.raise_for_status()
    items = (r.json().get("items") or [[]])[0]
    return [(it[0][0], it[1][0]) for it in items]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Day:
    name: str
    hourly: dict[int, dict] = field(default_factory=dict)  # 오늘 시각 → {t, feel, rain, amt, txt, wind}
    tmin: float | None = None
    tmax: float | None = None
    now_t: float | None = None
    now_vs_yday: float | None = None      # 지금 기온 - 어제 같은 시각
    yday_min: float | None = None          # 어제 실측
    yday_max: float | None = None
    air: str = ""                          # 나쁨·매우나쁨일 때만
    warns: list[str] = field(default_factory=list)


def parse(cr: dict, today: date, name: str) -> Day:
    d = Day(name=name)
    ymd = today.strftime("%Y%m%d")
    yday = (today - timedelta(days=1)).strftime("%Y%m%d")

    for x in (cr.get("hourlyFcast~~1") or {}).get("domesticHourlyFcastList") or []:
        ymdt = str(x.get("aplYmdt", ""))
        if not ymdt.startswith(ymd) or len(ymdt) < 10:
            continue
        hr = int(ymdt[8:10])
        d.hourly.setdefault(hr, {
            "t": _num(x.get("tmpr")), "feel": _num(x.get("stmpr")),
            "rain": _num(x.get("rainProb")) or 0.0, "amt": _num(x.get("rainAmt")) or 0.0,
            "txt": x.get("wetrTxt") or "", "wind": _num(x.get("windSpd")) or 0.0,
        })

    syn = cr.get("nowSynthesisFcast~~1") or {}
    now = syn.get("nowFcast") or {}
    d.now_t = _num(now.get("tmpr"))
    d.now_vs_yday = _num(now.get("ytmpr"))
    air = syn.get("airFcast") or {}
    worst = [air.get("stationPM10Legend1"), air.get("stationPM25Legend1")]
    d.air = next((w for w in ("매우나쁨", "나쁨") if w in worst), "")

    for w in (syn.get("weeklyFcastList") or (cr.get("weeklyFcast~~1") or {}).get("domesticWeeklyFcastList") or []):
        if str(w.get("aplYmd")) == ymd:
            d.tmin, d.tmax = _num(w.get("minTmpr")), _num(w.get("maxTmpr"))

    for c in (cr.get("pastWetr~~1") or {}).get("pastWetrCalendarList") or []:
        p = c.get("pastWetr")
        if p and str(p.get("aplYmd")) == yday:
            d.yday_min, d.yday_max = _num(p.get("minTmpr")), _num(p.get("maxTmpr"))

    for w in (cr.get("notice~~1") or {}).get("warnList") or []:
        # 네이버 화면 코드 기준 필드: stypeNm(호우·대설·강풍…) + warnLevelName(주의보·경보)
        t = f"{w.get('stypeNm') or ''}{w.get('warnLevelName') or ''}".strip()
        if t:
            d.warns.append(t)
    return d


# ── 어제 스냅샷 (실측 대체·저녁 비교용) ──────────────────────────
def _snap_path(key: str, d: date) -> Path:
    return STATE / f"{d.isoformat()}-{key}.json"


def save_snapshot(key: str, today: date, day: Day, prep_keys: list[str] | None = None) -> None:
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        return
    STATE.mkdir(parents=True, exist_ok=True)
    _snap_path(key, today).write_text(json.dumps(
        {"tmin": day.tmin, "tmax": day.tmax, "hourly": {str(k): v["t"] for k, v in day.hourly.items()},
         "prep_keys": prep_keys or []}, ensure_ascii=False), encoding="utf-8")
    for old in STATE.glob("*.json"):
        if old.name[:10] < (today - timedelta(days=14)).isoformat():
            old.unlink(missing_ok=True)


def load_snapshot(key: str, d: date) -> dict:
    try:
        return json.loads(_snap_path(key, d).read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 말로 바꾸기 ──────────────────────────────────────────────
def feel(delta: float | None, ref: float | None) -> str:
    """기온 차이를 체감 말로. ref=오늘 그 시간대 기온(춥다/쌀쌀/따뜻/덥다 고르기)."""
    if delta is None:
        return ""
    a = abs(delta)
    n = f"{a:.0f}도" if a >= 1.5 else ""
    if a < 1.5:
        return "어제랑 비슷해요"
    if delta > 0:
        word = "더워요" if (ref or 0) >= 25 else "따뜻해요"
    else:
        word = "추워요" if (ref if ref is not None else 10) < 8 else "쌀쌀해요"
    deg = "살짝 " if a < 3 else ("꽤 " if a < 6 else "확 ")
    return f"어제보다 {deg}{word} ({'+' if delta > 0 else '−'}{n})"


def _rng(hours: list[int]) -> str:
    """[15,16,17] → '15~18시'(마지막 시각 끝까지)."""
    groups, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h != prev + 1:
            groups.append((start, prev))
            start = h
        prev = h
    groups.append((start, prev))
    return ", ".join(f"{a}시" if a == b else f"{a}~{b + 1}시" for a, b in groups)


@dataclass
class Brief:
    headline: str
    rows: list[str]
    prep: list[str]
    keys: list[str] = field(default_factory=list)   # 오늘 낸 '챙길 것' 종류(내일 반복 억제용)


# 어제도 같은 말을 했으면 오늘은 빼는 종류(옷차림·바람). 비·특보·미세먼지·레이더는 그날 사건이라 매번 낸다.
REPEATABLE = ("layer", "coat", "gloves", "heat", "wind")
PREP_KEY = {"벗기 쉬운 겉옷": "layer", "겉옷": "layer", "두꺼운 외투": "coat", "장갑·목도리": "gloves",
            "더위 대비": "heat", "바람": "wind"}


def build(home: Day, office: Day | None, workday: bool,
          yday_home: dict, yday_office: dict) -> Brief:
    day_src = office or home
    yday_day = yday_office if office else yday_home
    active = ACTIVE_WORK if workday else ACTIVE_OFF
    rows: list[str] = []

    # 아침: 지금(발송 시각) 기온 vs 어제 같은 시각
    m_ref = next((home.hourly[h]["t"] for h in MORNING if h in home.hourly), home.now_t)
    m_delta = home.now_vs_yday
    if m_delta is None and yday_home.get("hourly"):
        pairs = [(home.hourly[h]["t"], yday_home["hourly"].get(str(h))) for h in MORNING if h in home.hourly]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        if pairs:
            m_delta = sum(a - b for a, b in pairs) / len(pairs)
    if m_delta is not None:
        rows.append(f"아침 · {feel(m_delta, m_ref)}")

    # 낮: 오늘 최고 vs 어제 최고(실측 우선, 없으면 어제 스냅샷)
    y_max = day_src.yday_max if day_src.yday_max is not None else yday_day.get("tmax")
    d_delta = (day_src.tmax - y_max) if (day_src.tmax is not None and y_max is not None) else None
    if d_delta is not None:
        rows.append(f"낮 · {feel(d_delta, day_src.tmax)}")

    # 저녁: 19시 vs 어제 19시(어제 스냅샷)
    e_now = (day_src.hourly.get(EVENING_HOUR) or {}).get("t")
    e_yday = (yday_day.get("hourly") or {}).get(str(EVENING_HOUR))
    label = "퇴근길" if workday else "저녁"
    if e_now is not None and e_yday is not None:
        rows.append(f"{label} · {feel(e_now - e_yday, e_now)}")

    # 머리줄: 아침·낮 중 더 크게 다른 쪽이 오늘의 인상
    deltas = [x for x in (m_delta, d_delta) if x is not None]
    def short(delta, ref):
        return feel(delta, ref).split(" (")[0].replace("어제보다 ", "")
    if (m_delta is not None and d_delta is not None and abs(m_delta) >= 1.5 and abs(d_delta) >= 1.5
            and (m_delta > 0) != (d_delta > 0)):
        headline = f"아침은 어제보다 {short(m_delta, m_ref)}, 낮은 {short(d_delta, day_src.tmax)}"
    elif deltas:
        main = max(deltas, key=abs)
        ref = day_src.tmax if main is d_delta else m_ref
        headline = "오늘은 " + feel(main, ref).split(" (")[0]
    else:
        headline = ""

    # 챙길 것
    prep: list[str] = []
    hrs = {h: v for src in ([home, office] if office else [home]) for h, v in src.hourly.items() if h in active}
    wet = sorted(h for h, v in hrs.items() if v["rain"] >= 60 or v["amt"] > 0 or "비" in v["txt"] or "눈" in v["txt"])
    maybe = sorted(h for h, v in hrs.items() if 40 <= v["rain"] < 60 and h not in wet)
    snow = any("눈" in hrs[h]["txt"] for h in wet)
    if wet:
        pmax = max(hrs[h]["rain"] for h in wet)
        prep.append(f"우산 — {_rng(wet)} {'눈' if snow else '비'}" + (f" ({pmax:.0f}%)" if pmax else ""))
    elif maybe:
        prep.append(f"접는 우산 — {_rng(maybe)} 비 올 수도 ({max(hrs[h]['rain'] for h in maybe):.0f}%)")

    temps = [v["t"] for v in hrs.values() if v["t"] is not None]
    if temps and max(temps) - min(temps) >= 10:
        cold_h = min(hrs, key=lambda h: hrs[h]["t"] if hrs[h]["t"] is not None else 99)
        prep.append(f"벗기 쉬운 겉옷 — 낮과 {cold_h}시 차이가 {max(temps) - min(temps):.0f}도")
    elif e_now is not None and day_src.tmax is not None and day_src.tmax - e_now >= 7:
        prep.append(f"겉옷 — {label}엔 낮보다 {day_src.tmax - e_now:.0f}도 내려가요")

    feels = [v["feel"] for v in hrs.values() if v["feel"] is not None]
    if feels:
        lo = min(feels)
        if lo <= -5:
            prep.append(f"장갑·목도리 — 체감 {lo:.0f}°까지")
        elif lo <= 2:
            prep.append(f"두꺼운 외투 — 체감 {lo:.0f}°까지")
        if max(feels) >= 31:
            prep.append(f"더위 대비 — 체감 {max(feels):.0f}°까지")

    windy = max((v["wind"] for v in hrs.values()), default=0)
    if windy >= 7:
        prep.append(f"바람 — 초속 {windy:.0f}m, 실제보다 더 춥게 느껴져요")
    air = home.air or (office.air if office else "")
    if air:
        prep.append(f"마스크 — 지금 미세먼지 {air}")
    for w in dict.fromkeys(home.warns + (office.warns if office else [])):
        prep.insert(0, f"특보 — {w}")

    # 어제도 같은 옷차림 조언을 했고 오늘이 어제보다 확 춥거나 덥지 않으면 빼기(매일 같은 처방 금지)
    said = set(yday_home.get("prep_keys") or [])
    big_change = any(abs(x) >= 3 for x in deltas)
    keys, kept = [], []
    for item in prep:
        k = PREP_KEY.get(item.split(" — ")[0])
        if k:
            keys.append(k)
        if k in REPEATABLE and k in said and not big_change:
            continue
        kept.append(item)
    return Brief(headline=headline, rows=rows, prep=kept, keys=keys)


def icon(day: Day, brief: Brief) -> str:
    txt = " ".join(v["txt"] for v in day.hourly.values())
    if any(p.startswith(("우산", "출근길 비", "곧 비")) for p in brief.prep):
        return "☔️" if "눈" not in txt else "❄️"
    if "흐림" in txt or "구름많" in txt:
        return "⛅️"
    return "☀️"


def render(brief: Brief, home: Day, office: Day | None, today: date) -> str:
    if not brief.headline and not brief.rows and not brief.prep:
        raise RuntimeError("어제 비교·챙길 것이 모두 비어 있습니다(데이터 누락 의심) — 빈 메시지는 보내지 않습니다")
    head = brief.headline or "오늘 날씨"
    lines = [f"{icon(office or home, brief)} <b>{escape(head)}</b>"]
    lines += [escape(r) for r in brief.rows]
    if brief.prep:
        lines += ["", "<b>챙길 것</b>"] + [f"· {escape(p)}" for p in brief.prep]
    span = []
    for d in ([home, office] if office else [home]):
        if d.tmin is not None and d.tmax is not None:
            span.append(f"{d.name} {d.tmin:.0f}~{d.tmax:.0f}°")
    if len(span) == 2 and span[0].split(" ", 1)[1] == span[1].split(" ", 1)[1]:
        span = [span[0]]
    if span:
        lines += ["", f"<i>{escape(' · '.join(span))}</i>"]
    return "\n".join(lines)


def commute_radar(home_cfg: dict, workday: bool, today: date) -> str:
    """레이더로 본 앞으로 1시간 반 안의 비(출근·외출 준비 시간). 못 읽으면 빈 문자열."""
    if "lat" not in home_cfg:
        return ""
    try:
        import radar
        import rain_watch
        now = jay_desk.now()
        ep = rain_watch.radar_episode(radar.forecast({"h": (home_cfg["lat"], home_cfg["lng"])}, now)["h"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 레이더 실패: {e}", file=sys.stderr)
        return ""
    if not ep:
        return ""
    start, end, peak, last_wet = ep
    if os.getenv("DRY_RUN") not in ("1", "true", "TRUE"):
        # 이 비는 아침 한 통이 알렸다 — 비 알림(rain_watch)이 같은 비로 또 보내지 않게
        st = rain_watch.load_state(today)
        st["wet_until"] = max(last_wet.isoformat(), st.get("wet_until", ""))
        rain_watch.save_state(today, st)
    label = "출근길 비" if workday else "곧 비"
    when = "지금 오는 중" if start <= now + timedelta(minutes=5) else f"{start:%H:%M}부터"
    return f"{label} — {when}" + (f", {end:%H:%M}쯤 그침" if end else "") + f" (레이더, 최대 {peak:g}mm/h)"


# ── 실행 ────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not (cfg.get("home") or {}).get("code"):
        raise SystemExit(f"{CONFIG}에 home.code가 없습니다 — `weather_brief.py find 동이름`으로 찾으세요")
    return cfg


def sent_flag(today: date) -> Path:
    return STATE / f"sent-{today.isoformat()}"


def run(slot: str | None, force: bool = False) -> int:
    today = jay_desk.now().date()
    workday = not jay_desk.is_weekend(today)
    if slot and not force and slot != ("work" if workday else "off"):
        print(f"[skip] {today} {'근무일' if workday else '쉬는 날'} — {slot} 칸 아님")
        return 0
    dry = os.getenv("DRY_RUN") in ("1", "true", "TRUE")
    if not dry and not force and sent_flag(today).exists():
        print(f"[skip] {today} 이미 보냄")
        return 0

    cfg = load_config()
    yday = today - timedelta(days=1)
    home = parse(fetch_raw(cfg["home"]["code"]), today, cfg["home"].get("name", "집"))
    office = None
    oc = cfg.get("office") or {}
    if workday and oc.get("code") and oc["code"] != cfg["home"]["code"]:
        office = parse(fetch_raw(oc["code"]), today, oc.get("name", "회사"))
    if not home.hourly:
        raise RuntimeError("네이버 날씨 시간대별 예보가 비어 있습니다(페이지 구조 변경 의심)")

    brief = build(home, office, workday, load_snapshot("home", yday),
                  load_snapshot("office", yday) if office else {})
    radar_line = commute_radar(cfg["home"], workday, today)
    if radar_line:
        brief.prep = [p for p in brief.prep if not p.startswith("접는 우산")]
        brief.prep.insert(0, radar_line)
    text = render(brief, home, office, today)
    save_snapshot("home", today, home, brief.keys)
    if oc.get("code"):
        o_today = office or (parse(fetch_raw(oc["code"]), today, oc.get("name", "회사"))
                             if oc["code"] != cfg["home"]["code"] else home)
        save_snapshot("office", today, o_today)

    jay_desk.send_telegram(text)
    if not dry:
        STATE.mkdir(parents=True, exist_ok=True)
        sent_flag(today).write_text(jay_desk.now().isoformat(), encoding="utf-8")
        for old in STATE.glob("sent-*"):
            if old.name[5:15] < (today - timedelta(days=14)).isoformat():
                old.unlink(missing_ok=True)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["find"]:
        for name, code in find_region(" ".join(argv[1:])):
            print(code, name)
        return 0
    slot = None
    if "--slot" in argv:
        slot = argv[argv.index("--slot") + 1]
    return run(slot, force="--force" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
