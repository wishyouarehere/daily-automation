"""
주간 패턴 리뷰 — 매주 금요일 18:00 KST
이번 주 Daily 파일 5개 + _INDEX.md + 결정-로그 → Claude 분석 → 텔레그램 전송
"""

import os
import re
import sys
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
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)


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


# ── 이번 주 Daily 파일 수집 ──────────────────────────────────────
def get_weekly_dailies() -> str:
    """workflowy-sync 레포의 Daily 폴더에서 이번 주 파일 5개 수집"""
    files = fetch_github_dir(WF_REPO, "Daily")
    if not files:
        # _DAILY_LATEST.md 하나라도 가져오기
        return fetch_github_file(WF_REPO, "_DAILY_LATEST.md")

    # 최신순 5개
    recent = sorted(files, key=lambda f: f["name"], reverse=True)[:5]
    parts = []
    for f in recent:
        content = fetch_github_file(WF_REPO, f["path"])
        # frontmatter 제거
        content = re.sub(r"^---.*?---\n", "", content, flags=re.DOTALL).strip()
        parts.append(f"### {f['name']}\n{content}")
    return "\n\n".join(parts)


# ── Claude 주간 리뷰 생성 ─────────────────────────────────────────
def generate_weekly_review(daily_text: str, index_text: str, context_text: str) -> str:
    now = datetime.now(KST)
    week_str = f"{now.year}년 {now.month}월 {now.isocalendar()[1]}주차"

    prompt = f"""당신은 20년차 CPO 장홍석(Jay)의 주간 리뷰 파트너입니다.
Jay는 다니엘프로젝트 부대표/CPO로, 매주 금요일 저녁 이 리뷰를 받습니다.

아래 이번 주 기록을 분석해서 4가지를 짚어주세요.
- 마크다운 기호 없이 plain text
- 각 항목은 이모지 + 굵은 HTML 태그(<b>) + 내용
- 날카롭고 솔직하게. "~하세요" 금지. 관찰과 질문만.

[분석 항목]
1. 🔋 에너지 분배 — 이번 주 어디에 가장 많은 에너지를 썼나. 의도한 것인가.
2. ⚠️ 피하고 있는 결정 — 계속 미뤄지고 있는 것. 왜 그런가.
3. 💎 이번 주 가장 잘한 것 — 작아도 좋음. 구체적으로.
4. 🎯 다음 주 단 하나 — 다음 주에 반드시 결정하거나 끝내야 할 것 하나.

[조직/팀 컨텍스트]
{context_text[:2000]}

[이번 주 프로젝트 현황]
{index_text[:800]}

[이번 주 Daily 기록]
{daily_text[:3000]}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"[*_`#]+", "", text).strip()
    return text, week_str


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"주간 리뷰 시작: {now.strftime('%Y-%m-%d %H:%M')}")

    daily_text   = get_weekly_dailies()
    index_text   = fetch_github_file(WF_REPO, "_INDEX.md")
    context_text = fetch_github_file(WF_REPO, "_CONTEXT.md")

    review_text, week_str = generate_weekly_review(daily_text, index_text, context_text)

    message = f"""📊 <b>주간 리뷰 — {week_str}</b>

{review_text}"""

    send_telegram(message)
    print("✅ 주간 리뷰 전송 완료")


if __name__ == "__main__":
    main()
