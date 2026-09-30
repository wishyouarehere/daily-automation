"""클로드 앱 대화 배치 — claude.ai 공식 '데이터 내보내기' zip을 하루 기록 재료로 (2026-09-30 Jay 결정).

맥 데스크톱 앱은 대화 중 obsidian-write `log_day`로 볼트 수신함에 한 줄씩 남긴다(실시간).
폰 대화는 그 도구가 없어서, Jay가 가끔 설정 → 개인정보 → 데이터 내보내기로 받은 zip을
양맥 ~/Downloads에 두면(최근 14일 zip 중 conversations.json이 든 것) 매일 밤 day_log가 이걸 읽어 날짜별 캐시(app-YYYY-MM-DD.json)로 풀고,
이미 만들어진 하루 기록 중 새 대화가 생긴 날을 다시 만든다(하룻밤 최대 REBUILD_CAP일, 나머지는 다음 밤).
캐시가 있는 날은 내보내기(폰+맥 전체)가 수신함 줄보다 우선한다 — 같은 대화가 두 번 나오지 않게.

(2026-09-30 claude.ai를 브라우저로 직접 읽는 방식은 Cloudflare 사람 확인에 막혀 폐기했다. 위장 금지.)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HOME = Path.home()
CACHE_DIR = HOME / ".local/state/jay-desk/daylog"
STATE = CACHE_DIR / "export-state.json"
INBOX = HOME / ".local/state/jay-desk/claude-export"  # 집맥에서 가져온 zip 보관
PEER = "jay@100.79.115.1"
REBUILD_CAP = 5
SINCE = date(2026, 9, 22)  # 하루 기록이 시작된 날. 그 전 대화는 풀지 않는다


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": [], "pending": []}


def _save_state(st: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _candidates() -> list[Path]:
    """이 맥 ~/Downloads와 집맥 ~/Downloads의 내보내기 zip(집맥 것은 INBOX로 복사)."""
    INBOX.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", PEER,
                            "find ~/Downloads -maxdepth 1 -name '*.zip' -mtime -14 2>/dev/null | while read f; do "
                            "unzip -l \"$f\" 2>/dev/null | grep -q conversations.json && echo \"$(stat -f %z \"$f\") $f\"; done"],
                           capture_output=True, text=True, timeout=30)
        for row in r.stdout.splitlines():
            size, _, remote = row.partition(" ")
            local = INBOX / f"{size}-{Path(remote).name}"  # 같은 이름의 새 내보내기도 구분
            if local.exists():
                continue
            part = local.with_suffix(".part")
            cp = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{PEER}:{remote}", str(part)],
                                timeout=600, check=False)
            if cp.returncode == 0 and part.stat().st_size == int(size):
                part.rename(local)  # 다 받은 것만 zip 이름으로 — 끊긴 반쪽 파일이 '처리됨'으로 굳지 않게
            else:
                part.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 집맥 내보내기 확인 실패: {e}", file=sys.stderr)
    cutoff = datetime.now().timestamp() - 14 * 86400
    files = [p for p in (HOME / "Downloads").glob("*.zip") if p.stat().st_mtime >= cutoff]
    for old in INBOX.glob("*.zip"):
        if old.stat().st_mtime < datetime.now().timestamp() - 30 * 86400:
            old.unlink(missing_ok=True)
    files = [p for p in files if _has_conversations(p)] + list(INBOX.glob("*.zip"))
    return sorted(files, key=lambda p: p.stat().st_mtime)


def _has_conversations(zp: Path) -> bool:
    try:
        with zipfile.ZipFile(zp) as z:
            return any(n.endswith("conversations.json") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _conversations(zp: Path) -> list[dict] | None:
    try:
        with zipfile.ZipFile(zp) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            return json.loads(z.read(name)) if name else None
    except (zipfile.BadZipFile, OSError, ValueError, KeyError):
        return None


def _kst(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def _text(m: dict) -> str:
    parts = [c.get("text", "") for c in (m.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return (" ".join(parts) or m.get("text") or "").strip()


def split_by_day(convs: list[dict]) -> dict[str, list[dict]]:
    """대화를 메시지 날짜(KST)별로 나눈다. 자정을 넘긴 대화는 두 날에 모두 들어간다."""
    days: dict[str, dict[str, dict]] = {}
    for c in convs:
        for m in c.get("chat_messages") or []:
            t = _kst(m.get("created_at") or "")
            txt = _text(m)
            if not t or not txt or t.date() < SINCE:
                continue
            conv = days.setdefault(t.date().isoformat(), {}).setdefault(
                c["uuid"], {"uuid": c["uuid"], "name": c.get("name") or "", "project": "", "messages": []})
            conv["messages"].append({"sender": m.get("sender"), "created_at": m.get("created_at"), "text": txt})
    return {d: list(v.values()) for d, v in days.items()}


def ingest(log_exists=lambda d: False) -> list[date]:
    """새 zip이 있으면 캐시를 갱신하고, 다시 만들어야 할 날짜 목록(대기열 포함)을 돌려준다."""
    st = _load_state()
    for zp in _candidates():
        sig = hashlib.sha1(f"{zp.name.split('-', 1)[-1] if zp.parent == INBOX else zp.name}:{zp.stat().st_size}".encode()).hexdigest()
        if sig in st["seen"]:
            continue
        convs = _conversations(zp)
        st["seen"].append(sig)
        if convs is None:
            print(f"[warn] 내보내기 파일이 아님: {zp.name}", file=sys.stderr)
            continue
        for d, rows in split_by_day(convs).items():
            path = CACHE_DIR / f"app-{d}.json"
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = {}
            before = {(c["uuid"], len(c["messages"])) for c in old.get("conversations") or []}
            after = {(c["uuid"], len(c["messages"])) for c in rows}
            if after - before:
                path.write_text(json.dumps({"ok": True, "source": "export", "conversations": rows},
                                           ensure_ascii=False), encoding="utf-8")
                if log_exists(date.fromisoformat(d)) and d not in st["pending"]:
                    st["pending"].append(d)
        print(f"✅ 클로드 내보내기 반영: {zp.name} ({len(convs)}개 대화)")
    st["pending"].sort()
    _save_state(st)
    return [date.fromisoformat(d) for d in st["pending"]]


def done(day: date) -> None:
    st = _load_state()
    st["pending"] = [d for d in st["pending"] if d != day.isoformat()]
    _save_state(st)


if __name__ == "__main__":
    print(ingest())
