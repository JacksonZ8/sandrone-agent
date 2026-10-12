from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.schemas import ChatRequest
from tests.llm_fakes import FakeStream, reply
from app.services import chat_service
from app.services.chat_prompt import reply_style_block


class ReplyStyleBlockTests(unittest.TestCase):
    def test_no_preferences_gives_no_block(self) -> None:
        self.assertEqual(reply_style_block(), "")

    def test_short_and_long_lengths(self) -> None:
        self.assertIn("一到三句", reply_style_block(reply_length="short"))
        self.assertIn("多说一些", reply_style_block(reply_length="long"))

    def test_action_text_toggle(self) -> None:
        self.assertIn("可以偶尔用全角括号", reply_style_block(action_text=True))
        self.assertIn("不要写括号里的动作", reply_style_block(action_text=False))

    def test_voice_mode_overrides_action_text(self) -> None:
        block = reply_style_block(reply_length="short", action_text=True, voice_mode=True)
        self.assertIn("合成语音", block)
        self.assertIn("不写括号里的动作", block)
        self.assertNotIn("可以偶尔用全角括号", block)

    def test_request_defaults_and_validation(self) -> None:
        request = ChatRequest(content="你好")
        self.assertIsNone(request.reply_length)
        self.assertIsNone(request.action_text)
        self.assertFalse(request.voice_mode)
        with self.assertRaises(ValidationError):
            ChatRequest(content="你好", reply_length="medium")


class ReplyStyleWiringTests(unittest.TestCase):
    def test_style_context_reaches_system_prompt(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        fake_llm = FakeStream(reply("嗯。"))
        style = reply_style_block(reply_length="short", voice_mode=True)
        try:
            with Session(engine) as session:
                with patch.object(chat_service, "chat_completion_stream", fake_llm):
                    result = asyncio.run(
                        chat_service.create_chat_turn(session, "在吗", style_context=style)
                    )
            self.assertEqual(result["error"], "")
            system_prompt = fake_llm.system_text
            self.assertIn("## 本轮回复要求", system_prompt)
            self.assertIn("合成语音", system_prompt)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
