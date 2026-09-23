"""「다시, 여기」 문장 뱅크 — 볼트 파일 하나를 DB처럼 읽고 쓴다.

정본: 볼트 `20-Areas/Personal/다시-여기.md`
  ## 섹션 제목        ← 출처·묶음 (아티팩트 탭)
  1. 문장            ← 한 줄 = 문장 하나 (번호는 쓸 때마다 다시 매긴다)

읽는 곳: 아침·저녁 메시지(morning_brief·daily_review), 아티팩트 재생성.
쓰는 곳: 치프봇 텔레그램 명령(집맥), 클로드 앱 obsidian-write, 이 파일 직접 편집.
양맥 모두 같은 볼트 상대경로를 쓰고 iCloud가 동기화한다.

CLI:
  python again_bank.py list
  python again_bank.py add "문장" [--section 스토아]
  python again_bank.py remove "문장 일부"
  python again_bank.py html > page.html     # 아티팩트 본문 생성
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

VAULT = Path(os.getenv("VAULT_DIR", str(
    Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/jay")))
BANK_REL = "20-Areas/Personal/다시-여기.md"
BANK = VAULT / BANK_REL
DEFAULT_SECTION = "모음"
EMPTY_MARK = "(아직 없음"
KEEP_EMPTY = {"아침 1분"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _read() -> str:
    return nfc(BANK.read_text(encoding="utf-8"))


def _split(text: str) -> tuple[str, list[dict]]:
    """(머리말, 섹션들). 섹션 = {title, lines, extra}. extra = 문장 아닌 줄(빈 표식 등)."""
    head, sections, cur = [], [], None
    for raw in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", raw)
        if m:
            cur = {"title": m.group(1).strip(), "lines": [], "extra": []}
            sections.append(cur)
            continue
        if cur is None:
            head.append(raw)
            continue
        li = re.match(r"^\s*(?:\d+[.)]|[-*])\s+(.+?)\s*$", raw)
        if li:
            cur["lines"].append(li.group(1).strip())
        elif raw.strip():
            cur["extra"].append(raw.strip())
    return "\n".join(head).rstrip() + "\n", sections


def load() -> list[dict]:
    """[{title, lines:[...]}] — 파일 순서 그대로."""
    _, sections = _split(_read())
    return [{"title": s["title"], "lines": list(s["lines"])} for s in sections]


def all_lines() -> list[dict]:
    """[{id, section, text}] — id는 '섹션#순번'(순번 1부터). 선택·이력 추적용."""
    out = []
    for s in load():
        for i, t in enumerate(s["lines"], 1):
            out.append({"id": f"{s['title']}#{i}", "section": s["title"], "text": t})
    return out


def _render(head: str, sections: list[dict]) -> str:
    today = date.today().isoformat()
    head = re.sub(r"^updated:.*$", f"updated: {today}", head, count=1, flags=re.M)
    parts = [head.rstrip()]
    for s in sections:
        block = [f"## {s['title']}", ""]
        if s["lines"]:
            block += [f"{i}. {t}" for i, t in enumerate(s["lines"], 1)]
        else:
            block += [e for e in s["extra"]] or ["(아직 없음)"]
        parts.append("\n".join(block))
    return "\n\n".join(parts) + "\n"


def _write(text: str) -> None:
    """NFC + 원자적 교체 + 재독 검증."""
    text = nfc(text)
    fd, tmp = tempfile.mkstemp(dir=str(BANK.parent), prefix=".again-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, BANK)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if _read() != text:
        raise RuntimeError("다시-여기 쓰기 검증 실패")


def _clean(sentence: str) -> str:
    s = nfc(sentence).strip().strip('"“”「」').strip()
    return re.sub(r"\s+", " ", s)


def _find_section(sections: list[dict], name: str | None) -> dict | None:
    if not name:
        return None
    key = re.sub(r"[\s·]", "", nfc(name))
    for s in sections:
        if re.sub(r"[\s·]", "", s["title"]) == key:
            return s
    for s in sections:
        if key and key in re.sub(r"[\s·]", "", s["title"]):
            return s
    return None


def add(sentence: str, section: str | None = None) -> dict:
    """문장 추가. 섹션이 없으면 새로 만든다(기본 '모음'). 중복이면 추가 안 함."""
    sentence = _clean(sentence)
    if not sentence:
        raise ValueError("빈 문장")
    head, sections = _split(_read())
    for s in sections:
        if sentence in s["lines"]:
            return {"status": "exists", "section": s["title"], "text": sentence}
    target = _find_section(sections, section)
    if target is None:
        title = nfc(section).strip() if section else DEFAULT_SECTION
        target = _find_section(sections, title)
        if target is None:
            target = {"title": title, "lines": [], "extra": []}
            sections.append(target)
    target["lines"].append(sentence)
    target["extra"] = [e for e in target["extra"] if not e.startswith(EMPTY_MARK)]
    _write(_render(head, sections))
    return {"status": "added", "section": target["title"], "text": sentence}


def remove(query: str) -> dict:
    """문장 삭제. 정확히 같거나, 일부가 한 문장에만 걸리면 지운다. 여러 개면 후보만 돌려준다."""
    q = _clean(query)
    if not q:
        raise ValueError("빈 검색어")
    head, sections = _split(_read())
    exact = [(s, t) for s in sections for t in s["lines"] if t == q]
    hits = exact or [(s, t) for s in sections for t in s["lines"] if q in t]
    if not hits:
        return {"status": "not_found", "query": q}
    if len(hits) > 1:
        return {"status": "ambiguous", "candidates": [t for _, t in hits[:5]]}
    s, t = hits[0]
    s["lines"].remove(t)
    if not s["lines"] and s["title"] not in KEEP_EMPTY:
        sections.remove(s)   # 텔레그램으로 생긴 묶음이 비면 지운다
    _write(_render(head, sections))
    return {"status": "removed", "section": s["title"], "text": t}


def summary() -> str:
    secs = load()
    n = sum(len(s["lines"]) for s in secs)
    return f"{n}문장 · " + " · ".join(f"{s['title']} {len(s['lines'])}" for s in secs)


# ── 아티팩트 페이지 ───────────────────────────────────────────────
_TEMPLATE = Path(__file__).with_name("again_page.html")


def render_html() -> str:
    """볼트 → 아티팩트 HTML. 템플릿의 /*DATA*/ 자리에 섹션 배열을 넣는다."""
    data = []
    for i, s in enumerate(load()):
        data.append({"id": f"s{i}", "tab": s["title"].replace(" · ", "·"),
                     "title": s["title"], "lines": s["lines"]})
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    stamp = f"{date.today():%Y-%m-%d} 볼트 기준"
    return (_TEMPLATE.read_text(encoding="utf-8")
            .replace("/*DATA*/[]", payload)
            .replace("{{STAMP}}", stamp))


# ── 아티팩트 동기화 표식 ──────────────────────────────────────────
# 헤드리스 claude -p는 Artifact 도구를 못 쓴다(2026-09-23 실측). 아티팩트 갱신은 Jay가 클로드 세션에서
# 「다시 여기 갱신」을 요청할 때 한다. sync-check는 게시본과 볼트가 다른지만 알려준다.
ARTIFACT_URL = "https://claude.ai/code/artifact/53c035c9-2e70-4721-b376-b65dea73f0a3"
PUBLISHED = Path.home() / ".local/state/jay-desk/again_published.json"


def _fingerprint() -> str:
    import hashlib
    body = json.dumps(load(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def sync_check() -> str:
    """게시본과 볼트가 다르면 세션에 줄 안내문, 같으면 ""."""
    try:
        cur = _fingerprint()
    except Exception:
        return ""
    try:
        last = json.loads(PUBLISHED.read_text()).get("fp", "")
    except Exception:
        last = ""
    if cur == last:
        return ""
    return (f"다시-여기: 볼트({summary()})가 아티팩트 게시본과 다름. "
            f"갱신 방법 = again_bank.py html → 아티팩트 {ARTIFACT_URL} 재게시 → again_bank.py mark-published")


def mark_published() -> None:
    PUBLISHED.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED.write_text(json.dumps({"fp": _fingerprint(), "at": date.today().isoformat()}))


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "list":
        for s in load():
            print(f"## {s['title']}")
            for i, t in enumerate(s["lines"], 1):
                print(f"  {i}. {t}")
        return 0
    cmd = argv[0]
    if cmd == "add":
        sec = None
        if "--section" in argv:
            k = argv.index("--section")
            sec = argv[k + 1]
            argv = argv[:k] + argv[k + 2:]
        print(json.dumps(add(" ".join(argv[1:]), sec), ensure_ascii=False))
        return 0
    if cmd == "remove":
        print(json.dumps(remove(" ".join(argv[1:])), ensure_ascii=False))
        return 0
    if cmd == "html":
        sys.stdout.write(render_html())
        return 0
    if cmd == "sync-check":
        msg = sync_check()
        if msg:
            print(msg)
        return 0
    if cmd == "mark-published":
        mark_published()
        print("게시 표식 갱신")
        return 0
    if cmd == "summary":
        print(summary())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
