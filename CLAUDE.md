# CLAUDE.md — daily-automation 프로젝트 컨텍스트

## 프로젝트 개요

**오너**: 장홍석 (Jay) — 20년차 CPO, 다니엘프로젝트 부대표
**목적**: CPO 개인 생산성 자동화. 아침 브리핑 + 저녁 기록을 자동화한다.

---

## 프로젝트 구성

### 프로젝트 1: 아침 브리핑 텔레그램 봇 (`morning_brief.py`)

- **실행 시각**: 매일 07:30 KST (GitHub Actions cron: `30 22 * * *` UTC)
- **전송 내용**: 서울 날씨 / 오늘 구글 캘린더 일정 / Todoist 오늘 할 일 / 내일 일정 미리보기
- **사용 API**: OpenWeatherMap, Google Calendar API (OAuth2), Todoist REST API, Telegram Bot API

### 프로젝트 2: 저녁 Todoist→Obsidian 자동 기록 (`evening_sync.py`)

- **실행 시각**: 매일 22:00 KST (GitHub Actions cron: `0 13 * * *` UTC)
- **동작**: Todoist 오늘 완료 항목 → Obsidian Daily Note 하단에 append
- **저장 경로**: `/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/다니엘프로젝트/Daily/{YYYY-MM-DD}.md`
- **주의**: GitHub Actions는 로컬 Obsidian vault에 접근 불가 → 로컬 맥북에서 실행 권장

---

## 환경변수

| 변수명 | 용도 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 인증 |
| `TELEGRAM_CHAT_ID` | 메시지 수신 채팅 ID |
| `OPENWEATHER_API_KEY` | 날씨 API |
| `WEATHER_CITY` | 날씨 도시 (기본: Seoul) |
| `TODOIST_API_TOKEN` | Todoist 할 일 / 완료 항목 |
| `GOOGLE_CLIENT_ID` | Google Calendar OAuth2 |
| `GOOGLE_CLIENT_SECRET` | Google Calendar OAuth2 |
| `GOOGLE_REFRESH_TOKEN` | Google Calendar OAuth2 (auth_google.py로 발급) |
| `GOOGLE_CALENDAR_ID` | 조회할 캘린더 ID (보통 Gmail 주소) |

---

## 공통 패턴

- **에러 처리**: 모든 외부 API 오류는 텔레그램으로도 알림 (`send_error()`)
- **타임존**: 한국 표준시 (KST = UTC+9) 기준으로 동작
- **직접 실행**: `python morning_brief.py` / `python evening_sync.py` 로 로컬 실행 가능
- **언어**: 코드 주석은 한국어

---

## 파일 구조

```
daily-automation/
├── .github/workflows/
│   ├── morning_brief.yml
│   └── evening_sync.yml
├── morning_brief.py
├── evening_sync.py
├── auth_google.py          # Google OAuth2 refresh_token 발급 (최초 1회)
├── .env.example
├── .gitignore
├── requirements.txt
├── CLAUDE.md               # 이 파일
└── README.md
```

---

## GitHub 정보

- **GitHub 유저명**: wishyouarehere
- **레포 이름**: daily-automation
- **Remote URL**: https://github.com/wishyouarehere/daily-automation
