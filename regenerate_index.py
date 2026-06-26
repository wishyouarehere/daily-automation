"""
_INDEX 자동 재생성 (저녁 배치) — A안

매일 22:10 KST, 기존 evening_sync(22:00 Daily 쓰기) 직후에 돈다.
10-Projects/다니엘프로젝트/_INDEX.md 를
  최근 Daily(7일) + 결정-로그(최신) + 직전 _INDEX(diff용) + 최신 주간회고
에서 고정 4섹션 포맷으로 다시 뽑는다.

근거: Decisions/결정-로그.md 2026-06-19 "지식 문서 운영 구조 확정".
  _INDEX = 파생 뷰("아침에 가장 먼저 여는 문서"). 손유지 대신 재생성.

가드레일:
  - 이 스크립트는 _INDEX.md 단 하나만 쓴다. 그 외 문서(핵심-컨텍스트·결정로그·
    회고·Daily·source:workflowy)는 전부 읽기 전용.
  - LLM 실패·빈 결과·4섹션 누락이면 이전 _INDEX 를 보존하고 덮어쓰지 않는다.
  - 쓰기 전 _INDEX.md.bak 으로 1단계 롤백본을 남긴다.
  - ANTHROPIC_API_KEY 가 없으면 아무것도 쓰지 않고 조용히 종료(키 주입 = 수동 단계).
  - 비밀값은 로그·텔레그램에 절대 노출하지 않는다.

직접 실행: ./venv/bin/python regenerate_index.py
"""

import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# ── 경로/환경 ─────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
# cron CWD 와 무관하게 레포 .env 를 절대경로로 로드 (find_dotenv 의존 X)
load_dotenv(SCRIPT_DIR / ".env")

KST = timezone(timedelta(hours=9))

VAULT_PROJECT = Path(
    "/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian"
    "/Documents/jay/10-Projects/다니엘프로젝트"
)
INDEX_FILE = VAULT_PROJECT / "_INDEX.md"
BAK_FILE = VAULT_PROJECT / "_INDEX.md.bak"
DAILY_DIR = VAULT_PROJECT / "Daily"
DECISIONS_FILE = VAULT_PROJECT / "결정-로그.md"
WEEKLY_DIR = VAULT_PROJECT / "Weekly"

# 이 스크립트가 쓰는 유일 파일이 _INDEX.md 인지 보장하기 위한 상수
WRITE_ALLOWLIST = {INDEX_FILE, BAK_FILE}

# 입력 길이 상한 (Sonnet 컨텍스트 안전)
MAX_DAILY = 7
DAILY_CHARS = 2800
DECISIONS_CHARS = 7000
WEEKLY_CHARS = 4500

# 재생성 본문 상단에 항상 유지하는 고정 헤더(주석) — 손대지 않는다
HEADER = """<!--
이 문서는 "아침에 가장 먼저 여는 문서"다. 오늘 움직일 것만 담는다.
자동 재생성 대상(저녁 배치): Daily + 결정로그에서 아래 4섹션 포맷으로 다시 뽑는다.
레퍼런스(팀 명단·전략·인물·로드맵·인프라)는 여기 두지 않는다 → [[핵심-컨텍스트]].
고정 4섹션: 🔴 열린 막힘/결정 대기 · 📍 오늘 · 🆕 어제까지의 변화 · 🎯 이번 주 포커스
-->"""

REQUIRED_MARKERS = [
    "## 🔴 열린 막힘 / 결정 대기",
    "## 📍 오늘",
    "## 🆕 어제까지의 변화",
    "## 🎯 이번 주 포커스",
]

DIFF_DELIM = "===DIFF==="


# ── 텔레그램(선택, 로컬 자격 재사용) ───────────────────────────────
def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("INFO: 텔레그램 자격 없음 — 알림 생략", file=sys.stderr)
        return
    try:
        import requests

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"WARN: 텔레그램 전송 실패: {type(e).__name__}", file=sys.stderr)


def fail(context: str, detail: str = "") -> None:
    """이전 _INDEX 보존하고 종료. 비밀값 노출 금지."""
    print(f"ERROR [{context}] {detail}", file=sys.stderr)
    send_telegram(
        f"⚠️ <b>[_INDEX 재생성 실패]</b>\n{context}\n"
        f"이전 _INDEX 를 그대로 보존했습니다."
    )
    sys.exit(1)


