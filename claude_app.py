"""클로드 앱(claude.ai 웹·데스크톱·폰) 대화를 하루 기록 재료로 가져온다 (2026-09-30 Jay 결정).

앱이 대화마다 구글 문서를 만들던 방식은 대화 화면에 카드·로그인 패널을 띄워 은퇴했다.
대신 수집 전용 브라우저 프로필(~/.claude-app-log-profile, Jay가 한 번 로그인)로 claude.ai를 열고,
웹이 쓰는 내부 API를 그 페이지 안에서 fetch한다. Jay의 평소 Chrome은 건드리지 않는다.
🔴 claude.ai(Cloudflare)는 화면 없는(headless) 브라우저를 막는다. 식별값을 속여 뚫지 않는다(2026-09-30 Jay 확인) —
실제 창을 화면 밖 구석에 10~20초 띄웠다 닫는다. 그래서 GUI 세션이 있는 launchd(gui)에서만 돈다.
🔴 비공식 경로라 Anthropic이 바꾸거나 로그인이 풀리면 실패한다 — 실패는 notify_policy(warning,
날짜를 넘겨 재발하면 승격)로 알리고 그날 클로드 앱 칸만 빈다.

  python3 claude_app.py --login             전용 창을 띄워 claude.ai 로그인(처음·만료 때)
  python3 claude_app.py [YYYY-MM-DD]        그날 대화를 JSON 목록으로 출력(기본 오늘)
  python3 claude_app.py --cache             launchd 00:05: 어제 것(+실패했던 그저께)을 캐시에 저장

캐시 ~/.local/state/jay-desk/daylog/app-YYYY-MM-DD.json 을 00:15 cron day_log가 읽는다.
실패하면 notify_policy(warning, 날짜를 넘겨 재발하면 승격).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
PROFILE = Path.home() / ".claude-app-log-profile"
CACHE_DIR = Path.home() / ".local/state/jay-desk/daylog"
HOME_URL = "https://claude.ai/new"

# 페이지 안에서 도는 수집 스크립트: 조직 → 최근 수정순 대화 목록 → 그날 건드린 대화 본문.
_JS = """
async ({start}) => {
  const get = async (p) => {
    const r = await fetch('/api' + p, {credentials: 'include'});
    if (!r.ok) throw new Error('claude.ai ' + r.status + ' ' + p.split('?')[0]);
    return r.json();
  };
  const orgs = await get('/organizations');
  const org = orgs.find(o => (o.capabilities || []).includes('chat')) || orgs[0];
  if (!org) throw new Error('조직 목록이 비어 있음');
  const base = '/organizations/' + org.uuid + '/chat_conversations';
  let list = [];
  for (let off = 0; off < 1000; off += 50) {
    const page = await get(base + '?limit=50&offset=' + off);
    list = list.concat(page);
    if (page.length < 50 || page[page.length - 1].updated_at < start) break;
  }
  const out = [];
  for (const c of list.filter(c => c.updated_at >= start)) {
    const full = await get(base + '/' + c.uuid + '?tree=True&rendering_mode=messages&render_all_tools=true');
    out.push({uuid: c.uuid, name: full.name || c.name || '',
              project: (full.project || {}).name || '',
              messages: (full.chat_messages || []).map(m => ({
                sender: m.sender, created_at: m.created_at,
                text: ((m.content || []).filter(x => x.type === 'text').map(x => x.text).join(' ')
                       || m.text || '')}))});
  }
  return out;
}
"""


def _context(p, visible: bool):
    """visible=False도 진짜 창이다 — 화면 밖 좌표에 작게 띄울 뿐(headless는 Cloudflare가 막는다)."""
    PROFILE.mkdir(exist_ok=True)
    args = [] if visible else ["--window-position=-2400,-2400", "--window-size=500,400"]
    return p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=False, args=args,
                                                chromium_sandbox=True)  # 기본값은 --no-sandbox(격리 끔)라 켠다


def login() -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _context(p, visible=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://claude.ai/login")
        print("전용 창에서 claude.ai에 로그인하세요. 채팅 화면이 뜨면 자동으로 닫혀요(최대 10분).")
        try:
            page.wait_for_url(lambda u: "/new" in u or "/chat" in u or "/recents" in u, timeout=600_000)
            page.wait_for_timeout(3000)
            print("✅ 로그인 저장 완료")
            return 0
        except Exception:
            print("❌ 10분 안에 로그인이 확인되지 않았어요")
            return 1
        finally:
            ctx.close()


def fetch(day: date) -> list[dict]:
    """그날 대화의 원자료(메시지 목록). 요약·정리는 day_log가 한다."""
    from playwright.sync_api import sync_playwright
    start = datetime(day.year, day.month, day.day, tzinfo=KST).astimezone(timezone.utc)
    with sync_playwright() as p:
        ctx = _context(p, visible=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)  # 첫 화면 보안 검사가 끝날 시간
            relogin = "로그인 풀림 — 회사맥에서 `python3 ~/Documents/daily-automation/claude_app.py --login` 한 번"
            if "/login" in page.url:
                raise RuntimeError(relogin)
            try:
                return page.evaluate(_JS, {"start": start.strftime("%Y-%m-%dT%H:%M:%S")})
            except Exception as e:  # noqa: BLE001
                if "401 /organizations" in str(e) or "403 /organizations" in str(e):
                    raise RuntimeError(relogin) from None
                raise RuntimeError(str(e).split("\n")[0]) from None
        finally:
            ctx.close()


def cache_path(day: date) -> Path:
    return CACHE_DIR / f"app-{day.isoformat()}.json"


def read_cache(day: date) -> dict | None:
    try:
        return json.loads(cache_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache() -> int:
    """어제 것을 저장하고, 그저께가 실패(맥 잠자기 등)였으면 한 번 더 시도한다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(KST).date()
    days = [today - timedelta(days=1)]
    old = read_cache(today - timedelta(days=2))
    if old is None or not old.get("ok"):
        days.append(today - timedelta(days=2))
    rc, err = 0, ""
    for day in days:
        try:
            data = {"ok": True, "conversations": fetch(day)}
        except Exception as e:  # noqa: BLE001
            data = {"ok": False, "error": str(e)[:300]}
            rc, err = 1, data["error"]
        prev = read_cache(day)
        if data["ok"] or not (prev and prev.get("ok")):
            cache_path(day).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"{day}: " + (f"✅ {len(data['conversations'])}건" if data["ok"] else f"❌ {data['error']}"))
    if rc:
        try:
            sys.path.insert(0, str(Path.home() / "ops-bot"))
            import notify_policy
            notify_policy.send(f"하루기록: 클로드 앱 대화 수집 실패 — {err[:150]}",
                               severity="warning", source="day_log")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 알림 실패: {e}", file=sys.stderr)
    return rc


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--login":
        return login()
    if argv and argv[0] == "--cache":
        return cache()
    day = date.fromisoformat(argv[0]) if argv else datetime.now(KST).date()
    try:
        print(json.dumps({"ok": True, "conversations": fetch(day)}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)[:300]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
