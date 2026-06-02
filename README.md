# daily-automation

CPO 개인 생산성 자동화 프로젝트.
매일 아침 브리핑 텔레그램 봇과 저녁 Todoist→Obsidian 자동 기록을 실행합니다.

## 스크립트

| 스크립트 | 설명 | 실행 시각 |
|---|---|---|
| `morning_brief.py` | 날씨 / 캘린더 / 할 일 → 텔레그램 전송 | 07:30 KST |
| `evening_sync.py` | Todoist 완료 항목 → Obsidian Daily Note | 22:00 KST |
| `auth_google.py` | Google OAuth2 refresh_token 발급 (최초 1회) | 수동 |

## 초기 설정

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 실제 값 입력

# 3. Google Calendar 인증 (최초 1회)
python auth_google.py
# 출력된 GOOGLE_REFRESH_TOKEN을 .env에 복사
```

## 로컬 실행

```bash
python morning_brief.py
python evening_sync.py
```

## GitHub Actions 설정

레포지토리 → Settings → Secrets and variables → Actions → New repository secret

필요한 시크릿:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OPENWEATHER_API_KEY`
- `WEATHER_CITY` (기본값: Seoul)
- `TODOIST_API_TOKEN`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CALENDAR_ID`

## 저녁 동기화 주의사항

GitHub Actions 서버는 로컬 Obsidian vault(`~/Library/...`)에 접근할 수 없습니다.
저녁 동기화(`evening_sync.py`)는 **로컬 맥북에서 직접 실행**하거나,
`launchd` / `cron` 으로 맥북에 스케줄링하는 것을 권장합니다.

```bash
# 로컬 cron 설정 예시 (crontab -e)
0 22 * * * cd /Users/dp-tech-jhs/Documents/daily-automation && python evening_sync.py
```
