"""
하루 마감 리뷰 — 매주 월~금 19:00 KST
오늘(KST) Daily 파일 1개 + _CONTEXT.md + 볼트 회의록(오늘분) → Claude 분석 → 텔레그램 전송

회의록은 로컬 볼트(iCloud)에서만 읽힌다. GitHub Actions 러너엔 볼트가 없어 빈 맥락으로
떨어지므로 이 잡은 회사 맥 crontab에서 도는 게 정본이다(2026-08-19).
"""

import os
import re
import sys
import exec_events  # 🔴 톱레벨 고정: 이후 sys.path.insert(0, 다른 repo)가 같은 이름의 외부 사본을 가리지 못하게 먼저 sys.modules에 올려둔다
import time
import base64
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv()

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")  # 로컬 실행엔 불필요(wf-sync 클론 우선)

WF_REPO  = "wishyouarehere/workflowy-sync"
WF_SYNC_DIR = Path.home() / "wf-sync"   # 로컬 클론(= meeting_ctx 정본 위치)
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


# ── workflowy-sync 읽기 (로컬 클론 우선 → GitHub API 폴백) ────────
def fetch_file(path: str) -> str:
    """wf-sync 로컬 클론이 있으면 거기서, 없으면 GitHub API로 읽는다."""
    local = WF_SYNC_DIR / path
    if local.is_file():
        return local.read_text(encoding="utf-8", errors="ignore")
    if not GITHUB_TOKEN:
        return ""
    return fetch_github_file(WF_REPO, path)


def list_daily_files() -> list[dict]:
    """Daily 폴더의 .md 목록 [{name, path}] — 로컬 우선."""
    local_dir = WF_SYNC_DIR / "Daily"
    if local_dir.is_dir():
        return [
            {"name": f.name, "path": f"Daily/{f.name}"}
            for f in sorted(local_dir.glob("*.md"))
        ]
    if not GITHUB_TOKEN:
        return []
    return fetch_github_dir(WF_REPO, "Daily")


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


