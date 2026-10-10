from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

from sqlmodel import Session, select

from ...database import engine
from ...models import MemoryConsolidationRun, MemoryLink, SemanticMemory, utcnow
from ..llm_service import chat_completion
from ..provider_service import get_agent_state, get_config
from .common import _age_days, _age_hours, _token_similarity


def apply_forgetting(session: Session) -> int:
    """按重要度决定半衰期；长期未被唤起的低强度记忆会休眠，而不是被物理删除。"""
    memories = session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all()
    now = utcnow()
    suppressed = 0
    for memory in memories:
        last_used = memory.last_accessed_at or memory.created_at
        age_days = _age_days(last_used)
        # 重要记忆保留更久：7 天起步，最高约 127 天
        half_life_days = 7.0 + memory.importance * 120.0
        decay = math.exp(-age_days / half_life_days)
        effective = round(memory.strength * decay, 4)
        if effective <= 0.12:
            memory.status = "suppressed"
            reason = "因长期未唤起而进入休眠"
            memory.notes = f"{memory.notes} | {reason}".strip(" |")
            suppressed += 1
        else:
            memory.strength = effective
        session.add(memory)

    state = get_agent_state(session)
    state.last_forgetting_at = now
    state.updated_at = now
    session.add(state)
    session.commit()
    return suppressed


def maybe_apply_forgetting(session: Session) -> int:
    state = get_agent_state(session)
    if state.last_forgetting_at is None or _age_hours(state.last_forgetting_at) >= 12:
        return apply_forgetting(session)
    return 0


