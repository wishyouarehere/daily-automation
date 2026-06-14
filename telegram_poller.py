#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
텔레그램 인라인 버튼 클릭 → Todoist 할일 등록

아침 브리핑의 '할일 후보' 버튼(callback_data="td|<할일>")을 눌렀을 때,
GitHub Actions(5분 cron)가 이 스크립트를 실행해 클릭을 수집하고
Todoist '📌 다니엘 프로젝트'에 할일을 등록한 뒤 확인 메시지를 회신한다.

stateless 환경(GitHub Actions)이므로 offset은 처리 직후 confirm 방식으로 관리:
  getUpdates() → 처리 → getUpdates(offset=last+1) 로 처리분을 텔레그램에서 비움.
"""

import os
import sys
import json
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TODOIST_TOKEN  = os.environ["TODOIST_API_TOKEN"]

TARGET_PROJECT = "📌 다니엘 프로젝트"
TG = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TD_HEADERS = {"Authorization": f"Bearer {TODOIST_TOKEN}"}


# ── Todoist ───────────────────────────────────────────────────────
def get_project_id() -> str | None:
    """'📌 다니엘 프로젝트' 프로젝트 ID. 못 찾으면 '다니엘' 부분매칭, 그래도 없으면 None(Inbox)."""
    resp = requests.get("https://api.todoist.com/api/v1/projects", headers=TD_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    projects = data.get("results", data) if isinstance(data, dict) else data
    for p in projects:
        if p.get("name") == TARGET_PROJECT:
            return p["id"]
    for p in projects:
        if "다니엘" in (p.get("name") or ""):
            return p["id"]
    return None


def add_task(content: str, project_id: str | None) -> None:
    payload = {"content": content}
    if project_id:
        payload["project_id"] = project_id
    resp = requests.post("https://api.todoist.com/api/v1/tasks",
                         headers=TD_HEADERS, json=payload, timeout=10)
    resp.raise_for_status()


def get_active_contents(project_id: str | None) -> set:
    """해당 프로젝트의 미완료 할일 content 집합 (중복 등록 방지용)."""
    try:
        params = {"project_id": project_id} if project_id else {}
        resp = requests.get("https://api.todoist.com/api/v1/tasks",
                            headers=TD_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tasks = data.get("results", data) if isinstance(data, dict) else data
        return {(t.get("content") or "").strip() for t in tasks}
    except Exception:
        return set()


# ── 텔레그램 ──────────────────────────────────────────────────────
def get_updates(offset: int = None) -> list:
    params = {"timeout": 0, "allowed_updates": json.dumps(["callback_query"])}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{TG}/getUpdates", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("result", [])


def answer_callback(cb_id: str, text: str) -> None:
    try:
        requests.post(f"{TG}/answerCallbackQuery",
                      json={"callback_query_id": cb_id, "text": text}, timeout=10)
    except Exception:
        pass


def send_message(chat_id, text: str) -> None:
    try:
        requests.post(f"{TG}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception:
        pass


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    updates = get_updates()
    if not updates:
        print("새 업데이트 없음")
        return

    callbacks = [u for u in updates if "callback_query" in u]
    project_id = get_project_id() if callbacks else None
    existing = get_active_contents(project_id) if callbacks else set()  # Todoist 기존 미완료
    seen = set()  # 이번 실행 내 중복 방지

    processed = 0
    for u in callbacks:
        cq = u["callback_query"]
        data = cq.get("data", "")
        cb_id = cq["id"]
        chat_id = cq["message"]["chat"]["id"]

        if not data.startswith("td|"):
            answer_callback(cb_id, "")
            continue

        content = data[3:].strip()
        if not content:
            answer_callback(cb_id, "내용 없음")
            continue

        # 중복 방지: 이번 실행에서 이미 처리했거나, Todoist에 같은 미완료 할일이 있으면 스킵
        if content in seen or content in existing:
            answer_callback(cb_id, "이미 등록된 할일이에요")
            continue

        try:
            add_task(content, project_id)
            seen.add(content)
            answer_callback(cb_id, "✅ Todoist에 등록됨")
            send_message(chat_id, f"✅ <b>등록됨</b> — {content}")
            processed += 1
        except Exception as e:
            answer_callback(cb_id, "❌ 등록 실패")
            send_message(chat_id, f"⚠️ 등록 실패 — {content}\n<code>{e}</code>")

    # offset confirm: 처리분을 텔레그램에서 비움 (다음 실행은 새 것만)
    last_id = updates[-1]["update_id"]
    try:
        get_updates(offset=last_id + 1)
    except Exception:
        pass

    print(f"✅ 콜백 {processed}건 등록 완료 (수신 update {len(updates)}건)")


if __name__ == "__main__":
    main()
