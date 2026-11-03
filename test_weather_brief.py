"""weather_brief 규칙 테스트 (네트워크·발송 없음). ./venv/bin/python test_weather_brief.py"""
from weather_brief import Day, build, feel, render, _rng
from datetime import date


def hourly(temps, rain=None, txt=None, feels=None):
    rain = rain or {}
    txt = txt or {}
    return {h: {"t": t, "feel": (feels or {}).get(h, t), "rain": rain.get(h, 0.0), "amt": 0.0,
                "txt": txt.get(h, "맑음"), "wind": 1.0} for h, t in temps.items()}


def test_feel():
    assert feel(0.8, 15) == "어제랑 비슷해요"
    assert feel(-4, 5) == "어제보다 꽤 추워요 (−4도)"
    assert feel(-2, 15) == "어제보다 살짝 쌀쌀해요 (−2도)"
    assert feel(7, 28) == "어제보다 확 더워요 (+7도)"
    assert feel(None, 1) == ""


def test_rng():
    assert _rng([15, 16, 17]) == "15~18시"
    assert _rng([8, 15, 16]) == "8시, 15~17시"


def test_cold_rainy_day():
    temps = {h: 3 + (h - 7) for h in range(7, 22)}          # 3° → 17°, 일교차 14도
    temps.update({19: 9, 20: 8, 21: 7})
    home = Day("집", hourly=hourly(temps, rain={16: 70, 17: 80}, txt={16: "비", 17: "비"}),
               tmin=3, tmax=17, now_t=3, now_vs_yday=-5)
    home.yday_max = 21
    b = build(home, None, True, {}, {})
    assert b.headline == "오늘은 어제보다 꽤 추워요", b.headline
    assert any(r.startswith("아침 · 어제보다 꽤 추워요") for r in b.rows), b.rows
    assert any(p.startswith("우산 — 16~18시 비") for p in b.prep), b.prep
    assert any(p.startswith("벗기 쉬운 겉옷") for p in b.prep), b.prep
    text = render(b, home, None, date(2026, 11, 4))
    assert "<b>챙길 것</b>" in text and "집 3~17°" in text


def test_calm_day_has_no_prep():
    temps = {h: 15 + (1 if 12 <= h <= 16 else 0) for h in range(7, 22)}
    home = Day("집", hourly=hourly(temps), tmin=14, tmax=16, now_t=15, now_vs_yday=0.5, yday_max=16)
    b = build(home, None, True, {}, {})
    assert b.headline == "오늘은 어제랑 비슷해요"
    assert b.prep == []


def test_evening_uses_yesterday_snapshot():
    temps = {h: 10 for h in range(7, 22)}
    home = Day("집", hourly=hourly(temps), tmin=10, tmax=10, now_vs_yday=0)
    b = build(home, None, False, {"tmax": 10, "hourly": {"19": 14}}, {})
    assert "저녁 · 어제보다 꽤 쌀쌀해요 (−4도)" in b.rows, b.rows


def test_rain_episode_and_message():
    from datetime import datetime
    import rain_watch as rw
    h = hourly({h: 10 for h in range(7, 22)}, rain={15: 70, 16: 80}, txt={15: "비", 16: "비"})
    assert rw.episode(h, 12) is None                      # 3시간 뒤 비는 아직
    s, e, peak = rw.episode(h, 14)
    assert (s, e) == (15, 16)
    msg = rw.message(s, e, peak, datetime(2026, 11, 4, 14, 30), "회사")
    assert "15시쯤(30분 뒤) 비가 시작돼요" in msg and "17시까지" in msg, msg
    s, e, peak = rw.episode(h, 15)
    assert "지금부터 비가 와요" in rw.message(s, e, peak, datetime(2026, 11, 4, 15, 5), "집")


def test_radar_episode_and_dedupe():
    from datetime import datetime, timedelta, timezone
    import rain_watch as rw
    kst = timezone(timedelta(hours=9))
    t0 = datetime(2026, 11, 4, 14, 0, tzinfo=kst)
    series = [(t0 + timedelta(minutes=10 * i), v) for i, v in enumerate([0, 0, 0.3, 1.0, 4.0, 2.0, 0.2, 0])]
    start, end, peak, last = rw.radar_episode(series)
    assert (start.minute, end.minute, peak, last.minute) == (30, 0, 4.0, 50)
    msg = rw.radar_message(start, end, peak, t0 + timedelta(minutes=2), "회사")
    assert "14:30쯤(28분 뒤) 비가 시작돼요" in msg and "15:00쯤 그칠" in msg and "강한 비" in msg, msg
    assert rw.is_new_rain(start, {})
    assert not rw.is_new_rain(start, {"wet_until": (t0 + timedelta(minutes=20)).isoformat()})
    assert rw.is_new_rain(start + timedelta(hours=2), {"wet_until": last.isoformat()})
    assert rw.radar_episode([(t0, 0.2), (t0, 0.0)]) is None


def test_radar_pixel_and_colors():
    import radar
    x, y = radar.pixel(37.5665, 126.9780, 1197, 1253)   # 서울시청
    assert 0 < x < 1197 and 0 < y < 1253
    assert radar.color_value((15, 59, 235, 255)) == 0.5
    assert radar.color_value((15, 59, 235, 0)) == 0.0
    assert radar.color_value((200, 200, 200, 255)) == 0.0     # 색상표와 먼 색


