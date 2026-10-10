from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlmodel import Session, select

from ...database import engine
from ...models import MemoryExtractionRun, MemoryLink, Message, SemanticMemory
from ..games.scope import message_scope
from ..llm_service import chat_completion
from ..provider_service import get_config
from .common import PERSPECTIVES, _persona_text, _token_similarity, estimate_valence
from .diary import maybe_write_diary
from .retrieval import retrieve_semantic_memories


def _is_extraction_worthwhile(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if len(text) < 4 and not any(k in text for k in ("好", "嗯", "记住", "我叫")):
        return False
    if any(k in text for k in ("记住", "别忘了", "我叫", "我喜欢", "我不喜欢", "我讨厌", "偏好", "项目", "计划")):
        return True
    return len(text) >= 12


def _heuristic_candidates(user_text: str) -> list[dict[str, Any]]:
    """无 LLM 或 LLM 失败时的规则兜底。只提取最可靠的用户视角事实。"""
    candidates: list[dict[str, Any]] = []
    text = (user_text or "").strip()

    name_match = re.search(r"(?:我叫|我的名字(?:是|叫)|你可以叫我)\s*([^\s，。！？,.!?]{1,16})", text)
    if name_match:
        candidates.append(
            {
                "perspective": "user",
                "type": "fact",
                "content": f"用户希望被称为「{name_match.group(1)}」。",
                "importance": 0.85,
                "confidence": 0.95,
                "valence": 0.0,
            }
        )

    for prefix in ("我喜欢", "我不喜欢", "我讨厌", "我更偏好", "我偏好"):
        if prefix in text:
            candidates.append(
                {
                    "perspective": "user",
                    "type": "preference",
                    "content": f"用户表达过：{text}",
                    "importance": 0.65,
                    "confidence": 0.8,
                    "valence": estimate_valence(text),
                }
            )
            break

    if any(k in text for k in ("记住", "别忘了", "以后都")):
        candidates.append(
            {
                "perspective": "user",
                "type": "preference",
                "content": f"用户要求长期遵守：{text}",
                "importance": 0.8,
                "confidence": 0.85,
                "valence": 0.0,
            }
        )

    if any(k in text for k in ("这个项目", "我们的项目", "桑多涅项目", "计划")):
        candidates.append(
            {
                "perspective": "user",
                "type": "project",
                "content": f"用户提到当前项目进展：{text}",
                "importance": 0.6,
                "confidence": 0.75,
                "valence": estimate_valence(text),
            }
        )

    for cue in ("今天", "昨天", "最近", "刚才"):
        if cue in text and len(text) >= 16:
            candidates.append(
                {
                    "perspective": "user",
                    "type": "event",
                    "content": f"用户分享了一段经历：{text}",
                    "importance": 0.45,
                    "confidence": 0.7,
                    "valence": estimate_valence(text),
                }
            )
            break
    return candidates


def _normalize_memory_type(value: str) -> str:
    value = (value or "").strip().lower()
    aliases = {
        "fact": "fact",
        "factual": "fact",
        "action": "event",
        "activity": "event",
        "occurrence": "event",
        "story": "event",
        "experience": "event",
        "event": "event",
        "preference": "preference",
        "preferences": "preference",
        "feeling": "preference",
        "feelings": "preference",
        "opinion": "preference",
        "trait": "preference",
        "habit": "preference",
        "like": "preference",
        "dislike": "preference",
        "relationship": "relationship",
        "relation": "relationship",
        "agreement": "relationship",
        "promise": "relationship",
        "connection": "relationship",
        "shared": "relationship",
        "project": "project",
        "goal": "project",
        "plan": "project",
        "project_info": "project",
    }
    return aliases.get(value, "fact" if value else "")


def _parse_llm_memories(raw: str) -> list[dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        return []
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
        if start == -1 or end == -1 or end < start:
            return []
        raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

    if isinstance(data, dict):
        data = data.get("memories", data if data.get("content") else [])
    if not isinstance(data, list):
        return []

    result: list[dict[str, Any]] = []
    for item in data[:20]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        mtype = _normalize_memory_type(str(item.get("type", "fact")).strip())
        perspective = str(item.get("perspective", "user")).strip()
        if not content or not mtype:
            continue
        if perspective not in PERSPECTIVES:
            perspective = "user"
        try:
            importance = min(max(float(item.get("importance", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            importance = 0.5
        try:
            confidence = min(max(float(item.get("confidence", 0.7)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.7
        try:
            valence = min(max(float(item.get("valence", 0.0)), -1.0), 1.0)
        except (TypeError, ValueError):
            valence = 0.0
        if len(content) > 500:
            content = content[:500]
        result.append(
            {
                "perspective": perspective,
                "type": mtype,
                "content": content,
                "importance": importance,
                "confidence": confidence,
                "valence": valence,
            }
        )
    return result


def _save_candidates(
    session: Session,
    candidates: list[dict[str, Any]],
    source_ids: list[int],
    *,
    extraction_run: MemoryExtractionRun,
    scope: str = "",
) -> int:
    existing = session.exec(select(SemanticMemory).where(SemanticMemory.status == "active")).all()
    saved = 0
    for candidate in candidates:
        similarities: list[tuple[float, SemanticMemory]] = []
        dup = False
        for old in existing:
            similarity = _token_similarity(candidate["content"], old.content)
            if similarity >= 0.72:
                dup = True
                break
            if similarity >= 0.32 and old.id is not None:
                similarities.append((similarity, old))
        if dup:
            continue

        memory = SemanticMemory(
            perspective=candidate.get("perspective", "user"),
            type=candidate["type"],
            content=candidate["content"],
            source_message_ids=json.dumps(source_ids, ensure_ascii=False),
            confidence=float(candidate["confidence"]),
            importance=float(candidate["importance"]),
            valence=float(candidate.get("valence", 0.0)),
            strength=1.0,
            status="active",
            scope=scope,
        )
        session.add(memory)
        session.flush()

        # 低于重复阈值、但明显相关的记忆，建立联想边
        for similarity, old in similarities[:4]:
            session.add(
                MemoryLink(
                    source_id=memory.id,
                    target_id=old.id,
                    relation="related",
                    weight=round(similarity, 4),
                )
            )

        existing.append(memory)
        saved += 1

    if saved:
        session.commit()
    extraction_run.status = "success" if candidates else "success_empty"
    return saved


def run_extraction_background(user_message_id: int, assistant_message_id: int) -> None:
    """后台记忆巩固：编码 -> LLM 三视角提取 -> 去重 -> 入库 -> 必要时写日记。"""
    with Session(engine) as session:
        user_message = session.get(Message, user_message_id)
        assistant_message = session.get(Message, assistant_message_id)
        if user_message is None:
            return

        source_ids = [user_message_id]
        if assistant_message is not None:
            source_ids.append(assistant_message_id)

        run = MemoryExtractionRun(
            source_message_ids=json.dumps(source_ids, ensure_ascii=False),
            status="pending",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        candidates: list[dict[str, Any]] = []
        fallback = _heuristic_candidates(user_message.content)

        if _is_extraction_worthwhile(user_message.content):
            try:
                config = get_config(session)
                run.provider = config.provider
                run.model = config.model
                session.add(run)
                session.commit()

                if config.provider != "mock":
                    existing_memories = retrieve_semantic_memories(
                        session,
                        user_message.content,
                        limit=8,
                        touch=False,
                    )
                    existing_block = "\n".join(
                        f"- [{m.perspective}][{m.type}] {m.content}" for m in existing_memories
                    )
                    system_prompt = (
                        _persona_text()
                        + "\n\n你是一个记忆巩固模块，为桑多涅本人整理她的长期记忆。"
                        "不要把记忆写成用户档案；要从三个视角分别考虑：\n"
                        "1) perspective=user：关于用户的稳定事实、偏好、项目；用第三人称写。\n"
                        "2) perspective=self：关于桑多涅自己的真实感受、偏好、自我认知、在意的事；"
                        "必须用桑多涅的第一人称「我」写，并且只从她这轮实际说的话和态度中谨慎推断，不要凭空加戏。\n"
                        "3) perspective=shared：关于「我们」的关系与共同约定；用「我们」写。\n"
                        "忽略寒暄和临时情绪。importance 表示对未来对话的重要程度（0-1），"
                        "confidence 表示证据是否明确（0-1），valence 表示该记忆的情感正负（-1 到 1）。"
                        "已有记忆如下，避免重复提取：\n" + (existing_block or "（暂无）") + "\n\n"
                        "只输出 JSON，格式："
                        '{"memories":[{"perspective":"self","type":"preference","content":"我其实不讨厌……","importance":0.7,"confidence":0.7,"valence":0.2}]}'
                    )
                    user_prompt = (
                        f"用户说：{user_message.content}\n"
                        f"桑多涅说：{assistant_message.content if assistant_message else ''}"
                    )
                    result = asyncio.run(
                        chat_completion(
                            session,
                            config,
                            [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            purpose="memory_extraction",
                            max_tokens=2000,
                            temperature=0.1,
                        )
                    )
                    llm_candidates = _parse_llm_memories(result["text"])
                    if llm_candidates:
                        candidates.extend(llm_candidates)
                    else:
                        run.status = "fallback"
                else:
                    run.status = "fallback"
            except Exception as exc:  # noqa: BLE001
                run.status = "failed"
                run.error = str(exc)[:2000]
                session.add(run)
                session.commit()

        if not candidates and run.status != "failed":
            candidates = fallback
            if candidates:
                run.status = "fallback"
            elif not _is_extraction_worthwhile(user_message.content):
                run.status = "skipped"

        if candidates:
            # 发生在某场游戏中、或只聊到一个游戏的记忆归到那个游戏
            scope = message_scope(session, user_message.content, user_message.created_at)
            _save_candidates(session, candidates, source_ids, extraction_run=run, scope=scope)

        run.candidates_json = json.dumps(candidates, ensure_ascii=False)
        if run.status in ("pending", "skipped", "success_empty"):
            run.status = "skipped" if not candidates else run.status
        session.add(run)
        session.commit()

        turn_index = user_message.turn_index
        maybe_write_diary(session, turn_index)

        # 聊到某个游戏时，她在后台决定要不要更新账号档案、记里程碑、记通用知识（不拖慢回复）
        try:
            from ..games.bookkeeping import run_game_bookkeeping

            run_game_bookkeeping(session, user_message, assistant_message)
        except Exception:  # noqa: BLE001 - 记账失败不影响记忆提取
            pass
