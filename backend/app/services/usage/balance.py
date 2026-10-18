"""DeepSeek 账户余额（桌宠连击时显示）：由后端用已保存的 Key 查询，Key 不离开后端。

原来桌宠直接读数据库里的 Key；Key 改为加密存放后，统一由这里查询。
规则与桌宠原实现一致：只对官方 api.deepseek.com 发起请求，不跟随重定向，只认 CNY/USD。
"""

from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlmodel import Session

from ..llm_service import record_api_call
from ..provider_service import get_config
from .audit import sanitize_error

BALANCE_URL = "https://api.deepseek.com/user/balance"
TIMEOUT_SECONDS = 2.8
SYMBOLS = {"CNY": "¥", "USD": "US$"}
_NUMBER = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")

NOT_CONFIGURED = "请在 Agent 配置 DeepSeek"
AUTH_FAILED = "密钥验证失败"
UNREACHABLE = "暂时无法查询余额"
UNAVAILABLE = "余额暂不可用"


def official_origin(value: str) -> bool:
    url = urlparse(value or "")
    return (
        url.scheme == "https"
        and (url.hostname or "").lower() == "api.deepseek.com"
        and url.port in (None, 443)
        and not url.username
        and not url.password
    )


def format_balance(payload: dict[str, Any]) -> str | None:
    """['¥12.50', 'US$1.00'] 用 ' · ' 连接；有任何一项无法识别就返回 None。"""
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        return None
    parts = []
    for info in infos:
        currency = str((info or {}).get("currency") or "")
        total = str((info or {}).get("total_balance") or "")
        if currency not in SYMBOLS or not _NUMBER.match(total):
            return None
        try:
            value = Decimal(total)
        except InvalidOperation:
            return None
        text = f"{value:f}"
        whole, _, fraction = text.partition(".")
        fraction = fraction.rstrip("0").ljust(2, "0")[:8]
        parts.append(f"{SYMBOLS[currency]}{whole}.{fraction}")
    return " · ".join(parts)


def fetch_balance(session: Session, *, client: httpx.Client | None = None) -> dict[str, Any]:
    config = get_config(session)
    key = (config.api_key or "").strip()
    if config.provider != "deepseek" or not official_origin(config.base_url) or not key or "\n" in key or "\r" in key:
        return {"ok": False, "text": NOT_CONFIGURED}
    started = time.perf_counter()
    status, error, text, ok = "error", "", UNAVAILABLE, False
    try:
        owned = client is None
        client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
        try:
            response = client.get(BALANCE_URL, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        finally:
            if owned:
                client.close()
        if response.status_code == 401:
            text, error = AUTH_FAILED, "401"
        elif response.status_code == 200:
            display = format_balance(response.json())
            if display:
                text, ok, status = display, True, "ok"
            else:
                error = "unrecognized balance payload"
        else:
            error = f"HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        text, error = UNREACHABLE, sanitize_error(str(exc) or type(exc).__name__, limit=200)
    record_api_call(
        session,
        purpose="deepseek_balance",
        provider="deepseek",
        model="",
        latency_ms=int((time.perf_counter() - started) * 1000),
        status=status,
        error=error,
    )
    return {"ok": ok, "text": text}