def _parse_consolidation_actions(raw: str) -> list[dict[str, Any]]:
    raw = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            start = raw.find("{")
            end = raw.rfind("}")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("actions", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _apply_consolidation_actions(session: Session, actions: list[dict[str, Any]]) -> list[str]:
    summary: list[str] = []
    for action in actions[:30]:
        kind = str(action.get("action", "")).strip()
        try:
            if kind == "merge":
                ids = [int(i) for i in action.get("ids", [])][:6]
                memories = [session.get(SemanticMemory, i) for i in ids]
                memories = [m for m in memories if m is not None]
                if len(memories) >= 2:
                    primary = max(memories, key=lambda m: m.importance)
                    new_content = str(action.get("content", "")).strip()
                    if not new_content:
                        new_content = primary.content
                    merged = SemanticMemory(
                        perspective=primary.perspective,
                        type=primary.type,
                        content=new_content[:500],
                        source_message_ids=json.dumps(
                            sorted({i for m in memories for i in json.loads(m.source_message_ids or "[]")}),
                            ensure_ascii=False,
                        ),
                        confidence=max(m.confidence for m in memories),
                        importance=max(m.importance for m in memories),
                        valence=sum(m.valence for m in memories) / len(memories),
                        strength=max(m.strength for m in memories),
                        status="active",
                        notes="由记忆整理合并",
                    )
                    session.add(merged)
                    session.flush()
                    for m in memories:
                        m.status = "suppressed"
                        m.notes = f"{m.notes} | 已合并到记忆 #{merged.id}".strip(" |")
                        session.add(m)
                    session.flush()
                    summary.append(f"合并 {len(memories)} 条记忆为：{new_content[:40]}")
            elif kind == "suppress":
                memory_id = int(action.get("id", 0))
                memory = session.get(SemanticMemory, memory_id)
                if memory is not None and memory.status == "active":
                    memory.status = "suppressed"
                    reason = str(action.get("reason", "记忆整理")).strip()[:200]
                    memory.notes = f"{memory.notes} | 休眠：{reason}".strip(" |")
                    session.add(memory)
                    summary.append(f"休眠记忆 #{memory_id}：{reason[:40]}")
            elif kind == "link":
                source_id = int(action.get("source_id", 0))
                target_id = int(action.get("target_id", 0))
                relation = str(action.get("relation", "related")).strip()[:40] or "related"
                if source_id and target_id and source_id != target_id:
                    if session.get(SemanticMemory, source_id) and session.get(SemanticMemory, target_id):
                        session.add(
                            MemoryLink(
                                source_id=source_id,
                                target_id=target_id,
                                relation=relation,
                                weight=float(action.get("weight", 0.6)),
                            )
                        )
                        summary.append(f"建立联想 #{source_id} -> #{target_id}（{relation}）")
        except Exception:
            continue
    session.commit()
    return summary


def _rule_based_links(session: Session) -> list[dict[str, Any]]:
    memories = session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all()
    existing_links = {
        (link.source_id, link.target_id)
        for link in session.exec(select(MemoryLink)).all()
    }
    actions: list[dict[str, Any]] = []
    for i, a in enumerate(memories):
        for b in memories[i + 1 :]:
            if a.id is None or b.id is None:
                continue
            similarity = _token_similarity(a.content, b.content)
            if 0.45 <= similarity < 0.72:
                if (a.id, b.id) not in existing_links and (b.id, a.id) not in existing_links:
                    actions.append(
                        {
                            "action": "link",
                            "source_id": a.id,
                            "target_id": b.id,
                            "relation": "related",
                            "weight": round(similarity, 4),
                        }
                    )
    return actions[:20]


def _consolidate_with_session(session: Session, trigger: str) -> MemoryConsolidationRun:
    memories = session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all()
    run = MemoryConsolidationRun(trigger=trigger, status="pending")
    session.add(run)
    session.commit()
    session.refresh(run)

    if len(memories) < 3:
        run.status = "skipped"
        run.error = "活跃记忆少于 3 条，暂不整理。"
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    actions: list[dict[str, Any]] = []
    config = get_config(session)
    run.provider = config.provider
    run.model = config.model

    if config.provider == "mock" or not config.api_key:
        actions = _rule_based_links(session)
        run.status = "success" if actions else "skipped"
    else:
        memory_block = "\n".join(
            f"#{m.id} [{m.perspective}][{m.type}] strength={m.strength:.2f} access={m.access_count} "
            f"importance={m.importance:.2f} valence={m.valence:.2f} age_days={_age_days(m.created_at):.1f} "
            f"content={m.content}"
            for m in memories[:60]
        )
        system_prompt = (
            "你是桑多涅的记忆整理模块。请检查这些长期记忆，找出：\n"
            "1) 明显重复、可合并的记忆；\n"
            "2) 已经过时、相互矛盾或不再重要的记忆；\n"
            "3) 相关但尚未关联的记忆。\n"
            "不要删除记忆，只能合并或休眠。输出 JSON："
            '{"actions":[{"action":"merge","ids":[1,2],"content":"合并后的表述"},'
            '{"action":"suppress","id":3,"reason":"原因"},'
            '{"action":"link","source_id":1,"target_id":2,"relation":"related"}]}'
        )
        user_prompt = f"当前记忆：\n{memory_block}"
        try:
            result = asyncio.run(
                chat_completion(
                    session,
                    config,
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    purpose="memory_consolidation",
                    max_tokens=900,
                    temperature=0.1,
                )
            )
            actions = _parse_consolidation_actions(result["text"])
            run.status = "success" if actions else "skipped"
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error = str(exc)[:2000]
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    summary = _apply_consolidation_actions(session, actions)
    run.actions_json = json.dumps(actions, ensure_ascii=False)
    if summary:
        run.status = "success"
    elif run.status == "success":
        run.status = "skipped"
        run.error = "没有需要执行的动作。"
    session.add(run)
    session.commit()
    session.refresh(run)

    state = get_agent_state(session)
    state.last_consolidation_at = utcnow()
    session.add(state)
    session.commit()
    session.refresh(run)
    return run


def maybe_consolidate_memories(session: Session, trigger: str = "diary") -> MemoryConsolidationRun | None:
    state = get_agent_state(session)
    active_count = len(session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all())
    if active_count < 3:
        return None
    if state.last_consolidation_at is not None and _age_hours(state.last_consolidation_at) < 20:
        return None
    return _consolidate_with_session(session, trigger)


def run_consolidation_now() -> MemoryConsolidationRun:
    with Session(engine) as session:
        return _consolidate_with_session(session, trigger="manual")
