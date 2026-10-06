"""「다시, 여기」 새로 온 문장 — 매일 새벽 후보를 몇 개씩 모은다(회사맥 cron).

출처 세 갈래, 출처마다 구독 LLM 1회:
  own  = Jay가 발행한 글(sns-tracker)에서 Jay 자신의 문장
  book = 책 밑줄 사진(book_archive, 집맥) + 하루한문장 발행 글의 책 문장
  web  = 뱅크에 있는 저자 한 명(날마다 돌아가며)을 카카오 웹·블로그 검색 → 페이지 본문에서 그 저자의 문장

검증(코드): 후보 문장은 재료 원문의 연속 구간이어야 한다(공백만 무시, 글자 하나도 못 바꿈).
웹은 그 페이지에 저자 이름이 있어야 한다. 이미 뱅크에 있거나 버린 문장은 다시 안 올린다.
후보는 허브 /again '새로 온 문장'에 뜨고, ♡=넣기 −=버리기(again_bank.promote_candidates).

  ./venv/bin/python again_candidates.py            # 실행
  DRY_RUN=1 ./venv/bin/python again_candidates.py  # 저장 없이 결과만 출력
  ./venv/bin/python again_candidates.py --only web
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import again_bank  # noqa: E402
import desk_sources as S  # noqa: E402
import jay_desk as J  # noqa: E402

PENDING_MAX = 12        # 대기 후보가 이만큼 쌓이면 새로 안 모은다(밀린 숙제 방지)
PER_SOURCE = 2
WEB_AUTHORS = 3        # 웹은 하루 작가 3명 × 1문장. 고르는 순서는 pick_authors
SNS_PY = str(Path.home() / "sns-tracker/.venv/bin/python")
SNS_SCRIPT = str(Path(__file__).resolve().parent / "again_sources_sns.py")
NOT_AUTHORS = {"책에서", "아침 1분", "모음", "현대", "신비주의", "스토아", "노자 · 장자 · 선", "내 문장"}
# 볼트에 아직 문장이 없어도 웹 로테이션에 넣는 좋아하는 작가. 값=검색어에 붙일 책 제목(동명이인 거르기, 날마다 돌아가며)
FAVORITE_AUTHORS = {
    "이하영": ["인생의 연금술", "말은 운명을 데려온다", "나는 나의 스무살을 가장 존중한다", "더 바이브"],
    "네빌 고다드": ["", "전제의 법칙", "상상의 힘"],
    "에크하르트 톨레": [""],
    "마이클 싱어": [""],
    "루퍼트 스파이라": [""],
    "디팩 초프라": [""],
    "웨인 다이어": [""],
    "사이토 히토리": [""],
    "밥 프록터": [""],
    "라이언 홀리데이": [""],
}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
DRY = os.getenv("DRY_RUN") in ("1", "true", "TRUE")


def log(msg: str) -> None:
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", file=sys.stderr)


def squash(s: str) -> str:
    return re.sub(r"\s+", "", again_bank.nfc(s or ""))


# ── 재료 ─────────────────────────────────────────────────────────────
def sns_posts(kind: str, seen: list[str], n: int) -> list[dict]:
    try:
        r = subprocess.run([SNS_PY, SNS_SCRIPT, kind, ",".join(seen[-3000:]), str(n)],
                           capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            return json.loads(r.stdout or "[]")
        log(f"sns {kind} 실패: {r.stderr[-300:]}")
    except Exception as e:  # noqa: BLE001
        log(f"sns {kind} 스킵: {e}")
    return []


def _kakao_key() -> str:
    try:
        for line in (Path.home() / "sns-tracker/.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("KAKAO_REST_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ""


def kakao_search(query: str, kind: str = "blog", size: int = 6, page: int = 1) -> list[dict]:
    key = _kakao_key()
    if not key:
        return []
    url = f"https://dapi.kakao.com/v2/search/{kind}?" + urllib.parse.urlencode({"query": query, "size": size, "page": page})
    req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("documents") or []
    except Exception as e:  # noqa: BLE001
        log(f"카카오 검색 실패({kind}): {type(e).__name__}")
        return []


def page_text(url: str) -> str:
    """페이지 본문 텍스트. 네이버 블로그는 모바일 주소로 바꿔 iframe을 피한다."""
    m = re.match(r"https?://blog\.naver\.com/([^/?#]+)/(\d+)", url)
    if m:
        url = f"https://m.blog.naver.com/{m.group(1)}/{m.group(2)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        log(f"페이지 실패 {url[:60]}: {type(e).__name__}")
        return ""
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = "\n".join(re.sub(r"[ \t ]+", " ", l).strip() for l in text.splitlines())
    return again_bank.nfc(re.sub(r"\n{2,}", "\n", text)).strip()


# ── LLM 선택 ─────────────────────────────────────────────────────────
def _examples(n: int = 6) -> str:
    lines = again_bank.all_lines()
    return "\n".join(f"- {l['text']}" for l in random.sample(lines, min(n, len(lines))))


def choose(materials: list[dict], what: str, job: str, limit: int = PER_SOURCE) -> list[dict]:
    """materials=[{ref, text}] → [{ref, text}] (원문 구간 그대로)."""
    if not materials:
        return []
    body = "\n\n".join(f"[{m['ref']}]\n{m['text']}" for m in materials)
    prompt = f"""「다시, 여기」는 Jay가 하루에도 몇 번씩 들여다보며 마음을 가라앉히는 문장 모음이다.
