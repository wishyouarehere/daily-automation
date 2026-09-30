"""하루 기록 — Jay의 하루를 날짜별 볼트 파일 하나로 묶는다 (2026-09-23 Jay 결정).

  볼트 20-Areas/Productivity/하루기록/YYYY-MM-DD.md
    ## 요약 · ## 회의 (PLAUD) · ## GPT 워크 (코덱스) · ## 클로드 CLI · ## 클로드 앱

재료(두 맥):
  - GPT 워크 = ~/.codex/sessions (originator codex_work_desktop 등 사람 세션만)
  - 클로드 CLI = ~/.claude/projects/*/*.jsonl (자동화 -p 세션 제외)
  - 클로드 앱 = 맥 데스크톱 앱이 obsidian-write `log_day`로 남긴 볼트 클로드앱-수신함.md 줄(실시간)
    + 폰까지 전부는 claude.ai 데이터 내보내기 zip 배치(claude_export.py, 있으면 그날은 이쪽 우선)
  - PLAUD = 볼트 회의록/
세션별로 시각·제목·요청·결과 한두 줄만 남긴다. 원문은 원래 폴더에 있고 경로만 적는다.
결과 요약은 하루 한 번 구독 claude -p(sonnet). 토큰·키 모양 문자열은 지운다.

cron(회사맥): 매일 00:15 전날 기록(--yesterday). 드라이런: DRY_RUN=1 python day_log.py [YYYY-MM-DD]
상대 맥 수집용: python day_log.py collect YYYY-MM-DD   (JSON 출력)
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HOME = Path.home()
VAULT = Path(os.getenv("VAULT_DIR", str(
    HOME / "Library/Mobile Documents/iCloud~md~obsidian/Documents/jay")))
LOG_DIR = VAULT / "20-Areas/Productivity/하루기록"
APP_INBOX = LOG_DIR / "클로드앱-수신함.md"
MEETINGS_DIR = VAULT / "10-Projects/다니엘프로젝트/회의록"
MAC = "집" if os.getenv("USER") == "jay" else "회사"
PEER = {"dp-tech-jhs": "jay@100.79.115.1", "jay": "dp-tech-jhs@100.75.205.84"}.get(os.getenv("USER", ""))
PEER_DIR = {"dp-tech-jhs": "~/daily-automation", "jay": "~/Documents/daily-automation"}.get(os.getenv("USER", ""))
HUMAN_CODEX = {"codex_work_desktop", "Codex Desktop", "codex-tui"}
WEEKDAY_KR = "월화수목금토일"

_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{30,}|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
    r"|\b[0-9a-f]{40,}\b)")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def scrub(s: str) -> str:
    return _SECRET.sub("[지움]", nfc(s))


def _kst(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
    except Exception:
        return None


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def active_minutes(times: list) -> int:
    """메시지 시각 사이 간격이 10분 이하인 구간만 더한다(자리 비움 제외). 최소 2분."""
    ts = sorted(times)
    total = sum((b - a).total_seconds() for a, b in zip(ts, ts[1:]) if (b - a).total_seconds() <= 600)
    return max(2, round(total / 60))


# ── 수집: 이 맥 ────────────────────────────────────────────────────
def _claude_cli(day: date) -> list[dict]:
    out = []
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        if "private-tmp" in f or "automation" in f or "bridge-worktrees" in f:
            continue  # 자동화·자동복구 브리지 세션은 Jay의 하루가 아니다
        try:
            if datetime.fromtimestamp(os.path.getmtime(f), KST).date() < day:
                continue
            rows = open(f, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        title, reqs, last, times, beats = "", [], "", [], []
        for raw in rows:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if o.get("type") == "ai-title":
                title = o.get("aiTitle") or title
            if o.get("type") == "custom-title":
                title = o.get("customTitle") or title
            t = _kst(str(o.get("timestamp") or ""))
            if not t or t.date() != day:
                continue
            if o.get("type") in ("user", "assistant"):
                beats.append(t)
            msg = o.get("message") or {}
            c = msg.get("content")
            if o.get("type") == "user" and not o.get("isMeta"):
                text = c if isinstance(c, str) else " ".join(
                    x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text")
                text = text.strip()
                if text and not text.startswith(("<", "Caveat:")) and "claude -p" not in text[:80]:
                    reqs.append(text)
                    times.append(t)
            elif o.get("type") == "assistant" and isinstance(c, list):
                txt = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if txt.strip():
                    last = txt
                    times.append(t)
        if reqs:
            out.append({"src": "claude_cli", "mac": MAC, "title": title or _clip(reqs[0], 40),
                        "start": min(times).strftime("%H:%M"), "end": max(times).strftime("%H:%M"),
                        "active": active_minutes(beats or times),
                        "requests": [scrub(_clip(r, 400)) for r in reqs[:12]],
                        "last": scrub(_clip(last, 900)), "path": f.replace(str(HOME), "~")})
    return out


def _codex(day: date) -> list[dict]:
    names = {}
    try:
        for raw in open(HOME / ".codex/session_index.jsonl", encoding="utf-8"):
            try:
                o = json.loads(raw)
                names[o["id"]] = o.get("thread_name", "")
            except Exception:
                continue
    except OSError:
        pass
    out = []
    dirs = {(day - timedelta(days=i)).strftime("%Y/%m/%d") for i in range(0, 3)}
    for d in dirs:
        for f in glob.glob(str(HOME / ".codex/sessions" / d / "*.jsonl")):
            try:
                if datetime.fromtimestamp(os.path.getmtime(f), KST).date() < day:
                    continue
                rows = open(f, encoding="utf-8").read().splitlines()
            except OSError:
                continue
            origin, sid, reqs, last, times, beats = "", "", [], "", [], []
            for raw in rows:
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                p = o.get("payload") or {}
                if o.get("type") == "session_meta":
                    origin, sid = p.get("originator", ""), p.get("id", "")
                    continue
                t = _kst(str(o.get("timestamp") or ""))
                if t and t.date() == day:
                    beats.append(t)
                if not t or t.date() != day or p.get("type") != "message":
                    continue
                txt = " ".join(x.get("text", "") for x in (p.get("content") or []) if isinstance(x, dict))
                if p.get("role") == "user":
                    if txt.strip() and not txt.lstrip().startswith(("<", "#", "The following is the Codex")):
                        reqs.append(txt.strip())
                        times.append(t)
                elif p.get("role") == "assistant" and txt.strip():
                    last = txt
                    times.append(t)
            if origin in HUMAN_CODEX and reqs:
                out.append({"src": "codex", "mac": MAC, "title": names.get(sid) or _clip(reqs[0], 40),
                            "start": min(times).strftime("%H:%M"), "end": max(times).strftime("%H:%M"),
                            "active": active_minutes(beats or times),
                            "requests": [scrub(_clip(r, 400)) for r in reqs[:12]],
                            "last": scrub(_clip(last, 900)), "path": f.replace(str(HOME), "~")})
    return out


def collect_local(day: date) -> list[dict]:
    return _claude_cli(day) + _codex(day)


def collect_all(day: date) -> list[dict]:
    rows = collect_local(day)
    if PEER:
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", PEER,
                                f"cd {PEER_DIR} && /usr/bin/python3 day_log.py collect {day.isoformat()}"],
                               capture_output=True, text=True, timeout=90)
            if r.returncode == 0:
                rows += json.loads(r.stdout or "[]")
            else:
                print(f"[warn] 상대 맥 수집 실패: {r.stderr[-300:]}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 상대 맥 수집 스킵: {e}", file=sys.stderr)
    return sorted(rows, key=lambda r: r["start"])


# ── 회의·클로드 앱 ─────────────────────────────────────────────────
def meetings(day: date) -> list[dict]:
    try:
        sys.path.insert(0, str(HOME / "wf-sync"))
        import meeting_ctx
    except Exception:
        return []
    out = []
    for p in MEETINGS_DIR.rglob("*.md"):
        if p.name.startswith("."):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if meeting_ctx.meeting_date(p, text) != day:
            continue
        rel = p.relative_to(VAULT)
        out.append({"title": nfc(p.stem), "path": nfc(str(rel)),
                    "body": scrub(meeting_ctx.strip_meeting_noise(text)[:3000])})
    return sorted(out, key=lambda m: m["title"])


def app_lines(day: date) -> list[dict]:
    """맥 데스크톱 앱이 obsidian-write `log_day`로 수신함에 남긴 그날 줄을 세션 형식으로.
    형식: `- 2026-09-30 14:10 | 주제 | 결론` (시각은 도구가 서버에서 찍는다)."""
    try:
        text = nfc(APP_INBOX.read_text(encoding="utf-8"))
    except OSError:
        return []
    out: dict = {}  # 같은 대화에서 결론이 바뀌어 다시 남기면 주제가 같다 — 마지막 줄만(첫 시각 유지)
    for line in text.splitlines():
        m = re.match(rf"- {day.isoformat()}\s+(\d{{2}}:\d{{2}}|--:--)?\s*\|\s*([^|]+?)\s*(?:\|\s*(.+))?$", line.strip())
        if not m:
            continue
        hm = m.group(1) if m.group(1) and m.group(1) != "--:--" else "--:--"
        topic = scrub(m.group(2))
        first = out.get(topic, {}).get("start", hm)
        out[topic] = {"src": "claude_app", "mac": "앱", "title": topic, "start": first, "end": hm,
                      "active": 0, "requests": [topic], "last": scrub(m.group(3) or ""),
                      "path": str(APP_INBOX.relative_to(VAULT))}
    return list(out.values())


def app_sessions(day: date) -> list[dict] | None:
    """claude.ai 내보내기 zip에서 푼 그날 대화(claude_export.py 캐시). 캐시가 없으면 None."""
    try:
        data = json.loads((DATA_DIR / f"app-{day.isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("ok"):
        return None
    out = []
    for c in data.get("conversations") or []:
        reqs, last, times = [], "", []
        for m in c.get("messages") or []:
            t = _kst(str(m.get("created_at") or ""))
            txt = (m.get("text") or "").strip()
            if not t or t.date() != day or not txt:
                continue
            times.append(t)
            if m.get("sender") == "human":
                reqs.append(txt)
            else:
                last = txt
        if reqs:
            out.append({"src": "claude_app", "mac": "앱", "title": nfc(c.get("name") or "") or _clip(reqs[0], 40),
                        "start": min(times).strftime("%H:%M"), "end": max(times).strftime("%H:%M"),
                        "active": active_minutes(times),
                        "requests": [scrub(_clip(nfc(x), 400)) for x in reqs[:12]],
                        "last": scrub(_clip(nfc(last), 900)), "path": f"https://claude.ai/chat/{c['uuid']}"})
    return out


# ── 요약 (하루 한 번 구독 -p) ──────────────────────────────────────
def summarize(day: date, sessions: list[dict], meets: list[dict], app: list[str]) -> dict:
    if not (sessions or meets or app):
        return {}
    parts = []
    for i, s in enumerate(sessions, 1):
        reqs = "\n".join(f"  - {r}" for r in s["requests"])
        parts.append(f"[S{i}] {s['src']} · {s['mac']} · {s['start']}–{s['end']} · {s['title']}\n"
                     f"요청:\n{reqs}\n마지막 답:\n  {s['last']}")
    for i, m in enumerate(meets, 1):
        parts.append(f"[M{i}] 회의 · {m['title']}\n{m['body']}")
    if app:
        parts.append("[클로드 앱 수신함]\n" + "\n".join(app))
    prompt = f"""Jay(장홍석, 다니엘프로젝트 부대표·CPO)의 {day.isoformat()} 하루 기록을 정리한다.
