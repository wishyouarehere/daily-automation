"""종이책 밑줄 후보 — sns-tracker book_archive(사진 앱 자동 수집)의 밑줄 텍스트를 뽑는다.

집맥에서 sns-tracker 가상환경으로 실행한다(저녁 한 통이 SSH로 부른다):
  ~/.venvs/sns-tracker-unified/bin/python ~/daily-automation/book_underlines.py [제외ID,...]
출력: JSON [{id, text, captured}] — 최근 21일 밑줄 전부 + 예전 밑줄 무작위 20개.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone

LIVE = os.path.expanduser("~/sns-tracker-unified")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def candidates(exclude: set[str], recent_days: int = 21, backlog: int = 20) -> list[dict]:
    os.chdir(LIVE)
    sys.path.insert(0, LIVE)
    from book_archive import state

    cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()
    recent, old = [], []
    for r in state.items(decision="accepted"):
        text = _norm(r.get("underlined_text"))
        rid = str(r.get("photos_asset_id") or r.get("asset_id") or "")
        if not rid or rid in exclude or not (10 <= len(text) <= 220):
            continue
        row = {"id": rid, "text": text, "captured": str(r.get("captured_at") or "")[:10]}
        (recent if str(r.get("captured_at") or "") >= cutoff else old).append(row)
    random.shuffle(old)
    return recent[:20] + old[:backlog]


if __name__ == "__main__":
    ex = set(filter(None, (sys.argv[1] if len(sys.argv) > 1 else "").split(",")))
    print(json.dumps(candidates(ex), ensure_ascii=False))
