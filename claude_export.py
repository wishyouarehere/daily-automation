"""폰 클로드 앱 대화 배치 — claude.ai 공식 '데이터 내보내기' zip으로 하루 기록을 보탠다 (2026-09-30 Jay 결정).

맥 데스크톱 앱은 대화 중 obsidian-write `log_day`로 볼트 수신함에 한 줄씩 남긴다(정본).
폰에는 그 도구가 없어서, Jay가 가끔 받은 내보내기 zip을 양맥 ~/Downloads에 두면(최근 14일, conversations.json이
든 것) 매일 밤 day_log가 날짜별 요약 캐시(app-YYYY-MM-DD.json, 세션 형식·요청 12개·글자 수 상한)로 줄여 둔다.
day_log는 수신함 줄이 이미 있는 대화(시각이 겹침)는 빼고 나머지만 보탠다 — 폰 대화만 더해지는 셈.
새 대화가 생긴 지난 하루 기록은 하룻밤 최대 REBUILD_CAP일만 다시 만든다(AI 사용량 보호).

🔴 원문 사본을 남기지 않는다: 집맥 zip은 임시 파일로 받아 처리 직후 지우고, 처리 이력은 이름·크기 서명만.
캐시는 30일 지나면 지운다. (claude.ai 브라우저 직접 읽기는 Cloudflare에 막혀 폐기 — 위장 금지.)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

import day_log
from day_log import _clip, _kst, active_minutes, nfc, scrub

HOME = Path.home()
CACHE_DIR = day_log.DATA_DIR
STATE = CACHE_DIR / "export-state.json"
PEER = "jay@100.79.115.1"
REBUILD_CAP = 2
KEEP_DAYS = 30
RECENT = 14 * 86400


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": [], "pending": []}


def _save_state(st: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _sig(where: str, name: str, size: int) -> str:
    return hashlib.sha1(f"{where}:{name}:{size}".encode()).hexdigest()


def _conversations(zp: Path) -> list[dict] | None:
    try:
        with zipfile.ZipFile(zp) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            return json.loads(z.read(name)) if name else None
    except (zipfile.BadZipFile, OSError, ValueError, KeyError):
        return None


def _text(m: dict) -> str:
    parts = [c.get("text", "") for c in (m.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return (" ".join(parts) or m.get("text") or "").strip()


def sessions_by_day(convs: list[dict], since: date) -> dict[str, list[dict]]:
    """대화를 KST 날짜별 세션(day_log 형식)으로 줄인다. 자정을 넘긴 대화는 두 날에 나뉘어 들어간다."""
    raw: dict[str, dict[str, dict]] = {}
    for c in convs:
        for m in c.get("chat_messages") or []:
            t = _kst(str(m.get("created_at") or ""))
            txt = _text(m)
            if not t or not txt or t.date() < since:
                continue
            r = raw.setdefault(t.date().isoformat(), {}).setdefault(
                c["uuid"], {"name": nfc(c.get("name") or ""), "reqs": [], "last": "", "times": []})
            r["times"].append(t)
            if m.get("sender") == "human":
                r["reqs"].append(txt)
            else:
                r["last"] = txt
    out: dict[str, list[dict]] = {}
    for d, convs_ in raw.items():
        for uuid, r in convs_.items():
            if not r["reqs"]:
                continue
            out.setdefault(d, []).append({
                "src": "claude_app", "mac": "앱", "title": r["name"] or _clip(r["reqs"][0], 40),
                "start": min(r["times"]).strftime("%H:%M"), "end": max(r["times"]).strftime("%H:%M"),
                "active": active_minutes(r["times"]),
                "requests": [scrub(_clip(nfc(x), 400)) for x in r["reqs"][:12]],
                "last": scrub(_clip(nfc(r["last"]), 900)), "path": f"https://claude.ai/chat/{uuid}"})
    return out


def _apply(convs: list[dict], st: dict, log_exists) -> None:
    for d, rows in sessions_by_day(convs, day_log.LOG_START).items():
        path = CACHE_DIR / f"app-{d}.json"
        try:
            old = json.loads(path.read_text(encoding="utf-8")).get("sessions") or []
        except (OSError, ValueError):
            old = []
        key = lambda s: (s["path"], len(s["requests"]), s["end"])
        if {key(s) for s in rows} - {key(s) for s in old}:
            path.write_text(json.dumps({"ok": True, "sessions": rows}, ensure_ascii=False), encoding="utf-8")
            if log_exists(date.fromisoformat(d)) and d not in st["pending"]:
                st["pending"].append(d)


def ingest(log_exists=lambda d: False) -> list[date]:
    """새 zip이 있으면 캐시를 갱신하고, 다시 만들 날짜 목록(지난 대기열 포함)을 돌려준다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    st = _load_state()
    now = time.time()
    # 이 맥 ~/Downloads
    for zp in (HOME / "Downloads").glob("*.zip"):
        try:
            size, mtime = zp.stat().st_size, zp.stat().st_mtime
        except OSError:
            continue
        sig = _sig("local", zp.name, size)
        if mtime < now - RECENT or sig in st["seen"]:
            continue
        convs = _conversations(zp)
        if convs is not None:  # 내보내기가 아닌 zip은 서명만 남기고 넘어간다
            _apply(convs, st, log_exists)
            print(f"✅ 클로드 내보내기 반영: {zp.name} ({len(convs)}개 대화)")
        st["seen"].append(sig)
    # 집맥 ~/Downloads — 임시 파일로 받아 처리 직후 지운다
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", PEER,
                            "find ~/Downloads -maxdepth 1 -name '*.zip' -mtime -14 2>/dev/null | while read f; do "
                            "unzip -l \"$f\" 2>/dev/null | grep -q conversations.json && echo \"$(stat -f %z \"$f\") $f\"; done"],
                           capture_output=True, text=True, timeout=60)
        for row in r.stdout.splitlines():
            size, _, remote = row.partition(" ")
            sig = _sig("home", Path(remote).name, int(size))
            if sig in st["seen"]:
                continue
            fd, tmp = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            try:
                cp = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{PEER}:{remote}", tmp],
                                    timeout=600, check=False)
                convs = _conversations(Path(tmp)) if cp.returncode == 0 else None
                if convs is not None and os.path.getsize(tmp) == int(size):
                    _apply(convs, st, log_exists)
                    st["seen"].append(sig)
                    print(f"✅ 클로드 내보내기 반영(집맥): {Path(remote).name} ({len(convs)}개 대화)")
            finally:
                os.unlink(tmp)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 집맥 내보내기 확인 실패: {e}", file=sys.stderr)
    for old in CACHE_DIR.glob("app-*.json"):  # 대화 요약 캐시도 오래 두지 않는다
        if old.stat().st_mtime < now - KEEP_DAYS * 86400:
            old.unlink(missing_ok=True)
    st["pending"].sort()
    _save_state(st)
    return [date.fromisoformat(d) for d in st["pending"]]


def done(day: date) -> None:
    st = _load_state()
    st["pending"] = [d for d in st["pending"] if d != day.isoformat()]
    _save_state(st)


if __name__ == "__main__":
    print(ingest())
