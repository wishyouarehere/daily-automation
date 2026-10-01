"""「다시, 여기」 피드백·새 문장 회귀 테스트. 실제 볼트·상태 파일은 건드리지 않는다.

  ./venv/bin/python test_again.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="again-test-"))
(TMP / "vault/20-Areas/Personal").mkdir(parents=True)
(TMP / "vault/20-Areas/Personal/다시-여기.md").write_text(
    "---\ntitle: 다시, 여기\nupdated: 2026-09-30\n---\n\n# 다시, 여기\n\n"
    "## 다사카 히로시\n1. 문제의 원인은 내 안에 있다.\n\n## 책에서\n1. 오래된 문장.\n", encoding="utf-8")
os.environ["VAULT_DIR"] = str(TMP / "vault")
os.environ["AGAIN_STATE_DIR"] = str(TMP / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import again_bank as A  # noqa: E402
import again_candidates as C  # noqa: E402


def feedback(sid, v):
    A.FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
    with A.FEEDBACK.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "t", "id": sid, "text": "", "v": v}) + "\n")


def test_sentence_id_ignores_nfd_and_edges():
    import unicodedata
    t = "문제의 원인은 내 안에 있다."
    assert A.sentence_id(t) == A.sentence_id(" " + unicodedata.normalize("NFD", t) + " ")


def test_scores_sum_and_skip_bad_rows():
    A.FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
    A.FEEDBACK.write_text('{"id":"aaaaaaaaaaaa","v":1}\nnot json\n{"id":"aaaaaaaaaaaa","v":1}\n{"id":"aaaaaaaaaaaa","v":-1}\n',
                          encoding="utf-8")
    assert A.feedback_scores() == {"aaaaaaaaaaaa": 1}
    A.FEEDBACK.unlink()


def test_boundaries_block_fragments():
    src = "용서하라. 상대는 기억하지 못하고 잘살 테니. 담아둘수록 적자가 커진다."
    assert C.on_boundaries("상대는 기억하지 못하고 잘살 테니.", src)
    assert not C.on_boundaries("기억하지 못하고 잘살 테니.", src)      # 문장 중간에서 시작
    assert not C.on_boundaries("담아둘수록 적자가", src)               # 문장 중간에서 끝
    assert C.on_boundaries("삶과 죽음은 이어진다.", "1\n삶과 죽음은  이어진다. 다음")  # 번호 줄 뒤, 공백 차이
    assert C.on_boundaries("마음의 제목", "머리\n마음의 제목\n본문")


def test_verify_rejects_changed_letters_dupes_and_missing_author():
    known = {C.squash("문제의 원인은 내 안에 있다.")}
    src = "다사카 히로시는 말했다. 감사는 기술이다. 문제의 원인은 내 안에 있다."
    assert C.verify({"text": "감사는 기술이다."}, src, known) == "감사는 기술이다."
    assert C.verify({"text": "감사는 기술이었다."}, src, known) is None          # 글자 바뀜
    assert C.verify({"text": "문제의 원인은 내 안에 있다."}, src, known) is None  # 이미 뱅크에 있음
    assert C.verify({"text": "감사는 기술이다."}, "감사는 기술이다. 끝.", set(), "다사카 히로시") is None  # 저자 미표기
    assert C.verify({"text": "짧다."}, "짧다.", set()) is None                     # 너무 짧음


def test_promote_adds_liked_and_drops_disliked():
    items = [{"text": "새 문장 하나다.", "section": "책에서", "source": "book", "ref": "r", "url": ""},
             {"text": "버릴 문장이다.", "section": "내 문장", "source": "own", "ref": "r", "url": ""},
             {"text": "아직 고민 중인 문장이다.", "section": "다사카 히로시", "source": "web", "ref": "r", "url": "u"}]
    d = A.load_candidates()
    d["items"] = [{"id": A.sentence_id(i["text"]), **i} for i in items]
    A.save_candidates(d)
    feedback(A.sentence_id(items[0]["text"]), 1)
    feedback(A.sentence_id(items[1]["text"]), -1)
    done = A.promote_candidates()
    assert {x["result"] for x in done} == {"added", "dismissed"}
    secs = {s["title"]: s["lines"] for s in A.load()}
    assert "새 문장 하나다." in secs["책에서"] and "버릴 문장이다." not in str(secs)
    left = A.load_candidates()
    assert [c["text"] for c in left["items"]] == ["아직 고민 중인 문장이다."]
    assert A.sentence_id(items[1]["text"]) in left["dismissed"]
    assert A.promote_candidates() == []   # 두 번 돌려도 같은 문장을 또 넣지 않는다
    assert sum(1 for l in A.all_lines() if l["text"] == "새 문장 하나다.") == 1


def test_rested_sentence_not_picked():
    import jay_desk as J
    feedback(A.sentence_id("오래된 문장."), -1)
    feedback(A.sentence_id("오래된 문장."), -1)
    texts = [c["text"] for c in J.quote_candidates()]
    assert "오래된 문장." not in texts and "문제의 원인은 내 안에 있다." in texts


def test_judge_fails_closed():
    orig = C.J.ask_json
    try:
        C.J.ask_json = lambda *a, **k: {}
        assert C.judge([{"text": "아무 문장이다."}]) == []
        C.J.ask_json = lambda *a, **k: {"results": [{"i": 0, "ok": True}, {"i": 1, "ok": "yes"}]}
        assert [c["text"] for c in C.judge([{"text": "가."}, {"text": "나."}])] == ["가."]
    finally:
        C.J.ask_json = orig


def test_run_respects_pending_cap_without_llm():
    d = A.load_candidates()
    d["items"] = [{"id": f"{i:012x}", "text": f"대기 {i}.", "section": "모음"} for i in range(C.PENDING_MAX)]
    A.save_candidates(d)
    called = []
    C.SOURCES = {k: (lambda *a, **k: called.append(1) or []) for k in C.SOURCES}
    assert C.run() == [] and not called


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    order = ["test_sentence_id", "test_scores", "test_boundaries", "test_verify", "test_promote", "test_rested", "test_judge", "test_run"]
    tests.sort(key=lambda f: next(i for i, p in enumerate(order) if f.__name__.startswith(p)))
    try:
        for t in tests:
            t()
            print("ok", t.__name__)
        print(f"{len(tests)} passed")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
