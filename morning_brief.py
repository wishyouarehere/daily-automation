"""
아침 브리핑 텔레그램 봇
매일 07:30 KST에 날씨 / 오늘 일정 / 할 일 / 내일 일정 / 다니엘프로젝트 브리핑을 전송합니다.

직접 실행: python morning_brief.py
"""

import os
import re
import sys
import requests
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import anthropic

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
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

# workflowy-sync 레포 파일들 (GitHub API)
INDEX_REPO = "wishyouarehere/workflowy-sync"
INDEX_FILE = "_INDEX.md"
CONTEXT_FILE = "_CONTEXT.md"
DAILY_FILE = "_DAILY_LATEST.md"
DANIEL_DEADLINE = datetime(2026, 7, 20, tzinfo=timezone(timedelta(hours=9)))

KST = timezone(timedelta(hours=9))


# ── 텔레그램 전송 ─────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    resp.raise_for_status()


def send_error(context: str, error: Exception) -> None:
    msg = f"⚠️ <b>[morning_brief 오류]</b>\n{context}\n<code>{type(error).__name__}: {error}</code>"
    try:
        send_telegram(msg)
    except Exception:
        pass
    print(f"ERROR [{context}]: {error}", file=sys.stderr)


# ── 날씨 ─────────────────────────────────────────────────────────
def get_weather() -> str:
    try:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {"q": WEATHER_CITY, "appid": OWM_KEY, "units": "metric", "lang": "kr", "cnt": 8}
        data = requests.get(url, params=params, timeout=10).json()

        current = data["list"][0]
        desc = current["weather"][0]["description"]
        temp_now = round(current["main"]["temp"])

        temps = [item["main"]["temp"] for item in data["list"]]
        temp_max = round(max(temps))
        temp_min = round(min(temps))

        icon_map = {"맑음": "☀️", "구름": "⛅", "비": "🌧", "눈": "❄️", "안개": "🌫", "흐림": "☁️"}
        icon = next((v for k, v in icon_map.items() if k in desc), "🌤")

        return f"{icon} {escape(desc)} {temp_now}°C  🔻{temp_min} 🔺{temp_max}"
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
            dt = datetime.fromisoformat(start).astimezone(KST)
            time_str = dt.strftime("%H:%M")
        else:
            time_str = "종일"
        lines.append(f"  {time_str} {escape(e.get('summary', '(제목 없음)'))}")
    return lines


def get_schedule_section() -> tuple[str, str]:
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


