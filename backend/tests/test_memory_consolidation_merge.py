from __future__ import annotations

import json
import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import SemanticMemory
from app.services.memory.consolidation import _apply_consolidation_actions


class ConsolidationMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_merged_sources_point_to_new_memory_id(self) -> None:
        with Session(self.engine) as session:
            first = SemanticMemory(content="用户喜欢浓咖啡", source_message_ids="[1]", importance=0.4)
            second = SemanticMemory(content="用户偏好浓咖啡", source_message_ids="[2]", importance=0.7)
            session.add_all([first, second])
            session.commit()
            ids = [first.id, second.id]

            _apply_consolidation_actions(
                session, [{"action": "merge", "ids": ids, "content": "用户喜欢浓咖啡"}]
            )

            merged = session.exec(
                select(SemanticMemory).where(SemanticMemory.status == "active")
            ).one()
            self.assertEqual(json.loads(merged.source_message_ids), [1, 2])
            for memory_id in ids:
                old = session.get(SemanticMemory, memory_id)
                self.assertEqual(old.status, "suppressed")
                self.assertIn(f"已合并到记忆 #{merged.id}", old.notes)
                self.assertNotIn("#?", old.notes)


if __name__ == "__main__":
    unittest.main()
