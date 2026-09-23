"""work_calendar 테스트 — 캘린더 없이 내장표·설정 파일만으로 판정이 맞는지.

실행: ./venv/bin/python test_work_calendar.py
"""
import json
import tempfile
from datetime import date
from pathlib import Path

import work_calendar as W


def _isolated(overrides=None):
    tmp = Path(tempfile.mkdtemp())
    W.CACHE = tmp / "holidays.json"
    W.OVERRIDES = tmp / "workdays.json"
    if overrides is not None:
        W.OVERRIDES.write_text(json.dumps(overrides, ensure_ascii=False))
    W._memo = None

    def _fail():
        raise RuntimeError("offline")
    W._fetch = _fail


def test_chuseok_week_and_substitute_holiday_offline():
    _isolated()
    assert W.is_last_workday_of_week(date(2026, 9, 23))         # 추석 연휴 전 수요일
    assert not W.is_workday(date(2026, 9, 24)) and W.day_off_reason(date(2026, 9, 24)) == "추석 연휴"
    assert W.is_first_workday_of_week(date(2026, 10, 6))        # 10/5 대체공휴일 → 화요일
    assert W.is_last_workday_of_week(date(2026, 10, 8))         # 10/9 한글날 → 목요일
    assert W.is_first_workday_of_week(date(2026, 9, 21)) and not W.is_first_workday_of_week(date(2026, 9, 22))
    assert W.day_off_reason(date(2026, 9, 27)) == "일요일"


def test_overrides_add_off_and_work_days():
    _isolated({"off": {"2026-09-23": "회사 휴무"}, "work": ["2026-09-24"]})
    assert W.day_off_reason(date(2026, 9, 23)) == "회사 휴무"
    assert W.is_workday(date(2026, 9, 24))                       # 연휴지만 출근으로 지정
    assert W.is_last_workday_of_week(date(2026, 9, 24))


def test_cache_used_and_merged_with_builtin():
    _isolated()
    W.CACHE.write_text(json.dumps({"fetched_at": "2026-09-23T00:00:00+09:00",
                                   "days": {"2026-11-11": "임시공휴일"}}, ensure_ascii=False))
    W._memo = None
    assert W.day_off_reason(date(2026, 11, 11)) == "임시공휴일"   # 저장본(캘린더) 우선
    assert W.day_off_reason(date(2026, 9, 25)) == "추석"          # 내장표로 빈 곳 메움


def test_whole_week_off_has_no_last_workday():
    _isolated({"off": {f"2026-10-{d:02d}": "휴가" for d in (5, 6, 7, 8, 9)}})
    assert W.week_workdays(date(2026, 10, 7)) == []
    assert not W.is_last_workday_of_week(date(2026, 10, 8))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