아래는 그날 Jay가 AI 도구와 한 세션(S)과 회의(M)의 요청·마지막 답·회의록이다.

출력은 JSON 객체 하나만:
{{"overview": ["...", "..."], "titles": {{"S1": "...", ...}}, "sessions": {{"S1": "...", ...}}, "categories": {{"S1": "회사", ...}}, "meetings": {{"M1": "...", ...}}}}

overview: 그날 Jay가 실제로 한 일의 큰 줄기 2~4줄(각 ~60자). 무엇을 결정·완성·논의했는지.
titles: 세션마다 무슨 일이었는지 한국어 명사구 제목(8~20자). 영어·명령문·"#" 머리말 금지.
sessions: 세션마다 무엇을 했고 어떻게 끝났는지 한두 문장(~80자). 요청만 있고 결론이 없으면 "진행 중"이라고 쓴다.
categories: 세션마다 하나 — "회사"(다니엘·데이원·조직·회사 대시보드 등 회사 일), "개인 툴"(개인 자동화·봇·SNS 트래커·자산 등 Jay 개인 도구 개발), "글쓰기·건강"(글쓰기·콘텐츠·건강·개인 생활), "기타".
meetings: 회의마다 결론·결정 한두 문장(~80자). 회의 이름을 앞에 되풀이하지 않는다. 결론이 없으면 "논의만"이라고 쓴다.
규칙: 재료에 있는 사실만. 평가·조언·추측 금지. 사람 이름은 재료에 적힌 그대로. 해요체 대신 간결한 기록체(~했다, ~함).

