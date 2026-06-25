"""
Telegram → Obsidian 아이디어 메모 봇

Telegram으로 받은 메시지를 Obsidian vault의 00-Inbox/Ideas.md 하단에 append합니다.
polling 방식으로 동작 (외부 서버 불필요).

직접 실행: python idea_bot.py
launchd 자동 실행: com.jay.idea-bot.plist
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 환경변수 ──────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["IDEA_BOT_TOKEN"]
ALLOWED_CHAT_ID = os.environ.get("IDEA_BOT_CHAT_ID", "")  # 빈 값이면 모든 채팅 허용

# Obsidian vault Ideas.md 경로
IDEAS_FILE = Path(
    "/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/00-Inbox/Ideas.md"
)

KST = timezone(timedelta(hours=9))

# ── 로깅 설정 ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Telegram API 헬퍼 ─────────────────────────────────────────────────
def tg_get(method: str, params: dict = None, timeout: int = 10) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)


# ── Ideas.md append ──────────────────────────────────────────────────
def append_idea(text: str) -> None:
    now = datetime.now(KST)
    timestamp = now.strftime("%Y-%m-%d %H:%M")

    entry = f"\n## {timestamp}\n{text}\n\n---\n"

    IDEAS_FILE.parent.mkdir(parents=True, exist_ok=True)

    if IDEAS_FILE.exists():
        current = IDEAS_FILE.read_text(encoding="utf-8")
        IDEAS_FILE.write_text(current + entry, encoding="utf-8")
    else:
        IDEAS_FILE.write_text(f"# Ideas Inbox\n{entry}", encoding="utf-8")

    log.info("저장됨: %s", text[:60])


# ── 메시지 처리 ───────────────────────────────────────────────────────
def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()

    if not text:
        return

    # 허용된 chat_id 검증
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        log.warning("허용되지 않은 chat_id: %s", chat_id)
        send_message(chat_id, "❌ 권한 없음")
        return

    try:
        append_idea(text)
        send_message(chat_id, "✅ 저장됨")
    except Exception as e:
        log.error("저장 실패: %s", e)
        send_message(chat_id, "❌ 저장 실패")


# ── polling 루프 ──────────────────────────────────────────────────────
def run_polling() -> None:
    log.info("아이디어 봇 시작 (polling)")
    offset = None

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset

            data = tg_get("getUpdates", params, timeout=35)

            for update in data.get("result", []):
                handle_update(update)
                offset = update["update_id"] + 1

        except requests.exceptions.Timeout:
            # long polling 정상 타임아웃
            pass
        except requests.exceptions.ConnectionError as e:
            log.warning("네트워크 오류, 10초 후 재시도: %s", e)
            time.sleep(10)
        except Exception as e:
            log.error("예상치 못한 오류: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
