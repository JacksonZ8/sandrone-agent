from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import WeChatAccount
from app.services import wechat_service


def _message(sender: str, text: str, message_id: str) -> dict:
    return {
        "from_user_id": sender,
        "message_id": message_id,
        "item_list": [{"type": wechat_service.ITEM_TEXT, "text_item": {"text": text}}],
    }


class WeChatPairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(WeChatAccount(id=1, enabled=True, account_id="bot", token="token"))
            session.commit()
        self.engine_patch = patch.object(wechat_service, "engine", self.engine)
        self.engine_patch.start()
        # 测试绝不能触达真实 iLink：任何底层外发都会直接失败。
        self.network_patch = patch.object(
            wechat_service, "_api_post_json", AsyncMock(side_effect=AssertionError("network"))
        )
        self.network_patch.start()
        wechat_service._PAIRING.clear()
        self.runtime = wechat_service.WeChatRuntime()
        self.runtime.send_text = AsyncMock()
        self.runtime._handle_owner_dm = AsyncMock()
        self.runtime._send_text = AsyncMock()
        self.counter = 0

    def tearDown(self) -> None:
        wechat_service._PAIRING.clear()
        self.network_patch.stop()
        self.engine_patch.stop()
        self.engine.dispose()

    def _receive(self, sender: str, text: str) -> None:
        self.counter += 1
        asyncio.run(self.runtime._process_message(_message(sender, text, f"m{self.counter}")))

    def _owner(self) -> str:
        with Session(self.engine) as session:
            return session.get(WeChatAccount, 1).owner_user_id

    def _start_pairing(self) -> str:
        with Session(self.engine) as session:
            result = wechat_service.start_owner_pairing(session)
        self.assertTrue(result["ok"])
        return result["code"]

    def test_first_sender_is_not_bound_without_code(self) -> None:
        self._receive("stranger", "你好")
        self.assertEqual(self._owner(), "")
        self.runtime.send_text.assert_awaited_once_with("stranger", wechat_service.UNPAIRED_REPLY)
        self.runtime._handle_owner_dm.assert_not_awaited()

        self._receive("stranger", "在吗")
        self.assertEqual(self.runtime.send_text.await_count, 1)

    def test_valid_code_binds_sender_and_later_messages_reach_brain(self) -> None:
        code = self._start_pairing()
        self._receive("owner", "000000" if code != "000000" else "111111")
        self.assertEqual(self._owner(), "")

        self._receive("owner", code)
        self.assertEqual(self._owner(), "owner")
        self.runtime.send_text.assert_awaited_with("owner", wechat_service.PAIRED_REPLY)
        self.runtime._handle_owner_dm.assert_not_awaited()

        self._receive("owner", "今天有点累")
        self.runtime._handle_owner_dm.assert_awaited_once_with("owner", "今天有点累")

        self._receive("other", code)
        self.assertEqual(self._owner(), "owner")
        self.assertEqual(self.runtime._handle_owner_dm.await_count, 1)
        self.runtime._send_text.assert_awaited_once()

    def test_code_is_single_use_and_expires(self) -> None:
        code = self._start_pairing()
        wechat_service._PAIRING["expires_at"] = time.monotonic() - 1
        self._receive("owner", code)
        self.assertEqual(self._owner(), "")
        self.assertEqual(wechat_service._PAIRING, {})

    def test_too_many_wrong_codes_invalidate_pairing(self) -> None:
        code = self._start_pairing()
        wrong = "999999" if code != "999999" else "888888"
        for _ in range(wechat_service.PAIRING_MAX_FAILURES):
            self.assertFalse(wechat_service._match_pairing_code(wrong))
        self.assertFalse(wechat_service._match_pairing_code(code))

    def test_non_ascii_text_does_not_crash_matching(self) -> None:
        self._start_pairing()
        self.assertFalse(wechat_service._match_pairing_code("配对码是多少"))

    def test_pairing_refused_when_owner_exists(self) -> None:
        with Session(self.engine) as session:
            account = session.get(WeChatAccount, 1)
            account.owner_user_id = "owner"
            session.add(account)
            session.commit()
            result = wechat_service.start_owner_pairing(session)
        self.assertFalse(result["ok"])
        self.assertEqual(wechat_service._PAIRING, {})


if __name__ == "__main__":
    unittest.main()
