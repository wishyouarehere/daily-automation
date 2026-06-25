#!/bin/bash
# launchd에서 직접 python 실행 시 권한 문제 우회용 래퍼 스크립트
cd /Users/dp-tech-jhs/Documents/daily-automation
exec /usr/bin/python3 /Users/dp-tech-jhs/Documents/daily-automation/idea_bot.py
