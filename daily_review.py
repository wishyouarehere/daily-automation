"""
하루 마감 리뷰 — 매주 월~금 19:00 KST
오늘(KST) Daily 파일 1개 + _CONTEXT.md → Claude 분석 → 텔레그램 전송
"""

import os
import re
import sys
import time
import base64
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import anthropic

load_dotenv()

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN    = os.environ["GITHUB_TOKEN"]

WF_REPO  = "wishyouarehere/workflowy-sync"
KST      = timezone(timedelta(hours=9))


# ── 텔레그램 ──────────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    last_err = None
    for attempt in range(3):  # 일시적 네트워크 지연 대비 재시도 (백오프 2s, 4s)
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last_err


# ── GitHub 파일 읽기 ──────────────────────────────────────────────
def fetch_github_file(repo: str, path: str) -> str:
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers=headers, timeout=10,
    )
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return base64.b64decode(resp.json()["content"]).decode("utf-8")


def fetch_github_dir(repo: str, path: str) -> list[dict]:
    """디렉토리 내 파일 목록 반환 [{name, path, sha}]"""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers=headers, timeout=10,
    )
    if not resp.ok:
        return []
    return [f for f in resp.json() if f.get("type") == "file" and f["name"].endswith(".md")]


# ── 오늘 Daily 파일 수집 ─────────────────────────────────────────
def get_today_daily() -> str:
    """workflowy-sync 레포의 Daily 폴더에서 오늘(KST) 날짜 파일 하나 수집"""
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    files = fetch_github_dir(WF_REPO, "Daily")

    # 오늘 날짜(YYYY-MM-DD)로 시작하는 파일 찾기, 없으면 _DAILY_LATEST.md 폴백
    today_file = next((f for f in files if f["name"].startswith(today_str)), None)
    if today_file:
        content = fetch_github_file(WF_REPO, today_file["path"])
    else:
        content = fetch_github_file(WF_REPO, "_DAILY_LATEST.md")

    # frontmatter 제거
    content = re.sub(r"^---.*?---\n", "", content, flags=re.DOTALL).strip()
    return content


# ── Claude 하루 마감 리뷰 생성 ────────────────────────────────────
def generate_daily_review(daily_text: str, context_text: str) -> str:
    prompt = f"""당신은 20년차 CPO 장홍석(Jay)의 하루 마감 파트너입니다.
Jay 본인이 다니엘프로젝트 부대표(20년차 CPO)입니다. Jay를 직책(부대표/CPO)으로 3인칭처럼 지칭하지 말 것. 정렬·보고 상대가 필요하면 기록에 등장한 실제 인물(예: 대표)로 쓸 것.
오늘 기록(인풋)에서 내일로 가져갈 아웃풋 하나를 뽑아내는 게 목적입니다.

아래 오늘 기록을 보고 3가지를 각각 1~2문장으로만 짚어주세요.
- 마크다운 기호 없이 plain text
- 각 항목: 이모지 + <b>제목</b> + 개행 + 내용 (짧게, 핵심만)
- 말투: 자연스러운 해요체. 가까운 파트너가 말하듯 담백하고 직설적으로, 사람 말처럼. 보고서·홍보문구 톤, 진부한 클리셰, 과장, 영혼 없는 칭찬 금지. 날은 세우되 딱딱하지 않게. 훈계조("~하세요" 식 조언) 금지.
- 오늘 기록이 빈약하면 억지로 채우지 말고 "오늘은 건질 게 적어요"라고 솔직히 말할 것.

1. 💡 <b>오늘 건진 것</b> — 오늘 기록에서 남길 인사이트/관찰 하나. (1~2문장)
2. 🔁 <b>매듭 안 지은 것</b> — 오늘 안 끝냈거나 미룬 것 하나. (1문장)
3. 🎯 <b>내일 단 하나</b> — 내일 반드시 끝낼 하나. (1문장)

[조직/팀 컨텍스트]
{context_text[:1500]}

[오늘 기록]
{daily_text[:3000]}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"[*_`#]+", "", text).strip()
    return text


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"하루 마감 리뷰 시작: {now.strftime('%Y-%m-%d %H:%M')}")

    daily_text   = get_today_daily()
    context_text = fetch_github_file(WF_REPO, "_CONTEXT.md")

    review_text = generate_daily_review(daily_text, context_text)

    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    message = f"""🌙 <b>하루 마감 — {now.month}/{now.day} ({weekday_kr})</b>

{review_text}"""

    send_telegram(message)
    print("✅ 하루 마감 리뷰 전송 완료")


if __name__ == "__main__":
    main()
