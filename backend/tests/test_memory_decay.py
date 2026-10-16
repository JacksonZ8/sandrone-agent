from __future__ import annotations

import math
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import MemoryExtractionRun, SemanticMemory, utcnow
from app.services.memory import embedding
from app.services.memory.common import _memory_to_dict, effective_strength
from app.services.memory.consolidation import apply_forgetting
from app.services.memory.extraction import _parse_reaffirmed, _save_candidates
from app.services.memory.retrieval import retrieve_semantic_memories_scored


class MemoryDecayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        # 只测衰减与强化：关掉本地向量模型，免得加载模型
        self.patch = patch.object(embedding, "get_embedder", return_value=None)
        self.patch.start()
        embedding.embed_query.cache_clear()

    def tearDown(self) -> None:
        self.patch.stop()
        embedding.embed_query.cache_clear()
        self.engine.dispose()

    def _add(self, session: Session, content: str, *, importance: float = 0.5, days_ago: float = 0.0, **fields) -> SemanticMemory:
        memory = SemanticMemory(content=content, importance=importance, created_at=utcnow() - timedelta(days=days_ago), **fields)
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return memory

    def _run(self, session: Session) -> MemoryExtractionRun:
        run = MemoryExtractionRun(source_message_ids="[]", status="pending")
        session.add(run)
        session.commit()
        return run

    def test_repeated_forgetting_runs_do_not_compound(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "用户在学水彩画", importance=1.0)
            born = utcnow()
            for run in range(1, 41):  # 20 天里每 12 小时跑一次，期间没被提起
                memory.created_at = born - timedelta(days=run * 0.5)
                session.add(memory)
                session.commit()
                apply_forgetting(session)
            session.refresh(memory)
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.strength, 1.0)  # 锚点不被改写
            self.assertAlmostEqual(effective_strength(memory), math.exp(-20 / 127), places=3)

    def test_forgetting_suppresses_by_strength_since_anchor(self) -> None:
        with Session(self.engine) as session:
            stale = self._add(session, "用户今天午饭吃了面", importance=0.0, days_ago=20)
            fresh = self._add(session, "用户今天晚饭吃了饺子", importance=0.0, days_ago=1)
            self.assertEqual(apply_forgetting(session), 1)
            session.refresh(stale)
            session.refresh(fresh)
            self.assertEqual(stale.status, "suppressed")
            self.assertEqual(fresh.status, "active")

    def test_retrieval_does_not_reinforce(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "用户喜欢机械研究", days_ago=10)
            ranked = retrieve_semantic_memories_scored(session, "机械研究")
            self.assertEqual(ranked[0][1].id, memory.id)
            session.refresh(memory)
            self.assertEqual(memory.access_count, 0)
            self.assertIsNone(memory.last_accessed_at)

    def test_user_restating_a_memory_reinforces_it(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "用户要求长期遵守：回复里不要用表情符号", days_ago=30)
            before = effective_strength(memory)
            candidate = {"content": "用户要求长期遵守：回复里不要用表情符号", "type": "preference",
                         "confidence": 0.85, "importance": 0.8, "perspective": "user"}
            self.assertEqual(_save_candidates(session, [candidate], [], extraction_run=self._run(session)), 0)
            session.refresh(memory)
            self.assertAlmostEqual(memory.strength, before + 0.2 * (1 - before), places=3)
            self.assertEqual(memory.access_count, 1)
            self.assertLess((utcnow() - memory.last_accessed_at.replace(tzinfo=utcnow().tzinfo)).total_seconds(), 60)
            self.assertEqual(len(session.exec(select(SemanticMemory)).all()), 1)  # 没有重复入库

    def test_self_perspective_repeat_is_not_user_reiteration(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "我其实不讨厌和他讨论齿轮结构", days_ago=30)
            candidate = {"content": "我其实不讨厌和他讨论齿轮结构", "type": "preference",
                         "confidence": 0.7, "importance": 0.5, "perspective": "self"}
            _save_candidates(session, [candidate], [], extraction_run=self._run(session))
            session.refresh(memory)
            self.assertEqual(memory.strength, 1.0)
            self.assertEqual(memory.access_count, 0)
            self.assertIsNone(memory.last_accessed_at)

    def test_reaffirmed_ids_reinforce_without_new_candidates(self) -> None:
        with Session(self.engine) as session:
            reaffirmed = self._add(session, "用户养了一只叫团子的猫", days_ago=40)
            other = self._add(session, "用户住在深圳", days_ago=40)
            run = self._run(session)
            # 同一条既被模型认定、又字面重复：只强化一次
            candidate = {"content": "用户养了一只叫团子的猫", "type": "fact",
                         "confidence": 0.9, "importance": 0.6, "perspective": "user"}
            _save_candidates(session, [candidate], [], extraction_run=run, reaffirmed_ids={reaffirmed.id})
            session.refresh(reaffirmed)
            session.refresh(other)
            self.assertEqual(reaffirmed.access_count, 1)
            self.assertEqual(other.access_count, 0)
            self.assertIsNone(other.last_accessed_at)

    def test_parse_reaffirmed_only_accepts_shown_ids(self) -> None:
        raw = '```json\n{"memories":[],"reaffirmed":[3,"#5",9,"x",null]}\n```'
        self.assertEqual(_parse_reaffirmed(raw, {3, 5, 7}), {3, 5})
        self.assertEqual(_parse_reaffirmed('{"memories":[]}', {3}), set())
        self.assertEqual(_parse_reaffirmed("不是 JSON", {3}), set())

    def test_api_reports_current_strength(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "用户在学水彩", importance=0.5, days_ago=67)
            self.assertAlmostEqual(_memory_to_dict(memory)["strength"], math.exp(-1), places=3)


if __name__ == "__main__":
    unittest.main()
