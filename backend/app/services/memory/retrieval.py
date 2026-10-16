from __future__ import annotations

import math
from typing import Any

from sqlmodel import Session, select

from ...config import EPISODIC_RECALL_LIMIT, SEMANTIC_MEMORY_LIMIT
from ...models import AgentDiary, MemoryLink, MemoryRedaction, Message, SemanticMemory
from ..games.scope import active_game_scopes
from ..provider_service import get_agent_state
from .common import _age_days, _dt, effective_strength, tokenize
from .embedding import semantic_relevance, semantic_scores

GAME_SCOPE_BOOST = 0.12
GAME_SCOPE_PENALTY = 0.4
GAME_OUT_OF_SCOPE_MIN_MATCH = 0.2
RELEVANCE_WEIGHT = 0.65  # 相关度占比；原为 0.44，在召回评测上 hit@3 0.23→0.37（纯分词）


def memory_relevance(
    query_tokens: frozenset[str], query_norm: str, memory: SemanticMemory, cosine: float | None
) -> tuple[float, int, float, float]:
    """这句话和一条记忆有多相关：返回 (相关度, 重合词数, 子串加分, 语义相关度)。"""
    tokens = tokenize(memory.content)
    overlap = len(query_tokens & tokens)
    jaccard = overlap / max(len(query_tokens | tokens), 1)
    substring_boost = 0.0
    if query_norm and query_norm in (memory.content or "").lower():
        substring_boost = 0.35
    elif (memory.content or "").lower() in query_norm and len(memory.content) >= 4:
        substring_boost = 0.25
    semantic = semantic_relevance(cosine)
    return max(min(jaccard + substring_boost, 1.0), semantic), overlap, substring_boost, semantic


def retrieve_semantic_memories_scored(
    session: Session,
    query: str,
    *,
    limit: int = SEMANTIC_MEMORY_LIMIT,
    perspectives: tuple[str, ...] | None = None,
    scopes: set[str] | None = None,
) -> list[tuple[float, SemanticMemory]]:
    query_tokens = tokenize(query)
    # 游戏记忆只在那个游戏的语境里加分；聊别的时降权，避免把游戏细节带进日常对话
    if scopes is None:
        scopes = active_game_scopes(session, query)
    statement = select(SemanticMemory).where(SemanticMemory.status == "active")
    if perspectives:
        statement = statement.where(SemanticMemory.perspective.in_(perspectives))
    memories = session.exec(
        statement.order_by(SemanticMemory.updated_at.desc())
    ).all()

    # 按意思的相似度（有本地向量模型时）；和字面匹配取较大者，字面命中的老行为不变
    cosines = semantic_scores(session, query, list(memories)) or {}
    scored: list[tuple[float, SemanticMemory]] = []
    query_norm = (query or "").lower()
    for memory in memories:
        relevance, overlap, substring_boost, semantic = memory_relevance(
            query_tokens, query_norm, memory, cosines.get(memory.id) if memory.id is not None else None
        )
        recency = math.exp(-_age_days(memory.created_at) / 21.0)
        access = min(math.log1p(memory.access_count) / 6.0, 1.0)
        emotion_salience = min(abs(memory.valence), 1.0)
        prior = (
            0.22 * memory.importance
            + 0.12 * recency
            + 0.08 * access
            + 0.08 * effective_strength(memory)
            + 0.06 * emotion_salience
        ) / 0.56
        score = RELEVANCE_WEIGHT * relevance + (1 - RELEVANCE_WEIGHT) * prior
        if memory.scope:
            if memory.scope in scopes:
                score += GAME_SCOPE_BOOST
            elif relevance < GAME_OUT_OF_SCOPE_MIN_MATCH:
                continue  # 不在这个游戏的语境、内容也不相关：不唤起
            else:
                score *= GAME_SCOPE_PENALTY
        if overlap == 0 and not substring_boost and not semantic and score < 0.25:
            continue
        scored.append((score, memory))

    scored.sort(key=lambda item: item[0], reverse=True)
    # 只读：被检索到不算强化，否则常被想起的记忆会越用越强、其余的被挤到休眠
    return scored[:limit]


