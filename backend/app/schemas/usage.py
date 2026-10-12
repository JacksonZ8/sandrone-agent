"""API 用量汇总的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UsageMetrics(BaseModel):
    calls: int
    success_calls: int
    failed_calls: int
    other_calls: int
    success_rate: float | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    average_latency_ms: int | None
    max_latency_ms: int | None


class UsagePurposeBreakdown(UsageMetrics):
    purpose: str


class UsageModelBreakdown(UsageMetrics):
    provider: str
    model: str


class UsageErrorPublic(BaseModel):
    created_at: datetime
    purpose: str
    provider: str
    model: str
    status: str
    latency_ms: int | None
    error: str


class UsageSummaryResponse(BaseModel):
    range: dict[str, datetime | None]
    filters: dict[str, str]
    totals: UsageMetrics
    by_purpose: list[UsagePurposeBreakdown]
    by_model: list[UsageModelBreakdown]
    recent_errors: list[UsageErrorPublic]
    available_providers: list[str]
    available_purposes: list[str]