# ── Todoist 오늘 할 일 ────────────────────────────────────────────
def get_todoist_today() -> str:
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        all_tasks = []
        cursor = None
        while True:
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get("https://api.todoist.com/api/v1/tasks", headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            all_tasks.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not cursor:
                break

        today_tasks = [
            t for t in all_tasks
            if t.get("due") and t["due"].get("date", "").startswith(today_str)
        ]

        if not today_tasks:
            return "  오늘 할 일 없음"

        proj_resp = requests.get("https://api.todoist.com/api/v1/projects", headers=headers, timeout=10)
        proj_data = proj_resp.json()
        proj_list = proj_data.get("results", proj_data) if isinstance(proj_data, dict) else proj_data
        proj_map = {p["id"]: p["name"] for p in proj_list} if proj_resp.ok else {}

        lines = []
        for t in today_tasks:
            proj_name = escape(proj_map.get(t.get("project_id"), ""))
            label = f"[{proj_name}] " if proj_name else ""
            lines.append(f"  · {label}{escape(t['content'])}")

        return "\n".join(lines)
    except Exception as e:
        send_error("Todoist 조회", e)
        return "  할 일 정보를 가져오지 못했습니다."


# ── Todoist 어제 완료 항목 ────────────────────────────────────────
def get_todoist_completed_yesterday() -> list[str]:
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        now = datetime.now(KST)
        yesterday = now - timedelta(days=1)
        since = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        until = yesterday.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

        resp = requests.get(
            "https://api.todoist.com/api/v1/tasks/completed/get_all",
            headers=headers,
            params={"since": since, "until": until, "limit": 50},
            timeout=10,
        )
        if not resp.ok:
            return []

        items = resp.json().get("items", [])
        return [escape(item["content"]) for item in items]
    except Exception:
        return []


# ── GitHub에서 파일 읽기 ─────────────────────────────────────────
def fetch_github_file(filename: str) -> str:
    try:
        import base64
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.get(
            f"https://api.github.com/repos/{INDEX_REPO}/contents/{filename}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return base64.b64decode(resp.json()["content"]).decode("utf-8")
    except Exception as e:
        send_error(f"{filename} GitHub 조회", e)
        return ""


# ── _INDEX.md 에서 미결 항목 파싱 ────────────────────────────────
def get_index_pending(text: str) -> list[str]:
    items = re.findall(r"- \[ \] (.+)", text)
    return [escape(item.strip()) for item in items]


# ── _INDEX.md 에서 이번 주 주요 일정 파싱 ────────────────────────
def get_index_weekly(text: str) -> list[str]:
    section = re.search(r"이번 주 포커스\n(.*?)(?=\n#|\n---|\Z)", text, re.DOTALL)
    if not section:
        return []
    return [
        escape(l.lstrip("- ·").strip())
        for l in section.group(1).splitlines()
        if l.strip().startswith(("-", "·", "*"))
    ]


# ── Claude 한마디 생성 ────────────────────────────────────────────
def get_claude_comment(index_text: str, context_text: str, daily_text: str, completed: list[str]) -> str:
    try:
        if not index_text and not completed and not daily_text:
            return "  어제 기록을 찾을 수 없습니다."

        completed_text = "\n".join(f"- {c}" for c in completed) if completed else "없음"

        prompt = f"""당신은 20년차 CPO 장홍석(Jay)의 업무 어드바이저입니다.
Jay는 현재 다니엘프로젝트 부대표/CPO로, 7/20 전사 데이원 전환 데드라인을 앞두고 있습니다.

아래 정보를 종합해서, 오늘 아침 Jay에게 가장 유용한 관찰 3개를 뽑아주세요.

규칙:
- 각 항목은 마크다운 기호 없이 plain text, 30자 이내로 끊을 것
- 번호 없이 각 줄을 "· "로 시작
- 서로 다른 영역 (제품 진행 상황 / 사람·조직 / 오늘 결정해야 할 것)
- 조언이 아닌 날카로운 관찰. "~하세요" 금지
- 어제 기록과 현재 컨텍스트를 교차해서 비자명한 것을 짚을 것
- 문장이 길어질 것 같으면 핵심 주어+술어만 남기고 나머지 버릴 것

[조직/팀 전체 컨텍스트]
{context_text}

[현재 프로젝트 현황]
{index_text}

[어제 업무 기록]
{daily_text}

[Todoist 어제 완료]
{completed_text}"""

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        # 짤림 감지
        if message.stop_reason == "max_tokens":
            print("WARNING: 어드바이저 노트 max_tokens 도달 — 짤림 발생", file=sys.stderr)
        # 마크다운 제거 (**, *, __, #, ` 등)
        text = message.content[0].text.strip()
        text = re.sub(r"[*_`#]+", "", text).strip()
        return f"  {text}"
    except Exception as e:
        send_error("Claude 한마디 생성", e)
        return "  (생성 실패)"


# ── 다니엘프로젝트 브리핑 섹션 ───────────────────────────────────
def get_daniel_section() -> str:
    try:
        now = datetime.now(KST)
        d_day = (DANIEL_DEADLINE - now).days

        # GitHub에서 컨텍스트 파일들 읽기
        index_text   = fetch_github_file(INDEX_FILE)
        context_text = fetch_github_file(CONTEXT_FILE)
        daily_text   = fetch_github_file(DAILY_FILE)

        # 어제 완료 항목 (Todoist)
        todoist_done = get_todoist_completed_yesterday()
        all_done = [f"  · {escape(t)}" for t in todoist_done]
        done_text = "\n".join(all_done) if all_done else "  기록 없음"

        # 미결 항목 (_INDEX.md 파싱)
        pending = get_index_pending(index_text)
        pending_text = "\n".join(f"  · {p}" for p in pending[:4]) if pending else "  없음"

        # 이번 주 주요 일정 (_INDEX.md 파싱)
        weekly = get_index_weekly(index_text)
        weekly_text = "\n".join(f"  · {w}" for w in weekly[:3]) if weekly else "  없음"

        # 어드바이저 노트
        claude_comment = get_claude_comment(index_text, context_text, daily_text, todoist_done)

        return f"""━━━━━━━━━━━━━━━
🏢 <b>다니엘프로젝트</b>  ·  D-{d_day} | 7/20 전사 전환

🔴 <b>오늘 포커스 (미결)</b>
{pending_text}

📅 <b>이번 주 주요 일정</b>
{weekly_text}

✅ <b>어제 완료</b>
{done_text}

⚡ <b>어드바이저 노트</b>
{claude_comment}"""
    except Exception as e:
        send_error("다니엘프로젝트 브리핑", e)
        return "━━━━━━━━━━━━━━━\n🏢 다니엘프로젝트 브리핑을 가져오지 못했습니다."


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    day_kr = "월화수목금토일"[now.weekday()]
    date_str = now.strftime(f"%y/%m/%d/{day_kr}")

    weather = get_weather()
    today_sched, tomorrow_sched = get_schedule_section()
    todos = get_todoist_today()
    daniel = get_daniel_section()

    message = f"""📋 <b>아침 브리핑 — {date_str}</b>

🌤 {weather}

━━━━━━━━━━━━━━━
📅 <b>오늘 일정</b>
{today_sched}

✅ <b>오늘 할 일</b>
{todos}

📌 <b>내일 일정 미리보기</b>
{tomorrow_sched}

{daniel}"""

    send_telegram(message)
    print("✅ 아침 브리핑 전송 완료")


if __name__ == "__main__":
    main()
