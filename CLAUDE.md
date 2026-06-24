# CLAUDE.md — daily-automation 프로젝트 컨텍스트

## 프로젝트 개요

**오너**: 장홍석 (Jay) — 20년차 CPO, 다니엘프로젝트 부대표
**목적**: CPO 개인 생산성 자동화. 아침 브리핑 + 저녁 기록 + 일간/주간 리뷰를 자동화한다.
**실행 환경**: GitHub Actions(cron) 기준. 단 evening_sync는 로컬 맥북 실행에 의존(아래 주의).

---

## 프로젝트 구성 (스크립트 4종)

### 1. 아침 브리핑 (`morning_brief.py`)
- **시각**: 매일 **06:30 KST** (Actions cron `30 21 * * *` UTC)
- **내용**: 서울 날씨 / 오늘 구글 캘린더 / Todoist 오늘 할 일 / 내일 일정 / **다니엘 어드바이저 노트**(workflowy-sync 레포의 `_INDEX`·`_CONTEXT`·`_DAILY_LATEST`를 읽어 Claude 분석)
- **API**: OpenWeatherMap, Google Calendar(OAuth2), Todoist, Telegram, Anthropic, GitHub(읽기)

### 2. 저녁 기록 (`evening_sync.py`)
- **시각**: 매일 22:00 KST (Actions cron `0 13 * * *` UTC)
- **동작**: Todoist 오늘 완료 항목 → Obsidian Daily Note 하단에 append
- **저장 경로**: `/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/다니엘프로젝트/Daily/{YYYY-MM-DD}.md`
- **🔴 주의 & ❓미검증 (2026-06-24)**: GitHub Actions는 로컬 Obsidian vault에 접근 불가 → 실제 Daily 기록은 **회사맥북 로컬 실행**에 의존. 단 **회사맥북에 evening_sync용 로컬 cron/launchd가 실제 등록·동작 중인지 미확인** (→ 2026-06-25 오전 회사맥북에서 검증 예정). Actions에 등록된 evening_sync 잡이 vault를 못 써 헛도는지도 함께 확인 필요. (Jay 홈 CLAUDE.md의 `저녁 Todoist 싱크 ⚠️확인 필요`와 동일 이슈.)

### 3. 하루 마감 리뷰 (`daily_review.py`)
- **시각**: 월~금 19:00 KST (Actions cron `0 10 * * 1-5` UTC)
- **동작**: 오늘 Daily 파일 1개 + `_CONTEXT.md`(workflowy-sync) → Claude 분석 → 텔레그램 전송
- **API**: Telegram, Anthropic, GitHub(읽기)

### 4. 주간 패턴 리뷰 (`friday_review.py`)
- **시각**: **토 09:00 KST** (Actions cron `0 0 * * 6` UTC)
  - ⚠️ 스크립트 독스트링엔 "금 18:00"이라 적혀 있으나 **실제 동작은 토 09:00 KST**(cron 기준). 표기만 불일치 — 인지만.
- **동작**: 이번 주 Daily 5개 + `_INDEX.md` + `결정-로그`(workflowy-sync) → Claude 분석 → 텔레그램 전송
- **API**: Telegram, Anthropic, GitHub(읽기)

---

## 환경변수 (GitHub Actions Secrets)

| 변수명 | 용도 | 쓰는 스크립트 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 인증 | 전체 |
| `TELEGRAM_CHAT_ID` | 메시지 수신 채팅 ID | 전체 |
| `OPENWEATHER_API_KEY` | 날씨 API | morning |
| `WEATHER_CITY` | 날씨 도시 (기본 Seoul) | morning |
| `TODOIST_API_TOKEN` | Todoist 할 일/완료 | morning, evening |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` / `GOOGLE_CALENDAR_ID` | Google Calendar OAuth2 | morning |
| `ANTHROPIC_API_KEY` | Claude 분석 | morning, daily, friday |
| `GH_PAT` | **workflowy-sync 레포 읽기용 토큰** (워크플로에서 `GITHUB_TOKEN` 환경변수로 주입) | morning, daily, friday |

### 🔑 GH_PAT 주의 (2026-06-24 정리)
- **Fine-grained PAT** — `wishyouarehere/workflowy-sync` **한 레포만 / Contents: Read-only / 무기한**.
- 용도: morning/daily/friday가 `api.github.com`으로 어드바이저 소스(`_INDEX`·`_CONTEXT`·`_DAILY_LATEST`·`Daily/`·`결정-로그`)를 **읽기**만 함. push 없음.
- 과거 classic PAT(전 레포 권한)는 명령줄 노출 사고로 **폐기됨**(2026-06-24). 토큰은 절대 명령줄/코드에 인라인하지 말 것 → 갱신은 GitHub 웹 UI에서 시크릿만 교체.

---

## 공통 패턴
- **에러 처리**: 외부 API 오류는 텔레그램으로도 알림(`send_error()` / `send_telegram()`)
- **타임존**: KST(UTC+9). Actions cron은 UTC라 9시간 차 주의.
- **직접 실행**: `python <스크립트>.py` 로 로컬 실행/디버그 가능. Actions는 `workflow_dispatch`로 수동 실행도 됨.
- **언어**: 코드 주석 한국어

---

## 파일 구조
```
daily-automation/
├── .github/workflows/
│   ├── morning_brief.yml
│   ├── evening_sync.yml
│   ├── daily_review.yml
│   └── friday_review.yml
├── morning_brief.py
├── evening_sync.py
├── daily_review.py
├── friday_review.py
├── auth_google.py          # Google OAuth2 refresh_token 발급(최초 1회)
├── .env.example
├── requirements.txt
├── CLAUDE.md               # 이 파일
└── README.md
```

---

## GitHub 정보
- **유저/레포**: wishyouarehere/daily-automation
- **Remote**: https://github.com/wishyouarehere/daily-automation
- **데이터 소스 레포**: `wishyouarehere/workflowy-sync` (어드바이저 노트·Daily·결정-로그)

---

## 열린 항목 (TODO)
- [ ] **2026-06-25 오전 회사맥북 검증**: evening_sync 로컬 cron/launchd 등록·동작 여부, 실제 `.env`, evening 로그 최근 실행, Actions evening_sync 잡의 유효성. (Tailscale SSH는 회사맥북 원격 로그인 ON 후 가능.)
