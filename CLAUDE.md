# CLAUDE.md — daily-automation 프로젝트 컨텍스트

## 프로젝트 개요

**오너**: 장홍석 (Jay) — 20년차 CPO, 다니엘프로젝트 부대표
**목적**: CPO 개인 생산성 자동화. 아침 브리핑 + 저녁 기록을 자동화한다.

---

## 프로젝트 구성

### 프로젝트 1: 아침 브리핑 텔레그램 봇 (`morning_brief.py`)

- **실행 시각·주체**: 매일 06:30 KST. **2026-06-30 집맥 로컬 launchd로 이전**(`com.jay.morning-brief`, 실행 자산은 집맥 `~/daily-automation`). GitHub Actions(`.github/workflows/morning_brief.yml`)는 schedule 비활성·`workflow_dispatch` 폴백만. 이전 사유=정시성·예측가능성.
  - 🔴 **집맥 경로 주의**: 집맥은 launchd가 `~/Documents`(TCC 보호 폴더)를 못 읽어 repo를 `~/daily-automation`(홈 직하)으로 옮김. **회사맥은 그대로 `~/Documents/daily-automation`**(개발 클론, evening_sync는 cron이라 TCC 영향 없음). 즉 집/회사 경로가 갈림.
  - **컨텍스트 파일 소스**: `GITHUB_TOKEN` 있으면(Actions) workflowy-sync 레포 API, 없으면(집맥) 로컬 미러 `~/wf-sync/{_INDEX,_CONTEXT,_DAILY_LATEST}.md` 직접 읽기(하이브리드 분기).
- **형식**: 참모 브리핑 3블록 (데이터 덤프 아님 — '판단'이 본체). 2026-06-30 전면 재설계.
  - **① 오늘 한눈에**: 날짜+날씨 꼬리표(비/눈/극한기온일 때만) · `오늘 무게중심`(LLM 1줄) · 오늘 시간표 · `꼭:` 할일 top 1~3(Todoist 우선순위) · `내일:` 한 줄
  - **② 참모 판단**: D-day(7/3 v1·8/3 전사전환, 지난 건 자동 생략) + 오늘 짚을 결정 0~3건, 각 `→ 네 결정 / 대표로 올릴 것 / 위임` 레벨 태그. 마땅한 게 없으면 "오늘 급한 결정 없음".
  - **③ 백그라운드**: 텔레그램 `<blockquote expandable>` 접이식. 어제 완료·이번주 포커스·신선도(⚠️ 임계 초과 시만).
- **요일 변주**(골격 고정, 블록 ②만): 월=`위클리 대비` / 금=`주간 회고`(level=닫음·넘김·미뤄짐) / 토·일=경량(①+1줄, ③ 생략·LLM 호출 안 함).
- **LLM**: 단일 콜(`get_chief_brief`)로 무게중심+판단 동시 생성(JSON). 모델 `claude-opus-4-8`. 입력 = `chief_pack`(농축 참모 팩, 집맥 ~/wf-sync/cache, ~05:40 갱신) 우선 → 없으면 `_CONTEXT.md` 폴백 + `_INDEX`(열린 막힘·이번주 포커스) + 어제 기록 + 오늘 캘린더 + 오늘 Todoist.
- **사용 API**: OpenWeatherMap, Google Calendar API (OAuth2), Todoist REST API, Anthropic API, Telegram Bot API
  - 🔴 `.env`의 `GOOGLE_REFRESH_TOKEN`이 만료(`invalid_grant`)면 캘린더 섹션만 빔 → `python auth_google.py`로 재발급 후 `.env` 갱신(집·회사 .env 각각).
- **테스트**: `DRY_RUN=1 FORCE_WEEKDAY=0~6 venv/bin/python morning_brief.py` (전송 안 함, 요일 강제, stdout 출력)
- **참모 팩 재사용**: `~/wf-sync/chief_pack.py`(distill 캐시)·`chief_qa.py`(레벨 판정 철학)와 동일 자산. 새로 만들지 말 것.

### 프로젝트 2: 저녁 Todoist→Obsidian 자동 기록 (`evening_sync.py`)

- **실행 시각**: 매일 22:00 KST — **실제 실행 주체는 회사 맥북 로컬 crontab** (`0 22 * * * /opt/homebrew/bin/python3 ~/Documents/daily-automation/evening_sync.py`). GitHub Actions(`0 13 * * *` UTC)도 등록돼 있으나 vault 미접근(아래 주의)이라 텔레그램/아티팩트용일 뿐.
- **동작**: Todoist 오늘 완료 항목 → Obsidian Daily Note 하단에 append
- **저장 경로**: `/Users/dp-tech-jhs/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/다니엘프로젝트/Daily/{YYYY-MM-DD}.md`
- **주의**: GitHub Actions는 로컬 Obsidian vault에 접근 불가 → **로컬 crontab이 실제 기록 주체**. (2026-06-25 검증: `/tmp/evening_sync.log` 6/24 22:00 실행에서 6/22·23·24 노트 기록 성공)

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
