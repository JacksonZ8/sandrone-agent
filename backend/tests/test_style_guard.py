from __future__ import annotations

import unittest

from app.config import PROMPTS_DIR
from tests.llm_fakes import FakeStream, reply
from app.services.style_guard import recent_repetition_block


class StyleGuardTests(unittest.TestCase):
    def test_no_history_or_no_repetition_gives_no_block(self) -> None:
        self.assertEqual(recent_repetition_block([], "你好"), "")
        self.assertEqual(recent_repetition_block(["嗯。", "知道了。"], "你好"), "")

    def test_repeated_motif_is_flagged_with_count(self) -> None:
        replies = ["茶已经泡好了。", "顺便给你倒了杯茶。", "工房里很安静。"]
        block = recent_repetition_block(replies, "今天好累")
        self.assertIn("茶（2 次）", block)
        self.assertNotIn("工房", block)

    def test_motif_raised_by_user_is_not_blocked(self) -> None:
        replies = ["茶已经泡好了。", "再来一杯茶？"]
        self.assertEqual(recent_repetition_block(replies, "你今天喝茶了吗"), "")

    def test_catchphrase_used_once_is_flagged(self) -> None:
        block = recent_repetition_block(["荒谬可笑。你又熬夜了。"], "嗯")
        self.assertIn("「荒谬可笑」", block)

    def test_only_recent_window_counts(self) -> None:
        old = ["茶。"] * 5
        fresh = ["好。"] * 12
        self.assertEqual(recent_repetition_block(old + fresh, "嗯"), "")

    def test_persona_examples_no_longer_lean_on_tea(self) -> None:
        persona = (PROMPTS_DIR / "sandrone_persona.md").read_text(encoding="utf-8")
        examples = persona.split("## 语气示例", 1)[1]
        self.assertNotIn("茶", examples)
        self.assertIn("不是每轮都要展示的道具", persona)


if __name__ == "__main__":
    unittest.main()


class StyleGuardChatWiringTests(unittest.TestCase):
    def test_repetition_hint_reaches_system_prompt(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        from app.models import Message
        from app.services import chat_service

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        fake_llm = FakeStream(reply("嗯。"))
        try:
            with Session(engine) as session:
                for index, text in enumerate(["茶泡好了。", "再喝点茶。"], start=1):
                    session.add(Message(turn_index=index, role="assistant", content=text))
                session.commit()
                with patch.object(chat_service, "chat_completion_stream", fake_llm):
                    result = asyncio.run(chat_service.create_chat_turn(session, "今天好累"))
            self.assertEqual(result["error"], "")
            system_prompt = fake_llm.system_text
            self.assertIn("茶（2 次）", system_prompt)
        finally:
            engine.dispose()
