# CLAUDE.md — daily-automation

개인 생산성 자동화 모음이다. 텔레그램 브리핑·회고를 보내고, Todoist와
Obsidian의 다니엘프로젝트 기록을 연결한다.

## 환경과 정본

- 집 맥 저장소: `~/daily-automation` (launchd의 Documents TCC 제약 회피).
- 회사 맥 저장소: `~/Documents/daily-automation`.
- 두 저장소의 경로와 실행 주체가 다르다. 한쪽 경로를 다른 쪽에 그대로 적용하지 않는다.
- 실제 가동 여부와 시각은 문서의 정적 설명이 아니라 해당 맥의 `launchd`와
  `crontab`을 실시간 조회해 판단한다.
- 로컬 Obsidian 쓰기는 회사 맥 vault의
  `10-Projects/다니엘프로젝트` 아래에서 일어난다. GitHub Actions는 로컬 vault에
  접근할 수 없다.
- 모델명·모델별 비용은 각 실행 파일의 상수/설정과 `llm_ledger.py`가 정본이다.
  이 문서를 고치는 작업에서 런타임 모델을 변경하지 않는다.

## 파일 라우팅

- `morning_brief.py`: 캘린더·Todoist·날씨·wf-sync 문맥을 모아 아침 브리핑 생성.
- `evening_sync.py`: Todoist 완료 항목을 Obsidian Daily에 기록.
- `daily_review.py`: Daily와 컨텍스트를 바탕으로 일간 회고 생성·전송.
- `regenerate_index.py`: Daily·결정로그·주간회고를 바탕으로 `_INDEX.md` 재생성.
- `weekly_retro.py`: 주간 자료를 모아 회고 초안 생성.
- `idea_bot.py`: 텔레그램 입력을 Obsidian에 저장하는 봇.
- `auth_google.py`: Google Calendar OAuth 토큰 발급 보조.
- `llm_ledger.py`: LLM 사용량·비용 기록. 공용 정본과 동기화 관계를 파일 상단에서 확인.
- `README.md`와 각 파일의 docstring: 실행법과 세부 동작을 확인하는 첫 경로.

## 작업 규칙

- `.env`, OAuth 값, API 키, 텔레그램 토큰과 채팅 ID를 출력·커밋하지 않는다.
- 외부 API 실패 시 기존 fail-safe를 보존한다. 일부 입력 실패를 빈 데이터로 오인해
  메시지나 Obsidian에 쓰지 않는다.
- 텔레그램 전송, Obsidian 쓰기, 예약 실행은 외부 상태 변경이다. 코드별 안전 모드를
  확인하지 않은 채 직접 실행하지 않는다.
- 아침 브리핑은 `DRY_RUN=1`을 사용하고, 주간회고는 `WEEKLY_RETRO_DRY=1`을
  사용한다. 다른 실행 파일은 전송·쓰기 경로를 읽어 안전성이 확인될 때만 실행한다.
- 검증은 변경 파일에 가장 가까운 테스트와 안전한 dry-run부터 한다. 검증 중 실제
  메시지를 보내거나 실제 노트를 쓰지 않는다.
- 자동화·봇·파이프라인·게이트를 추가·변경·은퇴하면 같은 작업에서
  `~/sns-tracker/sysmap/registry.py`의 노드·서비스 라벨·엣지도 갱신한다.
- 시간대는 KST 기준을 유지하고, 경로·스케줄·모델처럼 변하기 쉬운 값을 문서에
  중복 고정하지 않는다.
