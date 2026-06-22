"""
주간회고 초안 자동 생성 — 매주 금요일 (회사맥북 cron)

설계(2026-06-22 Jay 합의):
  - regenerate_index 의 ✅(누적 완료) + 이번 주 Daily + 결정로그 + (있으면) Slack 을 모아
    주간회고 '초안'을 만든다. 4섹션 중 ①②④ 는 채우고, ③ 블라인드스팟은 '빈칸'으로 둔다.
  - ③(전략적 사고)는 Jay 가 직접 쓴다. 기계는 ②(이번 주 움직임 원재료)까지만 차린다.
    근거: 결정-로그 2026-06-16 "전략적 사고 자체는 자동화에서 뺀다".
  - 결과는 'Weekly/Week{N}-주간회고-...-draft.md' 로 저장(초안 표시). Jay 가 ③ 채우고
    파일명에서 '-draft' 떼면 확정. 그 다음 regen 이 ✅ 리셋.

가드레일:
  - 이 스크립트는 Weekly/ 아래 draft 파일 1개만 쓴다. 그 외는 전부 읽기 전용.
  - ANTHROPIC_API_KEY 없으면 조용히 종료.
  - SLACK_USER_TOKEN 없으면 Slack 없이(볼트 소스만) 초안 생성 — 토큰 들어오면 자동 포함.
  - LLM 실패·빈 결과면 파일 안 쓰고 텔레그램으로만 실패 알림.
  - 같은 주 중복 실행은 락으로 1회만 통과.
  - 비밀값은 로그·텔레그램에 절대 노출하지 않는다.

직접 실행:        ./venv/bin/python weekly_retro.py
드라이런(안전):   WEEKLY_RETRO_DRY=1 ./venv/bin/python weekly_retro.py   # 쓰기·전송 없이 출력만
"""

import glob
import os
import re
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# regen 과 같은 디렉토리 — 경로 상수/헬퍼 재사용
from regenerate_index import (
    KST, SCRIPT_DIR, VAULT_PROJECT, INDEX_FILE, DECISIONS_FILE,
    DAILY_DIR, WEEKLY_DIR, DECISIONS_CHARS,
    extract_completed_block, read_tail,
)

load_dotenv(SCRIPT_DIR / ".env")

SETUP_DOC = VAULT_PROJECT / "Docs" / "주간회고-소스채널-설정.md"

# 입력 길이 상한 (Sonnet 컨텍스트 안전)
DAILY_CHARS = 2500
WEEKLY_CHARS = 5000
SLACK_PER_CH_CHARS = 1800
SLACK_TOTAL_CHARS = 22000

DRY = os.environ.get("WEEKLY_RETRO_DRY") == "1"

LOCK_FILE_TMPL = "/tmp/weekly_retro.{week}.lock"


# ── 텔레그램 ──────────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    if DRY:
        print("[DRY] 텔레그램 생략:\n" + text)
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("INFO: 텔레그램 자격 없음 — 알림 생략", file=sys.stderr)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"WARN: 텔레그램 전송 실패: {type(e).__name__}", file=sys.stderr)


def fail(context: str, detail: str = "") -> None:
    print(f"ERROR [{context}] {detail}", file=sys.stderr)
    send_telegram(f"⚠️ <b>[주간회고 초안 실패]</b>\n{context}")
    sys.exit(1)


# ── 멱등 락 (같은 ISO 주 1회) ─────────────────────────────────────
def acquire_week_lock(now) -> bool:
    if DRY:
        return True
    iso = now.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"
    path = LOCK_FILE_TMPL.format(week=week_key)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"pid={os.getpid()} at {now.isoformat()}\n".encode())
    finally:
        os.close(fd)
    for old in glob.glob("/tmp/weekly_retro.*.lock"):
        if old != path:
            try:
                os.remove(old)
            except OSError:
                pass
    return True


# ── 주차 번호 (기존 Weekly 파일에서 +1) ───────────────────────────
def next_week_number() -> int:
    nums = []
    if WEEKLY_DIR.is_dir():
        for p in WEEKLY_DIR.glob("Week*주간회고*.md"):
            m = re.match(r"Week(\d+)", p.name)
            if m:
                nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def read_latest_weekly_text() -> str:
    if not WEEKLY_DIR.is_dir():
        return ""
    files = [p for p in WEEKLY_DIR.glob("Week*주간회고*.md") if p.is_file()]
    if not files:
        return ""
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        return files[0].read_text(encoding="utf-8")[:WEEKLY_CHARS]
    except Exception:
        return ""


