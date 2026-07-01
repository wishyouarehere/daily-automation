"""
아침 브리핑 텔레그램 봇 — 참모 브리핑 형식(3블록).
매일 06:30 KST. ① 오늘 한눈에(무게중심·시간표·꼭할일·내일) ② 참모 판단(결정+레벨) ③ 백그라운드(접이식).

설계 원칙: 데이터 덤프가 아니라 '판단'이 본체. 원천 데이터는 압축하거나 접는다.
요일 변주: 월=위클리 대비 / 금=주간 회고 / 주말=경량. (골격 고정, 블록 ②만 성격 변형)

직접 실행: python morning_brief.py
요일 강제 테스트: FORCE_WEEKDAY=0(월)~6(일) python morning_brief.py
드라이런(전송 안 함, stdout만): DRY_RUN=1 python morning_brief.py
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, date, timedelta, timezone
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
# 컨텍스트 파일 소스 분기:
#   - GITHUB_TOKEN 있음(예: GitHub Actions) → workflowy-sync 레포에서 API로 읽기
#   - 없음(예: 집맥 로컬 cron/launchd) → 로컬 미러 디렉토리(~/wf-sync)에서 직접 읽기
# 로컬 미러(_INDEX.md·_CONTEXT.md·_DAILY_LATEST.md)는 wf_sync.py가 볼트→복사해 두는 정본이라
# GitHub 미러와 동일 내용. 이렇게 분기하면 Actions·로컬 양쪽 다 안전하고 토큰 의존이 사라진다.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MIRROR_DIR = Path(os.getenv("WF_MIRROR_DIR", str(Path.home() / "wf-sync")))

# workflowy-sync 레포 파일들 (GitHub API)
INDEX_REPO = "wishyouarehere/workflowy-sync"
INDEX_FILE = "_INDEX.md"
CONTEXT_FILE = "_CONTEXT.md"
DAILY_FILE = "_DAILY_LATEST.md"

# 다니엘 마일스톤(권위값 = 볼트 _INDEX/결정-로그). 이미 지난 건 자동 생략.
#   7/3 = 데이원 광고주 v1 출시 · 8/3 = Day 0 전사 전환(v2 출시)
MILESTONES = [
    (date(2026, 7, 3), "7/3 v1"),
    (date(2026, 8, 3), "8/3 전사전환"),
]

KST = timezone(timedelta(hours=9))

# 참모 브리핑 본문 생성 모델
CHIEF_MODEL = "claude-sonnet-4-6"


# ── 텔레그램 전송 ─────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()


def send_error(context: str, error: Exception) -> None:
    msg = f"⚠️ <b>[morning_brief 오류]</b>\n{context}\n<code>{type(error).__name__}: {error}</code>"
    try:
        send_telegram(msg)
    except Exception:
        pass
    print(f"ERROR [{context}]: {error}", file=sys.stderr)


# ── 날씨 (조건부 꼬리표 — 행동 바꾸는 날씨만) ──────────────────────
def get_weather_tag() -> str:
    """헤더에 붙일 짧은 날씨 꼬리표. 비/눈/소나기/극한기온일 때만 반환, 평범하면 "".
    25도 맑음을 매일 한 줄 쓰는 noise를 없앤다."""
    try:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {"q": WEATHER_CITY, "appid": OWM_KEY, "units": "metric", "lang": "kr", "cnt": 8}
        data = requests.get(url, params=params, timeout=10).json()

        desc = data["list"][0]["weather"][0]["description"]
        temps = [item["main"]["temp"] for item in data["list"]]
        temp_max = round(max(temps))
        temp_min = round(min(temps))

        wet = next((k for k in ("비", "소나기", "눈") if k in desc), None)
        hot = temp_max >= 33
        cold = temp_min <= 0

        if not (wet or hot or cold):
            return ""  # 평범한 날 → 표시 안 함

        icon = {"비": "🌧", "소나기": "🌦", "눈": "❄️"}.get(wet, "🌡")
        bits = []
        if wet:
            bits.append(f"{icon} {wet}")
        if hot:
            bits.append(f"🔺{temp_max}° 더위")
        if cold:
            bits.append(f"🔻{temp_min}° 추위")
        return " · ".join(bits)
    except Exception as e:
        send_error("날씨 조회", e)
        return ""


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
        lines.append(f"  {time_str}  {escape(e.get('summary') or '(제목 없음)')}")
    return lines


def _tomorrow_oneline(events: list[dict]) -> str:
    """내일 일정을 한 줄로 압축. 아침 머리세팅엔 '내일 빡센가' 정도만 필요."""
    lines = format_events(events)
    if not lines:
        return "한가"
    heads = [l.strip() for l in lines[:2]]
    more = f" 외 {len(lines) - 2}건" if len(lines) > 2 else ""
    return " / ".join(heads) + more


def get_schedule_section() -> tuple[str, str, str]:
    """returns (오늘 시간표 문자열, 내일 한 줄, LLM 교차용 오늘 일정 평문)."""
    try:
        service = get_calendar_service()
        now = datetime.now(KST)
        today_events = get_events(service, now)
        tomorrow_events = get_events(service, now + timedelta(days=1))

        today_lines = format_events(today_events) or ["  일정 없음"]
        today_for_llm = "\n".join(l.strip() for l in today_lines)
        return "\n".join(today_lines), _tomorrow_oneline(tomorrow_events), today_for_llm
    except Exception as e:
        send_error("캘린더 조회", e)
        return "  캘린더 정보를 가져오지 못했습니다.", "확인 필요", ""


# ── URL 제거 (마크다운 링크 → 제목만, 단독 URL 삭제) ──────────────
def strip_urls(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(https?://[^\)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ── Todoist 오늘 할 일 (항목 리스트 — 우선순위 정렬) ──────────────
def get_todoist_today_items() -> list[dict]:
    """오늘 due 항목을 우선순위 내림차순으로. 각 {content, project, priority}.
    Todoist priority: 4=p1(최고)…1=p4. content는 URL 제거·escape 완료."""
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
            return []

        proj_resp = requests.get("https://api.todoist.com/api/v1/projects", headers=headers, timeout=10)
        proj_data = proj_resp.json()
        proj_list = proj_data.get("results", proj_data) if isinstance(proj_data, dict) else proj_data
        proj_map = {p["id"]: p["name"] for p in proj_list} if proj_resp.ok else {}

        items = []
        for t in today_tasks:
            content = strip_urls(t["content"])
            if not content:
                continue
            items.append({
                "content": escape(content),
                "project": escape(proj_map.get(t.get("project_id"), "")),
                "priority": t.get("priority", 1),
            })
        # 우선순위 내림차순(높은 게 위), 동순위는 원래 순서 보존
        items.sort(key=lambda x: -x["priority"])
        return items
    except Exception as e:
        send_error("Todoist 조회", e)
        return []


def format_todo_top(items: list[dict], n: int = 3) -> str:
    """블록 ① '꼭 할 일': 상위 n개를 한 줄씩 세로로(여백)."""
    if not items:
        return "<b>꼭 할 일</b>\n  오늘 지정된 할 일 없음"
    picks = [it["content"] for it in items[:n]]
    return "<b>꼭 할 일</b>\n" + "\n".join(f"  ▢ {c}" for c in picks)


# ── Todoist 재시도 GET ────────────────────────────────────────────
def todoist_get(url: str, headers: dict, params: dict = None, retries: int = 3, timeout: int = 15):
    """5xx 오류 시 최대 3회 재시도 (2초 간격)."""
    import time
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code in (500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last_exc


# ── Todoist 어제 완료 항목 ────────────────────────────────────────
def get_todoist_completed_yesterday() -> list[str]:
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        now = datetime.now(KST)
        yesterday = now - timedelta(days=1)
        since = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        until = yesterday.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

        resp = todoist_get(
            "https://api.todoist.com/api/v1/tasks/completed/by_completion_date",
            headers=headers,
            params={"since": since, "until": until, "limit": 50},
        )
        items = resp.json().get("items", [])

        # 프로젝트 ID → 이름 매핑 (관리함 제외용)
        try:
            proj_resp = todoist_get("https://api.todoist.com/api/v1/projects", headers=headers)
            proj_data = proj_resp.json()
            proj_list = proj_data.get("results", proj_data) if isinstance(proj_data, dict) else proj_data
            # is_inbox_project 플래그가 있는 프로젝트 ID 수집
            inbox_ids = {str(p["id"]) for p in proj_list if p.get("is_inbox_project")}
        except Exception:
            inbox_ids = set()

        import re
        def strip_urls(text: str) -> str:
            # 마크다운 링크 [제목](url) → 제목만 남기기
            text = re.sub(r'\[([^\]]+)\]\(https?://[^\)]+\)', r'\1', text)
            # 남은 단독 URL 제거
            text = re.sub(r'https?://\S+', '', text)
            return text.strip()

        return [
            escape(strip_urls(item["content"]))
            for item in items
            if str(item.get("project_id", "")) not in inbox_ids
            and strip_urls(item["content"])  # URL만 있던 항목은 빈 문자열 → 제외
        ]
    except Exception as e:
        send_error("Todoist 어제 완료 항목 조회", e)
        return []


# ── 컨텍스트 파일 읽기 (로컬 미러 우선, 토큰 있으면 GitHub) ────────
def fetch_github_file(filename: str) -> str:
    if not GITHUB_TOKEN:
        # 로컬 모드: ~/wf-sync 미러에서 직접 읽기
        try:
            p = MIRROR_DIR / filename
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception as e:
            send_error(f"{filename} 로컬 조회", e)
            return ""
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


# ── 데이터 신선도 (파일이 며칠 묵었나) ───────────────────────────
def file_age_days(filename: str):
    """해당 파일이 며칠 묵었는지. 로컬 모드=파일 mtime, GitHub 모드=마지막 커밋 기준. 실패 시 None."""
    if not GITHUB_TOKEN:
        try:
            p = MIRROR_DIR / filename
            if not p.exists():
                return None
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            return (datetime.now(timezone.utc) - mtime).days
        except Exception:
            return None
    try:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.get(
            f"https://api.github.com/repos/{INDEX_REPO}/commits",
            headers=headers, params={"path": filename, "per_page": 1}, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        dt = datetime.fromisoformat(data[0]["commit"]["committer"]["date"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def get_freshness_warning() -> str:
    """묵은 데이터가 있을 때만 ⚠️ 한 줄. 다 신선하면 "" (매일 green 표시 안 함).
    임계치: 컨텍스트 14일·INDEX 2일·기록 4일 이상."""
    checks = [("컨텍스트", CONTEXT_FILE, 14), ("INDEX", INDEX_FILE, 2), ("기록", DAILY_FILE, 4)]
    stale = []
    for label, fn, threshold in checks:
        d = file_age_days(fn)
        if d is not None and d >= threshold:
            stale.append(f"{label} {d}d")
    return ("⚠️ 신선도 — " + " · ".join(stale)) if stale else ""


# ── _INDEX.md 섹션 불릿 파서 (## 헤딩 기준 — 헤더 주석 '-->' 오탐 방지) ──
def _section_bullets(text: str, heading_kw: str) -> list[str]:
    m = re.search(
        r"^##[^\n]*" + re.escape(heading_kw) + r"[^\n]*\n(.*?)(?=^## |\Z)",
        text, re.DOTALL | re.MULTILINE,
    )
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s.startswith(("- ", "· ", "* ")):
            continue
        s = re.sub(r"^[-·*]\s*", "", s)        # 불릿 기호 제거
        s = re.sub(r"^\[[ xX]\]\s*", "", s)     # 체크박스 제거
        s = re.sub(r"[*_`]+", "", s)             # 마크다운 강조 제거
        s = s.strip()
        if s:
            out.append(escape(s, quote=False))   # 아포스트로피 보존(텔레그램)
    return out


# ── 🔴 오늘 포커스(미결) = _INDEX '열린 막힘 / 결정 대기' 섹션 ──
def get_index_pending(text: str) -> list[str]:
    return _section_bullets(text, "열린 막힘")


# ── 📅 이번 주 주요 일정 = _INDEX '이번 주 포커스' 섹션 ──
def get_index_weekly(text: str) -> list[str]:
    return _section_bullets(text, "이번 주 포커스")


# ── D-day (지난 마일스톤 자동 생략) ───────────────────────────────
def dday_label() -> str:
    today = datetime.now(KST).date()
    parts = [f"D-{(d - today).days} ({label})" for d, label in MILESTONES if (d - today).days >= 0]
    return " · ".join(parts) if parts else "다니엘프로젝트"


# ── 참모 팩 로드 (~/wf-sync/cache, chief_pack 모듈 재사용) ─────────
def load_chief_pack() -> str:
    """매일 ~05:40 갱신되는 농축 참모 팩(인물·관계·막힘). 없거나 stale이면 "".
    iCloud 볼트를 직접 읽지 않고 캐시 파일만 본다(launchd TCC 회피)."""
    try:
        if str(MIRROR_DIR) not in sys.path:
            sys.path.insert(0, str(MIRROR_DIR))
        import chief_pack
        return chief_pack.load() or ""
    except Exception:
        return ""


# ── 참모 브리핑 본문 (단일 LLM 콜: 무게중심 + 판단 0~3건) ──────────
_MODE_INSTRUCTION = {
    "standard": "오늘 Jay가 짚어야 할 '운영 결정/막힘'을 뽑아라. level은 셋 중 하나로 분류한다.",
    "monday": (
        "오늘은 월요일 — 플랫폼본부 위클리·리더 위클리가 있다. calls는 '오늘 위클리에서 "
        "Jay가 채우거나 짚어야 할 것'으로 구성해라(이번 주 포커스·열린 막힘 기준). level은 셋 중 하나."
    ),
    "friday": (
        "오늘은 금요일 — 주간 회고 모드. calls는 회고 항목으로 구성해라: 이번 주 닫은 것 / "
        "다음 주로 넘어가는 것 / 미뤄진 결정. 이때 level은 '닫음' / '넘김' / '미뤄짐' 중 하나로 써라."
    ),
}


def _parse_brief_json(text: str) -> dict:
    """모델 출력에서 JSON 추출(코드펜스·앞뒤 잡설 견고 처리). 실패 시 {}."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except Exception:
            return {}
    return {}


