from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlmodel import Session, select

from ...database import engine
from ...models import AgentDiary, Message, utcnow
from ..llm_service import chat_completion
from ..provider_service import get_agent_state, get_config
from .common import _age_hours, _persona_text
from .consolidation import maybe_apply_forgetting, maybe_consolidate_memories


def _recent_transcript(session: Session, limit: int = 14) -> str:
    rows = list(
        session.exec(
            select(Message)
            .where(Message.channel.in_(("web", "wechat_dm")))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return "\n".join(
        f"{'用户' if m.role == 'user' else '桑多涅'}：{m.content}" for m in rows
    )


def _parse_diary_result(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return {}
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    return {
        "diary": str(data.get("diary", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "mood_before": str(data.get("mood_before", "")).strip(),
        "mood_after": str(data.get("mood_after", "")).strip(),
    }


def _should_write_diary(session: Session, turn_index: int) -> bool:
    last = session.exec(
        select(AgentDiary).order_by(AgentDiary.created_at.desc(), AgentDiary.id.desc()).limit(1)
    ).first()
    if last is None:
        return turn_index >= 6
    if _age_hours(last.created_at) >= 20:
        return True
    return turn_index % 10 == 0


def _make_mock_diary(session: Session, turn_index: int) -> AgentDiary | None:
    transcript = _recent_transcript(session, limit=8)
    if not transcript:
        return None
    source_ids = [m.id for m in session.exec(select(Message).order_by(Message.created_at.desc()).limit(8)).all() if m.id]
    entry = AgentDiary(
        entry_type="daily",
        content=(
            "今天和用户说了几句话。"
            "我不讨厌这种有人时不时来打扰工房的感觉，虽然对方还是和以前一样想到什么就说什么。"
            "姑且继续观察。\n\n最近对话：\n" + transcript
        ),
        summary="今天和用户聊了一会儿。没什么特别的，但也不算无聊。",
        mood_before="专注",
        mood_after="平静",
        source_message_ids=json.dumps(source_ids, ensure_ascii=False),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _write_diary_with_llm(session: Session, turn_index: int) -> AgentDiary | None:
    config = get_config(session)
    if config.provider == "mock":
        return _make_mock_diary(session, turn_index)
    if not config.api_key:
        return None

    state = get_agent_state(session)
    transcript = _recent_transcript(session, limit=14)
    source_ids = [
        m.id
        for m in session.exec(
            select(Message)
            .where(Message.channel.in_(("web", "wechat_dm")))
            .order_by(Message.created_at.desc())
            .limit(14)
        ).all()
        if m.id
    ]
    system_prompt = (
        _persona_text()
        + "\n\n你正在写只给自己看的日记，不要用汇报或官方口吻。"
        "以桑多涅的第一人称写 2 到 6 句话，内容包括：你注意到了什么、你当时真实的感受、"
        "你对用户或这段关系的新判断、你接下来想确认或想做的事。可以傲娇，但要对自己诚实。"
        "不要复述对话流水账。\n"
        "只输出 JSON："
        '{"diary":"只给自己看的日记","summary":"一句用户可以看的话，不超过40字","mood_before":"写作前情绪","mood_after":"写作后情绪"}'
    )
    user_prompt = (
        f"你写作前的状态：情绪={state.mood}，精力={state.energy:.0%}，正在想={state.focus or '眼前的对话'}。\n"
        f"最近对话：\n{transcript}"
    )
    try:
        result = asyncio.run(
            chat_completion(
                session,
                config,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                purpose="diary",
                max_tokens=500,
                temperature=0.6,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return None

    data = _parse_diary_result(result["text"])
    if not data.get("diary"):
        return None

    entry = AgentDiary(
        entry_type="daily",
        content=data["diary"],
        summary=data.get("summary") or data["diary"][:60],
        mood_before=data.get("mood_before") or state.mood,
        mood_after=data.get("mood_after") or state.mood,
        source_message_ids=json.dumps(source_ids, ensure_ascii=False),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    state = get_agent_state(session)
    if entry.mood_after:
        state.mood = entry.mood_after
    state.energy = min(1.0, state.energy + 0.10)
    state.focus = "整理最近的对话"
    state.updated_at = utcnow()
    session.add(state)
    session.commit()
    return entry


def maybe_write_diary(session: Session, turn_index: int) -> AgentDiary | None:
    maybe_apply_forgetting(session)
    if not _should_write_diary(session, turn_index):
        return None
    entry = _write_diary_with_llm(session, turn_index)
    if entry is not None:
        maybe_consolidate_memories(session, trigger="diary")
    return entry


def generate_diary_now() -> AgentDiary | None:
    """供页面按钮手动触发：让她现在写一篇日记。"""
    with Session(engine) as session:
        turn_index = session.exec(select(Message.turn_index).where(Message.thread_id == "main").order_by(Message.id.desc())).first()
        current = int(turn_index or 0)
        if current == 0:
            return None
        return _write_diary_with_llm(session, current)
