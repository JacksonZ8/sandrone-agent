from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..config import DATA_DIR
from ..models import PushSubscription, utcnow

logger = logging.getLogger(__name__)

PUSH_DIR = DATA_DIR / "push"
VAPID_KEY_FILE = PUSH_DIR / "vapid_private.pem"
# VAPID 联系方式只给推送服务看，用本机占位地址，不暴露真实邮箱
VAPID_CLAIMS = {"sub": "mailto:owner@sandrone.invalid"}


def _vapid():
    """读取或首次生成 VAPID 密钥（私钥只存在 data/push，不进 Git、不经接口返回）。"""
    from py_vapid import Vapid

    PUSH_DIR.mkdir(parents=True, exist_ok=True)
    if VAPID_KEY_FILE.is_file():
        return Vapid.from_file(str(VAPID_KEY_FILE))
    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(VAPID_KEY_FILE))
    VAPID_KEY_FILE.chmod(0o600)
    return vapid


def public_key() -> str:
    """浏览器订阅用的 applicationServerKey（未压缩公钥，base64url）。"""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def subscribe(session: Session, data: dict[str, Any], user_agent: str = "") -> PushSubscription:
    endpoint = str(data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("推送订阅信息不完整。")
    row = session.exec(select(PushSubscription).where(PushSubscription.endpoint == endpoint)).first()
    if row is None:
        row = PushSubscription(endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"])
    row.p256dh, row.auth, row.user_agent = keys["p256dh"], keys["auth"], user_agent[:200]
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def unsubscribe(session: Session, endpoint: str) -> bool:
    row = session.exec(select(PushSubscription).where(PushSubscription.endpoint == endpoint)).first()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def has_subscriptions(session: Session) -> bool:
    return session.exec(select(PushSubscription).limit(1)).first() is not None


def _send_one(row: PushSubscription, payload: str) -> int:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}},
            data=payload,
            vapid_private_key=str(VAPID_KEY_FILE),
            vapid_claims=dict(VAPID_CLAIMS),
            ttl=6 * 3600,
            timeout=15,
        )
        return 201
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("web push failed: %s", status)
        return status


async def notify(session: Session, *, title: str, body: str, url: str = "/#/chat", tag: str = "sandrone-message") -> int:
    """推给所有已订阅的设备，返回成功数；推送服务说订阅已失效（404/410）就删掉。"""
    rows = list(session.exec(select(PushSubscription)).all())
    if not rows:
        return 0
    _vapid()  # 确保密钥存在
    payload = json.dumps({"title": title, "body": body[:180], "url": url, "tag": tag}, ensure_ascii=False)
    sent = 0
    for row in rows:
        status = await asyncio.to_thread(_send_one, row, payload)
        if status in (200, 201, 202):
            row.last_sent_at = utcnow()
            session.add(row)
            sent += 1
        elif status in (404, 410):
            session.delete(row)
    session.commit()
    return sent


def key_path() -> Path:
    return VAPID_KEY_FILE
