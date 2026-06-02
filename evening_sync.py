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
    "/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/다니엘프로젝트/Daily"
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


# ── Todoist 완료 항목 조회 ────────────────────────────────────────
def get_completed_tasks(date: datetime) -> list[dict]:
    """오늘 완료된 Todoist 태스크 목록 반환"""
    try:
        headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
        since = date.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        until = date.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

        # Sync API로 완료 항목 조회 (REST API v2는 완료 항목 미지원)
        resp = requests.get(
            "https://api.todoist.com/api/v1/tasks/completed",
            headers=headers,
            params={"since": since, "until": until, "limit": 200},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

        # 프로젝트 ID → 이름 매핑
        proj_resp = requests.get("https://api.todoist.com/api/v1/projects", headers=headers, timeout=10)
        proj_data = proj_resp.json()
        proj_list = proj_data.get("results", proj_data) if isinstance(proj_data, dict) else proj_data
        proj_map = {p["id"]: p["name"] for p in proj_list} if proj_resp.ok else {}

        return [
            {
                "content": item["content"],
                "project_name": proj_map.get(str(item.get("project_id", "")), "Inbox"),
            }
            for item in items
        ]
    except Exception as e:
        send_error("Todoist 완료 항목 조회", e)
        return []


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
    append_to_daily_note(tasks, now)

    # 텔레그램 완료 알림
    date_str = now.strftime("%Y년 %m월 %d일")
    count = len(tasks)
    msg = f"📓 <b>저녁 기록 완료 — {date_str}</b>\nTodoist 완료 항목 {count}개를 Obsidian Daily Note에 기록했습니다."
    send_telegram(msg)


if __name__ == "__main__":
    main()
