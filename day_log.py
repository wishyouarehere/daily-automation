"""하루 기록 — Jay의 하루를 날짜별 볼트 파일 하나로 묶는다 (2026-09-23 Jay 결정).

  볼트 20-Areas/Productivity/하루기록/YYYY-MM-DD.md
    ## 요약 · ## 회의 (PLAUD) · ## GPT 워크 (코덱스) · ## 클로드 CLI · ## 클로드 앱

재료(두 맥):
  - GPT 워크 = ~/.codex/sessions (originator codex_work_desktop 등 사람 세션만)
  - 클로드 CLI = ~/.claude/projects/*/*.jsonl (자동화 -p 세션 제외)
  - 클로드 앱 = 앱이 구글 드라이브에 만든 `[하루기록] 날짜 시각 | 주제` 문서(폰·맥 공통)
    + 볼트 하루기록/클로드앱-수신함.md의 그날 줄(맥 앱 obsidian-write 보조 경로)
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


# ── 수집: 이 맥 ────────────────────────────────────────────────────
def _claude_cli(day: date) -> list[dict]:
    out = []
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        if "private-tmp" in f or "automation" in f:
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(f), KST).date() < day:
                continue
            rows = open(f, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        title, reqs, last, times = "", [], "", []
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
            origin, sid, reqs, last, times = "", "", [], "", []
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


def app_lines(day: date) -> list[str]:
    """수신함에서 그날 줄. 형식: `- 2026-09-23 14:10 | 주제 | 결론`."""
    try:
        text = nfc(APP_INBOX.read_text(encoding="utf-8"))
    except OSError:
        return []
    key = day.isoformat()
    return [l.strip() for l in text.splitlines() if l.strip().startswith(f"- {key}")]


def drive_app_entries(day: date) -> list[str]:
    """클로드 앱(폰·맥)이 구글 드라이브에 만든 `[하루기록] YYYY-MM-DD HH:MM | 주제` 문서들.

    앱의 드라이브 커넥터는 새 파일 생성만 되고, 자체 OAuth(drive.file)로는 남이 만든 파일을
    못 읽는다. 그래서 구독 claude(네이티브 바이너리)의 claude.ai 드라이브 커넥터로 읽는다.
    도구는 검색·읽기 두 개만 허용. 실패하면 []."""
    binary = next((b for b in ("/opt/homebrew/bin/claude", str(HOME / ".local/share/claude/claude"))
                   if os.path.exists(b)), None)
    if not binary:
        return []
    key = f"[하루기록] {day.isoformat()}"
    prompt = (f"Google Drive 커넥터로 제목에 '{key}'가 들어간 파일을 모두 찾고(ToolSearch로 "
              "Google_Drive search_files·read_file_content 도구를 먼저 불러온다) 각 파일 내용을 읽는다. "
              "출력은 JSON 배열 하나만: [{\"title\": \"...\", \"content\": \"...\"}]. "
              "없으면 []. 파일을 만들거나 고치지 않는다.")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        r = subprocess.run([binary, "-p", prompt, "--model", "haiku", "--output-format", "text",
                            "--allowedTools", "ToolSearch,mcp__claude_ai_Google_Drive__search_files,"
                            "mcp__claude_ai_Google_Drive__read_file_content"],
                           capture_output=True, text=True, timeout=240, env=env, cwd="/tmp")
        t = r.stdout.strip()
        a = t.find("[")
        rows = json.JSONDecoder().raw_decode(t[a:])[0] if a != -1 else []
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 드라이브 수신함 읽기 실패: {e}", file=sys.stderr)
        return []
    out = []
    for row in rows:
        title = nfc(str(row.get("title", ""))).replace("[하루기록]", "").strip()
        body = " ".join(nfc(str(row.get("content", ""))).split())
        if title.startswith(day.isoformat()):
            out.append(scrub(f"- {title}" + (f" | {_clip(body, 300)}" if body and body not in title else "")))
    return sorted(out)


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
{{"overview": ["...", "..."], "sessions": {{"S1": "...", ...}}, "meetings": {{"M1": "...", ...}}}}

overview: 그날 Jay가 실제로 한 일의 큰 줄기 2~4줄(각 ~60자). 무엇을 결정·완성·논의했는지.
sessions: 세션마다 무엇을 했고 어떻게 끝났는지 한두 문장(~80자). 요청만 있고 결론이 없으면 "진행 중"이라고 쓴다.
meetings: 회의마다 결론·결정 한두 문장(~80자). 결론이 없으면 "논의만"이라고 쓴다.
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
_SRC_TITLE = {"codex": "GPT 워크 (코덱스)", "claude_cli": "클로드 CLI"}


def render(day: date, sessions: list[dict], meets: list[dict], app: list[str], summ: dict) -> str:
    sm, mm = summ.get("sessions") or {}, summ.get("meetings") or {}
    out = ["---", f"date: {day.isoformat()}", "type: 하루기록",
           f"generated: {datetime.now(KST):%Y-%m-%d %H:%M}", "---", "",
           f"# {day.month}/{day.day} {WEEKDAY_KR[day.weekday()]} 하루 기록", ""]
    if summ.get("overview"):
        out += ["## 요약", ""] + [f"- {scrub(str(x))}" for x in summ["overview"]] + [""]
    out += ["## 회의 (PLAUD)", ""]
    if meets:
        for i, m in enumerate(meets, 1):
            res = mm.get(f"M{i}", "")
            out.append(f"- **{m['title']}**" + (f" — {scrub(res)}" if res else "") + f" · [[{m['path'][:-3]}|회의록]]")
    else:
        out.append("- 없음")
    out.append("")
    for src in ("codex", "claude_cli"):
        rows = [(i, s) for i, s in enumerate(sessions, 1) if s["src"] == src]
        out += [f"## {_SRC_TITLE[src]}", ""]
        if not rows:
            out += ["- 없음", ""]
            continue
        for i, s in rows:
            res = sm.get(f"S{i}", "")
            out.append(f"- {s['start']}–{s['end']} **{s['title']}** ({s['mac']}맥)")
            out.append(f"  - 요청: {_clip(s['requests'][0], 160)}")
            if res:
                out.append(f"  - 결과: {scrub(res)}")
            out.append(f"  - 원문: `{s['path']}`")
        out.append("")
    out += ["## 클로드 앱", ""] + (app or ["- 없음 (앱 지침으로 남긴 기록이 없음)"]) + [""]
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


def build(day: date) -> str:
    sessions = collect_all(day)
    meets = meetings(day)
    app = sorted(set(app_lines(day) + drive_app_entries(day)))
    summ = summarize(day, sessions, meets, app)
    return render(day, sessions, meets, app, summ)


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
    else:
        day = date.fromisoformat(argv[0]) if argv else datetime.now(KST).date()
    text = build(day)
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        print(text)
        return 0
    print(f"✅ 하루 기록 저장: {write(day, text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
