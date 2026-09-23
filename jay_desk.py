"""치프봇 아침·저녁 한 통의 공용 부품 (2026-09-23 개편).

설계 원칙(Jay 결정, 2026-09-23):
- 봇은 파트너다. 먼저 보내는 메시지는 확인된 사실 + 문장 하나 + 질문 하나.
- 판단은 출처를 댈 수 있을 때만 아침에 1건. 근거가 없으면 칸을 비운다.
- 문장은 볼트 「다시-여기」(again_bank)에서 원문 그대로 고른다. 2주 안 재사용 금지.
- 주말은 문장과 질문만.
LLM은 구독 claude -p(metrics-exchange claude_p.ask)만 쓴다.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

import again_bank  # noqa: E402

KST = timezone(timedelta(hours=9))
STATE = Path.home() / ".local/state/jay-desk"
USED = STATE / "again_used.json"
REUSE_DAYS = 14
WEEKDAY_KR = "월화수목금토일"


def now() -> datetime:
    return datetime.now(KST)


def is_weekend(d: date) -> bool:
    """쉬는 날(토·일·공휴일·대체공휴일·설정한 휴무)이면 True — 주말 모드(문장·질문만)."""
    try:
        import work_calendar
        return not work_calendar.is_workday(d)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 근무일 판정 실패 — 토·일만 쉬는 날로 봄: {e}", file=sys.stderr)
        return d.weekday() >= 5


def date_label(d: date) -> str:
    """'9/24 목' + 공휴일이면 ' · 추석 연휴'."""
    label = f"{d.month}/{d.day} {WEEKDAY_KR[d.weekday()]}"
    try:
        import work_calendar
        reason = work_calendar.day_off_reason(d)
        if reason and reason not in ("토요일", "일요일"):
            label += f" · {reason}"
    except Exception:
        pass
    return label


# ── 텔레그램 (치프봇 직접 발송 — 개인 메시지라 운영 게이트를 거치지 않는다) ──
def send_telegram(text: str) -> int | None:
    """발송하고 message_id를 돌려준다(드라이런이면 None)."""
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        print(text)
        return None
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    r.raise_for_status()
    return (r.json().get("result") or {}).get("message_id")


# ── 메시지 ↔ 문장 기록 (치프봇이 👎·❤️·답장을 문장에 연결) ─────────────
# 치프봇은 집맥에서 돈다. 반응 업데이트엔 메시지 본문이 없으므로 message_id→문장을
# 집맥 ~/.local/state/jay-desk/messages.jsonl에 남긴다(회사맥이면 SSH로 추가).
MESSAGES = STATE / "messages.jsonl"
HOME_MAC = "jay@100.79.115.1"


def record_message(mid: int | None, kind: str, text: str, section: str = "", ref: str = "") -> None:
    if not mid:
        return
    line = json.dumps({"mid": mid, "kind": kind, "text": text, "section": section,
                       "ref": ref, "date": now().date().isoformat()}, ensure_ascii=False)
    if os.getenv("USER") == "jay":
        STATE.mkdir(parents=True, exist_ok=True)
        with open(MESSAGES, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return
    import subprocess
    try:
        subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", HOME_MAC,
                        "mkdir -p ~/.local/state/jay-desk && cat >> ~/.local/state/jay-desk/messages.jsonl"],
                       input=line + "\n", text=True, timeout=20, check=True)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 메시지 기록 실패(반응 연결 불가, 답장은 동작): {e}", file=sys.stderr)


OFFERED = STATE / "underlines_offered.json"


def offered_underlines() -> set[str]:
    try:
        return set(json.loads(OFFERED.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_offered(uid: str) -> None:
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        return
    STATE.mkdir(parents=True, exist_ok=True)
    ids = sorted(offered_underlines() | {uid})
    OFFERED.write_text(json.dumps(ids[-2000:], ensure_ascii=False), encoding="utf-8")


def letters_within(part: str, whole: str) -> bool:
    """part가 whole의 연속 구간인가(공백·줄바꿈 무시). 발췌가 원문 글자를 바꾸지 않았는지 검증."""
    p = re.sub(r"\s+", "", part or "")
    return bool(p) and p in re.sub(r"\s+", "", whole or "")


def same_letters(a: str, b: str) -> bool:
    """공백·줄바꿈만 다르고 글자는 같은가(OCR 띄어쓰기 교정 검증)."""
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")


def emit_ledger(event_type: str, title: str, meta: dict) -> None:
    """운영 원장 기록(L1). 발송 여부와 무관하다."""
    try:
        from exec_emitter import emit_event
        emit_event(source="jay_desk", domain="ops", event_type=event_type,
                   title=title, decision_level="L1", metadata=meta)
    except Exception:
        pass


# ── 캘린더 ─────────────────────────────────────────────────────────
def calendar_lines(d: date) -> list[str]:
    """['11:00  데일리 스크럼', ...]. 실패하면 None 대신 빈 리스트 + 경고."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(
            token=None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        start = datetime(d.year, d.month, d.day, tzinfo=KST)
        items = svc.events().list(
            calendarId=os.environ["GOOGLE_CALENDAR_ID"],
            timeMin=start.isoformat(), timeMax=(start + timedelta(days=1)).isoformat(),
            singleEvents=True, orderBy="startTime").execute().get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 캘린더 조회 실패: {e}", file=sys.stderr)
        return []
    out = []
    for e in items:
        s = e["start"].get("dateTime", e["start"].get("date", ""))
        t = datetime.fromisoformat(s).astimezone(KST).strftime("%H:%M") if "T" in s else "종일"
        out.append(f"{t}  {e.get('summary') or '(제목 없음)'}")
    return out


