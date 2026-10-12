"""微信 iLink Bot 协议：常量、请求头与 JSON 收发（与 Hermes weixin.py 保持一致）。"""

from __future__ import annotations

import base64
import json
import os
import secrets
import struct
from typing import Any

import httpx

# 协议常量：与 Hermes weixin.py 保持一致
ILINK_BASE_URL = os.getenv("SANDRONE_WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
QR_TIMEOUT_MS = 35_000
SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _base_info() -> dict[str, Any]:
    return {"channel_version": "2.2.0"}


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _headers(token: str, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }


def _extract_text(item_list: list[dict[str, Any]]) -> str:
    for item in item_list or []:
        if item.get("type") == ITEM_TEXT:
            return str((item.get("text_item") or {}).get("text") or "").strip()
    return ""


async def _api_get_json(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=QR_TIMEOUT_MS / 1000, headers=_api_headers()) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _api_post_json(
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str,
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    body = _json_dumps({**payload, "base_info": _base_info()})
    url = f"{base_url.rstrip('/')}/{endpoint}"
    async with httpx.AsyncClient(timeout=timeout_ms / 1000, headers=_headers(token, body)) as client:
        response = await client.post(url, content=body)
        response.raise_for_status()
        return response.json()
