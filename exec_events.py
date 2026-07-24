"""
exec-office event type constants for daily-automation proactive senders.

Privacy rule: emit_event() metadata must never contain tokens, chat IDs,
prompts, tracebacks, or calendar/task/document contents.
Allowed metadata keys per sender: docs/exec-events-inventory.md.
"""
import os

# ── Domain constants ─────────────────────────────────────────────────
DOMAIN_OPS = "ops"    # operational automation senders
DOMAIN_ORG = "org"    # organizational decision events

# ── Semantic event type identifiers ─────────────────────────────────
BRIEF_SENT           = "daily_automation.morning_brief.sent"
DAILY_REVIEW_SENT    = "daily_automation.daily_review.sent"
WEEKLY_RETRO_SAVED   = "daily_automation.weekly_retro.draft_saved"
INDEX_REGEN_COMPLETE = "daily_automation.index_regen.complete"
EVENING_SYNC_ERROR   = "daily_automation.evening_sync.error"
EVENING_SYNC_DONE    = "daily_automation.evening_sync.done"
DECISION_PENDING     = "daily_automation.decision.pending"

ALL_TYPES = frozenset({
    BRIEF_SENT, DAILY_REVIEW_SENT, WEEKLY_RETRO_SAVED,
    INDEX_REGEN_COMPLETE, EVENING_SYNC_ERROR, EVENING_SYNC_DONE,
    DECISION_PENDING,
})


# ── Proactive delivery gate ──────────────────────────────────────────

def exec_gate_suppresses(emitted: bool) -> bool:
    """Returns True only when EXEC_GATE=on and emitter succeeded — suppresses direct delivery.

    Fail-open: emitter failure (emitted=False) always returns False, preserving direct delivery.
    Only exact 'on' (case-insensitive, stripped) suppresses; absent/'off'/invalid all return False.
    """
    if not emitted:
        return False
    return os.environ.get("EXEC_GATE", "").strip().lower() == "on"
