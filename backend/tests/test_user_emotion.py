from __future__ import annotations

import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Message, SemanticMemory, utcnow
from app.services.memory import embedding, extraction
from app.services.user_emotion import (
    current_user_emotion,
    parse_llm_emotion,
    record_user_emotion,
    rule_emotion,
    user_emotion_block,
)


class UserEmotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _user(self, session: Session, text: str, *, hours_ago: float = 0) -> Message:
        message = Message(role="user", content=text, turn_index=1, created_at=utcnow() - timedelta(hours=hours_ago))
        session.add(message)
        session.commit()
        session.refresh(message)
        return message

    def test_rules_only_fire_on_clear_words(self) -> None:
        self.assertEqual(rule_emotion("今天好累啊")["label"], "疲惫")
        self.assertEqual(rule_emotion("考试好紧张")["label"], "焦虑")
        self.assertIsNone(rule_emotion("我不累，还能再写会儿"))
        self.assertIsNone(rule_emotion("这道题有点困难"))
        self.assertIsNone(rule_emotion("帮我看下这个函数"))

    def test_llm_field_is_validated(self) -> None:
        self.assertEqual(parse_llm_emotion({"user_emotion": {"label": "难过", "confidence": 0.8}}),
                         {"label": "难过", "confidence": 0.8, "source": "llm"})
        self.assertIsNone(parse_llm_emotion({"user_emotion": None}))
        self.assertIsNone(parse_llm_emotion({"user_emotion": {"label": "sad"}}))
        self.assertIsNone(parse_llm_emotion(None))

    def test_recent_observation_decays_and_expires(self) -> None:
        with Session(self.engine) as session:
            self.assertIsNone(current_user_emotion(session))
            message = self._user(session, "好累", hours_ago=1)
            record_user_emotion(session, message, {"label": "疲惫", "confidence": 0.8, "source": "llm"})
            current = current_user_emotion(session)
            self.assertEqual(current["label"], "疲惫")
            self.assertAlmostEqual(current["confidence"], round(0.8 * 0.5 ** (1 / 3), 2))
            self.assertIn("对方看起来疲惫", user_emotion_block(session))
            # 六小时后衰减到不足 0.25：当作已经过去
            later = current_user_emotion(session, now=utcnow() + timedelta(hours=6))
            self.assertIsNone(later)

    def test_latest_observation_wins_and_calm_is_not_mentioned(self) -> None:
        with Session(self.engine) as session:
            record_user_emotion(session, self._user(session, "烦死了", hours_ago=2), rule_emotion("烦死了"))
            calm = self._user(session, "好了，没事了")
            record_user_emotion(session, calm, {"label": "平静", "confidence": 0.9, "source": "llm"})
            self.assertEqual(current_user_emotion(session)["label"], "平静")
            self.assertEqual(user_emotion_block(session), "")
            self.assertEqual(json.loads(calm.emotion_tags)["source"], "llm")

    def test_only_user_messages_are_tagged(self) -> None:
        with Session(self.engine) as session:
            reply = Message(role="assistant", content="嗯", turn_index=1)
            session.add(reply)
            session.commit()
            record_user_emotion(session, reply, {"label": "开心", "confidence": 0.9, "source": "llm"})
            self.assertEqual(reply.emotion_tags, "[]")


class ExtractionPassTests(unittest.TestCase):
    """记忆提取那次模型调用顺带给出用户情绪；遗忘/纠正回合不提取。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.reply = {"memories": [{"perspective": "user", "type": "fact", "content": "用户这周在赶毕业设计。", "importance": 0.7}],
                      "reaffirmed": [], "user_emotion": {"label": "焦虑", "confidence": 0.7}}

        async def fake_completion(*args, **kwargs):
            return {"text": json.dumps(self.reply, ensure_ascii=False)}

        self.patches = [
            patch.object(extraction, "engine", self.engine),
            patch.object(extraction, "get_config", return_value=SimpleNamespace(provider="deepseek", model="test")),
            patch.object(extraction, "chat_completion", side_effect=fake_completion),
            patch.object(extraction, "maybe_write_diary"),
            patch.object(embedding, "get_embedder", return_value=None),
        ]
        for item in self.patches:
            item.start()
        embedding.embed_query.cache_clear()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        embedding.embed_query.cache_clear()
        self.engine.dispose()

    def _turn(self, user_text: str, tool_calls: list[dict] | None = None) -> tuple[int, int]:
        with Session(self.engine) as session:
            user = Message(role="user", content=user_text, turn_index=1)
            reply = Message(role="assistant", content="嗯。", turn_index=1, tool_calls_json=json.dumps(tool_calls or []))
            session.add(user)
            session.add(reply)
            session.commit()
            return user.id, reply.id

    def test_model_emotion_is_recorded_on_the_user_message(self) -> None:
        user_id, reply_id = self._turn("这周毕业设计要交了，进度还差好多，有点慌")
        extraction.run_extraction_background(user_id, reply_id)
        with Session(self.engine) as session:
            tags = json.loads(session.get(Message, user_id).emotion_tags)
            self.assertEqual((tags["label"], tags["source"]), ("焦虑", "llm"))
            self.assertEqual(len(session.exec(select(SemanticMemory)).all()), 1)

    def test_revision_turn_skips_extraction(self) -> None:
        user_id, reply_id = self._turn("把我在赶毕业设计这件事忘掉吧，别再提了", [{"name": "forget_memory", "arguments": {}}])
        extraction.run_extraction_background(user_id, reply_id)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(SemanticMemory)).all(), [])


if __name__ == "__main__":
    unittest.main()