# ── 입력 수집 (전부 읽기 전용) ────────────────────────────────────
def read_recent_daily() -> str:
    if not DAILY_DIR.is_dir():
        return ""
    files = [p for p in DAILY_DIR.glob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    chunks = []
    for p in files[:MAX_DAILY]:
        try:
            txt = p.read_text(encoding="utf-8")[:DAILY_CHARS]
        except Exception:
            continue
        chunks.append(f"### Daily: {p.name}\n{txt}")
    return "\n\n".join(chunks)


def read_tail(path: Path, n_chars: int) -> str:
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return txt[-n_chars:] if len(txt) > n_chars else txt


def read_latest_weekly() -> tuple[str, str]:
    """(회고 텍스트, 주차 식별자) 반환. 주차는 파일명의 'Week<N>' (예: 'Week12').

    주차 식별자는 ✅ 완료 목록의 '주 전환 리셋' 판정에 쓴다.
    """
    if not WEEKLY_DIR.is_dir():
        return "", ""
    # 주간회고 파일만(슬랙 주간요약 제외). 없으면 Week* 전체로 폴백.
    files = [p for p in WEEKLY_DIR.glob("Week*주간회고*.md") if p.is_file()]
    if not files:
        files = [p for p in WEEKLY_DIR.glob("Week*.md") if p.is_file()]
    if not files:
        return "", ""
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = files[0]
    m = re.match(r"(Week\d+)", latest.name)
    week_id = m.group(1) if m else ""
    try:
        return f"### {latest.name}\n{latest.read_text(encoding='utf-8')[:WEEKLY_CHARS]}", week_id
    except Exception:
        return "", week_id


# ── 대조(reconciliation)용 직전 _INDEX 파싱 ───────────────────────
COMPLETED_HEADER = "### ✅ 이번 주 완료"


def extract_focus_week(prev_index: str) -> str:
    """직전 _INDEX 헤더에 박힌 '<!-- focus-week: WeekN -->' 마커를 읽는다."""
    m = re.search(r"focus-week:\s*(\S+)", prev_index)
    return m.group(1) if m else ""


def extract_completed_block(prev_index: str) -> str:
    """직전 _INDEX 의 '### ✅ 이번 주 완료' 아래 ~ 다음 헤더 전까지를 그대로 떼온다.

    carry-forward 의 입력. Daily 읽기 창(7파일)과 무관하게 주중 내내 누적되게 한다.
    """
    out, capturing = [], False
    for ln in prev_index.splitlines():
        if ln.strip().startswith(COMPLETED_HEADER):
            capturing = True
            continue
        if capturing:
            if ln.startswith("#"):  # 다음 헤더에서 종료
                break
            out.append(ln)
    return "\n".join(out).strip()


# ── 프롬프트 ──────────────────────────────────────────────────────
def build_prompt(
    prev_index, daily, decisions, weekly, today_label,
    reset_completed, prev_completed,
) -> str:
    if reset_completed:
        carry_rule = (
            "- 새 주차다(주간회고가 새 계획으로 바뀜). [직전 완료 목록]은 무시하고 "
            "'### ✅ 이번 주 완료'를 비운 상태에서 이번 주 새로 닫은 것만 채운다."
        )
    else:
        carry_rule = (
            "- [직전 완료 목록]을 '### ✅ 이번 주 완료'에 그대로 먼저 싣고, 이번에 새로 닫은 "
            "것만 덧붙인다. 같은 항목을 중복으로 넣지 마라."
        )

    return f"""당신은 20년차 CPO 장홍석(Jay)의 업무 비서다. 다니엘프로젝트 _INDEX.md(아침에 \
가장 먼저 여는 단기 현황 문서)를 아래 입력으로부터 재생성한다.

# 출력 규칙 (엄수)
- 아래 4개 섹션만, 정확히 이 순서·이 제목으로 출력한다. 다른 섹션을 추가하지 마라.
  ## 🔴 열린 막힘 / 결정 대기
  ## 📍 오늘 ({today_label})
  ## 🆕 어제까지의 변화 (지난 며칠)
  ## 🎯 이번 주 포커스
- 출력 맨 앞은 다음 두 줄로 시작한다:
  # 다니엘프로젝트 — _INDEX (아침 문서)
  > 마지막 갱신: {today_label}
  > 전략·구조·인물·로드맵 → [[핵심-컨텍스트]] / 결정 이력 → [[결정-로그]]
- 각 섹션 내용:
  · 🔴 = 결정로그의 '미결/관찰'과 Daily 속 막힘·대기 항목. 무엇을·누구 판단 대기인지 1줄씩.
    단, 결정로그·Daily에 그 막힘을 해소하는 결정·완료가 이미 있으면 싣지 않는다(해소된 막힘 제외).
  · 📍 오늘 = 임박/진행 중인 것만 가볍게. 상세 일정·할 일은 "(상세는 Todoist·캘린더 동기 반영)"
    한 줄로 위임한다(여기서 일정을 지어내지 마라).
  · 🆕 = 최근 며칠 Daily·결정로그의 실제 변화 델타. 날짜(M/D) 접두로 사실만.
  · 🎯 = 두 부분으로 구성한다.
    (1) 열린 항목: 최신 주간회고 '④ 차주 계획/액션' 중 아직 안 끝난 것만 '- [ ]'로. 회고에 있는 것만.
    (2) 그 바로 아래 '### ✅ 이번 주 완료' 하위 섹션: 이번 주 닫은 항목을 '- M/D 항목 — 근거 한 줄'로.
    · 완료 판정: 최근 Daily·결정로그에 명확한 닫힘 증거가 있는 ④계획 항목만 (1)에서 빼서 (2)로 옮긴다.
      '착수/진행/예정/검토 중'은 완료가 아니다.
      애매하면 (1)에 열린 채로 둔다 — 끝나지 않은 걸 닫는 것이 닫힌 걸 다시 여는 것보다 해롭다.
    · 완료 증거 우선순위 (앞쪽이 강함):
      ① Daily의 '오늘완료'(또는 '✅완료'·'완료') 노드 바로 아래 나열된 항목 — 명시적 완료. 추측 불필요.
      ② '## ✅ 오늘 완료한 일 (Todoist)' 섹션의 항목 — 명시적 완료. 추측 불필요.
      ③ 결정로그에 해당 항목을 닫는 결정·완료 기록이 있음.
      ④ 본문 텍스트의 맥락상 완료 표현(완료/확정/반영 완료/합격/해소/종료 등) — 보조 증거, 더 보수적으로.
      ①②③ 중 하나면 확정 완료로 (2)에 넣는다. ④만 있으면 확실할 때만.
    {carry_rule}
    · (1)에 열린 항목이 하나도 없으면 (1)은 비우고 '### ✅ 이번 주 완료'만 남겨도 된다.
- 절대 담지 말 것(→ 핵심-컨텍스트 소관): 팀 명단·파일 맵·전략 원칙·로드맵·인물 평가·과거 확정사항.
- 사실 근거가 입력에 없으면 지어내지 말고 생략한다. 한국어. 군더더기 없이.

# 추가 출력: 변경 요약
- 위 본문을 모두 출력한 뒤, 한 줄에 `{DIFF_DELIM}` 만 적고,
  그 아래에 '직전 _INDEX 대비 핵심 변화'를 1~3줄 불릿(- )으로 적는다(특히 이번에 ✅로 닫은 항목).
  없으면 "- 큰 변화 없음".

# 입력
## [직전 _INDEX (스타일·diff 기준)]
{prev_index}

## [직전 완료 목록 (✅ 이번 주 완료 — carry-forward 대상)]
{prev_completed or "(없음)"}

## [최근 Daily]
{daily}

## [결정-로그 (최신 일부)]
{decisions}

## [최신 주간회고]
{weekly}
"""


# ── 멱등 가드 (하루 1회·동시 1개만 통과) ──────────────────────────
# 같은 날 여러 번/병렬로 실행돼도(예: cron 캐치업으로 밀린 작업이 한꺼번에 발사)
# 텔레그램 중복 발송이 생기지 않도록, '오늘 날짜' 락을 원자적으로 잡는다.
LOCK_FILE_TMPL = "/tmp/regenerate_index.{date}.lock"


def acquire_daily_lock(now) -> bool:
    """오늘(KST) 락을 원자적으로 획득. 성공 True, 이미 처리됨/처리 중이면 False.

    O_CREAT|O_EXCL 은 파일이 없을 때만 생성에 성공하는 원자적 연산이라,
    3개가 동시에 깨어나도 단 하나만 True 를 받고 나머지는 조용히 빠진다.
    """
    path = LOCK_FILE_TMPL.format(date=now.strftime("%Y-%m-%d"))
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"pid={os.getpid()} at {now.isoformat()}\n".encode())
    finally:
        os.close(fd)
    # 지난 날짜 락은 정리(베스트 에포트) — /tmp 누적 방지
    for old in glob.glob("/tmp/regenerate_index.*.lock"):
        if old != path:
            try:
                os.remove(old)
            except OSError:
                pass
    return True


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # 키 주입은 수동 단계. 나머지 인프라는 준비됐으니, 키가 들어오면 곧장 돈다.
        # 나이틀리 텔레그램 스팸 방지를 위해 조용히 종료(로그만 남김).
        print(
            "SKIP: ANTHROPIC_API_KEY 미설정 — _INDEX 미변경. "
            ".env 에 키를 넣으면 다음 저녁부터 재생성됩니다.",
            file=sys.stderr,
        )
        sys.exit(0)

    if not INDEX_FILE.exists():
        fail("대상 _INDEX 없음", str(INDEX_FILE))

    # 멱등 가드: 오늘 이미 처리됐거나 동시에 다른 실행이 잡았으면 조용히 종료.
    # (작업 시작 전에 잡아, LLM 호출·텔레그램 전송까지 하루 1회만 일어나게 한다.)
    now = datetime.now(KST)
    if not acquire_daily_lock(now):
        print(
            f"SKIP: 오늘({now:%Y-%m-%d}) 이미 처리됨/처리 중 — 중복 실행 무시.",
            file=sys.stderr,
        )
        sys.exit(0)

    prev_index = INDEX_FILE.read_text(encoding="utf-8")
    daily = read_recent_daily()
    decisions = read_tail(DECISIONS_FILE, DECISIONS_CHARS)
    weekly, week_id = read_latest_weekly()

    if not daily and not decisions:
        fail("입력 빔", "Daily·결정로그를 못 읽음 — 보존")

    # 🎯 ✅완료 목록의 주 전환 리셋 판정:
    #   직전 _INDEX 헤더의 focus-week 마커 != 이번 최신 주간회고 주차  → 새 주차로 보고 리셋.
    #   week_id 를 못 읽으면(회고 없음) 리셋하지 않는다(누적 데이터 보호).
    prev_week = extract_focus_week(prev_index)
    reset_completed = bool(week_id) and (week_id != prev_week)
    prev_completed = extract_completed_block(prev_index)

    weekday = "월화수목금토일"[now.weekday()]
    today_label = f"{now.month}/{now.day} {weekday}"

    prompt = build_prompt(
        prev_index, daily, decisions, weekly, today_label,
        reset_completed, prev_completed,
    )

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason == "max_tokens":
            print("WARN: max_tokens 도달 — 출력 짤림 가능", file=sys.stderr)
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        fail("LLM 호출", f"{type(e).__name__}")
        return

    # 본문 / 변경요약 분리
    if DIFF_DELIM in raw:
        body, diff = raw.split(DIFF_DELIM, 1)
        body, diff = body.strip(), diff.strip()
    else:
        body, diff = raw, "- (변경 요약 미생성)"

    # 검증: 4섹션 전부 존재 + 최소 길이
    missing = [m for m in REQUIRED_MARKERS if m.split(" (")[0] not in body]
    if missing or len(body) < 200:
        fail("출력 검증 실패", f"누락 섹션/짧음: {missing} len={len(body)} — 보존")

    # 주차 마커를 헤더에 박는다(Python이 결정론적으로 씀) — 다음 실행의 리셋 판정 기준.
    week_marker = f"<!-- focus-week: {week_id} -->\n" if week_id else ""
    final = f"{HEADER}\n{week_marker}\n{body}\n"

    # 쓰기 (allowlist 밖 경로는 절대 건드리지 않음) — 백업 후 원자적 교체
    assert INDEX_FILE in WRITE_ALLOWLIST and BAK_FILE in WRITE_ALLOWLIST
    try:
        BAK_FILE.write_text(prev_index, encoding="utf-8")
        tmp = INDEX_FILE.with_suffix(".md.tmp")
        tmp.write_text(final, encoding="utf-8")
        tmp.replace(INDEX_FILE)
    except Exception as e:
        fail("_INDEX 쓰기", f"{type(e).__name__} — 백업({BAK_FILE.name})에서 복구 가능")
        return

    print(f"✅ _INDEX 재생성 완료 ({today_label}) — {len(final)} chars")

    # 변경 요약 알림 (Jay 검수용)
    send_telegram(
        f"🔄 <b>_INDEX 재생성 — {today_label}</b>\n"
        f"{diff}\n\n"
        f"<i>아침에 _INDEX 열어 확인 / 롤백: _INDEX.md.bak</i>"
    )


if __name__ == "__main__":
    main()
