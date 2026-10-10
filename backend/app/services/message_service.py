from __future__ import annotations

import json

from sqlmodel import Session, select

from ..models import AffectionEvent, AgentDiary, Message, ProactiveEvent, SemanticMemory


class MessageActionError(Exception):
    """删除或重新生成消息时的可预期错误；status 对应 HTTP 状态码。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _strip_id(raw: str, message_id: int) -> str | None:
    try:
        ids = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(ids, list) or message_id not in ids:
        return None
    return json.dumps([item for item in ids if item != message_id])


def _detach_references(session: Session, message_id: int) -> None:
    """删除消息前，把记忆、日记、好感与主动消息记录里指向它的引用去掉；记忆本身保留。"""
    for model in (SemanticMemory, AgentDiary):
        for row in session.exec(select(model).where(model.source_message_ids.contains(str(message_id)))):
            stripped = _strip_id(row.source_message_ids, message_id)
            if stripped is not None:
                row.source_message_ids = stripped
                session.add(row)
    for event in session.exec(select(AffectionEvent).where(AffectionEvent.source_message_id == message_id)):
        event.source_message_id = None
        session.add(event)
    for event in session.exec(select(ProactiveEvent).where(ProactiveEvent.message_id == message_id)):
        event.message_id = None
        session.add(event)


def delete_message(session: Session, message_id: int) -> None:
    message = session.get(Message, message_id)
    if message is None:
        raise MessageActionError(404, "这条消息不存在或已经删除。")
    _detach_references(session, message_id)
    session.delete(message)
    session.commit()


def prepare_regenerate(session: Session, message_id: int) -> tuple[Message, Message]:
    """校验重新生成的目标，返回（它回应的用户消息，要被替换的旧回复）。

    只允许重新生成时间线上最新的一条回复，且它必须有同一轮的用户发言
    （她主动发来的消息没有可以重答的问题）。旧回复在新回复生成成功后才删除。
    """
    message = session.get(Message, message_id)
    if message is None:
        raise MessageActionError(404, "这条消息不存在或已经删除。")
    if message.role != "assistant":
        raise MessageActionError(409, "只能重新生成她的回复。")
    latest = session.exec(
        select(Message)
        .where(Message.thread_id == message.thread_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    if latest is None or latest.id != message.id:
        raise MessageActionError(409, "只能重新生成最新的一条回复。")
    user_message = session.exec(
        select(Message)
        .where(
            Message.thread_id == message.thread_id,
            Message.turn_index == message.turn_index,
            Message.role == "user",
        )
        .order_by(Message.id.desc())
        .limit(1)
    ).first()
    if user_message is None:
        raise MessageActionError(409, "她主动发来的消息不能重新生成。")
    return user_message, message
