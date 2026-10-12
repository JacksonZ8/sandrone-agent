"""本地中文向量模型（bge-small-zh-v1.5，ONNX）：让记忆检索按意思匹配，而不只是字面重合。

“今天好累”和“他最近工作压力很大”没有共同的词，分词匹配想不起来；向量相似度可以。
模型由 scripts/fetch_memory_model.sh 下载；没有模型时 available() 为 False，检索退回纯分词匹配。
分词器是 BERT 的 BasicTokenizer + WordPiece 的纯 Python 实现（与 HuggingFace tokenizers 逐词一致，
见 tests/test_memory_embedding.py），不额外引入依赖。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ...config import EMBEDDING_MODEL_DIR
from ...models import MemoryEmbedding, SemanticMemory

logger = logging.getLogger(__name__)

MODEL_NAME = "bge-small-zh-v1.5"
MAX_TOKENS = 256  # 记忆和一句话都很短；超过的截断
# 余弦相似度换算成 0–1 的“相关度”：在真实记忆上量过，无关句子中位数约 0.37、九成低于 0.46，
# 相关句子中位数约 0.56（scripts/eval_memory_recall.py）
SEMANTIC_FLOOR = 0.42
SEMANTIC_CEIL = 0.68
# 两条记忆余弦不低于这个值就建联想边：真实记忆两两之间只有约 3% 的对超过它，且都是同一件事的不同侧面
LINK_COSINE = 0.70


def _is_cjk(code: int) -> bool:
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
        or 0x2F800 <= code <= 0x2FA1F
    )


def _is_punctuation(char: str) -> bool:
    code = ord(char)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(char).startswith("P")


def _is_control(char: str) -> bool:
    if char in "\t\n\r":
        return False
    return unicodedata.category(char) in ("Cc", "Cf")


class BertTokenizer:
    """bge-small-zh 的分词：不转小写、不去重音，汉字逐字切开，标点单独成词，再做 WordPiece。"""

    def __init__(self, vocab_path: Path) -> None:
        tokens = vocab_path.read_text(encoding="utf-8").split("\n")
        if tokens and tokens[-1] == "":
            tokens.pop()
        self.vocab = {token: index for index, token in enumerate(tokens)}
        self.unk = self.vocab["[UNK]"]
        self.cls = self.vocab["[CLS]"]
        self.sep = self.vocab["[SEP]"]

    def _basic(self, text: str) -> list[str]:
        cleaned = []
        for char in text:
            code = ord(char)
            if code == 0 or code == 0xFFFD or _is_control(char):
                continue
            if char.isspace() or unicodedata.category(char) == "Zs":
                cleaned.append(" ")
            elif _is_cjk(code):
                cleaned.append(f" {char} ")
            else:
                cleaned.append(char)
        words: list[str] = []
        for chunk in "".join(cleaned).split():
            current = ""
            for char in chunk:
                if _is_punctuation(char):
                    if current:
                        words.append(current)
                        current = ""
                    words.append(char)
                else:
                    current += char
            if current:
                words.append(current)
        return words

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self.unk]
        pieces: list[int] = []
        start = 0
        while start < len(word):
            end = len(word)
            found = None
            while start < end:
                piece = word[start:end] if start == 0 else "##" + word[start:end]
                if piece in self.vocab:
                    found = self.vocab[piece]
                    break
                end -= 1
            if found is None:
                return [self.unk]
            pieces.append(found)
            start = end
        return pieces

    def encode(self, text: str, max_tokens: int = MAX_TOKENS) -> list[int]:
        ids = [piece for word in self._basic(text) for piece in self._wordpiece(word)]
        return [self.cls, *ids[: max_tokens - 2], self.sep]


class Embedder:
    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2  # 别和通话里的识别、断句抢 CPU
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = BertTokenizer(model_dir / "vocab.txt")
        self.lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, 512) 的单位向量；按 bge 的做法取 [CLS] 位置。"""
        if not texts:
            return np.zeros((0, 512), dtype=np.float32)
        encoded = [self.tokenizer.encode(text or "") for text in texts]
        width = max(len(ids) for ids in encoded)
        input_ids = np.zeros((len(encoded), width), dtype=np.int64)
        mask = np.zeros_like(input_ids)
        for row, ids in enumerate(encoded):
            input_ids[row, : len(ids)] = ids
            mask[row, : len(ids)] = 1
        with self.lock:
            (hidden,) = self.session.run(
                ["last_hidden_state"],
                {"input_ids": input_ids, "attention_mask": mask, "token_type_ids": np.zeros_like(input_ids)},
            )
        vectors = hidden[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)


_EMBEDDER: Embedder | None = None
_FAILED = False
_LOAD_LOCK = threading.Lock()


