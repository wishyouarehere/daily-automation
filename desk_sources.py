"""아침·저녁 치프 메시지의 재료 수집 — 확인된 사실만 모은다.

재료: 구글 캘린더(호출부) · 볼트 Daily(WorkFlowy 동기화본) · 볼트 회의록(PLAUD)
     · 야간참모 결정 후보(Slack·Notion·결정-로그 반영, staff-office state)
     · 결정 노트 리뷰 도래 · 코덱스·클로드 세션 제목과 첫 요청.
각 함수는 실패하면 빈 값을 돌려준다(한 소스 장애가 메시지를 막지 않는다).

CLI(상대 맥 세션 수집용): python desk_sources.py sessions 2026-09-22T00:00 2026-09-23T00:00
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HOME = Path.home()
VAULT = Path(os.getenv("VAULT_DIR", str(
    HOME / "Library/Mobile Documents/iCloud~md~obsidian/Documents/jay")))
PROJECT = VAULT / "10-Projects" / "다니엘프로젝트"
STAFF_STATE = HOME / "staff-office" / "state"
PEERS = {"dp-tech-jhs": "jay@100.79.115.1", "jay": "dp-tech-jhs@100.75.205.84"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _strip_fm(text: str) -> str:
    return re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S).strip()


# ── WorkFlowy 데일리 (볼트 Daily) ─────────────────────────────────
def workflowy_daily(day) -> str:
    """해당 날짜의 WorkFlowy 데일리 본문. 파일명 `9-23(수).md`(WorkFlowy)·`2026-09-23.md`(저녁 동기화)."""
    folder = PROJECT / "Daily"
    names = (f"{day.month}-{day.day}(", day.strftime("%Y-%m-%d"))
    chunks = []
    try:
        for p in sorted(folder.iterdir()):
            name = nfc(p.name)
            if name.startswith(names) and name.endswith(".md"):
                body = _strip_fm(nfc(p.read_text(encoding="utf-8")))
                body = re.sub(r"^#.*$", "", body, flags=re.M).strip()
                if len(body) > 15:
                    chunks.append(body)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Daily 읽기 실패: {e}", file=sys.stderr)
    return "\n\n".join(chunks)[:4000]


# ── 회의록 ─────────────────────────────────────────────────────────
def meetings(days: int, total_cap: int = 12000) -> str:
    """최근 N일 회의록(노이즈 제거). meeting_ctx(wf-sync) 재사용."""
    try:
        wf = str(HOME / "wf-sync")
        if wf not in sys.path:
            sys.path.insert(0, wf)
        import meeting_ctx
        return meeting_ctx.recent_meetings(days=days, per_cap=3000,
                                           total_cap=total_cap, max_files=6)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 회의록 수집 실패: {e}", file=sys.stderr)
        return ""


# ── 야간참모 결정 후보 ─────────────────────────────────────────────
def night_chief_candidates(today) -> str:
    """staff-office night_chief가 05:55에 남긴 오늘 결정 후보 원문(추천·반론·근거 포함)."""
    try:
        d = json.loads((STAFF_STATE / "night_chief_latest.json").read_text(encoding="utf-8"))
        if d.get("date") == today.isoformat():
            return re.sub(r"<[^>]+>", "", d.get("text", "")).strip()
    except Exception:
        pass
    return ""


# ── 결정 노트 리뷰 도래 (구 decision_debt 흡수) ───────────────────
def _frontmatter(path: Path) -> dict:
    try:
        m = re.match(r"^---\n(.*?)\n---", nfc(path.read_text(encoding="utf-8")), re.S)
    except Exception:
        return {}
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def decision_reviews(today) -> list[str]:
    """확정+리뷰일 도래, 검토중+7일 경과 결정 노트 → 한 줄씩."""
    folder = PROJECT / "결정"
    t = today.isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()
    out = []
    try:
        for p in sorted(folder.glob("*.md")):
            fm = _frontmatter(p)
            title = fm.get("결정질문") or nfc(p.stem)
            if fm.get("status") == "확정" and fm.get("리뷰일") and fm["리뷰일"] <= t:
                out.append(f"리뷰일 도래({fm['리뷰일']}): {title} — 반증조건 확인 필요 (결정/{nfc(p.name)})")
            elif fm.get("status") == "검토중" and fm.get("생성일") and fm["생성일"] <= week_ago:
                out.append(f"{fm['생성일']}부터 검토중: {title} — 확정/폐기 미정 (결정/{nfc(p.name)})")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 결정 노트 스캔 실패: {e}", file=sys.stderr)
    return out[:5]


# ── 코덱스·클로드 세션 (제목·첫 요청만) ──────────────────────────
def _first_request(lines_iter, extract) -> str:
    for raw in lines_iter:
        try:
            text = extract(json.loads(raw))
        except Exception:
            continue
        if text:
            text = " ".join(text.split())
            if not text.startswith("<") and len(text) > 3:
                return text[:140]
    return ""


def _claude_sessions(since: datetime, until: datetime) -> list[str]:
    out = []
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        if "private-tmp" in f or "automation" in f:
            continue
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(f), KST)
        except OSError:
            continue
        if not (since <= mt < until):
            continue
        title, first = "", ""
        try:
            with open(f, encoding="utf-8") as fh:
                rows = fh.readlines()
        except OSError:
            continue

        def _user(o):
            if o.get("type") != "user" or o.get("isMeta"):
                return ""
            c = (o.get("message") or {}).get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(x.get("text", "") for x in c
                                if isinstance(x, dict) and x.get("type") == "text")
            return ""
        first = _first_request(rows, _user)
        for raw in reversed(rows):
            if '"ai-title"' in raw or '"custom-title"' in raw:
                try:
                    o = json.loads(raw)
                    title = o.get("customTitle") or o.get("aiTitle") or ""
                    break
                except Exception:
                    continue
        if first and "claude -p" not in first:
            out.append(f"[클로드] {title or first[:40]} — {first}")
    return out


def _codex_sessions(since: datetime, until: datetime) -> list[str]:
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
    days = {(since + timedelta(days=i)).strftime("%Y/%m/%d")
            for i in range((until - since).days + 2)}
    for d in days:
        for f in glob.glob(str(HOME / ".codex/sessions" / d / "*.jsonl")):
            try:
                mt = datetime.fromtimestamp(os.path.getmtime(f), KST)
            except OSError:
                continue
            if not (since <= mt < until):
                continue

            def _user(o):
                p = o.get("payload") or {}
                if o.get("type") == "event_msg" and p.get("type") == "user_message":
                    return str(p.get("message") or "")
                if p.get("type") == "message" and p.get("role") == "user":
                    txt = " ".join(x.get("text", "") for x in (p.get("content") or [])
                                   if isinstance(x, dict))
                    return "" if txt.lstrip().startswith(("#", "<")) else txt
                return ""
            try:
                with open(f, encoding="utf-8") as fh:
                    first = _first_request(fh, _user)
            except OSError:
                continue
            m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", f)
            name = names.get(m.group(1), "") if m else ""
            if first:
                out.append(f"[코덱스] {name or first[:40]} — {first}")
    return out


def local_sessions(since: datetime, until: datetime) -> list[str]:
    return _claude_sessions(since, until) + _codex_sessions(since, until)


def ai_sessions(since: datetime, until: datetime, limit: int = 12) -> list[str]:
    """이 맥 + 상대 맥(SSH, 15초 제한) 세션 목록. 상대 맥 실패는 무시."""
    rows = local_sessions(since, until)
    peer = PEERS.get(os.getenv("USER", ""))
    if peer:
        remote_dir = "~/Documents/daily-automation" if peer.startswith("dp-tech-jhs") else "~/daily-automation"
        cmd = (f"cd {remote_dir} && /usr/bin/python3 desk_sources.py sessions "
               f"{since.isoformat()} {until.isoformat()}")
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", peer, cmd],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                rows += json.loads(r.stdout or "[]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 상대 맥 세션 수집 스킵: {e}", file=sys.stderr)
    seen, out = set(), []
    for r in rows:
        key = r[:60]
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:limit]


# ── 종이책 밑줄 후보 (집맥 book_archive, SSH) ──────────────────────
HOME_MAC = "jay@100.79.115.1"
SNS_PY = "/Users/jay/.venvs/sns-tracker-unified/bin/python"


def underline_candidates(exclude: set[str]) -> list[dict]:
    """[{id, text, captured}] — 집맥 book_underlines.py 결과. 실패하면 []."""
    ex = ",".join(sorted(exclude))[:20000]
    cmd = f"{SNS_PY} ~/daily-automation/book_underlines.py '{ex}'"
    try:
        if os.getenv("USER") == "jay":
            r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=60)
        else:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", HOME_MAC, cmd],
                               capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return json.loads(r.stdout or "[]")
        print(f"[warn] 밑줄 후보 실패: {r.stderr[-300:]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 밑줄 후보 스킵: {e}", file=sys.stderr)
    return []


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "sessions":
        s = datetime.fromisoformat(sys.argv[2])
        u = datetime.fromisoformat(sys.argv[3])
        if s.tzinfo is None:
            s, u = s.replace(tzinfo=KST), u.replace(tzinfo=KST)
        print(json.dumps(local_sessions(s, u), ensure_ascii=False))
    else:
        print(__doc__)
