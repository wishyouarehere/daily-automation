"""
exec-office shadow emitter v0.2.0.

Fail-open bridge: EXEC_GATE defaults to off. When off, events are appended
to a JSONL spool file (EXEC_SPOOL_DIR/events.jsonl) without blocking or
raising. When on, events are forwarded to the exec-office adapter (placeholder
— also spools until a real adapter is wired). Existing Telegram sends are
never touched.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__version__ = "0.2.0"
_FINGERPRINT = f"exec_emitter/{__version__}"

_DEFAULT_SPOOL = "/tmp/exec_spool"


def _gate_on() -> bool:
    return os.getenv("EXEC_GATE", "0").strip() not in ("", "0", "false", "FALSE")


def _spool_dir() -> Path:
    return Path(os.getenv("EXEC_SPOOL_DIR", _DEFAULT_SPOOL))


def emit(event) -> None:
    """Emit an ExecEvent to the shadow channel. Never raises."""
    try:
        _emit_inner(event)
    except Exception as exc:
        print(f"[exec_emitter] WARN: {type(exc).__name__}: {exc}", file=sys.stderr)


def _emit_inner(event) -> None:
    row = event.to_dict()
    row["_emitter"] = _FINGERPRINT
    if _gate_on():
        _dispatch(row)
    else:
        _spool(row)


def _spool(row: dict) -> None:
    d = _spool_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _dispatch(row: dict) -> None:
    # Placeholder: real exec-office adapter plugged in here.
    # Falls back to spool until adapter is wired.
    _spool(row)
