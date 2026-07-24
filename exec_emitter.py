"""
공개 소비자 API = emit_event() 함수 하나.
gateway/receiver 내부 정규화(contracts.validate_event)와 혼동 금지 —
이 파일은 패키지 밖 단독 복사(vendor) 사용 가능, 표준 라이브러리만 사용.
버전 EMITTER_VERSION = "0.1.0"

DO NOT EDIT in consumer repos — 수정은 exec-office 원본에서, parity 테스트 통과 후 재배포
"""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

EMITTER_VERSION = "0.2.0"
SCHEMA_VERSION = 1

_KST = timezone(timedelta(hours=9))

VALID_DOMAINS = {"org", "content", "finance", "wellness", "ops"}
VALID_SEVERITIES = {"info", "notice", "warning", "critical"}
VALID_DECISION_LEVELS = {"L0", "L1", "L2", "L3"}
VALID_OWNER_AGENTS = {"org", "content", "finance", "wellness", "ops", "chief"}

MAX_TITLE = 200
MAX_SUMMARY = 2000
MAX_RECOMMENDED_ACTION = 500
MAX_DETAIL_REF = 500
MAX_ALTERNATIVES = 5
MAX_ALTERNATIVE_LEN = 200
MAX_METADATA_KEYS = 20
MAX_METADATA_BYTES = 8 * 1024
MAX_EVENT_BYTES = 32 * 1024

# These can be overridden via env for testing
_DEFAULT_SPOOL_MAX_PENDING = 5000
_DEFAULT_SPOOL_HARD_CAP = 10000
_DEFAULT_SPOOL_MAX_BYTES = 5 * 1024 * 1024  # 5MB