def retrieve_semantic_memories(
    session: Session,
    query: str,
    *,
    limit: int = SEMANTIC_MEMORY_LIMIT,
    perspectives: tuple[str, ...] | None = None,
) -> list[SemanticMemory]:
    return [
        memory
        for _, memory in retrieve_semantic_memories_scored(
            session,
            query,
            limit=limit,
            perspectives=perspectives,
        )
    ]


def retrieve_diary(session: Session, *, limit: int = 2) -> list[AgentDiary]:
    return list(
        session.exec(
            select(AgentDiary).order_by(AgentDiary.created_at.desc(), AgentDiary.id.desc()).limit(limit)
        ).all()
    )


def retrieve_episodic_memories(
    session: Session,
    query: str,
    *,
    recent_message_ids: set[int],
    limit: int = EPISODIC_RECALL_LIMIT,
) -> list[Message]:
    query_tokens = tokenize(query)
    all_messages = session.exec(
        select(Message)
        .where(Message.channel.in_(("web", "wechat_dm")))
        .order_by(Message.created_at.desc())
        .limit(800)
    ).all()

    # 用户要求忘掉或纠正过的内容，原话不再被当作旧对话想起
    redacted = set(session.exec(select(MemoryRedaction.message_id)).all())
    scored: list[tuple[float, Message]] = []
    for message in all_messages:
        if message.id in recent_message_ids or message.id in redacted:
            continue
        tokens = tokenize(message.content)
        jaccard = len(query_tokens & tokens) / max(len(query_tokens | tokens), 1)
        recency = math.exp(-_age_days(message.created_at) / 28.0)
        importance = message.importance
        score = 0.55 * jaccard + 0.20 * recency + 0.25 * importance
        if jaccard > 0 or importance >= 0.7:
            scored.append((score, message))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [message for _, message in scored[:limit]]


