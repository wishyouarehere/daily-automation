import brief_filter as BF


def test_drop_unsettled_sentence_level():
    assert BF.drop_unsettled("11/9에 A로 정했어요. 담당은 정해지지 않았어요.") == "11/9에 A로 정했어요."
    assert BF.drop_unsettled("결과는 기록이 없어요 [확인필요]") == ""
    assert BF.drop_unsettled("A안으로 확정했어요 [결정]") == "A안으로 확정했어요"


def test_same_title_notes_exact_and_generic(tmp_path, monkeypatch):
    from datetime import date
    (tmp_path / "2026-11-10 주간 아젠다 논의 — 채용.md").write_text("본문A", encoding="utf-8")
    (tmp_path / "2026-11-10 주간 아젠다 논의 후속 — 다른.md").write_text("본문B", encoding="utf-8")
    (tmp_path / "2026-11-10 온보딩 — x.md").write_text("본문C", encoding="utf-8")
    got = BF.same_title_notes("14:00  주간 아젠다 논의", tmp_path, date(2026, 11, 19))
    assert "본문A" in got and "본문B" not in got
    assert BF.same_title_notes("11:00  온보딩", tmp_path, date(2026, 11, 19)) == ""
