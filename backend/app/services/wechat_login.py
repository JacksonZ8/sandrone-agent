"""微信扫码登录：取二维码、轮询确认，确认后把凭据写进账号并重启运行时。"""

from __future__ import annotations

import base64
import io
import time
from typing import Any

from sqlmodel import Session

from ..database import engine
from ..models import utcnow
from . import wechat_service
from .wechat_ilink import EP_GET_BOT_QR, EP_GET_QR_STATUS, ILINK_BASE_URL, _api_get_json

_QR_STATES: dict[str, dict[str, Any]] = {}


async def start_wechat_login() -> dict[str, Any]:
    base_url = ILINK_BASE_URL
    try:
        response = await _api_get_json(f"{base_url}/{EP_GET_BOT_QR}?bot_type=3")
    except Exception as exc:
        return {
            "ok": False,
            "qrcode": "",
            "qr_url": "",
            "qr_data_url": "",
            "message": f"获取二维码失败：{exc}",
        }

    qrcode_value = str(response.get("qrcode") or "")
    qr_url = str(response.get("qrcode_img_content") or qrcode_value)
    if not qrcode_value:
        return {"ok": False, "qrcode": "", "qr_url": "", "qr_data_url": "", "message": "二维码响应为空。"}

    qr_data_url = ""
    try:
        import qrcode as qrcode_lib

        image = qrcode_lib.make(qr_url or qrcode_value)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        qr_data_url = ""

    _QR_STATES[qrcode_value] = {
        "base_url": base_url,
        "created_at": time.time(),
        "refresh_count": 0,
    }
    return {
        "ok": True,
        "qrcode": qrcode_value,
        "qr_url": qr_url,
        "qr_data_url": qr_data_url,
        "message": "请使用微信扫码，并在手机端确认。",
    }


async def poll_wechat_login(qrcode: str) -> dict[str, Any]:
    state = _QR_STATES.get(qrcode)
    if state is None:
        return {"status": "expired", "message": "二维码状态不存在，请重新发起登录。", "account_id": "", "base_url": ""}

    base_url = str(state.get("base_url") or ILINK_BASE_URL)
    try:
        response = await _api_get_json(f"{base_url}/{EP_GET_QR_STATUS}?qrcode={qrcode}")
    except Exception as exc:
        return {"status": "wait", "message": f"查询失败，继续等待：{exc}", "account_id": "", "base_url": ""}

    status = str(response.get("status") or "wait")
    if status == "wait":
        return {"status": "wait", "message": "等待扫码。", "account_id": "", "base_url": ""}
    if status == "scaned":
        return {"status": "scaned", "message": "已扫码，请在微信中确认。", "account_id": "", "base_url": ""}
    if status == "scaned_but_redirect":
        redirect_host = str(response.get("redirect_host") or "")
        if redirect_host:
            state["base_url"] = f"https://{redirect_host}"
        return {"status": "scaned", "message": "已扫码，正在跳转。", "account_id": "", "base_url": ""}
    if status == "expired":
        return {"status": "expired", "message": "二维码已过期，请重新发起登录。", "account_id": "", "base_url": ""}
    if status == "confirmed":
        account_id = str(response.get("ilink_bot_id") or "")
        token = str(response.get("bot_token") or "")
        confirmed_base_url = str(response.get("baseurl") or base_url)
        user_id = str(response.get("ilink_user_id") or "")
        if not account_id or not token:
            return {"status": "error", "message": "登录确认成功，但凭据不完整。", "account_id": "", "base_url": ""}

        with Session(engine) as session:
            account = wechat_service.get_wechat_account(session)
            account.enabled = True
            account.account_id = account_id
            account.token = token
            account.base_url = confirmed_base_url
            account.user_id = user_id
            account.status = "connecting"
            account.last_error = ""
            account.updated_at = utcnow()
            session.add(account)
            session.commit()
        _QR_STATES.pop(qrcode, None)
        if wechat_service._runtime is not None:
            await wechat_service._runtime.restart()
        return {
            "status": "confirmed",
            "message": "微信登录成功，正在连接。",
            "account_id": account_id,
            "base_url": confirmed_base_url,
        }

    return {"status": status, "message": f"未知状态：{status}", "account_id": "", "base_url": ""}
