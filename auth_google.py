"""
Google Calendar OAuth2 인증 헬퍼
refresh_token을 발급받아 .env의 GOOGLE_REFRESH_TOKEN 한 줄을 안전하게 갱신합니다.

🔴 토큰을 stdout/채팅에 출력하지 않습니다(로그 영구잔존 방지). .env는 백업 후 해당 줄만 교체.

사용법:
  1. .env 에 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET 입력
  2. python auth_google.py 실행
  3. 브라우저에서 '캘린더 소유 계정'(예: hs.jang@danielproject.co.kr) 선택 + 권한 허용
  4. .env의 GOOGLE_REFRESH_TOKEN 자동 갱신 (수동 복사 불필요)
"""

import os
import shutil
import time
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
ENV_PATH = Path(__file__).resolve().parent / ".env"


def update_env_token(token: str) -> None:
    """.env의 GOOGLE_REFRESH_TOKEN 줄만 교체(없으면 추가). 다른 비밀값은 그대로 보존.
    백업(.env.bak.<ts>)을 먼저 남긴 뒤 in-place rewrite — 통째 덮어쓰기(>) 금지 원칙 준수."""
    if not ENV_PATH.exists():
        raise SystemExit(f".env 없음: {ENV_PATH}")
    shutil.copy2(ENV_PATH, ENV_PATH.parent / f".env.bak.{int(time.time())}")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.startswith("GOOGLE_REFRESH_TOKEN="):
            out.append(f"GOOGLE_REFRESH_TOKEN={token}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"GOOGLE_REFRESH_TOKEN={token}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


client_config = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

if __name__ == "__main__":
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")  # prompt=consent → refresh_token 보장
    if not creds.refresh_token:
        raise SystemExit("❌ refresh_token 미발급 — 브라우저에서 권한을 새로 허용했는지 확인하세요.")
    update_env_token(creds.refresh_token)
    print("\n✅ 인증 성공 — .env의 GOOGLE_REFRESH_TOKEN 갱신 완료 (토큰은 화면에 출력하지 않음).")
    print("   다음: DRY_RUN=1 venv/bin/python morning_brief.py 로 캘린더 제목 확인.")
