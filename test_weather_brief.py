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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
