from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import secrets
import struct
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from ..database import engine
from ..models import Message, WeChatAccount, utcnow
from .chat_service import create_chat_turn
from .memory_service import run_extraction_background

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

PAIRING_CODE_TTL_SECONDS = 600
PAIRING_MAX_FAILURES = 10
UNPAIRED_REPLY = "这个账号还没有完成绑定。请先在桑多涅的设置页生成配对码，再把配对码发给我。"
PAIRED_REPLY = "绑定完成。之后这个账号只和你对话。"
WECHAT_FAILURE_REPLY = "……我这边的核心暂时连不上，没法好好回你。原因记在网页「用量」页的最近错误里，修好再来找我。"

_QR_STATES: dict[str, dict[str, Any]] = {}
_PAIRING: dict[str, Any] = {}
_runtime: "WeChatRuntime | None" = None


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


def seed_wechat_account(session: Session) -> WeChatAccount:
    account = session.get(WeChatAccount, 1)
    if account is None:
        account = WeChatAccount(id=1)
        session.add(account)
        session.commit()
        session.refresh(account)
    return account


def get_wechat_account(session: Session) -> WeChatAccount:
    return seed_wechat_account(session)


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}••••••••{value[-keep:]}"


def wechat_status(session: Session) -> dict[str, Any]:
    account = get_wechat_account(session)
    return {
        "enabled": account.enabled,
        "name": account.name,
        "account_id": account.account_id,
        "account_id_masked": _mask(account.account_id),
        "has_token": bool(account.token),
        "token_masked": _mask(account.token),
        "base_url": account.base_url,
        "owner_user_id": account.owner_user_id,
        "owner_paired": bool(account.owner_user_id),
        "status": account.status,
        "last_error": account.last_error,
        "updated_at": account.updated_at,
    }


def apply_wechat_config(session: Session, data: dict[str, Any]) -> WeChatAccount:
    account = get_wechat_account(session)
    allowed = ("enabled", "account_id", "token", "base_url", "cdn_base_url", "owner_user_id")
    for key, value in data.items():
        if key in allowed and value is not None:
            setattr(account, key, value)
    account.updated_at = utcnow()
    if not account.token or not account.account_id:
        account.enabled = False
        account.status = "stopped"
    else:
        account.status = "connecting"
    account.last_error = ""
    session.add(account)
    session.commit()
    session.refresh(account)

    return account


def start_owner_pairing(session: Session) -> dict[str, Any]:
    """生成一次性配对码；主人用自己的微信把它发给 Bot 才能完成绑定。"""
    account = get_wechat_account(session)
    if account.owner_user_id:
        return {
            "ok": False,
            "code": "",
            "expires_in_seconds": 0,
            "message": "已经绑定了主人。如需更换，请先断开微信再重新连接。",
        }
    code = f"{secrets.randbelow(1_000_000):06d}"
    _PAIRING.clear()
    _PAIRING.update(code=code, expires_at=time.monotonic() + PAIRING_CODE_TTL_SECONDS, failures=0)
    return {
        "ok": True,
        "code": code,
        "expires_in_seconds": PAIRING_CODE_TTL_SECONDS,
        "message": "请用你自己的微信把这 6 位配对码发给 Bot，10 分钟内有效。",
    }


def _match_pairing_code(text: str) -> bool:
    code = str(_PAIRING.get("code") or "")
    if not code or time.monotonic() >= float(_PAIRING.get("expires_at") or 0):
        _PAIRING.clear()
        return False
    if secrets.compare_digest(text.strip().encode("utf-8"), code.encode("utf-8")):
        _PAIRING.clear()
        return True
    _PAIRING["failures"] = int(_PAIRING.get("failures") or 0) + 1
    if _PAIRING["failures"] >= PAIRING_MAX_FAILURES:
        _PAIRING.clear()
    return False


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
            account = get_wechat_account(session)
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
        if _runtime is not None:
            await _runtime.restart()
        return {
            "status": "confirmed",
            "message": "微信登录成功，正在连接。",
            "account_id": account_id,
            "base_url": confirmed_base_url,
        }

    return {"status": status, "message": f"未知状态：{status}", "account_id": "", "base_url": ""}