# Masking patterns — identical rules to contracts.scrub
_PATTERNS = [
    (re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b'), '[MASKED_TG_TOKEN]'),
    (re.compile(r'(api\.telegram\.org/bot)\d{6,12}:[A-Za-z0-9_-]{30,}(/|$)'), r'\1[MASKED_TG_TOKEN]\2'),
    (re.compile(r'sk-[A-Za-z0-9_-]{20,}'), '[MASKED_KEY]'),
    (re.compile(r'xox[a-z]-[A-Za-z0-9-]{10,}'), '[MASKED_KEY]'),
    (re.compile(r'ghp_[A-Za-z0-9]{20,}'), '[MASKED_KEY]'),
    (re.compile(r'AKIA[A-Z0-9]{16}'), '[MASKED_KEY]'),
    (re.compile(r'(?i)(token|key|secret|password|authorization)=\S+'), r'\1=[MASKED]'),
]


def scrub(text: str) -> str:
    """민감 정보 마스킹."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\d+', '#', t)
    return t


def _make_dedupe_key(source: str, event_type: str, title: str) -> str:
    normalized = _normalize_title(title)
    raw = f"{source}|{event_type}|{normalized}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _now_kst() -> str:
    return datetime.now(_KST).isoformat()


def _parse_occurred_at(dt_str) -> str:
    """파싱 실패 시 현재 KST로 조용히 대체 (emitter 의도된 동작 — 경고 없음; contracts는 warnings 리스트에 기록)."""
    if not dt_str:
        return _now_kst()
    try:
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KST)
        return dt.isoformat()
    except (ValueError, TypeError):
        return _now_kst()  # 조용히 현재 KST로 대체


def _parse_deadline(dt_str):
    """파싱 실패 시 None 반환 (emitter 의도된 동작 — 경고 없음; contracts는 warnings 리스트에 기록)."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KST)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None  # 조용히 None으로 대체


def _scrub_event(event: dict) -> dict:
    """Apply scrub to all text fields."""
    e = dict(event)
    for field in ('title', 'summary', 'recommended_action', 'detail_ref'):
        if isinstance(e.get(field), str):
            e[field] = scrub(e[field])
    if isinstance(e.get('alternatives'), list):
        e['alternatives'] = [scrub(a) if isinstance(a, str) else a for a in e['alternatives']]
    if isinstance(e.get('metadata'), dict):
        meta = {}
        for k, v in e['metadata'].items():
            clean_key = scrub(str(k)[:64])  # key: str변환 + 64자 절단 + scrub
            meta[clean_key] = scrub(v) if isinstance(v, str) else v
        e['metadata'] = meta
    return e


def _get_home(home=None) -> Path:
    if home:
        return Path(home).expanduser()
    env = os.environ.get('EXEC_OFFICE_HOME', '')
    if env:
        return Path(env).expanduser()
    return Path.home() / '.exec-office'


def _get_spool_limits():
    max_pending = int(os.environ.get('EXEC_SPOOL_MAX_PENDING', _DEFAULT_SPOOL_MAX_PENDING))
    hard_cap = int(os.environ.get('EXEC_SPOOL_HARD_CAP', _DEFAULT_SPOOL_HARD_CAP))
    max_bytes = int(os.environ.get('EXEC_SPOOL_MAX_BYTES', _DEFAULT_SPOOL_MAX_BYTES))
    return max_pending, hard_cap, max_bytes


def _prepare_dirs(home: Path) -> dict:
    dirs = {
        'tmp': home / 'spool' / 'tmp',
        'pending': home / 'spool' / 'pending',
        'logs': home / 'logs',
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _check_capacity(pending_dir: Path, decision_level: str) -> tuple:
    """Returns (ok: bool, count: int, total_bytes: int)."""
    max_pending, hard_cap, max_bytes = _get_spool_limits()
    files = list(pending_dir.glob('*.json'))
    count = len(files)
    total = sum(f.stat().st_size for f in files if f.exists())

    if count > max_pending or total > max_bytes:
        if decision_level == 'L0':
            return False, count, total
        if count >= hard_cap:
            return False, count, total
    return True, count, total


_DEFAULT_FALLBACK_LOG_MAX = 1_048_576  # 1MB


def _rotate_fallback_log(log_file: Path):
    """1MB 초과 시 .1로 교체 후 새 파일."""
    max_bytes = int(os.environ.get('EXEC_FALLBACK_LOG_MAX', _DEFAULT_FALLBACK_LOG_MAX))
    try:
        if log_file.exists() and log_file.stat().st_size > max_bytes:
            rotated = log_file.with_suffix('.log.1')
            try:
                log_file.rename(rotated)
            except OSError:
                pass
    except OSError:
        pass


def _fallback_log(logs_dir: Path, msg: str):
    try:
        log_file = logs_dir / 'emitter_fallback.log'
        _rotate_fallback_log(log_file)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"{_now_kst()} {msg}\n")
    except Exception:
        pass


def emit_event(event=None, *, fail_open=True, home=None, **fields) -> bool:
    """
    이벤트를 spool/pending에 원자적으로 기록.

    호출 방식:
      - keyword 방식 (권장): emit_event(source=..., domain=..., ...)
      - dict 방식 (하위 호환): emit_event({...})
      - 혼용 금지: dict와 keyword 동시 사용 시 오류.

    fail_open=True: 모든 예외를 억제하고 False 반환.
    fail_open=False: 예외 전파.
    """
    # Resolve event dict from arguments
    if event is not None and not isinstance(event, dict):
        # non-dict positional arg
        err = ValueError("event는 dict여야 합니다")
        if fail_open:
            try:
                h = _get_home(home)
                logs_dir = h / 'logs'
                logs_dir.mkdir(parents=True, exist_ok=True)
                _fallback_log(logs_dir, f"emit_event 호출 오류: {err!r}")
            except Exception:
                pass
            return False
        raise err

    if event is not None and fields:
        # Both dict and keyword fields — conflict
        err = ValueError("event dict와 keyword 필드를 동시에 쓸 수 없습니다")
        if fail_open:
            try:
                h = _get_home(home)
                logs_dir = h / 'logs'
                logs_dir.mkdir(parents=True, exist_ok=True)
                _fallback_log(logs_dir, f"emit_event 호출 오류: {err!r}")
            except Exception:
                pass
            return False
        raise err

    if event is None and fields:
        # keyword-only style
        resolved = fields
    elif event is not None:
        # dict style
        resolved = event
    else:
        # emit_event() with no args — will fail validation below
        resolved = {}

    try:
        return _emit_inner(resolved, home)
    except Exception as exc:
        if fail_open:
            try:
                h = _get_home(home)
                logs_dir = h / 'logs'
                logs_dir.mkdir(parents=True, exist_ok=True)
                _fallback_log(logs_dir, f"emit_event 실패: {exc!r}")
            except Exception:
                pass
            return False
        raise


def _emit_inner(event: dict, home=None) -> bool:
    # 1. Minimal validation + defaults
    required = ('source', 'domain', 'event_type', 'title')
    for f in required:
        if not event.get(f):
            raise ValueError(f"필수 필드 누락: {f}")

    domain = event.get('domain', '')
    if domain not in VALID_DOMAINS:
        raise ValueError(f"domain 값 오류: {domain!r}")

    severity = event.get('severity', 'info')
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity 값 오류: {severity!r}")

    dl = event.get('decision_level', 'L0')
    if dl not in VALID_DECISION_LEVELS:
        raise ValueError(f"decision_level 값 오류: {dl!r}")

    # Build normalized event
    e = {}
    e['event_id'] = event.get('event_id') or uuid.uuid4().hex
    e['schema_version'] = SCHEMA_VERSION

    e['occurred_at'] = _parse_occurred_at(event.get('occurred_at'))

    e['source'] = str(event['source'])
    e['host'] = event.get('host') or os.environ.get('EXEC_OFFICE_HOST', 'unknown')
    e['domain'] = domain
    e['event_type'] = str(event['event_type'])
    e['severity'] = severity
    e['decision_level'] = dl

    title = str(event['title'])
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE]

    summary = str(event.get('summary', ''))
    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY]

    ra = str(event.get('recommended_action', ''))
    if len(ra) > MAX_RECOMMENDED_ACTION:
        ra = ra[:MAX_RECOMMENDED_ACTION]

    dr = str(event.get('detail_ref', ''))
    if '?' in dr:
        dr = dr[:dr.index('?')]  # query string 금지 — 토큰 유입 경로 차단
    if len(dr) > MAX_DETAIL_REF:
        dr = dr[:MAX_DETAIL_REF]

    meta = event.get('metadata', {})
    if not isinstance(meta, dict):
        meta = {}
    clean_meta = {}
    for k, v in list(meta.items())[:MAX_METADATA_KEYS]:
        if isinstance(v, (str, int, float, bool, type(None))):
            clean_meta[k] = v

    # metadata 8KB check
    meta_bytes = len(json.dumps(clean_meta).encode('utf-8'))
    if meta_bytes > MAX_METADATA_BYTES:
        clean_meta = {}  # 8KB 초과 시 비움 (contracts는 warning 추가 — emitter는 의도적 생략)

    # Scrub title before dedupe_key computation (mirrors contracts.validate_event ordering)
    title = scrub(title)

    # Assign fields
    e['title'] = title
    e['summary'] = summary
    e['recommended_action'] = ra
    e['detail_ref'] = dr
    e['metadata'] = clean_meta

    if event.get('dedupe_key'):
        e['dedupe_key'] = scrub(str(event['dedupe_key'])[:64])
    else:
        e['dedupe_key'] = _make_dedupe_key(e['source'], e['event_type'], e['title'])

    e['alternatives'] = [str(a)[:MAX_ALTERNATIVE_LEN] for a in (event.get('alternatives') or [])[:MAX_ALTERNATIVES]]
    e['deadline'] = _parse_deadline(event.get('deadline'))
    e['action_required'] = bool(event.get('action_required', False))
    e['reversible'] = bool(event.get('reversible', True))
    e['auto_recovery_allowed'] = bool(event.get('auto_recovery_allowed', False))

    try:
        cs = int(event.get('cooldown_seconds', 0))
    except (TypeError, ValueError):
        cs = 0
    if cs < 0:
        cs = 0
    if cs > 2592000:
        cs = 2592000
    e['cooldown_seconds'] = cs

    owner = event.get('owner_agent', domain)
    if owner not in VALID_OWNER_AGENTS:
        owner = domain
    e['owner_agent'] = owner

    # 2. Scrub — must happen after all fields set
    e = _scrub_event(e)

    # 3. Size trim
    serialized = json.dumps(e).encode('utf-8')
    if len(serialized) > MAX_EVENT_BYTES:
        e['summary'] = e['summary'][:500]
        serialized = json.dumps(e).encode('utf-8')
        if len(serialized) > MAX_EVENT_BYTES:
            e['metadata'] = {}

    # 4. Prepare dirs
    h = _get_home(home)
    dirs = _prepare_dirs(h)

    # 5. Capacity gate
    ok, count, total = _check_capacity(dirs['pending'], dl)
    if not ok:
        _fallback_log(dirs['logs'], f"DROP level={dl} reason=spool_capacity count={count} bytes={total} event_id={e['event_id']}")
        return False

    # 6. Atomic write: tmp -> pending
    # event_id는 직렬화 이후 절대 변경하지 않는다.
    # 파일명 충돌 시 파일명에만 suffix를 붙인다.
    # 중복 event_id 파일이 spool에 여러 개 남는 것은 허용 —
    # 중복 제거는 수신측 event_id 멱등 처리 책임.
    payload = json.dumps(e, ensure_ascii=False, indent=None).encode('utf-8')

    # Compact occurred_at for filename (safe chars)
    occurred_compact = e['occurred_at'].replace(':', '').replace('+', '').replace('-', '').replace('T', 'T')[:17]

    base_name = f"{occurred_compact}_{e['event_id']}"
    final_path = dirs['pending'] / f"{base_name}.json"

    if final_path.exists():
        # 멱등 재시도(같은 event_id 재전송)는 정상 동작 — event_id는 그대로 두고
        # 파일명에만 suffix를 붙여 충돌을 피한다.
        suffix = None
        for _ in range(5):
            suffix = uuid.uuid4().hex[:6]
            candidate = dirs['pending'] / f"{base_name}_{suffix}.json"
            if not candidate.exists():
                final_path = candidate
                break
        else:
            raise RuntimeError(f"파일명 충돌 해소 실패 (5회 시도): {base_name}")

    tmp_name = f".{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = dirs['tmp'] / tmp_name

    try:
        with open(tmp_path, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.rename(str(tmp_path), str(final_path))
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise

    return True