def get_embedder(model_dir: Path = EMBEDDING_MODEL_DIR) -> Embedder | None:
    """懒加载；模型缺失或加载失败时返回 None（只记一次日志），检索退回分词匹配。"""
    global _EMBEDDER, _FAILED
    if _EMBEDDER is not None or _FAILED:
        return _EMBEDDER
    with _LOAD_LOCK:
        if _EMBEDDER is None and not _FAILED:
            if not (model_dir / "model.onnx").is_file() or not (model_dir / "vocab.txt").is_file():
                _FAILED = True
                logger.info("没有向量模型（%s），记忆检索只用分词匹配", model_dir)
            else:
                try:
                    _EMBEDDER = Embedder(model_dir)
                except Exception:  # noqa: BLE001
                    _FAILED = True
                    logger.exception("向量模型加载失败，记忆检索只用分词匹配")
    return _EMBEDDER


def available() -> bool:
    return get_embedder() is not None


def embed_texts(texts: list[str]) -> np.ndarray | None:
    embedder = get_embedder()
    return None if embedder is None else embedder.embed(texts)


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


@lru_cache(maxsize=256)
def embed_query(text: str) -> np.ndarray | None:
    """一句话的向量（同一句在一轮里会被检索好几次，缓存起来）。"""
    embedder = get_embedder()
    if embedder is None:
        return None
    return embedder.embed([text])[0]


def warm_up() -> None:
    """后台预载模型并补齐记忆向量缓存（首次约 1 秒），免得第一条消息等它。"""
    embedder = get_embedder()
    if embedder is None:
        return
    embedder.embed(["你好"])
    from ...database import engine

    with Session(engine) as session:
        memories = list(session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all())
        memory_vectors(session, memories)


def semantic_relevance(cosine: float | None) -> float:
    if cosine is None:
        return 0.0
    return min(max((cosine - SEMANTIC_FLOOR) / (SEMANTIC_CEIL - SEMANTIC_FLOOR), 0.0), 1.0)


def memory_vectors(session: Session, memories: list[SemanticMemory]) -> dict[int, np.ndarray] | None:
    """取记忆向量：优先用缓存，没算过或内容变了的一次性补算并写回。没有模型时返回 None。"""
    embedder = get_embedder()
    if embedder is None:
        return None
    ids = [memory.id for memory in memories if memory.id is not None]
    cached = {
        row.memory_id: row
        for row in session.exec(select(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(ids))).all()
    }
    vectors: dict[int, np.ndarray] = {}
    stale: list[tuple[int, str, str, MemoryEmbedding | None]] = []
    for memory in memories:
        if memory.id is None:
            continue
        row = cached.get(memory.id)
        digest = content_hash(memory.content)
        if row is not None and row.model == MODEL_NAME and row.content_hash == digest:
            vectors[memory.id] = np.frombuffer(row.vector, dtype=np.float32)
        else:
            stale.append((memory.id, memory.content, digest, row))
    if stale:
        fresh = embedder.embed([content for _, content, _, _ in stale])
        for (memory_id, _, digest, row), vector in zip(stale, fresh):
            row = row or MemoryEmbedding(memory_id=memory_id, model=MODEL_NAME, content_hash=digest, vector=b"")
            row.model, row.content_hash, row.vector = MODEL_NAME, digest, vector.astype(np.float32).tobytes()
            session.add(row)
            vectors[memory_id] = vector
        try:
            session.commit()
        except IntegrityError:  # 另一个线程刚写入同一条；向量照用，缓存下次再写
            session.rollback()
    return vectors


def semantic_scores(session: Session, query: str, memories: list[SemanticMemory]) -> dict[int, float] | None:
    """每条记忆与这句话的余弦相似度；没有模型或没有内容时返回 None。"""
    if not (query or "").strip() or not memories:
        return None
    query_vector = embed_query(query)
    if query_vector is None:
        return None
    vectors = memory_vectors(session, memories)
    if vectors is None:
        return None
    return {memory_id: float(vector @ query_vector) for memory_id, vector in vectors.items()}


def similar_memory_pairs(
    session: Session, memories: list[SemanticMemory], *, exclude: set[frozenset[int]], limit: int = 20
) -> list[tuple[float, int, int]]:
    """意思相近、还没连过的记忆对 (cos, a, b)，按相似度降序。没有模型时为空。"""
    vectors = memory_vectors(session, memories)
    if not vectors:
        return []
    ids = list(vectors)
    matrix = np.stack([vectors[memory_id] for memory_id in ids])
    similarity = matrix @ matrix.T
    pairs = [
        (float(similarity[i, j]), ids[i], ids[j])
        for i in range(len(ids))
        for j in range(i + 1, len(ids))
        if similarity[i, j] >= LINK_COSINE and frozenset((ids[i], ids[j])) not in exclude
    ]
    pairs.sort(reverse=True)
    return pairs[:limit]