def test_redteam_noise_filters():
    from datetime import datetime, timedelta, timezone
    import rain_watch as rw
    kst = timezone(timedelta(hours=9))
    t0 = datetime(2026, 11, 4, 14, 0, tzinfo=kst)
    mk = lambda vals: [(t0 + timedelta(minutes=10 * i), v) for i, v in enumerate(vals)]
    assert rw.radar_episode(mk([0, 0.6, 0, 0, 0])) is None            # 10분 스치는 약한 비는 무시
    assert rw.radar_episode(mk([0, 1.2, 0, 0]))[0].minute == 10        # 짧아도 1mm/h↑면 비
    s, e, p, last = rw.radar_episode(mk([0, 0.6, 0, 0.7, 0.8, 0]))     # 첫 잡음 건너뛰고 지속되는 비
    assert s.minute == 30 and e.minute == 50
    obs = mk([0, 0, 0.9, 0])
    assert rw.judge({"start": (t0 + timedelta(minutes=10)).isoformat()}, obs) is True
    assert rw.judge({"start": (t0 + timedelta(minutes=10)).isoformat()}, mk([0, 0, 0.2, 0])) is False
    assert "눈이 시작돼요" in rw.radar_message(t0 + timedelta(minutes=30), None, 1, t0, "집", snow=True)


def test_redteam_brief_wording():
    temps = {h: 10 for h in range(7, 22)}
    # 아침은 추운데 낮은 따뜻 → 머리줄이 한쪽만 말하면 틀린 정보
    home = Day("집", hourly=hourly(temps), tmin=2, tmax=20, now_t=2, now_vs_yday=-4, yday_max=16)
    b = build(home, None, True, {}, {})
    assert b.headline.startswith("아침은 어제보다 꽤 쌀쌀해요, 낮은 꽤 따뜻해요"), b.headline
    # 어제도 '두꺼운 외투'를 말했고 큰 변화 없으면 오늘은 빼기
    cold = {h: 1 for h in range(7, 22)}
    d = Day("집", hourly=hourly(cold), tmin=1, tmax=1, now_vs_yday=-1, yday_max=2)
    assert any(p.startswith("두꺼운 외투") for p in build(d, None, True, {}, {}).prep)
    assert not any(p.startswith("두꺼운 외투") for p in build(d, None, True, {"prep_keys": ["coat"]}, {}).prep)
    # 확 추워진 날은 어제 말했어도 다시
    d2 = Day("집", hourly=hourly(cold), tmin=1, tmax=1, now_vs_yday=-6, yday_max=7)
    assert any(p.startswith("두꺼운 외투") for p in build(d2, None, True, {"prep_keys": ["coat"]}, {}).prep)
    # 비교도 챙길 것도 없으면 빈 메시지 대신 실패
    empty = Day("집", hourly=hourly({h: 10 for h in range(7, 22)}))
    try:
        render(build(empty, None, True, {}, {}), empty, None, date(2026, 11, 4))
        assert False, "빈 메시지가 렌더됨"
    except RuntimeError:
        pass


def test_warn_fields():
    from weather_brief import parse
    cr = {"notice~~1": {"warnList": [{"stypeNm": "호우", "warnLevelName": "주의보"}]}}
    assert parse(cr, date(2026, 11, 4), "집").warns == ["호우주의보"]


def test_selfheal_radar_guards():
    from datetime import datetime, timedelta, timezone
    from PIL import Image
    import radar
    img = Image.new("RGBA", (1197, 1253), (0, 0, 0, 0))
    for x in range(0, 400, 4):
        img.putpixel((x, 100), (15, 59, 235, 255))        # 색상표에 있는 색
    radar.check_image(img)                                 # 정상 통과
    bad = img.copy()
    for x in range(0, 400, 4):
        bad.putpixel((x, 100), (16, 60, 236, 255))         # 네이버가 색을 살짝 바꾼 상황
    try:
        radar.check_image(bad); assert False
    except radar.RadarUnavailable:
        pass
    try:
        radar.check_image(Image.new("RGBA", (1000, 1253))); assert False   # 범위 변경
    except radar.RadarUnavailable:
        pass
    kst = timezone(timedelta(hours=9))
    orig = radar.latest_frames
    radar.latest_frames = lambda: [{"obsYmdt": "202611040900", "photoYmdt": "202611040900", "photoNm": "x"}]
    try:
        radar.forecast({"a": (37.5, 127.0)}, datetime(2026, 11, 4, 12, 0, tzinfo=kst)); assert False
    except radar.RadarUnavailable as e:
        assert "멈춤" in str(e)
    finally:
        radar.latest_frames = orig


def test_selfheal_retry_and_fallback():
    import os
    import requests
    import weather_brief as wb
    os.environ["WX_NO_SLEEP"] = "1"
    calls = {"n": 0}

    class R:
        status_code = 200
        def raise_for_status(self): pass
    orig_get = requests.get
    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("일시 장애")
        return R()
    requests.get = flaky
    try:
        assert isinstance(wb.http_get("https://example.invalid"), R) and calls["n"] == 3
    finally:
        requests.get = orig_get

    orig_fetch, orig_om = wb.fetch_raw, wb.open_meteo_day
    wb.fetch_raw = lambda code: (_ for _ in ()).throw(ValueError("구조 변경"))
    wb.open_meteo_day = lambda lat, lng, today, name: Day(name, hourly=hourly({7: 5}), tmax=10)
    os.environ["DRY_RUN"] = "1"
    try:
        day, fb = wb.load_day({"code": "x", "lat": 1, "lng": 2}, date(2026, 11, 4), "집")
        assert fb and day.tmax == 10
        try:
            wb.load_day({"code": "x"}, date(2026, 11, 4), "집"); assert False   # 좌표 없으면 대체 불가 → 실패로
        except ValueError:
            pass
    finally:
        wb.fetch_raw, wb.open_meteo_day = orig_fetch, orig_om
        os.environ.pop("DRY_RUN", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
