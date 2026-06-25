#!/bin/bash
# install_idea_bot.sh
# Telegram → Obsidian 아이디어 봇 설치 스크립트
# 회사 맥북 / 집 맥북 공통 사용

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.jay.idea-bot"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$SCRIPT_DIR/logs"

# ── 컬러 출력 ────────────────────────────────────────────────────────
green() { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red() { echo -e "\033[0;31m$*\033[0m"; }

echo ""
green "=== Telegram → Obsidian 아이디어 봇 설치 ==="
echo ""

# ── 1. 의존성 확인 ────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    red "python3가 설치되어 있지 않습니다. Homebrew로 설치하세요: brew install python3"
    exit 1
fi

yellow "▸ 필요 패키지 설치 중..."
pip3 install requests python-dotenv --quiet
green "  패키지 OK"

# ── 2. Bot token 입력 ─────────────────────────────────────────────────
echo ""
yellow "▸ Telegram Bot Token을 입력하세요."
echo "  (BotFather에서 /newbot으로 발급한 토큰)"
read -r -p "  BOT_TOKEN: " BOT_TOKEN

if [ -z "$BOT_TOKEN" ]; then
    red "토큰이 비어 있습니다. 중단합니다."
    exit 1
fi

# ── 3. Chat ID 입력 ───────────────────────────────────────────────────
echo ""
yellow "▸ 허용할 Telegram Chat ID를 입력하세요."
echo "  (본인 채팅 ID. 모르면 엔터 → @userinfobot에서 확인 가능)"
read -r -p "  CHAT_ID (빈 값이면 모든 채팅 허용): " CHAT_ID

# ── 4. Vault 경로 확인 ────────────────────────────────────────────────
VAULT_USER=$(whoami)
IDEAS_PATH="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/jay/00-Inbox/Ideas.md"

echo ""
if [ -f "$IDEAS_PATH" ]; then
    green "▸ Ideas.md 발견: $IDEAS_PATH"
else
    yellow "▸ Ideas.md 없음 → 봇 실행 시 자동 생성됩니다."
fi

# ── 5. idea_bot.py 내 경로를 현재 사용자에 맞게 패치 ────────────────
# dp-tech-jhs 이외의 맥북에서는 사용자 이름이 다를 수 있음
if [ "$VAULT_USER" != "dp-tech-jhs" ]; then
    yellow "▸ 현재 사용자($VAULT_USER)에 맞게 idea_bot.py 경로 패치 중..."
    sed -i '' "s|/Users/dp-tech-jhs/|/Users/$VAULT_USER/|g" "$SCRIPT_DIR/idea_bot.py"
    green "  패치 완료"
fi

# ── 6. 로그 디렉토리 생성 ─────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── 7. plist 생성 ────────────────────────────────────────────────────
yellow "▸ launchd plist 생성 중..."

sed \
    -e "s|IDEA_BOT_DIR|$SCRIPT_DIR|g" \
    -e "s|REPLACE_BOT_TOKEN|$BOT_TOKEN|g" \
    -e "s|REPLACE_CHAT_ID|$CHAT_ID|g" \
    "$SCRIPT_DIR/com.jay.idea-bot.plist" > "$PLIST_DEST"

green "  plist → $PLIST_DEST"

# ── 8. 기존 서비스 unload (재설치 시) ────────────────────────────────
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# ── 9. 서비스 등록 ───────────────────────────────────────────────────
yellow "▸ launchd 서비스 등록 중..."
launchctl load "$PLIST_DEST"
green "  등록 완료"

# ── 완료 ─────────────────────────────────────────────────────────────
echo ""
green "✅ 설치 완료!"
echo ""
echo "  봇 상태 확인:   launchctl list | grep idea-bot"
echo "  로그 확인:      tail -f $LOG_DIR/idea_bot.log"
echo "  수동 중지:      launchctl unload $PLIST_DEST"
echo "  수동 시작:      launchctl load $PLIST_DEST"
echo ""
yellow "📌 테스트: Telegram에서 봇에게 아무 메시지나 보내보세요."
echo ""

# ── BotFather 안내 (처음 설치 시) ─────────────────────────────────────
if [ -z "$(launchctl list 2>/dev/null | grep idea-bot)" ]; then
    echo "──────────────────────────────────────────────"
    yellow "📖 Bot Token 발급 방법 (처음 설치하는 경우)"
    echo "  1. Telegram 앱에서 @BotFather 검색"
    echo "  2. /newbot 입력"
    echo "  3. 봇 이름 입력 (예: Jay's Idea Bot)"
    echo "  4. 봇 username 입력 (예: jay_idea_bot)"
    echo "  5. 발급된 토큰을 위에 입력"
    echo ""
    yellow "📖 Chat ID 확인 방법"
    echo "  1. Telegram에서 @userinfobot 에게 /start 전송"
    echo "  2. 또는 봇에게 메시지 보낸 후:"
    echo "     curl https://api.telegram.org/bot<TOKEN>/getUpdates"
    echo "──────────────────────────────────────────────"
fi
