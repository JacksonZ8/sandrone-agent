from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import EMBEDDING_MODEL_DIR
from app.models import MemoryEmbedding, MemoryExtractionRun, MemoryLink, SemanticMemory
from app.services.memory import embedding
from app.services.memory.consolidation import _consolidate_with_session
from app.services.memory.extraction import _save_candidates
from app.services.memory.retrieval import retrieve_semantic_memories_scored

FIXTURE = Path(__file__).parent / "fixtures" / "bge_tokenizer_cases.json"
HAS_MODEL = (EMBEDDING_MODEL_DIR / "model.onnx").is_file()

# 假向量模型：同一主题的句子向量相同，不同主题正交；没有主题词的句子落在第 0 维
TOPICS = {"累": 1, "焦虑": 1, "工作": 1, "面试": 1, "猫": 2, "狗": 2, "宠物": 2, "游戏": 3, "肉鸽": 3}


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        vectors = np.zeros((len(texts), 4), dtype=np.float32)
        for row, text in enumerate(texts):
            hits = {topic for word, topic in TOPICS.items() if word in text}
            vectors[row, min(hits) if hits else 0] = 1.0
        return vectors


class TokenizerTests(unittest.TestCase):
    @unittest.skipUnless((EMBEDDING_MODEL_DIR / "vocab.txt").is_file(), "没有向量模型（scripts/fetch_memory_model.sh）")
    def test_matches_huggingface_tokenizer(self) -> None:
        tokenizer = embedding.BertTokenizer(EMBEDDING_MODEL_DIR / "vocab.txt")
        for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]:
            self.assertEqual(tokenizer.encode(case["text"], 10**6), case["ids"], case["text"])

    @unittest.skipUnless(HAS_MODEL, "没有向量模型（scripts/fetch_memory_model.sh）")
    def test_real_model_matches_meaning_not_words(self) -> None:
        model = embedding.Embedder(EMBEDDING_MODEL_DIR)
        query, related, unrelated = model.embed(
            ["我想给你加个陪我玩游戏的功能", "用户打算给助手增加一个陪玩游戏的功能", "用户喜欢安静的界面"]
        )
        self.assertAlmostEqual(float(np.linalg.norm(query)), 1.0, places=4)
        self.assertGreater(float(query @ related), embedding.SEMANTIC_FLOOR + 0.2)
        self.assertLess(float(query @ unrelated), embedding.SEMANTIC_FLOOR + 0.05)


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.fake = FakeEmbedder()
        self.patch = patch.object(embedding, "get_embedder", return_value=self.fake)
        self.patch.start()
        embedding.embed_query.cache_clear()

    def tearDown(self) -> None:
        self.patch.stop()
        embedding.embed_query.cache_clear()
        self.engine.dispose()

    def _add(self, session: Session, content: str, importance: float = 0.5) -> SemanticMemory:
        memory = SemanticMemory(content=content, importance=importance)
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return memory

    def test_recalls_by_meaning_without_shared_words(self) -> None:
        with Session(self.engine) as session:
            job = self._add(session, "他在找工作，最近面试很多")
            self._add(session, "他喜欢深蓝色的界面", importance=0.9)
            ranked = retrieve_semantic_memories_scored(session, "今天好累")
            self.assertEqual(ranked[0][1].id, job.id)
            with patch.object(embedding, "get_embedder", return_value=None):
                embedding.embed_query.cache_clear()
                lexical = retrieve_semantic_memories_scored(session, "今天好累")
            self.assertNotIn(job.id, [memory.id for _, memory in lexical])  # 纯分词想不起来

    def test_vectors_are_cached_and_recomputed_when_content_changes(self) -> None:
        with Session(self.engine) as session:
            memory = self._add(session, "他养了一只猫")
            retrieve_semantic_memories_scored(session, "宠物")
            retrieve_semantic_memories_scored(session, "宠物还好吗")
            memory_batches = [batch for batch in self.fake.calls if "他养了一只猫" in batch]
            self.assertEqual(len(memory_batches), 1)  # 第二次检索用缓存
            memory.content = "他工作压力很大"
            session.add(memory)
            session.commit()
            ranked = retrieve_semantic_memories_scored(session, "好累")
            self.assertEqual(ranked[0][1].id, memory.id)
            row = session.get(MemoryEmbedding, memory.id)
            self.assertEqual(row.content_hash, embedding.content_hash("他工作压力很大"))

    def test_new_memory_links_to_old_one_with_same_meaning(self) -> None:
        with Session(self.engine) as session:
            old = self._add(session, "他家里有只狗")
            run = MemoryExtractionRun(source_message_ids="[]", status="pending")
            session.add(run)
            session.commit()
            candidate = {"content": "他说想养宠物", "type": "fact", "confidence": 0.8, "importance": 0.6, "perspective": "user"}
            self.assertEqual(_save_candidates(session, [candidate], [], extraction_run=run), 1)
            link = session.exec(select(MemoryLink)).one()
            self.assertEqual(link.target_id, old.id)
            self.assertAlmostEqual(link.weight, 1.0)

    def test_consolidation_backfills_links_once(self) -> None:
        with Session(self.engine) as session:
            a = self._add(session, "他工作压力很大")
            b = self._add(session, "他最近很累")
            self._add(session, "他养了一只猫")
            _consolidate_with_session(session, "manual")
            links = session.exec(select(MemoryLink)).all()
            self.assertEqual([(link.source_id, link.target_id, link.relation) for link in links], [(a.id, b.id, "similar")])
            _consolidate_with_session(session, "manual")
            self.assertEqual(len(session.exec(select(MemoryLink)).all()), 1)


if __name__ == "__main__":
    unittest.main()
