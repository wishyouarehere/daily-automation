"""
저녁 Todoist→Obsidian 자동 기록
매일 22:00 KST에 오늘 완료된 Todoist 항목을 Obsidian Daily Note에 append합니다.

직접 실행: python evening_sync.py
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 환경변수 ──────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TODOIST_TOKEN = os.environ["TODOIST_API_TOKEN"]

# Obsidian vault 내 Daily note 저장 경로
VAULT_DAILY_DIR = Path(
    "/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/10-Projects/다니엘프로젝트/Daily"
)

KST = timezone(timedelta(hours=9))


# ── 텔레그램 전송 ─────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)


def send_error(context: str, error: Exception) -> None:
    msg = f"⚠️ <b>[evening_sync 오류]</b>\n{context}\n<code>{type(error).__name__}: {error}</code>"
    try:
        send_telegram(msg)
    except Exception:
        pass
    print(f"ERROR [{context}]: {error}", file=sys.stderr)


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


# ── Todoist 완료 항목 조회 ────────────────────────────────────────
def get_completed_tasks(date: datetime) -> list[dict] | None:
    """오늘 완료된 Todoist 태스크 목록 반환 (관리함 제외).

    조회 실패 시 None 반환 — 빈 리스트(완료 0건)와 구분해야
    실패한 날 Obsidian에 '(완료된 항목 없음)'이 잘못 기록되는 것을 막는다.
    """
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        since = date.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        until = date.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

        resp = todoist_get(
            "https://api.todoist.com/api/v1/tasks/completed/by_completion_date",
            headers=headers,
            params={"since": since, "until": until},
        )
        items = resp.json().get("items", [])

        # 프로젝트 ID → 이름 매핑 + 관리함(inbox) ID 수집
        try:
            proj_resp = todoist_get("https://api.todoist.com/api/v1/projects", headers=headers)
            proj_data = proj_resp.json()
            proj_list = proj_data.get("results", proj_data) if isinstance(proj_data, dict) else proj_data
            proj_map = {str(p["id"]): p["name"] for p in proj_list}
            # Todoist v1 API는 inbox_project 필드를 사용한다 (구 is_inbox_project 아님).
            # 안전하게 두 필드 모두 체크해 Inbox(관리함) 항목을 확실히 제외한다.
            inbox_ids = {
                str(p["id"])
                for p in proj_list
                if p.get("inbox_project") or p.get("is_inbox_project")
            }
        except Exception:
            proj_map = {}
            inbox_ids = set()

        return [
            {
                "content": item["content"],
                "project_name": proj_map.get(str(item.get("project_id", "")), "기타"),
            }
            for item in items
            if str(item.get("project_id", "")) not in inbox_ids
        ]
    except Exception as e:
        send_error("Todoist 완료 항목 조회", e)
        return None


# ── Obsidian Daily Note append ────────────────────────────────────
def append_to_daily_note(tasks: list[dict], date: datetime) -> None:
    date_str = date.strftime("%Y-%m-%d")
    file_path = VAULT_DAILY_DIR / f"{date_str}.md"

    # 완료 항목 마크다운 생성
    if tasks:
        task_lines = "\n".join(f"- [{t['project_name']}] {t['content']}" for t in tasks)
    else:
        task_lines = "- (완료된 항목 없음)"

    section = f"\n## ✅ 오늘 완료한 일 (Todoist)\n{task_lines}\n"

    # 디렉토리가 없으면 생성
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.exists():
        # 이미 섹션이 있으면 덮어쓰지 않음
        content = file_path.read_text(encoding="utf-8")
        if "## ✅ 오늘 완료한 일 (Todoist)" in content:
            print(f"⚠️  이미 완료 섹션이 존재합니다: {file_path}")
            return
        file_path.write_text(content + section, encoding="utf-8")
    else:
        # 파일이 없으면 새로 생성 (기본 헤더 포함)
        header = f"# {date_str}\n"
        file_path.write_text(header + section, encoding="utf-8")

    print(f"✅ {file_path} 에 {len(tasks)}개 항목 기록 완료")


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    tasks = get_completed_tasks(now)

    # 조회 실패 시 기록하지 않고 종료 — 잘못된 '없음' 섹션이 남으면 재실행도 막힌다
    if tasks is None:
        date_str = now.strftime("%Y년 %m월 %d일")
        send_telegram(
            f"❌ <b>저녁 기록 실패 — {date_str}</b>\n"
            "Todoist 조회에 실패해 Obsidian에 기록하지 않았습니다.\n"
            "복구하려면 직접 실행: <code>python evening_sync.py</code>"
        )
        sys.exit(1)

    append_to_daily_note(tasks, now)

    # 텔레그램 완료 알림
    date_str = now.strftime("%Y년 %m월 %d일")
    count = len(tasks)
    msg = f"📓 <b>저녁 기록 완료 — {date_str}</b>\nTodoist 완료 항목 {count}개를 Obsidian Daily Note에 기록했습니다."
    send_telegram(msg)


if __name__ == "__main__":
    main()
