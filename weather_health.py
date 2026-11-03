"""날씨 잡 새벽 건강검사 (2026-11-03). 매일 04:15 cron.

아침 날씨(06:50)·비 알림(07시~)이 기대는 외부 구조가 그대로인지 미리 본다.
깨졌으면 마지막 줄에 원인을 남기고 종료코드 1 → cron-status → 04:40 auto_repair가 Claude로 원인·수정안을 만든다.
(daily-automation은 공개 저장소라 auto_repair가 자동 반영하지 않고 수정안을 준비해 Jay 확인을 받는다.)
그동안 본 잡들은 대체 경로(Open-Meteo·시간 단위 예보)로 계속 돈다.

검사: 네이버 날씨 페이지 JSON(시간대별·오늘 최고·어제 비교), 레이더 목록 신선도·이미지 비율·색상표,
대체 소스 Open-Meteo 응답.
"""
from __future__ import annotations

import io
from datetime import datetime
import sys

import jay_desk
import weather_brief as wb


def main() -> int:
    now = jay_desk.now()
    today = now.date()
    cfg = wb.load_config()
    problems: list[str] = []

    try:
        d = wb.parse(wb.fetch_raw(cfg["home"]["code"]), today, "집")
        if not d.hourly:
            problems.append("네이버 날씨: 시간대별 예보(hourlyFcast~~1.domesticHourlyFcastList)가 비어 있음")
        if d.tmax is None:
            problems.append("네이버 날씨: 오늘 최고기온(weeklyFcastList)이 없음")
        if d.now_vs_yday is None:
            problems.append("네이버 날씨: 어제 대비(nowFcast.ytmpr)가 없음")
    except Exception as e:  # noqa: BLE001
        problems.append(f"네이버 날씨 페이지 파싱 실패: {e}")

    try:
        import radar
        from PIL import Image
        frames = radar.latest_frames()
        if not frames:
            raise radar.RadarUnavailable("레이더 프레임 없음")
        img = Image.open(io.BytesIO(wb.http_get(radar.IMG_URL + frames[0]["photoNm"], timeout=15).content)).convert("RGBA")
        radar.check_image(img)
        # 새벽엔 발표가 늦을 수 있어 신선도는 넉넉히(3시간) 본다
        issued = frames[0]["obsYmdt"]
        age_min = (now.replace(tzinfo=None) - datetime.strptime(issued, "%Y%m%d%H%M")).total_seconds() / 60
        if age_min > 180:
            problems.append(f"레이더: 최신 발표가 {issued}로 {age_min:.0f}분째 멈춤")
    except Exception as e:  # noqa: BLE001
        problems.append(f"레이더 초단기강수예측 점검 실패: {e}")

    if "lat" in cfg["home"]:
        try:
            wb.open_meteo_day(cfg["home"]["lat"], cfg["home"]["lng"], today, "집")
        except Exception as e:  # noqa: BLE001
            problems.append(f"대체 소스 Open-Meteo 실패: {e}")

    if problems:
        for p in problems:
            print(f"[fail] {p}")
        print(f"날씨 건강검사 실패 {len(problems)}건: {problems[0]}")
        return 1
    print(f"[ok] {now:%H:%M} 날씨 건강검사 통과(네이버·레이더·대체 소스)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
