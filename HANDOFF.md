# HANDOFF — idea-bot (집에서 이어서 마무리)

> 회사맥에서 작성 2026-06-25 18:40. 집맥에서 `git pull` 후 이 파일대로 마무리.

## 무슨 작업
**Idea Bot** — Telegram으로 보낸 메모를 Obsidian `00-Inbox/Ideas.md` 하단에 append.
polling 방식(외부 서버 불필요), launchd 자동 실행. 아침 브리핑 봇과 **별개 봇**.

## 현재 상태
- `idea_bot.py` — 작성됨(폴링 루프 동작 형태).
- `com.jay.idea-bot.plist` — 템플릿. `IDEA_BOT_DIR` placeholder 미치환.
- `.env.example` — `IDEA_BOT_TOKEN`, `IDEA_BOT_CHAT_ID` 항목 추가됨.

## 집에서 마무리 할 일 (순서대로)
1. **봇 생성**: @BotFather → `/newbot` → `IDEA_BOT_TOKEN` 발급.
2. **시크릿 주입**: 실제 `.env`에 `IDEA_BOT_TOKEN`, `IDEA_BOT_CHAT_ID`를 **append(`>>`)로** 추가. 절대 `>` 덮어쓰기 금지([[never-clobber-env-secrets]]).
3. **🔴 경로 수정**: `idea_bot.py`의 `IDEAS_FILE`이 `/Users/dp-tech-jhs/...`로 하드코딩됨. 집맥은 `/Users/jay/...`. → `Path.home()` 기반으로 고치는 걸 권장(양쪽 맥 공용).
4. **plist 배선**: `IDEA_BOT_DIR`을 실제 repo 경로로 치환 → `~/Library/LaunchAgents/`에 복사 → `launchctl load`.
5. **테스트**: `python idea_bot.py` 직접 실행 → 텔레그램 메모 전송 → `Ideas.md` 하단 append 확인. (텔레그램 수신 0이면 [[telegram-allowed-updates-gotcha]] 점검)

## 검증 끝나면
- launchd 등록 확인, HANDOFF.md 삭제 또는 "완료" 처리.