# ── 볼트 회의록(오늘분) 수집 ─────────────────────────────────────
# meeting_ctx 정본은 wf-sync — 스트립·날짜판정 로직을 여기서 복제하지 않는다.
def get_today_meetings() -> str:
    """오늘(±1일) 회의록 본문을 노이즈 제거해 반환. 볼트가 없으면 빈 문자열(무해)."""
    try:
        if str(WF_SYNC_DIR) not in sys.path:
            sys.path.insert(0, str(WF_SYNC_DIR))
        import meeting_ctx
        return meeting_ctx.recent_meetings(
            days=1, per_cap=2500, total_cap=9000, max_files=5
        )
    except Exception as e:
        print(f"[warn] 회의록 맥락 수집 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


# ── 오늘 Daily 파일 수집 ─────────────────────────────────────────
def get_today_daily() -> str:
    """workflowy-sync 레포의 Daily 폴더에서 오늘(KST) 날짜 파일 하나 수집.

    파일명 규칙이 두 가지다 — 구형 `2026-08-19.md`와 현행 WorkFlowy 노드명 `8-19(수).md`.
    후자만 있는 날 `startswith("2026-08-19")`는 항상 빗나가 폴백으로 샜다(2026-08-19 수정).
    """
    now = datetime.now(KST)
    prefixes = (now.strftime("%Y-%m-%d"), f"{now.month}-{now.day}(")
    files = list_daily_files()

    today_file = next(
        (f for f in files if f["name"].startswith(prefixes)), None
    )
    if today_file:
        content = fetch_file(today_file["path"])
    else:
        content = fetch_file("_DAILY_LATEST.md")

    # frontmatter 제거
    content = re.sub(r"^---.*?---\n", "", content, flags=re.DOTALL).strip()
    return content


# ── Claude 하루 마감 리뷰 생성 ────────────────────────────────────
def generate_daily_review(daily_text: str, context_text: str, meeting_text: str = "") -> str:
    prompt = f"""당신은 20년차 CPO 장홍석(Jay)의 하루 마감 파트너입니다.
Jay 본인이 다니엘프로젝트 부대표(20년차 CPO)입니다. Jay를 직책(부대표/CPO)으로 3인칭처럼 지칭하지 말 것. 정렬·보고 상대가 필요하면 기록에 등장한 실제 인물(예: 대표)로 쓸 것.
오늘 기록(인풋)에서 내일로 가져갈 아웃풋 하나를 뽑아내는 게 목적입니다.

아래 오늘 기록을 보고 3가지를 각각 1~2문장으로만 짚어주세요.
- 마크다운 기호 없이 plain text
- 각 항목: 이모지 + <b>제목</b> + 개행 + 내용 (짧게, 핵심만)
- 말투: 자연스러운 해요체. 가까운 파트너가 말하듯 담백하고 직설적이되, 상대를 존중하는 공손함을 지킨다. 건방진 단정·반말 금지. 보고서·홍보문구 톤, 진부한 클리셰, 과장, 영혼 없는 칭찬 금지. 날은 세우되 딱딱하지 않게. 훈계조("~하세요" 식 조언) 금지.
- 오늘 기록이 빈약하면 억지로 채우지 말고 "오늘은 건질 게 적어요"라고 솔직히 말할 것.
- 근거 우선순위는 [오늘 회의록] > [오늘 기록]이다. 데일리에는 일정 제목만 있고 내용은 회의록에 있는 게 정상이니, 회의록에 결론·액션이 있으면 그걸 근거로 쓸 것.
- 회의록에 이미 결론이 적혀 있는 사안을 "기록에 없다"·"결론이 안 보인다"고 말하지 말 것. 정말로 회의록이 비어 있을 때만 그렇게 말한다.

1. 💡 <b>오늘 건진 것</b> — 오늘 기록에서 남길 인사이트/관찰 하나. (1~2문장)
2. 🔁 <b>매듭 안 지은 것</b> — 오늘 안 끝냈거나 미룬 것 하나. (1문장)
3. 🎯 <b>내일 단 하나</b> — 내일 반드시 끝낼 하나. (1문장)

[조직/팀 컨텍스트]
{context_text[:1500]}

[오늘 회의록 — 볼트 정본, 없으면 비어 있음]
{meeting_text if meeting_text else "(오늘 수집된 회의록 없음)"}

[오늘 기록 — 데일리(일정·메모)]
{daily_text[:3000]}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    from llm_ledger import log_anthropic
    log_anthropic("daily_review", message)
    text = message.content[0].text.strip()
    text = re.sub(r"[*_`#]+", "", text).strip()
    return text


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"하루 마감 리뷰 시작: {now.strftime('%Y-%m-%d %H:%M')}")

    daily_text   = get_today_daily()
    context_text = fetch_file("_CONTEXT.md")
    meeting_text = get_today_meetings()
    print(f"회의록 맥락: {meeting_text.count('### 회의록:')}건 / {len(meeting_text)}자")

    review_text = generate_daily_review(daily_text, context_text, meeting_text)

    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    message = f"""🌙 <b>하루 마감 — {now.month}/{now.day} ({weekday_kr})</b>

{review_text}"""

    if "--dry-run" in sys.argv:   # 점검용 — 전송·이벤트 없이 본문만 출력
        print("--- DRY RUN ---")
        print(message)
        return

    from exec_events import exec_gate_suppresses
    _emitted = False
    try:
        from exec_emitter import emit_event
        _emitted = emit_event(
            source="daily_review",
            domain="ops",
            event_type="daily_automation.daily_review.sent",
            title="일일 리뷰 전송",
            decision_level="L1",
            metadata={"weekday": weekday_kr},
        )
    except Exception:
        _emitted = False
    if not exec_gate_suppresses(_emitted):
        send_telegram(message)
    print("✅ 하루 마감 리뷰 전송 완료")


if __name__ == "__main__":
    main()