class WeChatRuntime:
    """iLink 长轮询运行时。只处理私聊文本，群聊默认忽略。"""

    def __init__(self) -> None:
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._client: httpx.AsyncClient | None = None
        self._seen: deque[str] = deque(maxlen=1000)
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._cold_replied: deque[str] = deque(maxlen=200)

    async def start(self) -> bool:
        with Session(engine) as session:
            account = get_wechat_account(session)
            if not account.enabled or not account.account_id or not account.token:
                account.status = "stopped"
                session.add(account)
                session.commit()
                return False
            account.status = "connecting"
            account.last_error = ""
            session.add(account)
            session.commit()

        await self.stop()
        self._running = True
        self._client = httpx.AsyncClient(timeout=40.0)
        self._poll_task = asyncio.create_task(self._poll_loop())
        with Session(engine) as session:
            account = get_wechat_account(session)
            account.status = "connected"
            session.add(account)
            session.commit()
        return True

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def restart(self) -> bool:
        await self.stop()
        return await self.start()

    def _account_snapshot(self, session: Session) -> WeChatAccount | None:
        account = get_wechat_account(session)
        if not account.enabled or not account.account_id or not account.token:
            return None
        return account

    async def _poll_loop(self) -> None:
        assert self._client is not None
        with Session(engine) as session:
            account = self._account_snapshot(session)
            if account is None:
                return
            base_url = account.base_url
            token = account.token
            sync_buf = account.last_sync_buf

        consecutive_failures = 0
        while self._running:
            try:
                response = await _api_post_json(
                    base_url=base_url,
                    endpoint=EP_GET_UPDATES,
                    payload={"get_updates_buf": sync_buf},
                    token=token,
                    timeout_ms=LONG_POLL_TIMEOUT_MS,
                )
            except (asyncio.TimeoutError, httpx.TimeoutException):
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                with Session(engine) as session:
                    account = get_wechat_account(session)
                    account.status = "connected" if consecutive_failures < 3 else "error"
                    account.last_error = str(exc)[:300]
                    session.add(account)
                    session.commit()
                await asyncio.sleep(2 if consecutive_failures < 3 else 15)
                if consecutive_failures >= 3:
                    consecutive_failures = 0
                continue

            ret = response.get("ret", 0)
            errcode = response.get("errcode", 0)
            if ret not in (0, None) or errcode not in (0, None):
                stale = (
                    ret == SESSION_EXPIRED_ERRCODE
                    or errcode == SESSION_EXPIRED_ERRCODE
                    or (
                        ret == RATE_LIMIT_ERRCODE
                        and str(response.get("errmsg", "")).lower() == "unknown error"
                    )
                )
                with Session(engine) as session:
                    account = get_wechat_account(session)
                    account.status = "error"
                    account.last_error = f"ret={ret} errcode={errcode} errmsg={response.get('errmsg', '')}"[:300]
                    session.add(account)
                    session.commit()
                if stale:
                    await asyncio.sleep(600)
                else:
                    await asyncio.sleep(5)
                continue

            consecutive_failures = 0
            new_sync_buf = str(response.get("get_updates_buf") or "")
            if new_sync_buf:
                sync_buf = new_sync_buf
                with Session(engine) as session:
                    account = get_wechat_account(session)
                    account.last_sync_buf = sync_buf
                    account.status = "connected"
                    account.last_error = ""
                    session.add(account)
                    session.commit()

            for message in response.get("msgs") or []:
                asyncio.create_task(self._process_message_safe(message))

    async def _process_message_safe(self, message: dict[str, Any]) -> None:
        try:
            await self._process_message(message)
        except Exception:
            pass

    async def _process_message(self, message: dict[str, Any]) -> None:
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id:
            return
        with Session(engine) as session:
            account = self._account_snapshot(session)
            if account is None:
                return
            if sender_id == account.account_id:
                return

        message_id = str(message.get("message_id") or "").strip()
        if message_id and message_id in self._seen:
            return
        if message_id:
            self._seen.append(message_id)

        room_id = str(message.get("room_id") or message.get("chat_room_id") or "").strip()
        if room_id or sender_id.endswith("@chatroom"):
            # 第一版只做私聊；群消息暂时不进入桑多涅大脑
            return

        text = _extract_text(message.get("item_list") or [])
        if not text:
            return

        context_token = str(message.get("context_token") or "").strip()
        notice = ""
        with Session(engine) as session:
            account = get_wechat_account(session)
            if context_token:
                try:
                    tokens = json.loads(account.context_tokens or "{}")
                except json.JSONDecodeError:
                    tokens = {}
                tokens[sender_id] = context_token
                account.context_tokens = json.dumps(tokens, ensure_ascii=False)
                session.add(account)
                session.commit()

            # 只有发来有效配对码的人才能成为主人；之后只认一个人
            if not account.owner_user_id:
                if _match_pairing_code(text):
                    account.owner_user_id = sender_id
                    account.updated_at = utcnow()
                    session.add(account)
                    session.commit()
                    notice = PAIRED_REPLY
                elif sender_id not in self._cold_replied:
                    self._cold_replied.append(sender_id)
                    notice = UNPAIRED_REPLY
                else:
                    return
            elif sender_id != account.owner_user_id:
                if sender_id not in self._cold_replied:
                    self._cold_replied.append(sender_id)
                    account_snapshot = WeChatAccount(
                        id=account.id,
                        enabled=account.enabled,
                        name=account.name,
                        account_id=account.account_id,
                        token=account.token,
                        base_url=account.base_url,
                        cdn_base_url=account.cdn_base_url,
                        user_id=account.user_id,
                        owner_user_id=account.owner_user_id,
                        status=account.status,
                        last_sync_buf=account.last_sync_buf,
                        context_tokens=account.context_tokens,
                        last_error=account.last_error,
                    )
                    await self._send_text(account_snapshot, sender_id, "这个账号只和一个人对话。抱歉。")
                return

        if notice:
            await self.send_text(sender_id, notice)
            return

        lock = self._chat_locks.setdefault(sender_id, asyncio.Lock())
        async with lock:
            await self._handle_owner_dm(sender_id, text)

    async def _handle_owner_dm(self, sender_id: str, text: str) -> None:
        with Session(engine) as session:
            # 只有已绑定的主人会走到这里：和网页共用同一条时间线，换渠道也能接着聊。
            result = await create_chat_turn(
                session,
                text,
                thread_id="main",
                channel="wechat_dm",
                sender_id=sender_id,
                chat_id=sender_id,
            )

        if result.get("assistant_message"):
            reply = result["assistant_message"]["content"]
            user_message_id = result["user_message"]["id"]
            assistant_message_id = result["assistant_message"]["id"]
            asyncio.create_task(
                asyncio.to_thread(
                    run_extraction_background,
                    user_message_id,
                    assistant_message_id,
                )
            )
        else:
            # 技术错误（可能含请求 ID、掩码 Key）只留在网页「用量」页的审计里，不发进微信。
            reply = WECHAT_FAILURE_REPLY

        await self.send_text(sender_id, reply)

    async def _send_text(self, account: WeChatAccount, to: str, text: str) -> dict[str, Any]:
        return await self._send_items(account, to, [{"type": ITEM_TEXT, "text_item": {"text": text}}])

    async def _send_items(
        self, account: WeChatAccount, to: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        context_tokens: dict[str, str] = {}
        try:
            context_tokens = json.loads(account.context_tokens or "{}")
        except json.JSONDecodeError:
            pass
        context_token = context_tokens.get(to)
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": f"sandrone-weixin-{uuid.uuid4().hex}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": items,
        }
        if context_token:
            message["context_token"] = context_token
        while True:
            response = await _api_post_json(
                base_url=account.base_url,
                endpoint=EP_SEND_MESSAGE,
                payload={"msg": message},
                token=account.token,
            )
            ret = response.get("ret", 0)
            errcode = response.get("errcode", 0)
            if ret in (0, None) and errcode in (0, None):
                return response
            expired = SESSION_EXPIRED_ERRCODE in (ret, errcode)
            if expired and "context_token" in message:
                # 与 Hermes 一致：会话过期时去掉 context_token 重试一次，主动消息依赖这条降级路径。
                message.pop("context_token")
                message["client_id"] = f"sandrone-weixin-{uuid.uuid4().hex}"
                continue
            raise RuntimeError(f"iLink send failed ret={ret} errcode={errcode} errmsg={response.get('errmsg', '')}")

    async def send_text(self, to: str, text: str) -> dict[str, Any]:
        return await self._send_text(self._account_copy(), to, text)

    def _account_copy(self) -> WeChatAccount:
        with Session(engine) as session:
            account = self._account_snapshot(session)
            if account is None:
                raise RuntimeError("微信账号未连接。")
            return WeChatAccount(
                id=account.id,
                enabled=account.enabled,
                name=account.name,
                account_id=account.account_id,
                token=account.token,
                base_url=account.base_url,
                cdn_base_url=account.cdn_base_url,
                user_id=account.user_id,
                owner_user_id=account.owner_user_id,
                status=account.status,
                last_sync_buf=account.last_sync_buf,
                context_tokens=account.context_tokens,
                last_error=account.last_error,
            )


def get_runtime() -> WeChatRuntime:
    global _runtime
    if _runtime is None:
        _runtime = WeChatRuntime()
    return _runtime


async def startup_wechat() -> None:
    await get_runtime().start()


async def shutdown_wechat() -> None:
    await get_runtime().stop()
