"""radar.forecast — 이미지가 아닌 프레임 응답은 건너뛴다(2026-11-24 'cannot identify image file' 반복 경보)."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import radar

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 25, 10, 30, tzinfo=KST)


class Resp:
    def __init__(self, content):
        self.content = content


def _png():
    w = 400
    want = (radar._mx(radar.NE_LNG) - radar._mx(radar.SW_LNG)) / (radar._my(radar.SW_LAT) - radar._my(radar.NE_LAT))
    buf = io.BytesIO()
    Image.new("RGBA", (w, round(w / want)), (0, 0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _frames(n=3):
    obs = "202611251020"
    return [{"obsYmdt": obs, "photoYmdt": (NOW + timedelta(minutes=10 * i)).strftime("%Y%m%d%H%M"),
             "photoNm": f"f{i}.png"} for i in range(n)]


def _setup(monkeypatch, bodies):
    monkeypatch.setattr(radar, "latest_frames", lambda: _frames(len(bodies)))
    calls = []

    def fake_get(url, **kw):
        name = url.rsplit("/", 1)[-1]
        calls.append(name)
        return Resp(bodies[int(name[1])])

    monkeypatch.setattr(radar, "http_get", fake_get)
    return calls


def test_non_image_frame_skipped(monkeypatch):
    png = _png()
    calls = _setup(monkeypatch, [png, b"<html>not yet</html>", png])
    out = radar.forecast({"home": (37.5, 127.0)}, NOW)["home"]
    assert len(out) == 2
    assert calls.count("f1.png") == 2   # 한 번 더 받아 봄


def test_all_frames_non_image_raises_unavailable(monkeypatch):
    _setup(monkeypatch, [b"", b"<html>", b"x"])
    with pytest.raises(radar.RadarUnavailable, match="이미지가 아닌 응답"):
        radar.forecast({"home": (37.5, 127.0)}, NOW)