def get_chief_brief(mode: str, pack: str, context_text: str, pending: list[str],
                    weekly: list[str], daily_text: str, today_cal: str,
                    todo_items: list[dict]) -> tuple[str, list[dict]]:
    """무게중심 한 줄 + 판단 0~3건을 한 번에 생성. (weight, calls) 반환.
    calls=[{headline, points, level}] — headline=스캔용 굵은 제목, points=짧은 불릿 리스트."""
    ctx = pack or context_text
    pending_text = "\n".join(f"- {p}" for p in pending[:8]) or "없음"
    weekly_text = "\n".join(f"- {w}" for w in weekly[:6]) or "없음"
    todo_text = "\n".join(f"- {it['content']}" for it in todo_items[:10]) or "없음"

    if not ctx and not pending and not daily_text and not todo_items:
        return "", []  # 근거 없음 → 억지 생성 안 함

    prompt = f"""너는 Jay(장홍석, 다니엘프로젝트 부대표·CPO, 20년차 프로덕트)의 참모다.
아래 맥락으로 오늘 아침 브리핑의 핵심을 만든다. 단순 정보 나열이 아니라 '판단'이 값이다.

{_MODE_INSTRUCTION.get(mode, _MODE_INSTRUCTION['standard'])}

출력은 아래 JSON 객체 하나만. 마크다운·코드펜스·설명 문장 금지:
{{"weight": "...", "calls": [{{"headline": "...", "points": ["...", "..."], "level": "..."}}]}}

weight: 오늘의 무게중심 한 줄. 오늘 Jay가 머리를 써야 할 단 하나를 결론부터 단정으로 (~60자).
calls: 오늘 짚을 것 0~3개. 가장 시급·비가역한 것부터.
  - headline: 무엇인지 한 토막(명사구, ~18자). 한눈에 스캔되는 제목. 추천·근거는 넣지 마라.
  - points: 2~3개의 짧은 불릿. 첫 불릿 = 내 추천(하나로 찍기). 다음 = 근거. 있으면 놓친 비용·다른 관점 한 불릿. 각 불릿 한 문장(간결, ~40자).
  - level: standard/monday면 반드시 "네 결정"(Jay 단독 운영결정)·"대표로 올릴 것"(경영 위로)·"위임"(팀에 기준만 주고 넘김) 중 하나.

규칙:
- 억지로 채우지 마라. 마땅한 게 1개면 1개, 없으면 calls는 빈 배열 [].
- 근거 없는 단정·맥락에 없는 항목 생성 절대 금지.
- 오늘 일정과 교차해라(예: 오늘 그 회의 있으면 "거기서 처리").
- 전체(weight+calls) 900자 이내. 넘으면 설명을 줄이지 말고 calls 개수를 줄여라.
- 단정적 구어, 결론 먼저. 군더더기·"~하세요"·마크다운 기호 금지.

[참모 팩 / 조직 맥락]
{ctx[:9000]}

[열린 막힘·결정 대기 (_INDEX)]
{pending_text}

[이번 주 포커스 (_INDEX)]
{weekly_text}

[어제 업무 기록]
{daily_text[:4000]}

[오늘 일정]
{today_cal or "없음"}

[오늘 Todoist]
{todo_text}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CHIEF_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "max_tokens":
            print("WARNING: 참모 브리핑 max_tokens 도달 — 짤림 가능", file=sys.stderr)
        raw = message.content[0].text.strip()
        data = _parse_brief_json(raw)
        # quote=False: 따옴표·아포스트로피를 &#x27;로 바꾸지 않음(텔레그램 가독성). < > & 만 이스케이프.
        demark = lambda s: re.sub(r"[*_`#]+", "", str(s or "")).strip()
        esc = lambda s: escape(demark(s), quote=False)
        weight = demark(data.get("weight"))
        calls = []
        for c in data.get("calls", []) or []:
            headline = esc(c.get("headline"))
            raw_pts = c.get("points")
            if isinstance(raw_pts, list):
                points = [esc(p) for p in raw_pts if demark(p)]
            else:  # 모델이 body 문자열로 흘리면 단일 불릿으로 폴백
                b = esc(c.get("body") or raw_pts)
                points = [b] if b else []
            level = esc(c.get("level"))
            if headline or points:
                calls.append({"headline": headline, "points": points, "level": level})
        return (escape(weight, quote=False) if weight else ""), calls[:3]
    except Exception as e:
        send_error("참모 브리핑 생성", e)
        return "", []


# ── 블록 ② 참모 판단 렌더 ─────────────────────────────────────────
_BLOCK2_HEADER = {"standard": "참모 판단", "monday": "위클리 대비", "friday": "주간 회고"}
_DIVIDER = "━━━━━━━━━━"


def render_block2(mode: str, calls: list[dict]) -> str:
    head = f"{_DIVIDER}\n🎩 <b>{_BLOCK2_HEADER.get(mode, '참모 판단')}</b>   ·   {dday_label()}"
    if not calls:
        return head + "\n\n오늘 급한 결정 없음.\n오전 집중블록 지키세요."
    items = []
    for i, c in enumerate(calls, 1):
        tag = f"   → {c['level']}" if c["level"] else ""
        hl = c.get("headline", "")
        title = f"<b>{i}. {hl}</b>{tag}" if hl else f"<b>{i}.</b>{tag}"
        pts = c.get("points", [])
        if pts:
            bullets = "\n".join(f"  · {p}" for p in pts)
            items.append(f"{title}\n{bullets}")
        else:
            items.append(title)
    # 항목 사이 빈 줄 → 스캔 가능·시원한 여백
    return head + "\n\n" + "\n\n".join(items)


# ── 블록 ③ 백그라운드 (접이식) ────────────────────────────────────
def render_block3(done: list[str], weekly: list[str], freshness: str) -> str:
    rows = []
    if done:
        bullets = "\n".join(f"  · {d}" for d in done[:6])
        rows.append(f"<b>어제 완료 {len(done)}</b>\n{bullets}")
    if weekly:
        bullets = "\n".join(f"  · {w}" for w in weekly[:4])
        rows.append(f"<b>이번주 포커스</b>\n{bullets}")
    if freshness:
        rows.append(freshness)
    body = "\n\n".join(rows) if rows else "특이사항 없음"
    # 텔레그램 접이식 인용 — 평소엔 접혀 '▾ 백그라운드' 한 줄만 보이고 탭하면 펼쳐짐
    return f"<blockquote expandable><b>▾ 백그라운드</b>\n\n{body}</blockquote>"


# ── 메인 ──────────────────────────────────────────────────────────
def resolve_mode(weekday: int) -> str:
    if weekday in (5, 6):
        return "weekend"
    if weekday == 0:
        return "monday"
    if weekday == 4:
        return "friday"
    return "standard"


def build_message() -> str:
    now = datetime.now(KST)
    forced = os.getenv("FORCE_WEEKDAY")
    weekday = int(forced) if forced not in (None, "") else now.weekday()
    day_kr = "월화수목금토일"[weekday]
    date_str = now.strftime(f"%y/%m/%d") + f" {day_kr}"
    mode = resolve_mode(weekday)

    weather_tag = get_weather_tag()
    today_sched, tomorrow_oneline, today_cal = get_schedule_section()
    todo_items = get_todoist_today_items()
    header = f"📋 <b>{date_str}</b>" + (f"   ·   {weather_tag}" if weather_tag else "")
    sched_block = f"<b>오늘</b>\n{today_sched}"
    tomorrow_block = f"<b>내일</b>\n  {tomorrow_oneline}"

    # ── 주말: 경량 (블록 ①만 + ② 1줄, ③ 생략, LLM 호출 안 함) ──
    if mode == "weekend":
        index_text = fetch_github_file(INDEX_FILE)
        pending = get_index_pending(index_text)
        if pending:
            tail = f"주말 — 급한 건 {len(pending)}개 쌓여 있어요.\n평일에 처리하고, 오늘은 일정만."
        else:
            tail = "주말 — 급한 결정 없음. 쉬어요."
        parts = [header, sched_block, format_todo_top(todo_items), tomorrow_block, tail]
        return "\n\n".join(parts)

    # ── 평일/월/금: 3블록 ──
    index_text = fetch_github_file(INDEX_FILE)
    context_text = fetch_github_file(CONTEXT_FILE)
    daily_text = fetch_github_file(DAILY_FILE)
    pending = get_index_pending(index_text)
    weekly = get_index_weekly(index_text)
    pack = load_chief_pack()

    weight, calls = get_chief_brief(
        mode, pack, context_text, pending, weekly, daily_text, today_cal, todo_items,
    )

    # 블록 ① — 라벨 섹션을 빈 줄로 띄워 시원하게
    parts = [header]
    if weight:
        parts.append(f"<b>오늘 무게중심</b>\n{weight}")
    parts += [sched_block, format_todo_top(todo_items), tomorrow_block]
    block1 = "\n\n".join(parts)

    # 블록 ②
    block2 = render_block2(mode, calls)

    # 블록 ③ (접이식)
    done = get_todoist_completed_yesterday()
    freshness = get_freshness_warning()
    block3 = render_block3(done, weekly, freshness)

    return f"{block1}\n\n\n{block2}\n\n\n{block3}"


def main():
    try:
        message = build_message()
    except Exception as e:
        send_error("브리핑 조립", e)
        return
    if os.getenv("DRY_RUN") in ("1", "true", "TRUE"):
        print(message)
        return
    send_telegram(message)
    print("✅ 아침 브리핑 전송 완료")


if __name__ == "__main__":
    main()