# ── 이번 주 Daily (월~오늘) ───────────────────────────────────────
def read_week_daily(monday) -> str:
    if not DAILY_DIR.is_dir():
        return ""
    cutoff = monday.timestamp()
    files = [p for p in DAILY_DIR.glob("*.md")
             if p.is_file() and p.stat().st_mtime >= cutoff]
    files.sort(key=lambda p: p.stat().st_mtime)
    chunks = []
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8")[:DAILY_CHARS]
        except Exception:
            continue
        chunks.append(f"### Daily: {p.name}\n{txt}")
    return "\n\n".join(chunks)


# ── Slack 수집 (토큰 있을 때만) ───────────────────────────────────
def slack_source_ids() -> list:
    """설정문서의 Tier 0~2 구간에서 채널/DM/유저 ID를 순서대로 뽑는다(제외 섹션 앞까지)."""
    try:
        txt = SETUP_DOC.read_text(encoding="utf-8")
    except Exception:
        return []
    end = txt.find("## 평소 제외")
    if end == -1:
        end = txt.find("## 제외")
    region = txt[:end] if end != -1 else txt
    ids, seen = [], set()
    for i in re.findall(r"`([CDU][A-Z0-9]{6,})`", region):
        if i not in seen:
            seen.add(i)
            ids.append(i)
    return ids


def _slack_get(method, token, params):
    r = requests.get(f"https://slack.com/api/{method}",
                     headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=20)
    return r.json()


def _slack_user_map(token) -> dict:
    """uid → 표시이름. 베스트 에포트(실패하면 빈 맵)."""
    out, cursor = {}, None
    try:
        for _ in range(10):
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = _slack_get("users.list", token, params)
            if not r.get("ok"):
                break
            for m in r.get("members", []):
                prof = m.get("profile", {})
                out[m["id"]] = (prof.get("display_name") or prof.get("real_name")
                                or m.get("name") or m["id"])
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception:
        pass
    return out


