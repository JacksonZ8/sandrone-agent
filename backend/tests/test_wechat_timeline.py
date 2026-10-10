from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Message
from tests.llm_fakes import FakeStream, reply
from app.services import chat_service, wechat_service

LLM_REPLY = {
    "text": "嗯，我在。", "tool_calls": [], "prompt_tokens": 0,
    "completion_tokens": 0, "total_tokens": 0, "latency_ms": 1,
}


class WeChatTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)
        self.patches = [
            patch.object(wechat_service, "engine", self.engine),
            patch.object(
                wechat_service, "_api_post_json", AsyncMock(side_effect=AssertionError("network"))
            ),
            patch.object(wechat_service, "run_extraction_background", lambda *args: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def test_owner_wechat_message_joins_main_timeline_with_web_context(self) -> None:
        with Session(self.engine) as session:
            session.add(Message(turn_index=1, role="user", content="网页上说到一半的话", channel="web"))
            session.commit()

        runtime = wechat_service.WeChatRuntime()
        runtime.send_text = AsyncMock()
        fake_llm = FakeStream(LLM_REPLY)
        with patch.object(chat_service, "chat_completion_stream", fake_llm):
            asyncio.run(runtime._handle_owner_dm("owner", "我换到微信了"))

        runtime.send_text.assert_awaited_once_with("owner", "嗯，我在。")
        with Session(self.engine) as session:
            rows = session.exec(select(Message).order_by(Message.id)).all()
        self.assertEqual([row.thread_id for row in rows], ["main", "main", "main"])
        self.assertEqual([row.channel for row in rows], ["web", "wechat_dm", "wechat_dm"])

        llm_messages = fake_llm.messages
        self.assertIn("对方正在用微信和你说话", fake_llm.system_text)
        self.assertIn("网页上说到一半的话", [item["content"] for item in llm_messages[1:]])

    def test_web_turn_has_no_wechat_hint(self) -> None:
        fake_llm = FakeStream(LLM_REPLY)
        with Session(self.engine) as session, patch.object(chat_service, "chat_completion_stream", fake_llm):
            asyncio.run(chat_service.create_chat_turn(session, "你好"))
        self.assertNotIn("对方正在用微信和你说话", fake_llm.system_text)


if __name__ == "__main__":
    unittest.main()


class WeChatSendRetryTests(unittest.TestCase):
    def _account(self) -> "wechat_service.WeChatAccount":
        return wechat_service.WeChatAccount(
            id=1, enabled=True, account_id="bot", token="token",
            context_tokens='{"owner": "ctx-1"}',
        )

    def test_expired_session_retries_once_without_context_token(self) -> None:
        sent: list[dict] = []
        responses = [{"ret": 0, "errcode": -14}, {"ret": 0}]

        async def post(**kwargs):
            sent.append(copy.deepcopy(kwargs["payload"]["msg"]))
            return responses[len(sent) - 1]

        with patch.object(wechat_service, "_api_post_json", post):
            asyncio.run(wechat_service.WeChatRuntime()._send_text(self._account(), "owner", "在吗"))
        self.assertEqual(len(sent), 2)
        first, second = sent
        self.assertEqual(first["context_token"], "ctx-1")
        self.assertNotIn("context_token", second)
        self.assertNotEqual(first["client_id"], second["client_id"])

    def test_other_errors_are_not_retried(self) -> None:
        post = AsyncMock(return_value={"ret": -2, "errcode": 0})
        with patch.object(wechat_service, "_api_post_json", post):
            with self.assertRaises(RuntimeError):
                asyncio.run(wechat_service.WeChatRuntime()._send_text(self._account(), "owner", "在吗"))
        self.assertEqual(post.await_count, 1)


class WeChatFailureReplyTests(unittest.TestCase):
    def test_llm_error_is_not_forwarded_to_wechat(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        runtime = wechat_service.WeChatRuntime()
        runtime.send_text = AsyncMock()
        failure = chat_service.LLMError("LLM 请求失败（HTTP 401）：Your api key: ****09b5 is invalid")
        try:
            with patch.object(wechat_service, "engine", engine), patch.object(
                chat_service, "chat_completion_stream", FakeStream(failure)
            ):
                asyncio.run(runtime._handle_owner_dm("owner", "在吗"))
        finally:
            engine.dispose()
        runtime.send_text.assert_awaited_once_with("owner", wechat_service.WECHAT_FAILURE_REPLY)
        self.assertNotIn("401", wechat_service.WECHAT_FAILURE_REPLY)
