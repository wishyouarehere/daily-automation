"""레이더 초단기강수예측 지점값 (2026-11-03).

네이버 날씨 지도 '초단기강수예측'(기상청 MAPLE/MOTION, 10분 간격·발표 후 2시간)을 키 없이 읽는다.
네이버 지도 자체가 하는 방식 그대로: 최신 발표 회차 PNG를 받아 메르카토르 좌표로 픽셀을 찾고,
색상표(oRainColorGuideMap, mm/h)로 강수 강도를 읽는다. 경로는 Aside 브라우저로 실측(2026-11-03).

- 목록: weather.naver.com/choiceApi/api {"visualMapSrmImage": {"photoType": "maple", "limit": N}}
- 이미지: ssl.pstatic.net/staticv2.weather/responsive/image/srmImage/<photoNm>
- 범위: oPhotoBounds.MAPLE (이미지 가로세로비가 메르카토르 범위와 일치함을 확인)
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime

import requests
from PIL import Image

from weather_brief import UA

LIST_URL = "https://weather.naver.com/choiceApi/api"
IMG_URL = "https://ssl.pstatic.net/staticv2.weather/responsive/image/srmImage/"
SW_LAT, SW_LNG, NE_LAT, NE_LNG = 30.9480170012299, 121.515108482848, 40.082924711855, 132.258959280917
SAMPLE_R = 1  # 주변 3×3 픽셀(약 ±0.8km) 중 최댓값 — 경계 번짐·좌표 오차 흡수

# 네이버 지도 oRainColorGuideMap (mm/h). "110~"·"~0.1"은 숫자로 바꿔 둔다.
_COLORS = {
    (51,51,52):110,(49,50,56):109,(46,48,60):108,(44,46,65):107,(41,44,69):106,(39,42,73):105,(36,40,78):104,
    (34,38,82):103,(31,36,87):102,(29,34,91):101,(26,32,95):100,(23,31,100):99,(21,29,104):98,(18,27,108):97,
    (16,25,113):96,(13,23,117):95,(11,21,121):94,(8,19,126):93,(6,17,130):92,(3,15,134):91,(0,13,138):90,
    (9,21,142):89,(16,27,145):88,(24,34,149):87,(31,41,152):86,(39,47,155):85,(46,54,159):84,(54,61,162):83,
    (62,67,165):82,(69,74,168):81,(76,80,171):80,(87,91,177):79,(98,101,181):78,(108,111,186):77,
    (118,121,191):76,(129,132,196):75,(139,142,201):74,(149,152,205):73,(159,162,210):72,(170,172,215):71,
    (179,181,219):70,(175,167,220):69,(170,152,220):68,(166,138,220):67,(161,124,220):66,(156,109,220):65,
    (152,95,220):64,(147,81,220):63,(143,67,220):62,(138,52,220):61,(133,38,219):60,(137,41,222):59,
    (140,43,225):58,(143,46,228):57,(146,48,230):56,(149,51,233):55,(152,53,236):54,(155,55,239):53,
    (158,58,241):52,(161,60,244):51,(163,62,246):50,(166,68,247):49,(168,73,247):48,(171,78,247):47,
    (173,84,247):46,(176,89,247):45,(178,94,247):44,(180,99,247):43,(183,104,247):42,(185,110,247):41,
    (187,114,247):40,(190,121,248):39,(193,127,248):38,(196,132,248):37,(199,138,249):36,(202,144,249):35,
    (204,150,249):34,(207,156,250):33,(210,162,250):32,(213,168,250):31,(215,173,250):30,(205,144,202):29,
    (194,115,155):28,(184,88,110):27,(173,60,65):26,(163,33,21):25,(169,35,23):24,(175,36,24):23,
    (181,38,25):22,(186,39,26):21,(191,40,27):20,(200,47,30):19,(208,53,32):18,(216,59,34):17,(225,66,36):16,
    (232,71,38):15,(233,80,40):14,(234,88,42):13,(234,96,43):12,(235,104,45):11,(235,111,46):10,
    (198,170,57):9,(217,186,63):8,(241,206,70):7,(249,220,81):6,(250,225,77):5,(39,88,21):4,(65,137,38):3,
    (90,186,55):2,(123,250,77):1,(102,212,110):0.9,(80,174,141):0.8,(58,135,173):0.7,(37,97,204):0.6,
    (15,59,235):0.5,(30,84,236):0.4,(44,108,237):0.3,(58,131,238):0.2,(71,154,238):0.1,(95,198,250):0.05,
    (0,0,0):0,
}


def _mx(lng: float) -> float:
    return (180 + lng) / 360


def _my(lat: float) -> float:
    return (180 - 180 / math.pi * math.log(math.tan(math.pi / 4 + lat * math.pi / 360))) / 360


def pixel(lat: float, lng: float, w: int, h: int) -> tuple[int, int]:
    x = (_mx(lng) - _mx(SW_LNG)) / (_mx(NE_LNG) - _mx(SW_LNG)) * w
    y = (_my(lat) - _my(NE_LAT)) / (_my(SW_LAT) - _my(NE_LAT)) * h
    return round(x), round(y)


def color_value(rgba: tuple) -> float:
    r, g, b = rgba[:3]
    if len(rgba) > 3 and rgba[3] == 0:
        return 0.0
    if (r, g, b) in _COLORS:
        return float(_COLORS[(r, g, b)])
    # 색상표에 없는 색(번짐)은 가장 가까운 색으로. 너무 멀면(지도 바탕 등) 0.
    best = min(_COLORS, key=lambda c: (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2)
    if sum((a - b2) ** 2 for a, b2 in zip(best, (r, g, b))) > 30 ** 2:
        return 0.0
    return float(_COLORS[best])


def value_at(img: Image.Image, lat: float, lng: float) -> float:
    w, h = img.size
    x0, y0 = pixel(lat, lng, w, h)
    px = img.load()
    vals = [color_value(px[x, y])
            for x in range(max(0, x0 - SAMPLE_R), min(w, x0 + SAMPLE_R + 1))
            for y in range(max(0, y0 - SAMPLE_R), min(h, y0 + SAMPLE_R + 1))]
    return max(vals, default=0.0)


def latest_frames() -> list[dict]:
    """최신 발표 회차의 프레임들(시각 오름차순)."""
    q = json.dumps({"visualMapSrmImage": {"photoType": "maple", "limit": 24}})
    r = requests.get(LIST_URL, params={"choiceQuery": q}, headers=UA, timeout=15)
    r.raise_for_status()
    items = r.json()["results"]["choiceResult"]["visualMapSrmImage"]
    if not items:
        return []
    obs = max(x["obsYmdt"] for x in items)
    return sorted((x for x in items if x["obsYmdt"] == obs), key=lambda x: x["photoYmdt"])


def forecast(points: dict[str, tuple[float, float]], now: datetime) -> dict[str, list[tuple[datetime, float]]]:
    """지점별 [(시각, mm/h)] — 지금 이후(10분 전 프레임 포함) 프레임만."""
    out: dict[str, list[tuple[datetime, float]]] = {k: [] for k in points}
    for f in latest_frames():
        t = datetime.strptime(f["photoYmdt"], "%Y%m%d%H%M").replace(tzinfo=now.tzinfo)
        if (now - t).total_seconds() > 600:
            continue
        r = requests.get(IMG_URL + f["photoNm"], headers=UA, timeout=15)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        for k, (lat, lng) in points.items():
            out[k].append((t, value_at(img, lat, lng)))
    return out


def observed(point: tuple[float, float], t0: datetime, t1: datetime, tz) -> list[tuple[datetime, float]]:
    """[t0, t1] 사이 레이더 관측(발표 시각=관측 시각인 프레임) 지점값 — 알림 검증용."""
    q = json.dumps({"visualMapSrmImage": {"photoType": "maple", "limit": 72}})
    r = requests.get(LIST_URL, params={"choiceQuery": q}, headers=UA, timeout=15)
    r.raise_for_status()
    out = []
    for f in r.json()["results"]["choiceResult"]["visualMapSrmImage"]:
        if f["obsYmdt"] != f["photoYmdt"]:
            continue
        t = datetime.strptime(f["photoYmdt"], "%Y%m%d%H%M").replace(tzinfo=tz)
        if not t0 <= t <= t1:
            continue
        img = Image.open(io.BytesIO(requests.get(IMG_URL + f["photoNm"], headers=UA, timeout=15).content)).convert("RGBA")
        out.append((t, value_at(img, *point)))
    return sorted(out)