def collect_slack(token, monday) -> str:
    """소스 채널들의 이번 주 메시지를 채널별로 모아 텍스트로. 채널 단위 에러는 건너뜀."""
    ids = slack_source_ids()
    if not ids:
        return ""
    oldest = f"{monday.timestamp():.6f}"
    umap = _slack_user_map(token)
    blocks, total, skipped = [], 0, []

    for cid in ids:
        ch = cid
        try:
            if cid.startswith("U"):  # 유저 → DM 채널 열기
                r = requests.post(
                    "https://slack.com/api/conversations.open",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"users": cid}, timeout=15).json()
                if not r.get("ok"):
                    skipped.append(f"{cid}({r.get('error')})")
                    continue
                ch = r["channel"]["id"]

            msgs, cursor = [], None
            for _ in range(4):  # 최대 4페이지
                params = {"channel": ch, "oldest": oldest, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                r = _slack_get("conversations.history", token, params)
                if not r.get("ok"):
                    skipped.append(f"{cid}({r.get('error')})")
                    break
                msgs.extend(r.get("messages", []))
                cursor = r.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            if not msgs:
                continue

            lines = []
            for m in reversed(msgs):  # 시간 오름차순
                if m.get("subtype") in ("channel_join", "channel_leave"):
                    continue
                t = (m.get("text") or "").strip()
                if not t:
                    continue
                who = umap.get(m.get("user", ""), m.get("user", "?"))
                lines.append(f"- {who}: {t}")
            if not lines:
                continue
            body = "\n".join(lines)[:SLACK_PER_CH_CHARS]
            block = f"#### Slack {cid}\n{body}"
            if total + len(block) > SLACK_TOTAL_CHARS:
                skipped.append(f"{cid}(길이초과)")
                break
            blocks.append(block)
            total += len(block)
        except Exception as e:
            skipped.append(f"{cid}({type(e).__name__})")
            continue

    note = f"\n\n(수집 {len(blocks)}채널" + (f", 건너뜀 {len(skipped)}" if skipped else "") + ")"
    return ("\n\n".join(blocks) + note) if blocks else ""


# ── 프롬프트 ──────────────────────────────────────────────────────
BLINDSPOT_TEMPLATE = (
    "## ③ 못 본 블라인드스팟\n\n"
    "> (Jay 직접 작성) 위 ② 움직임을 보고 '내가 지금 못 보고 있는 것·진짜 위험' 1~3개.\n\n"
    "- \n"
)


def build_prompt(week_label, completed, week_daily, decisions, latest_weekly, slack) -> str:
    slack_section = slack if slack else "(이번 주 Slack 소스 없음 — 볼트 기록만으로 작성)"
    return f"""당신은 20년차 CPO 장홍석(Jay)의 주간회고 파트너다. 아래 입력으로 \
다니엘프로젝트 주간회고 '초안'을 만든다. ({week_label})

# 출력 형식 (엄수)
맨 앞 2줄:
# 주간 회고 — {week_label}
> 상태: 초안(자동 생성) · ③ 블라인드스팟은 Jay 직접 작성 · 확정 시 파일명 '-draft' 제거

그 다음 정확히 이 4개 섹션, 이 순서·이 제목:
## ① 지난주 액션 점검
## ② 이번주 요약
## ③ 못 본 블라인드스팟
## ④ 차주 계획 + 액션

# 각 섹션 작성 규칙
- ① 지난주 액션 점검: [직전 회고]의 '④ 차주 계획'을 지난주 계획으로 보고, [완료 누적]·[이번주 Daily]·
  [결정로그]와 대조해 표로. 열: 지난주 계획 | 상태(✅완료/🟡진행/⬜미착수) | 메모. [완료 누적]에 있는 건 ✅.
- ② 이번주 요약: 실제 일어난 것만. 하위에 '결정' '움직임(제품·데이터 / 경영·자본)' '막힘'으로 묶어 사실 위주.
  Slack 입력이 있으면 거기서 새로 포착된 움직임·신호를 반드시 반영(누가-무엇). 날짜(M/D) 접두.
- ③ 못 본 블라인드스팟: **절대 내용을 쓰지 마라.** 아래 템플릿을 그대로 출력한다(빈칸 유지):
{BLINDSPOT_TEMPLATE}
- ④ 차주 계획 + 액션: [직전 회고 ④] 중 아직 안 끝난 것 + 이번주 새로 생긴 것을 '- [ ]'로.
  타운홀전/경영·자본/채용/제품·데이터/일정 등으로 묶어도 좋다. 끝난 건 넣지 마라.
- 근거 없는 건 지어내지 말고 생략. 한국어. 군더더기 없이. 인물은 직책 3인칭 말고 실명/역할로.

# 입력
## [완료 누적 (_INDEX ✅ 이번 주 완료)]
{completed or "(없음)"}

## [직전 회고]
{latest_weekly or "(없음)"}

## [이번주 Daily]
{week_daily or "(없음)"}

## [결정로그 (최신 일부)]
{decisions}

## [이번주 Slack]
{slack_section}
"""


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("SKIP: ANTHROPIC_API_KEY 미설정 — 미생성.", file=sys.stderr)
        sys.exit(0)

    now = datetime.now(KST)
    if not acquire_week_lock(now):
        print(f"SKIP: 이번 주 이미 생성됨 — 중복 무시.", file=sys.stderr)
        sys.exit(0)

    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week_n = next_week_number()
    week_label = (f"Week {week_n} ({monday.strftime('%Y-%m-%d')} ~ "
                  f"{now.strftime('%Y-%m-%d')})")

    completed = extract_completed_block(INDEX_FILE.read_text(encoding="utf-8")) \
        if INDEX_FILE.exists() else ""
    week_daily = read_week_daily(monday)
    decisions = read_tail(DECISIONS_FILE, DECISIONS_CHARS)
    latest_weekly = read_latest_weekly_text()

    slack_token = os.environ.get("SLACK_USER_TOKEN")
    slack = collect_slack(slack_token, monday) if slack_token else ""
    slack_state = "포함" if slack else ("토큰없음" if not slack_token else "수집0")

    if not week_daily and not completed and not slack:
        fail("입력 빔", "이번 주 소스를 못 읽음")

    prompt = build_prompt(week_label, completed, week_daily, decisions,
                          latest_weekly, slack)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        body = "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        fail("LLM 호출", f"{type(e).__name__}")
        return

    # 제목 띄어쓰기 변동을 허용하기 위해 번호 마커로만 검증
    required = ["## ①", "## ②", "## ③", "## ④"]
    missing = [m for m in required if m not in body]
    if missing or len(body) < 300:
        fail("출력 검증 실패", f"누락/짧음: {missing} len={len(body)}")

    fname = (f"Week{week_n}-주간회고-{monday.strftime('%m%d')}-"
             f"{now.strftime('%m%d')}-draft.md")
    out_path = WEEKLY_DIR / fname
    final = body + "\n"

    if DRY:
        print(f"\n===== [DRY] 저장 예정: {fname} (Slack: {slack_state}) =====\n")
        print(final)
        return

    try:
        WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".md.tmp")
        tmp.write_text(final, encoding="utf-8")
        tmp.replace(out_path)
    except Exception as e:
        fail("파일 쓰기", f"{type(e).__name__}")
        return

    print(f"✅ 주간회고 초안 생성: {fname} (Slack: {slack_state})")
    send_telegram(
        f"📝 <b>주간회고 초안 — {week_label}</b>\n"
        f"Slack: {slack_state}\n"
        f"볼트에 저장됨: <code>{fname}</code>\n\n"
        f"<i>③ 블라인드스팟 직접 채우고, 파일명에서 '-draft' 떼면 확정.</i>"
    )


if __name__ == "__main__":
    main()
