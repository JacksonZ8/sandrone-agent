from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from sqlmodel import Session, select

from ...models import ApiCall
from .audit import FAILURE_STATUSES, SUCCESS_STATUSES, normalize_status, sanitize_error

LLM_PURPOSES = {"chat", "test", "memory_extraction", "memory_consolidation", "diary", "proactive"}


def _safe_identity(call: ApiCall) -> tuple[str, str]:
    if call.purpose in LLM_PURPOSES:
        return call.provider or "unknown", call.model or "unknown"
    if call.purpose == "file_read":
        return "local", "filesystem"
    if call.purpose == "web_search":
        return call.provider or "search", "search"
    if call.purpose == "screen_analysis":
        return "vision", "screen"
    if call.purpose == "delegate_agent":
        provider = call.provider if call.provider in {"claude", "codex"} else "local-agent"
        return provider, "agent"
    if call.purpose == "tts":
        return "local-gpt-sovits", "voice"
    return "other", ""


def _tokens(call: ApiCall) -> tuple[int, int, int]:
    prompt = max(0, int(call.prompt_tokens or 0))
    completion = max(0, int(call.completion_tokens or 0))
    total = max(0, int(call.total_tokens or 0)) or (prompt + completion)
    return prompt, completion, total


def _metrics(rows: Iterable[ApiCall]) -> dict[str, Any]:
    calls = list(rows)
    success = 0
    failed = 0
    other = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    latencies: list[int] = []

    for call in calls:
        status = (call.status or "").strip().lower()
        if status in SUCCESS_STATUSES:
            success += 1
        elif status in FAILURE_STATUSES:
            failed += 1
        else:
            other += 1

        prompt, completion, total = _tokens(call)
        prompt_tokens += prompt
        completion_tokens += completion
        total_tokens += total
        if int(call.latency_ms or 0) > 0:
            latencies.append(int(call.latency_ms))

    count = len(calls)
    return {
        "calls": count,
        "success_calls": success,
        "failed_calls": failed,
        "other_calls": other,
        "success_rate": round(success / count, 4) if count else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
    }


def _time_rows(
    session: Session,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[ApiCall]:
    statement = select(ApiCall)
    if start_at is not None:
        statement = statement.where(ApiCall.created_at >= start_at)
    if end_at is not None:
        statement = statement.where(ApiCall.created_at < end_at)
    return list(session.exec(statement).all())


def get_usage_summary(
    session: Session,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    provider: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    range_rows = _time_rows(session, start_at, end_at)
    available_providers = sorted({_safe_identity(row)[0] for row in range_rows if _safe_identity(row)[0]})
    available_purposes = sorted({row.purpose for row in range_rows if row.purpose})

    rows = [
        row
        for row in range_rows
        if (not provider or _safe_identity(row)[0] == provider)
        and (not purpose or row.purpose == purpose)
    ]
    purpose_groups: dict[str, list[ApiCall]] = defaultdict(list)
    model_groups: dict[tuple[str, str], list[ApiCall]] = defaultdict(list)
    for row in rows:
        purpose_groups[row.purpose].append(row)
        if row.purpose in LLM_PURPOSES:
            model_groups[(row.provider, row.model)].append(row)

    by_purpose = [
        {"purpose": name, **_metrics(group)}
        for name, group in purpose_groups.items()
    ]
    by_purpose.sort(key=lambda item: (-item["calls"], item["purpose"]))

    by_model = [
        {"provider": key[0], "model": key[1], **_metrics(group)}
        for key, group in model_groups.items()
    ]
    by_model.sort(key=lambda item: (-item["calls"], item["provider"], item["model"]))

    error_rows = [row for row in rows if normalize_status(row.status) in FAILURE_STATUSES]
    error_rows.sort(key=lambda row: row.created_at, reverse=True)
    recent_errors = []
    for row in error_rows[:20]:
        safe_provider, safe_model = _safe_identity(row)
        recent_errors.append(
            {
                "created_at": row.created_at,
                "purpose": row.purpose,
                "provider": safe_provider,
                "model": safe_model,
                "status": normalize_status(row.status),
                "latency_ms": int(row.latency_ms) if int(row.latency_ms or 0) > 0 else None,
                "error": sanitize_error(row.error),
            }
        )

    return {
        "range": {"start_at": start_at, "end_at": end_at},
        "filters": {"provider": provider, "purpose": purpose},
        "totals": _metrics(rows),
        "by_purpose": by_purpose,
        "by_model": by_model,
        "recent_errors": recent_errors,
        "available_providers": available_providers,
        "available_purposes": available_purposes,
    }
