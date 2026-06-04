#!/usr/bin/env python3
"""sleepy_errlog — drop-in, dependency-free API-error / rate-limit logger.

Copy this single file into any app (Dev/<app>/sleepy_errlog.py) or import it in-repo.
It appends ONE JSON object per line to a central, LOCAL-ONLY, gitignored log so the
agent can rank recurring failures across every app and we kill them at the source.

Privacy law (references/privacy-golden-standard.md): local file ONLY. No telemetry,
no off-device shipping, no third-party processor. We log the error *shape* — type,
status, host+path, short message — NEVER payloads, tokens, full query strings, or PII.

Usage (the 3 surfaces):
    import sleepy_errlog as errlog

    # 1. explicit
    errlog.log_error("wealth-engine", "rate_limit", "RATE_LIMIT_EXCEEDED",
                     status_code=429, source="plaid",
                     endpoint="https://api.plaid.com/transactions/sync?token=...",
                     retry_after=60)

    # 2. context manager — auto-classifies + re-raises
    with errlog.guard("wealth-engine", source="plaid", endpoint=url):
        resp = requests.post(url, ...); resp.raise_for_status()

    # 3. decorator
    @errlog.track("mail-warden", source="gmail")
    def sync(): ...

Path resolution (in order):
    1. $SLEEPY_ERRLOG_PATH                              (explicit override)
    2. <sleepy-productions>/references/api-errors.jsonl (the hub, if reachable)
    3. ./.errlog.jsonl                                  (per-app fallback, never crash)
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

# Canonical error-type classifiers. rate_limit regex mirrors codex-delegate/engine/delegate.py:51.
_RATE_LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|quota|429|too many requests|exceeded your", re.I
)
_AUTH_RE = re.compile(r"\b401\b|\b403\b|unauthor|forbidden|invalid.?(token|api.?key|credential)|expired.?token", re.I)
_TIMEOUT_RE = re.compile(r"tim\w*out|timed out|deadline exceeded|\b408\b|\b504\b", re.I)
_NETWORK_RE = re.compile(r"connection|conn\w*reset|dns|getaddrinfo|unreachable|refused|\b502\b|\b503\b", re.I)
_SERVER_RE = re.compile(r"\b5\d\d\b|internal server error", re.I)
_NOTFOUND_RE = re.compile(r"\b404\b|not found", re.I)

# Sanitizer: scrub obvious secrets/PII from any free-text we keep.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]+|"
    r"Bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9._\-]{10,})", re.I,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MSG_MAX = 240

# Fixed default hub path (override with $SLEEPY_ERRLOG_PATH if the repo ever moves).
_DEFAULT_HUB = Path(r"C:\Users\Sleepy\Desktop\sleepy-productions") / "references" / "api-errors.jsonl"


def _log_path() -> Path:
    override = os.environ.get("SLEEPY_ERRLOG_PATH")
    if override:
        return Path(override)
    try:
        if _DEFAULT_HUB.parent.is_dir():
            return _DEFAULT_HUB
    except OSError:
        pass
    return Path.cwd() / ".errlog.jsonl"


def classify(message: str, status_code: int | None = None) -> str:
    """Map an exception/message + optional HTTP status onto a canonical error_type."""
    blob = f"{message} {status_code or ''}"
    if status_code == 429 or _RATE_LIMIT_RE.search(blob):
        return "rate_limit"
    if status_code in (401, 403) or _AUTH_RE.search(blob):
        return "auth"
    if status_code in (408, 504) or _TIMEOUT_RE.search(blob):
        return "timeout"
    if status_code == 404 or _NOTFOUND_RE.search(blob):
        return "not_found"
    if status_code in (502, 503) or _NETWORK_RE.search(blob):
        return "network"
    if (status_code and 500 <= status_code < 600) or _SERVER_RE.search(blob):
        return "server"
    return "error"


def _sanitize_msg(message: str) -> str:
    msg = str(message).replace("\n", " ").strip()
    msg = _SECRET_RE.sub("<redacted>", msg)
    msg = _EMAIL_RE.sub("<email>", msg)
    if len(msg) > _MSG_MAX:
        msg = msg[:_MSG_MAX] + "…"
    return msg


def _sanitize_endpoint(endpoint: str | None) -> str | None:
    """Keep host+path; drop scheme, query string, and any creds — those leak tokens."""
    if not endpoint:
        return None
    ep = str(endpoint).split("?", 1)[0].split("#", 1)[0]
    ep = re.sub(r"^[a-z]+://", "", ep, flags=re.I)   # strip scheme
    ep = re.sub(r"^[^/@]+@", "", ep)                  # strip user:pass@
    return _SECRET_RE.sub("<redacted>", ep.rstrip("/"))[:160]


def _status_from_exc(exc: BaseException) -> int | None:
    """Best-effort HTTP status pull from common client libs (requests/httpx/urllib)."""
    for attr in ("status_code", "code", "status"):
        for obj in (exc, getattr(exc, "response", None)):
            val = getattr(obj, attr, None)
            if isinstance(val, int) and 100 <= val < 600:
                return val
    m = re.search(r"\b([1-5]\d\d)\b", str(exc))
    return int(m.group(1)) if m else None


def log_error(
    app: str,
    error_type: str | None = None,
    message: str = "",
    *,
    status_code: int | None = None,
    source: str | None = None,
    endpoint: str | None = None,
    retry_after: int | float | None = None,
    extra: dict | None = None,
) -> None:
    """Append one sanitized error record to the central local log. Never raises."""
    try:
        rec = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "app": app,
            "source": source,
            "error_type": error_type or classify(message, status_code),
            "status_code": status_code,
            "endpoint": _sanitize_endpoint(endpoint),
            "message": _sanitize_msg(message),
        }
        if retry_after is not None:
            rec["retry_after"] = retry_after
        if extra:
            # only scalar values, sanitized — never dump payloads/objects
            for k, v in extra.items():
                if isinstance(v, (str, int, float, bool)) or v is None:
                    rec[str(k)] = _sanitize_msg(v) if isinstance(v, str) else v
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must NEVER break the host app. Swallow everything.
        pass


@contextmanager
def guard(app: str, *, source: str | None = None, endpoint: str | None = None, reraise: bool = True):
    """Catch any exception inside the block, classify + log it, then re-raise (default)."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — intentional broad catch around an API call
        status = _status_from_exc(exc)
        log_error(app, classify(str(exc), status), f"{type(exc).__name__}: {exc}",
                  status_code=status, source=source, endpoint=endpoint)
        if reraise:
            raise


def track(app: str, *, source: str | None = None, endpoint: str | None = None):
    """Decorator form of guard()."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with guard(app, source=source, endpoint=endpoint):
                return fn(*a, **kw)
        return wrapper
    return deco
