"""아침·저녁 한 통 공용 — 미결·기록없음 문장 필터, 같은 제목 회의록 매칭, 발송 본문 월별 기록.

규칙의 의미: Jay에게 '아직 안 정해진 것·기록 없는 것·담당 미정' 목록은 보내지 않는다(스트레스만 준다).
문장 단위로 그런 표현이 든 문장을 지운다. 규칙은 이 파일에 한 벌만 둔다.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

UNSETTLED_RE = re.compile(
    r"확인\s*필요|미정|미결|미확정|정해지지|정해진 (?:것|바|기록|내용|담당|일정)|정하지 (?:않|못)|"
    r"기록(?:이|은|도)? ?(?:없|남아 있지|확인되지)|(?:결과|회신|통보|전달|공유|후속)[^.。]{0,12}기록|"
    r"공유했는지|전달했는지|결론(?:이|은)? ?(?:나지|없|내려지지)|결정(?:이|은)? ?(?:나지|내려지지|안)|"
    r"확정(?:되지|은 아직|이 안)|아직 (?:정해|결정|확정|없|안 |결론|논의|미)|"
    r"담당(?:자|이|은|도)? ?(?:없|정해|미)|누가 [^.。]{0,12}(?:정하|맡)|"
    r"결정 대기|판단 대기|대기 중|여부(?:는|가|도)|논의가 필요|결정이 필요|정해야|결정해야|확인하세요|확인해 ?보세요")
_TAG_RE = re.compile(r"\s*\[(?:결정|대표|확인필요)\]")
_SENT_SPLIT = re.compile(r"(?<=[다요됨음])\.\s+|(?<=[다요])\s+(?=다만\s)")
GENERIC_TITLE_RE = re.compile(
    r"^(온보딩|미팅|회의|싱크|싱크\s*미팅|점심|식사|커피|커피챗|티타임|면담|논의|인사|sync|meeting|lunch|1on1|1:1|원온원)$",
    re.IGNORECASE)


def drop_unsettled(text) -> str:
    """미결·기록없음 문장을 지운 문자열. 남는 문장이 없으면 ''."""
    t = str(text or "").strip()
    if not t:
        return ""
    sents = [x.strip() for x in _SENT_SPLIT.split(t) if x.strip()]
    good = [x for x in sents if not UNSETTLED_RE.search(x)]
    if not good:
        return ""
    out = ". ".join(x.rstrip(".") for x in good)
    if t.rstrip().endswith("."):
        out += "."
    return _TAG_RE.sub("", out).strip()


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def norm_title(t: str) -> str:
    t = re.sub(r"^\s*(\[[^\]]*\]|\([^)]*\))\s*", "", _nfc(t))
    return re.sub(r"[\s\[\]()·\-—_]+", "", t).lower()


def same_title_notes(event_title: str, meetings_dir: Path, today: date, days: int = 60,
                     per_cap: int = 1500, max_notes: int = 2) -> str:
    """내일 일정과 제목이 완전히 같은 지난 회의록 본문. 일반명사 제목·없으면 ''."""
    title = re.sub(r"^\d{1,2}:\d{2}\s+|^종일\s+", "", event_title or "").strip()
    nt = norm_title(title)
    if not nt or GENERIC_TITLE_RE.match(title.strip()):
        return ""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "wf-sync"))
        import meeting_ctx as M
    except Exception:  # noqa: BLE001
        return ""
    rows = []
    for p in meetings_dir.rglob("*.md"):
        if p.name.startswith(".") or M.is_private(p):
            continue
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s*(.*)$", _nfc(p.stem))
        if not m:
            continue
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d >= today + timedelta(days=1) or d < today - timedelta(days=days):
            continue
        head = re.split(r"\s+[—–]\s+", m.group(4), maxsplit=1)[0]
        if norm_title(head) == nt:
            rows.append((d, p))
    rows.sort(reverse=True)
    out = []
    for d, p in rows[:max_notes]:
        try:
            body = M.strip_meeting_noise(p.read_text(encoding="utf-8", errors="ignore"))[:per_cap]
        except Exception:  # noqa: BLE001
            continue
        out.append(f"#### {d.isoformat()} {_nfc(p.stem)}\n{body}")
    return "\n\n".join(out)


def archive_sent(kind: str, body: str, meta: dict | None = None, dry: bool = False) -> None:
    """발송 본문을 월별 jsonl로 남긴다(사후 점검용). 실패해도 무시."""
    try:
        d = Path.home() / ".local/state/jay-desk/sent_archive"
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        row = {"at": now.isoformat(timespec="minutes"), "kind": kind, "dryrun": dry, "body": body,
               "meta": {k: v for k, v in (meta or {}).items() if k not in ("quote", "candidate")}}
        with (d / f"{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