마음·태도·감사·내면을 다잡는, 짧고 단단한 문장들이다. 지금 들어 있는 문장 예:
{_examples()}

아래는 {what}이다. 이 모음에 둘 만한 문장을 최대 {limit}개 골라라. 없으면 고르지 마라(억지로 채우지 않는다).
규칙:
- 재료 원문 안의 연속된 구간을 그대로 옮긴다. 완결된 문장 1~2개, 140자 이하.
- 글자를 하나도 바꾸거나 더하지 않는다. 앞뒤 문맥 없이 혼자 읽혀야 한다.
- 앞 문장 없이도 뜻이 통해야 한다. '그래서·그것·이것·~테니·~때문에'처럼 앞에 기대는 구간은 고르지 않는다.
- 오타·OCR 오류·잘린 글자가 있는 구간은 고르지 않는다(띄어쓰기만 틀린 건 띄어쓰기를 고쳐서 옮겨도 된다).
- 제목·소제목·목차·해시태그 줄은 고르지 않는다.
- 정보·홍보·사업 설명·특정 사건 이야기는 고르지 않는다. 지금 예시와 거의 같은 뜻도 고르지 않는다.

JSON만 출력: {{"picks": [{{"ref": "재료 대괄호 안 id", "text": "원문 구간"}}]}}