def condition_line() -> str:
    """건강 스냅샷 한 줄(있을 때만). 수치는 측정값 그대로."""
    try:
        p = Path.home() / "metrics-exchange/snapshots/health.json"
        payload = json.loads(p.read_text())
        d = payload.get("data") or {}
        if d.get("score") is None:
            return ""
        ts = datetime.fromisoformat(payload["ts"])
        if (datetime.now(ts.tzinfo) - ts).total_seconds() > 36 * 3600:
            return ""
        note = (d.get("reds") or d.get("yellows") or [""])[0]
        return f"{d.get('light', '')} 컨디션 {d['score']}" + (f" — {note}" if note else "")
    except Exception:
        return ""


# ── 문장 선택 ──────────────────────────────────────────────────────
def _used() -> list[dict]:
    try:
        return json.loads(USED.read_text(encoding="utf-8"))
    except Exception:
        return []


def quote_candidates(exclude_texts: set[str] | None = None) -> list[dict]:
    """최근 14일 안 쓴 문장. 전부 썼으면 가장 오래전에 쓴 순으로 되살린다."""
    lines = again_bank.all_lines()
    cutoff = (now().date() - timedelta(days=REUSE_DAYS)).isoformat()
    recent = {u["text"] for u in _used() if u.get("date", "") > cutoff}
    exclude = recent | (exclude_texts or set())
    cands = [l for l in lines if l["text"] not in exclude]
    if len(cands) < 5:
        last = {}
        for u in _used():
            last[u["text"]] = u.get("date", "")
        cands = sorted((l for l in lines if l["text"] not in (exclude_texts or set())),
                       key=lambda l: last.get(l["text"], ""))[:12]
    return cands


def mark_used(q: dict, slot: str) -> None:
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        return
    STATE.mkdir(parents=True, exist_ok=True)
    rows = _used()[-400:]
    rows.append({"date": now().date().isoformat(), "slot": slot,
                 "section": q["section"], "text": q["text"]})
    USED.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")


def used_today() -> set[str]:
    t = now().date().isoformat()
    return {u["text"] for u in _used() if u.get("date") == t}


def pick_quote(cands: list[dict], qid: str | None) -> dict | None:
    by_id = {c["id"]: c for c in cands}
    if qid and qid in by_id:
        return by_id[qid]
    return random.choice(cands) if cands else None


def render_quote(q: dict | None) -> str:
    if not q:
        return ""
    return f"<i>{escape(q['text'], quote=False)}</i>\n— {escape(q['section'], quote=False)}"


def quotes_for_prompt(cands: list[dict]) -> str:
    return "\n".join(f"[{c['id']}] {c['text']}" for c in cands)


# ── LLM (구독 claude -p) ───────────────────────────────────────────
def ask_json(prompt: str, job: str) -> dict:
    lib = str(Path.home() / "metrics-exchange/lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import claude_p
        raw = claude_p.ask(prompt, model="sonnet", job=job, timeout=300) or ""
        if not raw:
            print(f"[warn] LLM 실패: {claude_p.last_error()}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] LLM 호출 예외: {e}", file=sys.stderr)
        return {}
    t = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e <= s:
        return {}
    try:
        return json.loads(t[s:e + 1])
    except Exception:
        return {}


def clean(s) -> str:
    """모델 출력 한 줄 → 텔레그램 HTML 안전 텍스트."""
    s = re.sub(r"\*\*|__|`", "", str(s or ""))
    s = re.sub(r"^\s*#+\s+", "", s).strip()
    return escape(s, quote=False)


TONE = """말투: 옆자리 동료가 건네는 공손한 해요체. 담백하고 구체적으로.
금지: 명령·지시조, 훈계, 압박 어휘("반드시", "마지노선", "직격", "놓치면"), 과장, 영혼 없는 칭찬,
"~하시길 권해드려요" 같은 보고서체, 레벨 태그, 이모지, 마크다운 기호.
사실은 재료에 있는 것만 쓴다. 재료에 없는 사람·회의·결정·의도를 짐작해 만들지 않는다.
질문도 재료에 없는 사건(어긋남·갈등·실패 등)을 전제하지 않는다.
실수나 놓친 일을 질문의 중심에 두지 않는다. 꼭 다뤄야 하면 탓이 아니라 배움 쪽으로 묻는다."""
