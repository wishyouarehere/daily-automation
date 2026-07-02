"""LLM 사용량 공용 원장 헬퍼 — 모든 봇이 이 파일을 복사해 쓴다 (정본: ~/wf-sync/llm_ledger.py).

호출마다 ~/llm_usage.jsonl 에 한 줄 append. 실패해도 절대 예외를 던지지 않는다(본 기능 보호).
소비자: llm_cost_watch.py(매시 임계 경보) · llm_cost_report.py(집계).

사용법:
    from llm_ledger import log_anthropic, log_image
    resp = client.messages.create(...)
    log_anthropic("chief_qa", resp)          # 모델·토큰은 resp에서 자동 추출
    log_image("sns_image", "openai", "gpt-image-1")   # 이미지 1장
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_PATH = os.environ.get("LLM_USAGE_LOG", os.path.expanduser("~/llm_usage.jsonl"))

# USD / 1M tokens (input, output). 캐시 읽기=input×0.1, 캐시 쓰기(5m)=input×1.25
_PRICES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4-0": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),   # 4-x 계열 공통 prefix
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-haiku": (0.25, 1.25),
}
# 이미지 1장당 추정 단가 USD (env로 오버라이드: LLM_IMG_COST_<PROVIDER>)
_IMG_COST = {"openai": 0.25, "gemini": 0.04, "imagen": 0.04}


def _price(model: str):
    for k, p in _PRICES.items():
        if model.startswith(k):
            return p
    return (5.0, 25.0)  # 미등록 모델은 opus급 보수 추정


def _append(row: dict) -> None:
    try:
        with open(_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # 원장 실패가 본 기능을 막으면 안 됨
        print(f"[llm_ledger] append 실패: {e}", file=sys.stderr)


def log_anthropic(job: str, resp, model: str = "") -> None:
    """Anthropic messages 응답의 usage를 원장에 기록. resp는 SDK Message 또는 REST dict."""
    try:
        if isinstance(resp, dict):
            u = resp.get("usage", {}) or {}
            model = model or resp.get("model", "?")
            i = u.get("input_tokens", 0) or 0
            o = u.get("output_tokens", 0) or 0
            cr = u.get("cache_read_input_tokens", 0) or 0
            cw = u.get("cache_creation_input_tokens", 0) or 0
        else:
            u = getattr(resp, "usage", None)
            model = model or getattr(resp, "model", "?")
            i = getattr(u, "input_tokens", 0) or 0
            o = getattr(u, "output_tokens", 0) or 0
            cr = getattr(u, "cache_read_input_tokens", 0) or 0
            cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        pin, pout = _price(str(model))
        cost = (i * pin + o * pout + cr * pin * 0.1 + cw * pin * 1.25) / 1e6
        _append({
            "ts": datetime.now(KST).isoformat(timespec="seconds"),
            "job": job, "model": str(model),
            "in": i, "out": o, "cache_read": cr, "cache_write": cw,
            "cost_usd": round(cost, 6),
        })
    except Exception as e:
        print(f"[llm_ledger] log_anthropic 실패: {e}", file=sys.stderr)


def log_image(job: str, provider: str, model: str, count: int = 1) -> None:
    """이미지 생성 비용(장당 추정 단가 × count) 기록. provider: openai|gemini|imagen"""
    try:
        unit = float(os.environ.get(
            f"LLM_IMG_COST_{provider.upper()}", _IMG_COST.get(provider, 0.1)))
        _append({
            "ts": datetime.now(KST).isoformat(timespec="seconds"),
            "job": job, "model": f"{provider}/{model}",
            "in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
            "cost_usd": round(unit * count, 6),
        })
    except Exception as e:
        print(f"[llm_ledger] log_image 실패: {e}", file=sys.stderr)