{body}"""
    data = J.ask_json(prompt, job)
    picks = data.get("picks") if isinstance(data, dict) else None
    return [p for p in (picks or []) if isinstance(p, dict) and p.get("ref") and p.get("text")]


_OPEN = '.?!"”’\'」』)]…·-—:' + "0123456789"
_CLOSE = '.?!"”’\'」』)]…'


def on_boundaries(text: str, src: str) -> bool:
    """원문에서 문장 경계에서 시작하고 문장 경계에서 끝나는가(문장 중간 조각 차단)."""
    pat = r"\s*".join(re.escape(ch) for ch in squash(text))
    src = again_bank.nfc(src)
    m = re.search(pat, src)
    if not m:
        return False
    before = src[:m.start()].rstrip()
    after = src[m.end():].lstrip()
    starts = not before or before[-1] in _OPEN or "\n" in src[len(before):m.start()]
    ends = text[-1] in _CLOSE or not after or "\n" in src[m.end():len(src) - len(after)]
    return starts and ends


def verify(pick: dict, source_text: str, known: set[str], author: str | None = None) -> str | None:
    """통과하면 정리된 문장, 아니면 None(사유는 로그)."""
    text = again_bank._clean(str(pick["text"]))
    if not (8 <= len(text) <= 140):
        return log(f"길이 탈락: {text[:30]}")
    if not J.letters_within(text, source_text):
        return log(f"원문 불일치 탈락: {text[:40]}")
    if not on_boundaries(text, source_text):
        return log(f"문장 경계 탈락: {text[:40]}")
    if squash(text) in known:
        return log(f"중복 탈락: {text[:30]}")
    if author and squash(author) not in squash(source_text):
        return log(f"저자 미표기 탈락: {text[:30]}")
    return text


# ── 출처별 ───────────────────────────────────────────────────────────
def from_own(state: dict, known: set[str]) -> list[dict]:
    seen = state["seen"].setdefault("post", [])
    posts = sns_posts("own", seen, 6)
    seen += [p["id"] for p in posts]
    by = {f"p{p['id']}": p for p in posts}
    out = []
    for pk in choose([{"ref": k, "text": f"{p['title']}\n{p['body']}"} for k, p in by.items()],
                     "Jay가 직접 쓴 블로그 글들. Jay 자신의 생각을 담은 문장만 고른다(인용한 남의 말은 제외)",
                     "again_candidates_own"):
        p = by.get(pk["ref"])
        t = p and verify(pk, f"{p['title']}\n{p['body']}", known)
        if t:
            known.add(squash(t))
            out.append({"text": t, "section": "내 문장", "source": "own",
                        "ref": p["title"][:60], "url": p.get("url", "")})
    return out


def _book_title(title: str) -> str:
    m = re.search(r"<\s*책\s*:\s*([^>]+)>", title or "")
    return m.group(1).strip() if m else ""


def from_book(state: dict, known: set[str]) -> list[dict]:
    seen_u = state["seen"].setdefault("underline", [])
    seen_p = state["seen"].setdefault("book_post", [])
    rows = S.underline_candidates(J.offered_underlines() | set(seen_u))[:14]
    posts = sns_posts("book", seen_p, 4)
    seen_u += [r["id"] for r in rows]
    seen_p += [p["id"] for p in posts]
    mats = {}
    for i, r in enumerate(rows):
        mats[f"u{i}"] = {"text": r["text"], "ref": f"{r.get('captured', '')} 찍은 책 페이지", "url": "", "uid": r["id"]}
    for p in posts:
        mats[f"b{p['id']}"] = {"text": p["body"], "ref": f"「{_book_title(p['title'])}」" if _book_title(p["title"]) else p["title"][:40],
                               "url": p.get("url", "")}
    out = []
    for pk in choose([{"ref": k, "text": m["text"]} for k, m in mats.items()],
                     "Jay가 읽은 책의 밑줄(사진 OCR, 띄어쓰기 오류가 있을 수 있음)과, Jay가 블로그에 옮긴 책 문장. 책의 문장만 고른다(Jay 코멘트·번호 제외)",
                     "again_candidates_book"):
        m = mats.get(pk["ref"])
        t = m and verify(pk, m["text"], known)
        if t:
            known.add(squash(t))
            out.append({"text": t, "section": "책에서", "source": "book", "ref": m["ref"], "url": m["url"]})
            if m.get("uid") and not DRY:
                J.mark_offered(m["uid"])  # 저녁 한 통이 같은 밑줄을 또 제안하지 않게
    return out


def web_authors() -> list[str]:
    authors = [s["title"] for s in again_bank.load() if s["lines"] and s["title"] not in NOT_AUTHORS]
    return authors + [a for a in FAVORITE_AUTHORS if squash(a) not in {squash(x) for x in authors}]


def pick_authors(authors: list[str], log_rows: list[dict], scores: dict[str, int], n: int,
                 today: datetime | None = None) -> list[str]:
    """오래 안 나온 작가 먼저. ♡ 받은 작가는 더 자주, − 받은 작가는 더 드물게(작가별 최근 판정 합 −2~+3, 1점=3일)."""
    today = (today or datetime.now()).date()
    last: dict[str, str] = {}
    taste: dict[str, int] = {}
    for r in log_rows:
        a = r.get("author")
        last[a] = max(last.get(a, ""), r.get("date", ""))
        sc = scores.get(r.get("id"), 0)
        taste[a] = taste.get(a, 0) + (1 if sc > 0 else -1 if sc < 0 else 0)

    def key(a: str) -> float:
        gap = (today - datetime.strptime(last[a], "%Y-%m-%d").date()).days if a in last else 99
        return gap + 3 * max(-2, min(3, taste.get(a, 0)))
    return sorted(authors, key=lambda a: (-key(a), authors.index(a)))[:n]


def _author_pages(author: str, hint: str, seen: list[str], limit: int) -> list[dict]:
    q1, q2 = (f"{author} {hint} 구절", f"{author} {hint} 문장") if hint else (f"{author} 책 구절", f"{author} 명언")
    pages = []
    for pg in range(1, 6):  # 앞쪽 결과를 이미 다 훑은 작가는 다음 쪽으로 넘어간다
        docs = kakao_search(q1, "blog", page=pg) + kakao_search(q2, "web", page=pg)
        if not docs:
            break
        for d in docs:
            url = d.get("url") or ""
            if not url or url in seen:
                continue
            seen.append(url)
            txt = page_text(url)
            if len(txt) >= 200 and squash(author) in squash(txt):
                pages.append({"url": url, "text": txt[:5000], "author": author,
                              "title": html.unescape(re.sub(r"<[^>]+>", "", d.get("title", "")))})
            if len(pages) >= limit:
                return pages
    return pages


def from_web(state: dict, known: set[str]) -> list[dict]:
    authors = web_authors()
    if not authors:
        return []
    wlog = state.setdefault("web_log", [])  # [{id, author, date}] — 작가별 ♡·− 반응을 다음 선택에 쓴다
    picked = pick_authors(authors, wlog, again_bank.feedback_scores(), WEB_AUTHORS)
    seen = state["seen"].setdefault("web", [])
    pages = []
    for a in picked:
        hints = FAVORITE_AUTHORS.get(a) or [""]
        hint = hints[sum(1 for r in wlog if r.get("author") == a) % len(hints)]
        got = _author_pages(a, hint, seen, 2)
        log(f"웹: {a} 페이지 {len(got)}개")
        pages += got
    by = {f"w{i}": p for i, p in enumerate(pages)}
    out, used = [], set()
    for pk in choose([{"ref": k, "text": f"(작가: {p['author']})\n{p['text']}"} for k, p in by.items()],
                     "웹 페이지 본문들. 각 재료 첫 줄의 작가 문장으로 페이지에 명시된 것만 고른다"
                     "(블로그 글쓴이 자신의 말·다른 저자 문장 제외). 한 작가에서 1개까지",
                     "again_candidates_web", limit=len(picked)):
        p = by.get(pk["ref"])
        if not p or p["author"] in used:
            continue
        t = verify(pk, p["text"], known, p["author"])
        if t:
            known.add(squash(t))
            used.add(p["author"])
            out.append({"text": t, "section": p["author"], "source": "web",
                        "ref": (p["title"] or urllib.parse.urlparse(p["url"]).netloc)[:50], "url": p["url"]})
    today = datetime.now().strftime("%Y-%m-%d")
    wlog += [{"id": again_bank.sentence_id(c["text"]), "author": c["section"], "date": today} for c in out]
    wlog += [{"id": "", "author": a, "date": today} for a in picked if a not in used]  # 허탕도 '나온 날'로 친다
    state["web_log"] = wlog[-500:]
    return out


def judge(cands: list[dict]) -> list[dict]:
    """두 번째 눈: 혼자 읽혀 뜻이 완결되는지·오타가 없는지만 본다. 실패하면 아무것도 안 올린다(fail-closed)."""
    if not cands:
        return []
    listing = "\n".join(f"[{i}] {c['text']}" for i, c in enumerate(cands))
    data = J.ask_json(f"""아래 문장들은 마음을 다잡는 문장 모음에 올릴 후보다. 각각을 엄격하게 심사하라.