{chr(10).join(parts)[:300000]}"""
    lib = str(HOME / "metrics-exchange/lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import claude_p
        raw = claude_p.ask(prompt, model="sonnet", job="day_log", timeout=600) or ""
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 요약 실패: {e}", file=sys.stderr)
        return {}
    t = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    a, b = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[a:b + 1]) if a != -1 and b > a else {}
    except ValueError:
        return {}


# ── 렌더·저장 ──────────────────────────────────────────────────────
_SRC_TAG = {"codex": "GPT", "claude_cli": "CLI", "claude_app": "앱"}
_CATS = ("회사", "개인 툴", "글쓰기·건강", "기타")
_MEET_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\s*")


def render(day: date, sessions: list[dict], meets: list[dict], app: list[str], summ: dict) -> str:
    """보는 순서: 요약 → 회의 → AI 작업(분류별 한 줄) → 접힌 원문 경로.
    GPT·CLI·앱 세션은 출처 구분 없이 한 타임라인으로 합치고 출처는 꼬리표로만 붙인다."""
    sm, mm = summ.get("sessions") or {}, summ.get("meetings") or {}
    titles, cats = summ.get("titles") or {}, summ.get("categories") or {}
    out = ["---", f"date: {day.isoformat()}", "type: 하루기록",
           f"generated: {datetime.now(KST):%Y-%m-%d %H:%M}", "---", "",
           f"# {day.month}/{day.day} {WEEKDAY_KR[day.weekday()]} 하루 기록", ""]
    if summ.get("overview"):
        out += ["## 요약", ""] + [f"- {scrub(str(x))}" for x in summ["overview"]] + [""]
    if meets:
        out += [f"## 회의 {len(meets)}건", ""]
        for i, m in enumerate(meets, 1):
            name = _MEET_DATE.sub("", m["title"]).split(" — ")[0]
            res = mm.get(f"M{i}", "")
            out.append(f"- [[{m['path'][:-3]}|{name}]]" + (f" — {scrub(res)}" if res else ""))
        out.append("")
    if sessions:
        total = sum(s.get("active", 0) for s in sessions)
        out += [f"## AI 작업 {len(sessions)}건 · {total / 60:.1f}시간", ""]
        groups: dict = {}
        for i, s in enumerate(sessions, 1):
            c = cats.get(f"S{i}", "기타")
            groups.setdefault(c if c in _CATS else "기타", []).append((i, s))
        for c in _CATS:
            rows = groups.get(c)
            if not rows:
                continue
            out += [f"### {c}", ""]
            for i, s in rows:
                title = scrub(titles.get(f"S{i}") or s["title"])
                res = sm.get(f"S{i}") or (s["last"] if s["src"] == "claude_app" and s["last"] else "") \
                    or _clip(s["requests"][0], 80)
                tag = _SRC_TAG.get(s["src"], s["src"]) + ("" if s["src"] == "claude_app" else f"·{s['mac']}맥")
                mins = f" {s['active']}분" if s.get("active") else ""
                out.append(f"- {s['start']} **{title}** — {scrub(res)} `{tag}{mins}`")
            out.append("")
    if app:
        out += ["## 클로드 앱 메모", ""] + app + [""]
    if sessions:
        out += ["> [!note]- 원문 위치", ">"]
        out += [f"> - {s['start']} {scrub(titles.get(f'S{i}') or s['title'])}: "
                + (f"[대화 열기]({s['path']})" if s["path"].startswith("http") else f"`{s['path']}`")
                for i, s in enumerate(sessions, 1)]
        out.append("")
    return "\n".join(out)


def write(day: date, text: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{day.isoformat()}.md"
    fd, tmp = tempfile.mkstemp(dir=str(LOG_DIR), prefix=".daylog-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(nfc(text))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


DATA_DIR = HOME / ".local/state/jay-desk/daylog"


def build(day: date) -> str:
    # 클로드 앱: 내보내기 캐시(폰+맥 전체)가 있으면 그걸, 없으면 맥 앱 수신함 줄
    app_rows = app_sessions(day)
    sessions = sorted(collect_all(day) + (app_rows if app_rows is not None else app_lines(day)),
                      key=lambda r: r["start"])
    meets = meetings(day)
    app: list[str] = []
    summ = summarize(day, sessions, meets, app)
    cats = summ.get("categories") or {}
    _LAST_DATA.update({"date": day.isoformat(), "meetings": len(meets),
                       "app": sum(s["src"] == "claude_app" for s in sessions),
                       "sessions": [{"title": s["title"], "src": s["src"], "active": s.get("active", 0),
                                     "category": cats.get(f"S{i}", "기타")}
                                    for i, s in enumerate(sessions, 1)]})
    return render(day, sessions, meets, app, summ)


_LAST_DATA: dict = {}


def save_data() -> None:
    """시간 거울용 수치(세션 제목·활동분·분류)를 회사맥 state에 남긴다."""
    if not _LAST_DATA:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{_LAST_DATA['date']}.json").write_text(
        json.dumps(_LAST_DATA, ensure_ascii=False), encoding="utf-8")


def week_logs(start: date, days: int = 7, cap: int = 30000) -> str:
    """start부터 days일의 하루 기록 본문(원문 경로 줄 제외)을 이어 붙인다. 월요일 브리프·주간회고 재료."""
    chunks, total = [], 0
    for i in range(days):
        body = read(start + timedelta(days=i))
        if not body:
            continue
        body = re.sub(r"^---\n.*?\n---\n", "", body, flags=re.S)
        body = "\n".join(l for l in body.splitlines()
                         if not l.strip().startswith(("- 원문:", ">")))
        if total + len(body) > cap:
            break
        chunks.append(body.strip())
        total += len(body)
    return "\n\n".join(chunks)


def time_mirror(start: date, days: int = 7) -> str:
    """한 주 AI 작업 시간 배분(메시지 간격 10분 이하만 합산). 데이터 없으면 ""."""
    rows, meets, app = [], 0, 0
    for i in range(days):
        try:
            d = json.loads((DATA_DIR / f"{(start + timedelta(days=i)).isoformat()}.json").read_text())
        except Exception:
            continue
        rows += d.get("sessions", [])
        meets += d.get("meetings", 0)
        app += d.get("app", 0)
    total = sum(r.get("active", 0) for r in rows)
    if not total:
        return ""
    by_cat: dict = {}
    for r in rows:
        by_cat[r.get("category") or "기타"] = by_cat.get(r.get("category") or "기타", 0) + r.get("active", 0)
    share = " · ".join(f"{k} {round(v * 100 / total)}%" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))
    by_title: dict = {}
    for r in rows:
        by_title[r["title"]] = by_title.get(r["title"], 0) + r.get("active", 0)
    top = sorted(by_title.items(), key=lambda x: -x[1])[:3]
    tops = " · ".join(f"{t} {m / 60:.1f}시간" for t, m in top)
    return (f"회의 {meets}건(PLAUD) · 클로드 앱 기록 {app}건 · AI 작업 {total / 60:.1f}시간\n"
            f"AI 작업 배분: {share}\n"
            f"가장 오래 붙잡은 것: {tops}\n"
            f"(메시지 간격 10분 이하만 합산한 실제 작업 시간. 슬랙·문서 읽기·대면 대화는 빠짐)")


def read(day: date) -> str:
    """아침 메시지 등에서 읽기용. 없으면 ""."""
    try:
        return nfc((LOG_DIR / f"{day.isoformat()}.md").read_text(encoding="utf-8"))
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    if argv and argv[0] == "collect":
        print(json.dumps(collect_local(date.fromisoformat(argv[1])), ensure_ascii=False))
        return 0
    if argv and argv[0] == "--yesterday":
        day = datetime.now(KST).date() - timedelta(days=1)
        try:  # 폰 대화 배치: 새 내보내기 zip을 먼저 풀어야 어제 기록에도 들어간다
            import claude_export
            pending = claude_export.ingest(log_exists=lambda d: bool(read(d)))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 클로드 내보내기 처리 실패: {e}", file=sys.stderr)
            pending = []
    else:
        day = date.fromisoformat(argv[0]) if argv else datetime.now(KST).date()
    text = build(day)
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        print(text)
        return 0
    print(f"✅ 하루 기록 저장: {write(day, text)}")
    save_data()
    if argv and argv[0] == "--yesterday":
        import claude_export
        claude_export.done(day)
        for d in [x for x in pending if x != day][:claude_export.REBUILD_CAP]:
            _LAST_DATA.clear()
            print(f"↻ 클로드 내보내기 반영해 {d} 다시 생성: {write(d, build(d))}")
            save_data()
            claude_export.done(d)
        # 한도(DAILY_LIMIT) 등으로 요약 없이 저장된 날이 있으면 하루 한 개씩 다시 채운다
        for back in (1, 2):
            d = day - timedelta(days=back)
            old = read(d)
            if old and "## 요약" not in old:
                _LAST_DATA.clear()
                print(f"↻ 요약 없는 {d} 다시 생성: {write(d, build(d))}")
                save_data()
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