def retrieve_associative_memories(
    session: Session,
    seeds: list[SemanticMemory],
    *,
    limit: int = 6,
    max_hops: int = 2,
) -> list[tuple[float, SemanticMemory, str, int]]:
    """海马体式索引 v1：从已命中的记忆出发，沿 memory_links 多跳联想。

    返回 (score, memory, relation, hop)，按联想分降序，且不包含种子记忆。
    """
    seed_ids = {m.id for m in seeds if m.id is not None}
    if not seed_ids:
        return []

    all_links = session.exec(select(MemoryLink)).all()
    adjacency: dict[int, list[tuple[int, str, float]]] = {}
    for link in all_links:
        if link.source_id is not None and link.target_id is not None:
            adjacency.setdefault(link.source_id, []).append(
                (link.target_id, link.relation or "related", link.weight)
            )
            adjacency.setdefault(link.target_id, []).append(
                (link.source_id, link.relation or "related", link.weight)
            )

    active_by_id: dict[int, SemanticMemory] = {
        m.id: m
        for m in session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all()
        if m.id is not None
    }

    best: dict[int, tuple[float, str, int]] = {}
    frontier: set[int] = set(seed_ids)
    visited: set[int] = set(seed_ids)
    for hop in range(1, max_hops + 1):
        next_frontier: set[int] = set()
        for node in frontier:
            for neighbor, relation, weight in adjacency.get(node, []):
                if neighbor in visited:
                    continue
                next_frontier.add(neighbor)
                visited.add(neighbor)
                memory = active_by_id.get(neighbor)
                if memory is None:
                    continue
                recency = math.exp(-_age_days(memory.created_at) / 21.0)
                score = weight * (0.55 + 0.45 * memory.importance) * (0.7 + 0.3 * recency)
                if neighbor not in best or score > best[neighbor][0]:
                    best[neighbor] = (score, relation, hop)
        frontier = next_frontier

    ranked = sorted(
        (
            (score, active_by_id[mid], relation, hop)
            for mid, (score, relation, hop) in best.items()
            if mid in active_by_id
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return ranked[:limit]


def _memory_trace(
    layer: str,
    content: str,
    *,
    perspective: str = "",
    memory_type: str = "",
    score: float = 0.0,
    source_message_id: int | None = None,
) -> dict[str, Any]:
    return {
        "layer": layer,
        "perspective": perspective,
        "memory_type": memory_type,
        "content": content[:600],
        "score": round(score, 4),
        "source_message_id": source_message_id,
    }


def build_memory_context_with_traces(
    session: Session,
    query: str,
    recent_message_ids: set[int],
) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    traces: list[dict[str, Any]] = []

    state = get_agent_state(session)
    parts.append(
        "## 你此刻的状态（这是你自己的感受，不要向用户报数）\n"
        f"- 情绪：{state.mood}；精力：{state.energy:.0%}；你正在想：{state.focus or '眼前的对话'}"
    )
    traces.append(
        _memory_trace(
            "state",
            f"情绪：{state.mood} · 精力：{state.energy:.0%} · 正在想：{state.focus or '眼前的对话'}",
        )
    )

    semantic = retrieve_semantic_memories_scored(session, query, limit=SEMANTIC_MEMORY_LIMIT + 2)
    if semantic:
        groups: dict[str, list[str]] = {"user": [], "self": [], "shared": []}
        for score, memory in semantic:
            label = {
                "fact": "事实",
                "preference": "偏好",
                "relationship": "关系",
                "project": "项目",
                "event": "经历",
            }.get(memory.type, memory.type)
            groups.setdefault(memory.perspective, []).append(
                f"- [{label}] {memory.content}（置信度 {memory.confidence:.0%}）"
            )
            traces.append(
                _memory_trace(
                    "semantic",
                    memory.content,
                    perspective=memory.perspective,
                    memory_type=memory.type,
                    score=score,
                )
            )
        section_lines: list[str] = []
        if groups.get("user"):
            section_lines.append("关于用户：\n" + "\n".join(groups["user"]))
        if groups.get("self"):
            section_lines.append("关于你自己：\n" + "\n".join(groups["self"]))
        if groups.get("shared"):
            section_lines.append("你们之间：\n" + "\n".join(groups["shared"]))
        if section_lines:
            parts.append(
                "## 长期记忆（你自然记得的事；按语境使用，不要逐条背诵）\n"
                + "\n\n".join(section_lines)
            )

    associative = retrieve_associative_memories(
        session,
        [memory for _, memory in semantic],
        limit=6,
    )
    if associative:
        lines = []
        for score, memory, relation, hop in associative:
            lines.append(
                f"- [{relation}] {memory.content}（联想 {hop} 跳）"
            )
            traces.append(
                _memory_trace(
                    "associative",
                    memory.content,
                    perspective=memory.perspective,
                    memory_type=memory.type,
                    score=score,
                )
            )
        parts.append(
            "## 联想记忆（海马体索引：由上面的记忆自然联想出来，按需使用）\n"
            + "\n".join(lines)
        )

    diary = retrieve_diary(session, limit=2)
    if diary:
        lines = []
        for entry in reversed(diary):
            content = entry.content.replace("\n", " ")
            if len(content) > 160:
                content = content[:160] + "…"
            ts = _dt(entry.created_at)
            stamp = ts.strftime("%m月%d日") if ts else ""
            lines.append(f"- {stamp}：{content}")
            traces.append(
                _memory_trace(
                    "diary",
                    entry.summary or content[:160],
                    memory_type="diary",
                )
            )
        parts.append("## 你最近的内心记录（只供你自己参考，不要原样引用）\n" + "\n".join(lines))

    episodic = retrieve_episodic_memories(session, query, recent_message_ids=recent_message_ids)
    if episodic:
        lines = []
        for message in sorted(episodic, key=lambda m: m.created_at):
            ts = _dt(message.created_at)
            stamp = ts.strftime("%Y-%m-%d %H:%M") if ts else ""
            speaker = "用户" if message.role == "user" else "桑多涅"
            excerpt = message.content.replace("\n", " ")
            if len(excerpt) > 120:
                excerpt = excerpt[:120] + "…"
            lines.append(f"- {stamp} {speaker}：{excerpt}")
            traces.append(
                _memory_trace(
                    "episodic",
                    f"{stamp} {speaker}：{excerpt}",
                    memory_type="event",
                    source_message_id=message.id,
                )
            )
        parts.append("## 被唤起的旧对话片段（可在相关时自然提到，不要声称完整记得每个字）\n" + "\n".join(lines))

    return "\n\n".join(parts), traces


def build_memory_context(session: Session, query: str, recent_message_ids: set[int]) -> str:
    context, _ = build_memory_context_with_traces(session, query, recent_message_ids)
    return context
