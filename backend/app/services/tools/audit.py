from __future__ import annotations

from sqlmodel import Session

from ..usage.audit import record_api_call


def _record_tool_call(
    session: Session,
    purpose: str,
    provider: str,
    model: str,
    status: str,
    error: str = "",
    *,
    latency_ms: int = 0,
) -> None:
    record_api_call(
        session,
        purpose=purpose,
        provider=provider,
        model=model,
        status=status,
        error=error,
        latency_ms=latency_ms,
    )