통과 조건(모두 만족):
- 앞뒤 문맥 없이 이 문장만 읽어도 무엇에 대한 말인지 분명하다. 주어·대상이 빠진 조각, 첫 문장이 '이·그·따라서·이때'로 앞에 기대는 경우는 탈락(후보 안의 둘째 문장이 첫 문장을 받는 건 괜찮다).
- 오타·어색한 비문이 없다.
- 사실·사건·사업 정보가 아니다(마음·태도에 대한 통찰이면 정의형 문장도 괜찮다).

JSON만 출력: {{"results": [{{"i": 번호, "ok": true/false, "why": "탈락 이유 한 줄"}}]}}

{listing}""", "again_candidates_judge")
    res = data.get("results") if isinstance(data, dict) else None
    if not isinstance(res, list):
        log("심사 실패 — 이번엔 안 올림")
        return []
    ok = {r.get("i") for r in res if isinstance(r, dict) and r.get("ok") is True}
    for r in res:
        if isinstance(r, dict) and r.get("ok") is not True and isinstance(r.get("i"), int) and r["i"] < len(cands):
            log(f"심사 탈락: {cands[r['i']]['text'][:40]} — {r.get('why', '')}")
    return [c for i, c in enumerate(cands) if i in ok]


SOURCES = {"own": from_own, "book": from_book, "web": from_web}


def run(only: str | None = None) -> list[dict]:
    state = again_bank.load_candidates()
    if len(state["items"]) >= PENDING_MAX:
        log(f"대기 후보 {len(state['items'])}개 — 새로 안 모음")
        return []
    known = {squash(l["text"]) for l in again_bank.all_lines()}
    known |= {squash(c["text"]) for c in state["items"]}
    dismissed = set(state["dismissed"])
    added = []
    for name, fn in SOURCES.items():
        if only and name != only:
            continue
        try:
            got = fn(state, known)
        except Exception as e:  # noqa: BLE001 — 한 출처가 죽어도 나머지는 돈다
            log(f"{name} 실패: {type(e).__name__}: {e}")
            continue
        for c in got:
            cid = again_bank.sentence_id(c["text"])
            if cid in dismissed:
                continue
            added.append({"id": cid, **c, "added": datetime.now().strftime("%Y-%m-%d")})
        log(f"{name}: {len(got)}개")
    passed = judge(added)
    state["dismissed"] += [c["id"] for c in added if c not in passed]  # 탈락 문장은 다시 안 올린다
    added = passed
    for k in state["seen"]:
        state["seen"][k] = state["seen"][k][-3000:]
    if not DRY:
        state["items"] += added
        again_bank.save_candidates(state)
    return added


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    print(json.dumps(run(only), ensure_ascii=False, indent=1))
