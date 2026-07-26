"""
exec-office event type constants and structured event class.

Privacy rule: payloads must never contain tokens, chat IDs, prompts,
tracebacks, or calendar/task/document contents.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# ── Urgency levels ──────────────────────────────────────────────────
L0 = 0  # informational, no action
L1 = 1  # routine completion
L2 = 2  # actionable decision required
# L3 = 3  # reserved: imminent deadline + immediate action only; must not be set statically

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


@dataclass
class ExecEvent:
    event_type: str
    level: int
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
