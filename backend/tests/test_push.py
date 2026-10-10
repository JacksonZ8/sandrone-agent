from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Message, PushSubscription
from app.services import proactive_service, push_service
from app.services.proactive_service import WeChatTarget, send_to_owner

SUB = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "BPk", "auth": "au"}}


class PushServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.patches = [
            patch.object(push_service, "PUSH_DIR", folder),
            patch.object(push_service, "VAPID_KEY_FILE", folder / "vapid_private.pem"),
        ]
        for item in self.patches:
            item.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_public_key_is_generated_once_and_private_key_stays_on_disk(self) -> None:
        key = push_service.public_key()
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        self.assertEqual(len(raw), 65)  # 未压缩的 P-256 公钥
        self.assertEqual(raw[0], 4)
        self.assertEqual(push_service.public_key(), key)
        self.assertEqual(push_service.VAPID_KEY_FILE.stat().st_mode & 0o777, 0o600)

    def test_subscribe_is_idempotent_and_rejects_bad_input(self) -> None:
        with Session(self.engine) as session:
            push_service.subscribe(session, SUB, "iPhone")
            push_service.subscribe(session, {**SUB, "keys": {"p256dh": "new", "auth": "au"}})
            rows = session.exec(select(PushSubscription)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].p256dh, "new")
            with self.assertRaises(ValueError):
                push_service.subscribe(session, {**SUB, "endpoint": "http://insecure.example"})
            self.assertTrue(push_service.unsubscribe(session, SUB["endpoint"]))
            self.assertFalse(push_service.has_subscriptions(session))

    def test_notify_counts_successes_and_drops_expired_subscriptions(self) -> None:
        with Session(self.engine) as session:
            push_service.subscribe(session, SUB)
            push_service.subscribe(session, {**SUB, "endpoint": "https://fcm.googleapis.com/gone"})
            statuses = {SUB["endpoint"]: 201, "https://fcm.googleapis.com/gone": 410}
            with patch.object(push_service, "_send_one", lambda row, payload: statuses[row.endpoint]):
                sent = asyncio.run(push_service.notify(session, title="桑多涅", body="在吗"))
            self.assertEqual(sent, 1)
            remaining = [row.endpoint for row in session.exec(select(PushSubscription))]
            self.assertEqual(remaining, [SUB["endpoint"]])


class ProactivePushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_wechat_message_is_mirrored_to_push(self) -> None:
        notify = AsyncMock(return_value=1)
        with Session(self.engine) as session, patch.object(push_service, "notify", notify):
            target = WeChatTarget(owner_id="wx", send=AsyncMock())
            event = asyncio.run(send_to_owner(session, target=target, text="刚跑完数据。"))
            self.assertEqual(event.status, "sent")
            notify.assert_awaited_once()
            self.assertEqual(notify.await_args.kwargs["body"], "刚跑完数据。")

    def test_push_only_target_records_web_channel(self) -> None:
        with Session(self.engine) as session:
            target = WeChatTarget(owner_id="owner", send=AsyncMock(), channel="web")
            with patch.object(push_service, "notify", AsyncMock(return_value=1)) as notify:
                event = asyncio.run(send_to_owner(session, target=target, text="在吗。"))
            self.assertEqual(event.status, "sent")
            notify.assert_not_awaited()  # 推送本身就是发送通道，不再重复镜像
            message = session.exec(select(Message)).one()
            self.assertEqual(message.channel, "web")

    def test_factory_falls_back_to_push_when_wechat_is_off(self) -> None:
        with patch.object(proactive_service, "engine", self.engine):
            self.assertIsNone(proactive_service.default_wechat_target())
            with Session(self.engine) as session:
                push_service.subscribe(session, SUB)
            target = proactive_service.default_wechat_target()
        self.assertIsNotNone(target)
        self.assertEqual(target.channel, "web")


if __name__ == "__main__":
    unittest.main()
