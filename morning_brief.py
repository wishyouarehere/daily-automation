"""
아침 브리핑 텔레그램 봇
매일 07:30 KST에 날씨 / 오늘 일정 / 할 일 / 내일 일정을 텔레그램으로 전송합니다.

직접 실행: python morning_brief.py
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

# ── 환경변수 ──────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OWM_KEY = os.environ["OPENWEATHER_API_KEY"]
WEATHER_CITY = os.getenv("WEATHER_CITY", "Seoul")
TODOIST_TOKEN = os.environ["TODOIST_API_TOKEN"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]

KST = timezone(timedelta(hours=9))


# ── 텔레그램 전송 ─────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    resp.raise_for_status()


def send_error(context: str, error: Exception) -> None:
    """에러 발생 시 텔레그램으로 알림"""
    msg = f"⚠️ <b>[morning_brief 오류]</b>\n{context}\n<code>{type(error).__name__}: {error}</code>"
    try:
        send_telegram(msg)
    except Exception:
        pass  # 텔레그램 자체가 실패하면 로그만 남김
    print(f"ERROR [{context}]: {error}", file=sys.stderr)


# ── 날씨 ─────────────────────────────────────────────────────────
def get_weather() -> str:
    try:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {"q": WEATHER_CITY, "appid": OWM_KEY, "units": "metric", "lang": "kr", "cnt": 8}
        data = requests.get(url, params=params, timeout=10).json()

        # 현재 날씨 (첫 번째 항목)
        current = data["list"][0]
        desc = current["weather"][0]["description"]
        temp_now = round(current["main"]["temp"])

        # 오늘 최저/최고 (8개 항목 = 24시간)
        temps = [item["main"]["temp"] for item in data["list"]]
        temp_max = round(max(temps))
        temp_min = round(min(temps))

        # 날씨 아이콘 매핑
        icon_map = {"맑음": "☀️", "구름": "⛅", "비": "🌧", "눈": "❄️", "안개": "🌫", "흐림": "☁️"}
        icon = next((v for k, v in icon_map.items() if k in desc), "🌤")

        return f"{icon} 오늘 {WEATHER_CITY} 날씨: {desc} {temp_now}°C / 최저 {temp_min}°C 최고 {temp_max}°C"
    except Exception as e:
        send_error("날씨 조회", e)
        return "🌤 날씨 정보를 가져오지 못했습니다."


# ── Google Calendar ───────────────────────────────────────────────
def get_calendar_service():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=creds)


def get_events(service, date: datetime) -> list[dict]:
    """특정 날짜의 캘린더 이벤트 조회"""
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = date.replace(hour=23, minute=59, second=59, microsecond=0)
    result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def format_events(events: list[dict]) -> list[str]:
    lines = []
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        if "T" in start:
            # 시간 있는 이벤트
            dt = datetime.fromisoformat(start).astimezone(KST)
            time_str = dt.strftime("%H:%M")
        else:
            time_str = "종일"
        lines.append(f"  {time_str} {e.get('summary', '(제목 없음)')}")
    return lines


def get_schedule_section() -> tuple[str, str]:
    """오늘/내일 일정 섹션 반환 (today_text, tomorrow_text)"""
    try:
        service = get_calendar_service()
        now = datetime.now(KST)
        today_events = get_events(service, now)
        tomorrow_events = get_events(service, now + timedelta(days=1))

        today_lines = format_events(today_events) or ["  일정 없음"]
        tomorrow_lines = format_events(tomorrow_events) or ["  일정 없음"]

        return "\n".join(today_lines), "\n".join(tomorrow_lines)
    except Exception as e:
        send_error("캘린더 조회", e)
        return "  캘린더 정보를 가져오지 못했습니다.", "  캘린더 정보를 가져오지 못했습니다."


# ── Todoist ───────────────────────────────────────────────────────
def get_todoist_today() -> str:
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        resp = requests.get("https://api.todoist.com/rest/v2/tasks", headers=headers, timeout=10)
        resp.raise_for_status()
        tasks = resp.json()

        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        today_tasks = []
        for t in tasks:
            due = t.get("due")
            if due and due.get("date") == today_str:
                today_tasks.append(t)

        if not today_tasks:
            return "  오늘 할 일 없음"

        # 프로젝트 ID → 이름 매핑
        proj_resp = requests.get("https://api.todoist.com/rest/v2/projects", headers=headers, timeout=10)
        proj_map = {p["id"]: p["name"] for p in proj_resp.json()} if proj_resp.ok else {}

        lines = []
        for t in today_tasks:
            proj_name = proj_map.get(t.get("project_id"), "")
            label = f"[{proj_name}] " if proj_name else ""
            lines.append(f"  · {label}{t['content']}")

        return "\n".join(lines)
    except Exception as e:
        send_error("Todoist 조회", e)
        return "  할 일 정보를 가져오지 못했습니다."


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일 (%a)")

    weather = get_weather()
    today_sched, tomorrow_sched = get_schedule_section()
    todos = get_todoist_today()

    message = f"""📋 <b>아침 브리핑 — {date_str}</b>

🌤 {weather}

📅 <b>오늘 일정</b>
{today_sched}

✅ <b>오늘 할 일</b>
{todos}

📌 <b>내일 일정 미리보기</b>
{tomorrow_sched}"""

    send_telegram(message)
    print("✅ 아침 브리핑 전송 완료")


if __name__ == "__main__":
    main()
