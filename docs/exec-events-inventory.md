# exec-events-inventory — daily-automation proactive Telegram senders

Last updated: 2026-07-24
Scope: proactive (scheduled/automated) sends only — interactive bot responses excluded.

## Methodology

| Term | Definition |
|---|---|
| Proactive sender | Script that initiates a Telegram send on a schedule, without a user trigger |
| Interactive response | Reply to a user command (excluded) |
| Exec event | Privacy-minimized shadow event emitted alongside the Telegram send |
| Emitter | `exec_emitter.emit_event()` — canonical keyword API, always fail-open |
| Spool | `~/.exec-office/spool/pending/` — atomic JSON files (tmp→rename) |

---

## Inventory

### 1. morning_brief.py — Morning brief (06:30 KST daily)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.morning_brief.sent` |
| Domain | `ops` |
| Decision level | `L1` (routine completion) |
| Metadata keys | `mode` (standard/monday/friday/weekend), `decision_count` (int) |
| Decision event type | `daily_automation.decision.pending` |
| Decision domain | `org` |
| Decision level | `L2` — emitted only when `decision_count > 0` |
| Telegram channel | `TELEGRAM_CHAT_ID` |
| Error sends | `send_error()` — excluded (low-level diagnostic noise) |

### 2. daily_review.py — Day-end review (19:00 KST Mon–Fri)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.daily_review.sent` |
| Domain | `ops` |
| Decision level | `L1` (routine completion) |
| Metadata keys | `weekday` (Korean weekday char) |
| Telegram channel | `TELEGRAM_CHAT_ID` |

### 3. weekly_retro.py — Weekly retro draft (Friday, weekly)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.weekly_retro.draft_saved` |
| Domain | `ops` |
| Decision level | `L1` (routine completion) |
| Metadata keys | `week_n` (int), `slack_included` (bool), `patched_sections` (comma-joined str) |
| Telegram channel | `TELEGRAM_CHAT_ID` |
| Error sends | `fail()` — excluded (error path) |

### 4. evening_sync.py — Todoist→Obsidian sync (22:00 KST daily)

| Attribute | Value |
|---|---|
| Error event type | `daily_automation.evening_sync.error` |
| Error domain | `ops` |
| Error severity | `warning` |
| Error decision level | `L1` |
| Error metadata keys | `error_type` (string — fixed label only, no traceback) |
| Success event type | `daily_automation.evening_sync.done` |
| Success domain | `ops` |
| Success decision level | `L0` (informational; success sends no Telegram) |
| Success metadata keys | `task_count` (int) |
| Note | Success is Telegram-silent — exec event added for observability only |

### 5. regenerate_index.py — _INDEX nightly regen (22:10 KST daily)

| Attribute | Value |
|---|---|
| Exec event type | `daily_automation.index_regen.complete` |
| Domain | `ops` |
| Decision level | `L0` (informational) |
| Metadata keys | `chars` (int — character count of regenerated file) |
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

## Privacy constraints (enforced in tests)

Exec event metadata must NOT contain:
- Telegram tokens or chat IDs
- API keys or secrets
- LLM prompts or outputs
- Calendar event titles or task contents
- Tracebacks or full error messages
- Vault file paths

---

## Emitter behavior

The canonical `emit_event()` (exec_emitter.py) is always fail-open and never blocks existing Telegram sends:

| Condition | Behavior |
|---|---|
| Normal | Atomic JSON written to `~/.exec-office/spool/pending/` |
| Spool full (L0) | Event dropped, fallback-logged; Telegram send unaffected |
| Any error | Returns `False`, logs to `emitter_fallback.log`; never raises |
| `home=` parameter | Override spool root (used in tests via `tmp_path`) |

Spool home can be overridden via `EXEC_OFFICE_HOME` env var for non-default installations.

---

## EXEC_GATE — proactive delivery gate

`exec_gate_suppresses(emitted: bool)` in `exec_events.py` controls whether a proactive Telegram send
is suppressed after the exec event is emitted (shadow mode).

| `EXEC_GATE` value | `emitted` | `exec_gate_suppresses()` | Direct Telegram send |
|---|---|---|---|
| absent | True | False | proceeds |
| `off` | True | False | proceeds |
| invalid (any non-`on`) | True | False | proceeds |
| `on` (case-insensitive, stripped) | True | True | suppressed |
| `on` | False (emitter failure) | False | proceeds (fail-open) |

**Gated senders** (emit before send, gate applied):
- `morning_brief.py` → `send_telegram(message)`
- `daily_review.py` → `send_telegram(message)`
- `weekly_retro.py` → `send_telegram(...)`
- `evening_sync.py` error path → `send_telegram(...)` on Todoist fetch failure

**Not gated** (exec event only, no direct delivery to suppress):
- `regenerate_index.py` — `INDEX_REGEN_COMPLETE` is an observability event; regen diff notification is not gated
- `evening_sync.py` success path — `EVENING_SYNC_DONE` is Telegram-silent; no send to gate
- All interactive bot replies in `idea_bot.py` — user-triggered, not proactive
