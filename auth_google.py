"""
Google Calendar OAuth2 인증 헬퍼
최초 1회만 실행하면 refresh_token을 발급받을 수 있습니다.

사용법:
  1. .env 에 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET 입력
  2. python auth_google.py 실행
  3. 브라우저에서 구글 로그인 및 권한 허용
  4. 출력된 GOOGLE_REFRESH_TOKEN 값을 .env 에 복사
"""

import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

client_config = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0)

print("\n" + "=" * 60)
print("✅ 인증 성공! 아래 값을 .env 파일에 복사하세요:")
print("=" * 60)
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
print("=" * 60 + "\n")
