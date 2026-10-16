from __future__ import annotations

import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import MemoryExtractionRun, MemoryRedaction, Message, SemanticMemory, utcnow
from app.services.memory import embedding
from app.services.memory.extraction import _save_candidates
from app.services.memory.retrieval import retrieve_episodic_memories, retrieve_semantic_memories_scored
from app.services.memory.revision import correct_memory, finish_revision_turn, forget_memories, is_resurrection
from app.services.tools.registry import active_tool_schemas, execute_tool_call


class MemoryRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        # 只测字面匹配：关掉本地向量模型
        self.patch = patch.object(embedding, "get_embedder", return_value=None)
        self.patch.start()
        embedding.embed_query.cache_clear()

    def tearDown(self) -> None:
        self.patch.stop()
        embedding.embed_query.cache_clear()
        self.engine.dispose()

    def _message(self, session: Session, text: str, *, role: str = "user", minutes_ago: float = 0, **fields) -> Message:
        message = Message(role=role, content=text, created_at=utcnow() - timedelta(minutes=minutes_ago), turn_index=1, **fields)
        session.add(message)
        session.commit()
        session.refresh(message)
        return message

    def _memory(self, session: Session, content: str, sources: list[int] | None = None) -> SemanticMemory:
        memory = SemanticMemory(content=content, source_message_ids=json.dumps(sources or []))
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return memory

    def test_forget_hides_memory_and_its_source_messages(self) -> None:
        with Session(self.engine) as session:
            source = self._message(session, "我住在深圳南山", minutes_ago=60 * 24 * 3)
            memory = self._memory(session, "用户住在深圳。", [source.id])
            other = self._memory(session, "用户喜欢机械研究。")
            result = forget_memories(session, "我住在深圳")
            self.assertEqual(result["forgotten"], 1)
            session.refresh(memory)
            session.refresh(other)
            self.assertEqual((memory.status, other.status), ("forgotten", "active"))
            self.assertNotIn(memory.id, [m.id for _, m in retrieve_semantic_memories_scored(session, "深圳")])
            self.assertEqual(retrieve_episodic_memories(session, "深圳南山", recent_message_ids=set()), [])

    def test_forget_with_no_match_does_nothing(self) -> None:
        with Session(self.engine) as session:
            memory = self._memory(session, "用户喜欢机械研究。")
            self.assertEqual(forget_memories(session, "我的生日")["forgotten"], 0)
            session.refresh(memory)
            self.assertEqual(memory.status, "active")

    def test_correct_replaces_the_wrong_memory(self) -> None:
        with Session(self.engine) as session:
            source = self._message(session, "我住在深圳", minutes_ago=600)
            old = self._memory(session, "用户住在深圳。", [source.id])
            result = correct_memory(session, "住在深圳", "用户住在广州。")
            self.assertTrue(result["ok"])
            session.refresh(old)
            fresh = session.get(SemanticMemory, result["replacement"])
            self.assertEqual((old.status, fresh.status, fresh.content), ("corrected", "active", "用户住在广州。"))
            self.assertIsNotNone(session.exec(select(MemoryRedaction).where(MemoryRedaction.message_id == source.id)).first())

    def test_correct_asks_when_ambiguous(self) -> None:
        with Session(self.engine) as session:
            self._memory(session, "用户养了一只猫。")
            self._memory(session, "用户养了一只狗。")
            result = correct_memory(session, "用户养了一只", "用户养了一只仓鼠。")
            self.assertFalse(result["ok"])
            self.assertIn("说清", result["error"])

    def test_revision_turn_is_not_extracted_and_request_is_redacted(self) -> None:
        with Session(self.engine) as session:
            user = self._message(session, "把我住在深圳这件事忘了吧")
            assistant = self._message(
                session, "好，忘了。", role="assistant",
                tool_calls_json=json.dumps([{"name": "forget_memory", "arguments": {"query": "深圳"}}]),
            )
            plain = self._message(session, "嗯", role="assistant")
            self.assertTrue(finish_revision_turn(session, user, assistant))
            self.assertFalse(finish_revision_turn(session, user, plain))
            self.assertIsNotNone(session.exec(select(MemoryRedaction).where(MemoryRedaction.message_id == user.id)).first())

    def test_old_material_cannot_resurrect_a_forgotten_memory(self) -> None:
        with Session(self.engine) as session:
            old_source = self._message(session, "我住在深圳", minutes_ago=30)
            self._memory(session, "用户住在深圳。", [old_source.id])
            forget_memories(session, "住在深圳")
            run = MemoryExtractionRun(source_message_ids="[]", status="pending")
            session.add(run)
            session.commit()
            candidate = {"content": "用户住在深圳。", "type": "fact", "confidence": 0.9, "importance": 0.6, "perspective": "user"}
            # 还在后台跑的旧提取：来源早于遗忘，不能把它带回来
            self.assertTrue(is_resurrection(session, candidate["content"], [old_source.id]))
            self.assertEqual(_save_candidates(session, [candidate], [old_source.id], extraction_run=run), 0)
            # 用户之后自己又说起：照常记住
            new_source = self._message(session, "其实我又搬回深圳了")
            self.assertFalse(is_resurrection(session, candidate["content"], [new_source.id]))
            self.assertEqual(_save_candidates(session, [candidate], [new_source.id], extraction_run=run), 1)

    def test_tools_are_always_offered_and_dispatched(self) -> None:
        with Session(self.engine) as session:
            names = {schema["function"]["name"] for schema in active_tool_schemas(type("C", (), {"files_enabled": False, "screen_enabled": False, "agent_enabled": False})())}
            self.assertTrue({"forget_memory", "correct_memory"} <= names)
            self._memory(session, "用户住在深圳。")
            result = execute_tool_call(session, "forget_memory", {"query": "深圳"})
            self.assertEqual(result["forgotten"], 1)


if __name__ == "__main__":
    unittest.main()
