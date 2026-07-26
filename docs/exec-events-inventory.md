# exec-events-inventory — daily-automation proactive Telegram senders

Last updated: 2026-07-24
Scope: proactive (scheduled/automated) sends only — interactive bot responses excluded.

## Methodology

| Term | Definition |
|---|---|
| Proactive sender | Script that initiates a Telegram send on a schedule, without a user trigger |
| Interactive response | Reply to a user command (excluded) |
| Exec event | Privacy-minimized shadow event emitted alongside the Telegram send |
| EXEC_GATE | env var; default `0` (off) — off spools events locally, never blocks existing sends |

---

## Inventory

### 1. morning_brief.py — Morning brief (06:30 KST daily)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.morning_brief.sent` |
| Level | L1 (routine completion) |
| Payload keys | `mode` (standard/monday/friday/weekend), `decision_count` (int) |
| Decision event type | `daily_automation.decision.pending` |
| Decision level | L2 — emitted only when `decision_count > 0` |
| Telegram channel | `TELEGRAM_CHAT_ID` |
| Error sends | `send_error()` — excluded (low-level diagnostic noise) |

### 2. daily_review.py — Day-end review (19:00 KST Mon–Fri)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.daily_review.sent` |
| Level | L1 (routine completion) |
| Payload keys | `weekday` (Korean weekday char) |
| Telegram channel | `TELEGRAM_CHAT_ID` |

### 3. weekly_retro.py — Weekly retro draft (Friday, weekly)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.weekly_retro.draft_saved` |
| Level | L1 (routine completion) |
| Payload keys | `week_n` (int), `slack_included` (bool), `patched_sections` (list) |
| Telegram channel | `TELEGRAM_CHAT_ID` |
| Error sends | `fail()` — excluded (error path) |

### 4. evening_sync.py — Todoist→Obsidian sync (22:00 KST daily)

| Attribute | Value |
|---|---|
| Error event type | `daily_automation.evening_sync.error` |
| Error level | L1 |
| Error payload keys | `error_type` (string — class name only, no traceback) |
| Success event type | `daily_automation.evening_sync.done` |
| Success level | L0 (informational; success sends no Telegram) |
| Success payload keys | `task_count` (int) |
| Note | Success is Telegram-silent — exec event added for observability only |

### 5. regenerate_index.py — _INDEX nightly regen (22:10 KST daily)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.index_regen.complete` |
| Level | L0 (informational) |
| Payload keys | `chars` (int — character count of regenerated file) |
| Telegram channel | `TELEGRAM_CHAT_ID` |
| Error sends | `fail()` — excluded (error path) |

---

## Excluded senders

| File | Reason |
|---|---|
| `idea_bot.py` | Interactive bot — responds to user commands, not proactive |
| `morning_brief.py` `send_error()` | Error/diagnostic path |
| `weekly_retro.py` `fail()` | Error path |
| `regenerate_index.py` `fail()` | Error path |

---

## Privacy constraints (enforced in exec_events.py and tests)

Exec event payloads must NOT contain:
- Telegram tokens or chat IDs
- API keys or secrets
- LLM prompts or outputs
- Calendar event titles or task contents
- Tracebacks or full error messages
- Vault file paths

---

## EXEC_GATE behavior

| Value | Behavior |
|---|---|
| `0` (default/off) | Events spooled to `EXEC_SPOOL_DIR/events.jsonl`; existing Telegram sends unchanged |
| `1` (on) | Events forwarded to exec-office adapter (placeholder → also spools until adapter wired) |
