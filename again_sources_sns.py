"""「다시, 여기」 후보 재료 — sns-tracker 발행 글(회사맥 sns-tracker 가상환경에서 실행).

  ~/sns-tracker/.venv/bin/python again_sources_sns.py own|book '제외ID,...'
출력: JSON [{id, title, category, date, url, body}] — 아직 안 훑은 발행 글 무작위 몇 편.
  own  = Jay가 직접 쓴 글(생각·사업·명상록 계열)
  book = 하루한문장(책 문장 + Jay 코멘트)
  ~/sns-tracker/.venv/bin/python again_sources_sns.py author '작가' '제외ID,...' [n]
  author = 그 작가 책에서 Jay가 옮긴 문장(서재 책과 연결된 발행 글). 밑줄 OCR(book_quotes)은 표지 짝짓기가
           틀린 경우가 있어(다른 책 문장이 엉뚱한 작가로 붙음) 쓰지 않는다
"""
from __future__ import annotations

import json
import os
import random
import re
import sys

SNS = os.path.expanduser("~/sns-tracker")
OWN = {"짧은 생각들", "스타트업CEO의 명상록", "스타트업, 사업과 사람 이야기", "Product 이야기", "메모"}
BOOK = {"하루한문장"}


def _norm_cat(c: str | None) -> str:
    return re.sub(r"\s+", " ", (c or "").replace("\xa0", " ")).strip()


def posts(kind: str, exclude: set[str], n: int) -> list[dict]:
    os.chdir(SNS)
    sys.path.insert(0, SNS)
    import store

    cats = OWN if kind == "own" else BOOK
    rows = (store.get_client().table("posts")
            .select("id,title,category,published_date,url,body")
            .eq("status", "발행후").order("id", desc=True).limit(2000).execute().data) or []
    pool = [r for r in rows
            if (_norm_cat(r.get("category")) in cats or _norm_cat(r.get("category")).replace(" ", "") in
                {c.replace(" ", "") for c in cats})
            and str(r["id"]) not in exclude and len(r.get("body") or "") >= 40]
    random.shuffle(pool)
    return [{"id": str(r["id"]), "title": r.get("title") or "", "category": _norm_cat(r.get("category")),
             "date": str(r.get("published_date") or "")[:10], "url": r.get("url") or "",
             "body": (r.get("body") or "")[:3500]} for r in pool[:n]]


def author_materials(author: str, exclude: set[str], n: int) -> list[dict]:
    os.chdir(SNS)
    sys.path.insert(0, SNS)
    import store

    c = store.get_client()
    parts = author.split()  # '마이클 싱어' ↔ '마이클 A. 싱어'처럼 가운데 이니셜이 끼어도 잡는다
    books = {b["id"]: b for b in (c.table("books").select("id,title,author").ilike("author", f"%{parts[-1]}%")
                                  .limit(200).execute().data or [])
             if all(t in (b.get("author") or "") for t in parts)}
    if not books:
        return []
    out = []
    links = (c.table("book_post_links").select("book_id,post_id,posts(id,title,url,body,status)")
             .in_("book_id", list(books)).limit(500).execute().data) or []
    for l in links:
        p = l.get("posts") or {}
        pid = f"p{p.get('id')}"
        if p.get("status") == "발행후" and pid not in exclude and len(p.get("body") or "") >= 40 \
                and pid not in {o["id"] for o in out}:
            out.append({"id": pid, "title": books[l["book_id"]]["title"], "url": p.get("url") or "",
                        "body": (p.get("body") or "")[:3500]})
    random.shuffle(out)
    return out[:n]


if __name__ == "__main__":
    if sys.argv[1] == "author":
        ex = set(filter(None, (sys.argv[3] if len(sys.argv) > 3 else "").split(",")))
        print(json.dumps(author_materials(sys.argv[2], ex, int(sys.argv[4]) if len(sys.argv) > 4 else 4), ensure_ascii=False))
        sys.exit(0)
    kind = sys.argv[1]
    ex = set(filter(None, (sys.argv[2] if len(sys.argv) > 2 else "").split(",")))
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    print(json.dumps(posts(kind, ex, n), ensure_ascii=False))
