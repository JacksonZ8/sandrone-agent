from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session

from ...models import ApiCall

logger = logging.getLogger(__name__)

SUCCESS_STATUSES = {"ok", "empty", "generated"}
FAILURE_STATUSES = {"error", "timeout"}
STANDARD_STATUSES = {"ok", "empty", "error", "timeout"}

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_AUTH_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_SECRET_PARAM_RE = re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key)=)[^&\s]+")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_UNIX_USER_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")
_WINDOWS_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'<>]+(?:\\[^\s\"'<>]*)?", re.I)


def normalize_status(status: str) -> str:
    value = (status or "").strip().lower()
    if value in SUCCESS_STATUSES:
        return "ok" if value == "generated" else value
    if value in FAILURE_STATUSES:
        return value
    return "other"


def _strip_url_query(match: re.Match[str]) -> str:
    try:
        parsed = urlsplit(match.group(0))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return "<url>"


def sanitize_error(value: str, *, limit: int = 500) -> str:
    text = str(value or "")
    text = _AUTH_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _SK_RE.sub("<redacted-key>", text)
    text = _SECRET_PARAM_RE.sub(r"\1<redacted>", text)
    text = _URL_RE.sub(_strip_url_query, text)
    text = _UNIX_USER_PATH_RE.sub("<user-path>", text)
    text = _WINDOWS_USER_PATH_RE.sub("<user-path>", text)
    return text[:limit]


def record_api_call(
    session: Session,
    *,
    purpose: str,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    status: str = "ok",
    error: str = "",
) -> None:
    normalized = normalize_status(status)
    if normalized == "other":
        normalized = "error"
    try:
        session.add(
            ApiCall(
                purpose=str(purpose or "unknown")[:64],
                provider=str(provider or "")[:128],
                model=str(model or "")[:256],
                prompt_tokens=max(0, int(prompt_tokens or 0)),
                completion_tokens=max(0, int(completion_tokens or 0)),
                total_tokens=max(0, int(total_tokens or 0)),
                latency_ms=max(0, int(latency_ms or 0)),
                status=normalized,
                error=sanitize_error(error, limit=1000),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist API call audit")
