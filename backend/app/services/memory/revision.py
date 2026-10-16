"""对话里的“忘掉它”和“你记错了”。

- 忘掉：相关的长期记忆改为 forgotten（不物理删除，记录还在库里），它们的来源消息不再被当作旧对话想起。
- 纠正：记错的那条改为 corrected，另存一条正确的新记忆。
- 这一轮本身不做记忆提取，用户提出请求的那句话也不再被想起，免得把要忘的内容又记回来。
- 防止“复活”：来源早于遗忘/纠正的提取结果（例如还在后台跑的上一轮提取）如果和已忘掉的内容重复，就丢弃；
  之后用户自己再说起，照常记住。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, func, select

from ...models import MemoryRedaction, Message, SemanticMemory, utcnow
from .common import _token_similarity, tokenize
from .embedding import semantic_scores
from .retrieval import memory_relevance

MATCH_RELEVANCE = 0.5      # 至少这么相关才算“说的就是这条”
MAX_FORGET = 5             # 一次最多忘掉几条
TOMBSTONES = ("forgotten", "corrected")
REVISION_TOOLS = ("forget_memory", "correct_memory")
DUPLICATE_SIMILARITY = 0.72  # 与 extraction 的去重阈值一致


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def matching_memories(session: Session, query: str, *, limit: int = MAX_FORGET) -> list[tuple[float, SemanticMemory]]:
    """只按相关度找用户说的是哪几条（活跃和休眠的都算），不看重要度和新旧。"""
    memories = list(
        session.exec(select(SemanticMemory).where(SemanticMemory.status.in_(("active", "suppressed")))).all()
    )
    if not memories or not (query or "").strip():
        return []
    cosines = semantic_scores(session, query, memories) or {}
    query_tokens, query_norm = tokenize(query), query.lower()
    scored = []
    for memory in memories:
        relevance, *_ = memory_relevance(query_tokens, query_norm, memory, cosines.get(memory.id))
        if relevance >= MATCH_RELEVANCE:
            scored.append((relevance, memory))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def redact_messages(session: Session, message_ids: list[int], reason: str) -> None:
    known = set(session.exec(select(MemoryRedaction.message_id).where(MemoryRedaction.message_id.in_(message_ids))).all())
    for message_id in dict.fromkeys(message_ids):
        if message_id not in known:
            session.add(MemoryRedaction(message_id=message_id, reason=reason[:200]))


def _source_ids(memory: SemanticMemory) -> list[int]:
    try:
        return [int(value) for value in json.loads(memory.source_message_ids or "[]")]
    except (TypeError, ValueError):
        return []


def _tombstone(memory: SemanticMemory, status: str, note: str) -> None:
    memory.status = status
    memory.notes = f"{memory.notes} | {note}".strip(" |")
    memory.updated_at = utcnow()


def forget_memories(session: Session, query: str) -> dict[str, Any]:
    """forget_memory 工具：忘掉和 query 相关的长期记忆。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "没说要忘掉什么。"}
    matches = matching_memories(session, query)
    if not matches:
        return {"ok": True, "forgotten": 0, "message": "长期记忆里没有找到相关的内容；之后不会主动记下这件事。"}
    for _, memory in matches:
        _tombstone(memory, "forgotten", f"用户在对话中要求遗忘：{query[:60]}")
        session.add(memory)
        redact_messages(session, _source_ids(memory), "forget")
    session.commit()
    return {
        "ok": True,
        "forgotten": len(matches),
        "message": f"已忘掉 {len(matches)} 条相关记忆，相关的旧对话也不会再被想起。简短确认一句即可，不要复述细节。",
    }


def correct_memory(session: Session, wrong: str, correct: str) -> dict[str, Any]:
    """correct_memory 工具：把记错的那条换成正确的。"""
    wrong, correct = (wrong or "").strip(), (correct or "").strip()
    if not wrong or not correct:
        return {"ok": False, "error": "需要同时说明记错的内容和正确的内容。"}
    matches = matching_memories(session, wrong, limit=3)
    if not matches:
        return {"ok": False, "error": "没找到记错的那条记忆。可以请用户说得更具体一些。"}
    if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.1:
        options = "；".join(memory.content[:40] for _, memory in matches)
        return {"ok": False, "error": f"有几条都可能是：{options}。请用户说清是哪一条。"}
    old = matches[0][1]
    fresh = SemanticMemory(
        perspective=old.perspective,
        type=old.type,
        content=correct[:500],
        source_message_ids=old.source_message_ids,
        confidence=0.95,
        importance=max(old.importance, 0.6),
        valence=old.valence,
        strength=1.0,
        status="active",
        scope=old.scope,
        notes=f"由用户纠正，替换记忆 #{old.id}",
    )
    session.add(fresh)
    session.flush()
    _tombstone(old, "corrected", f"用户纠正为记忆 #{fresh.id}")
    session.add(old)
    redact_messages(session, _source_ids(old), "correct")
    session.commit()
    return {"ok": True, "corrected": old.id, "replacement": fresh.id, "message": "已改正。简短确认一句即可。"}


def finish_revision_turn(session: Session, user_message: Message, assistant_message: Message | None) -> bool:
    """这一轮调用过忘掉/纠正：不做记忆提取，提出请求的那句话也不再被想起。返回是否属于这种回合。"""
    if assistant_message is None:
        return False
    try:
        calls = json.loads(assistant_message.tool_calls_json or "[]")
    except (TypeError, ValueError):
        return False
    if not any(isinstance(call, dict) and call.get("name") in REVISION_TOOLS for call in calls):
        return False
    if user_message.id is not None:
        redact_messages(session, [user_message.id], "revision_request")
        session.commit()
    return True


def is_resurrection(session: Session, content: str, source_ids: list[int]) -> bool:
    """来源早于某次遗忘/纠正、内容又和被忘掉的那条重复：这是旧材料把它带回来了。"""
    if not source_ids:
        return False
    newest = session.exec(select(func.max(Message.created_at)).where(Message.id.in_(source_ids))).one()
    newest = _aware(newest)
    if newest is None:
        return False
    for memory in session.exec(select(SemanticMemory).where(SemanticMemory.status.in_(TOMBSTONES))).all():
        if _aware(memory.updated_at) >= newest and _token_similarity(content, memory.content) >= DUPLICATE_SIMILARITY:
            return True
    return False
