from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..database import get_session
from ..models import Message
from ..schemas import ChatRequest, ChatResponse, MessagePublic, ReplyStyleRequest
from ..services.chat_prompt import reply_style_block
from ..services.chat_service import create_chat_turn, create_chat_turn_stream, message_public
from ..services.memory_service import run_extraction_background
from ..services.message_service import MessageActionError, delete_message, prepare_regenerate

router = APIRouter(prefix="/api", tags=["chat"])


def _style_context(request: ReplyStyleRequest) -> str:
    return reply_style_block(
        reply_length=request.reply_length,
        action_text=request.action_text,
        voice_mode=request.voice_mode,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    result = await create_chat_turn(session, request.content, style_context=_style_context(request))
    assistant = result.get("assistant_message")
    if assistant:
        background_tasks.add_task(
            run_extraction_background,
            result["user_message"]["id"],
            assistant["id"],
        )
    return result


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    return _sse(
        create_chat_turn_stream(session, request.content, style_context=_style_context(request)),
        background_tasks,
    )


def _sse(stream, background_tasks: BackgroundTasks) -> StreamingResponse:
    async def events():
        async for event in stream:
            if event["type"] == "final" and event.get("assistant_message"):
                background_tasks.add_task(
                    run_extraction_background,
                    event["user_message"]["id"],
                    event["assistant_message"]["id"],
                )
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/messages/{message_id}")
def remove_message(message_id: int, session: Session = Depends(get_session)) -> dict:
    try:
        delete_message(session, message_id)
    except MessageActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"ok": True, "deleted_id": message_id}


@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: int,
    request: ReplyStyleRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    try:
        user_message, old_reply = prepare_regenerate(session, message_id)
    except MessageActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _sse(
        create_chat_turn_stream(
            session,
            user_message.content,
            thread_id=user_message.thread_id,
            style_context=_style_context(request),
            existing_user_message=user_message,
            replace_message_id=old_reply.id,
        ),
        background_tasks,
    )


@router.get("/messages", response_model=list[MessagePublic])
def list_messages(
    session: Session = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None),
) -> list[dict]:
    statement = select(Message).where(Message.thread_id == "main")
    if before_id is not None:
        statement = statement.where(Message.id < before_id)
    rows = list(
        session.exec(
            statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        ).all()
    )
    rows.reverse()
    return [message_public(m) for m in rows]
