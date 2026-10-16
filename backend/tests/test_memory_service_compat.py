from __future__ import annotations

import unittest

from sqlmodel import Session, SQLModel, create_engine

from app.models import SemanticMemory
from app.services import memory_service


class MemoryServiceCompatibilityTests(unittest.TestCase):
    def test_rule_scores_and_llm_parsing(self) -> None:
        self.assertGreaterEqual(memory_service.estimate_importance("请记住我喜欢机械"), 0.9)
        self.assertGreater(memory_service.estimate_valence("我很喜欢，真的很好"), 0)
        self.assertLess(memory_service.estimate_valence("我讨厌这个，令人失望"), 0)

        parsed = memory_service._parse_llm_memories(
            '```json\n{"memories":[{"perspective":"self","type":"feeling",'
            '"content":"我开始在意这段对话。","importance":0.8,"confidence":0.7,"valence":0.2}]}\n```'
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["perspective"], "self")
        self.assertEqual(parsed[0]["type"], "preference")

    def test_crud_and_retrieval_with_isolated_sqlite(self) -> None:
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            first = SemanticMemory(
                perspective="user",
                type="preference",
                content="用户喜欢机械研究。",
                confidence=0.9,
                importance=0.9,
            )
            second = SemanticMemory(
                perspective="user",
                type="fact",
                content="用户今天喝了咖啡。",
                confidence=0.7,
                importance=0.3,
            )
            session.add(first)
            session.add(second)
            session.commit()
            session.refresh(first)

            memories = memory_service.list_memories(session)
            self.assertEqual([memory.content for memory in memories], [first.content, second.content])

            scored = memory_service.retrieve_semantic_memories_scored(
                session,
                "机械研究",
            )
            self.assertEqual(scored[0][1].id, first.id)

            updated = memory_service.update_memory(session, first, {"content": "用户喜欢机械与数理研究。"})
            self.assertEqual(updated.content, "用户喜欢机械与数理研究。")

            deleted = memory_service.soft_delete_memory(session, updated)
            self.assertEqual(deleted.status, "deleted")


if __name__ == "__main__":
    unittest.main()
