from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from ..database import get_session
from ..models import AgentTask
from ..schemas import (
    AgentPermissionAnswer,
    AgentTaskActionResponse,
    AgentTaskPublic,
    AgentTaskThreadUpdate,
    CodexThreadPublic,
)
from ..services.agent_tasks import (
    answer_permission,
    cancel_task,
    confirm_task,
    list_tasks,
    live_view,
    resolve_workspace,
    set_task_thread,
    task_public,
)
from ..services.tools.codex_threads import list_codex_threads

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])


def _action(session: Session, task_id: int, ok: bool, message: str) -> dict:
    task = session.get(AgentTask, task_id)
    if task is not None:
        session.refresh(task)
    return {"ok": ok, "message": message, "task": task_public(task) if task else None}


@router.get("", response_model=list[AgentTaskPublic])
def get_tasks(limit: int = 20, session: Session = Depends(get_session)) -> list[dict]:
    return [task_public(task) for task in list_tasks(session, limit=max(1, min(limit, 100)))]


@router.get("/codex-threads", response_model=list[CodexThreadPublic])
def get_codex_threads(q: str = "", limit: int = 30) -> list[dict]:
    return list_codex_threads(q, limit=max(1, min(limit, 100)))


@router.post("/{task_id}/confirm", response_model=AgentTaskActionResponse)
async def post_confirm(task_id: int, session: Session = Depends(get_session)) -> dict:
    # async：确认后要在事件循环里启动后台任务
    ok, message = confirm_task(session, task_id)
    return _action(session, task_id, ok, message)


@router.post("/{task_id}/cancel", response_model=AgentTaskActionResponse)
def post_cancel(task_id: int, session: Session = Depends(get_session)) -> dict:
    ok, message = cancel_task(session, task_id)
    return _action(session, task_id, ok, message)


@router.put("/{task_id}/thread", response_model=AgentTaskActionResponse)
def put_thread(task_id: int, payload: AgentTaskThreadUpdate, session: Session = Depends(get_session)) -> dict:
    if session.get(AgentTask, task_id) is None:
        raise HTTPException(status_code=404, detail="没有这个任务。")
    ok, message = set_task_thread(session, task_id, payload.thread_id)
    return _action(session, task_id, ok, message)


@router.post("/{task_id}/workspace/{action}", response_model=AgentTaskActionResponse)
def post_workspace(task_id: int, action: str, session: Session = Depends(get_session)) -> dict:
    """独立工作区里的改动：merge 合并进你当前的分支，discard 丢弃。"""
    if action not in ("merge", "discard"):
        raise HTTPException(status_code=404, detail="只能合并或丢弃。")
    ok, message = resolve_workspace(session, task_id, action)
    return _action(session, task_id, ok, message)


@router.get("/{task_id}/live")
def get_live(task_id: int) -> dict:
    """执行中任务的实时过程（回复正文、工具调用、计划、待你决定的权限请求）；没有时返回 null。"""
    return {"live": live_view(task_id)}


@router.post("/{task_id}/permission", response_model=AgentTaskActionResponse)
def post_permission(task_id: int, payload: AgentPermissionAnswer, session: Session = Depends(get_session)) -> dict:
    ok, message = answer_permission(session, task_id, payload.option_id)
    return _action(session, task_id, ok, message)


@router.websocket("/{task_id}/terminal")
async def terminal_ws(websocket: WebSocket, task_id: int) -> None:
    """终端类智能体的终端：先补发已有输出，再实时推送；收到的按键原样写进终端（用户接手打字）。"""
    from ..services.tools.terminal_agent import terminal_for

    await websocket.accept()
    session = terminal_for(task_id)
    if session is None:
        await websocket.close(code=4404)
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    backlog, unsubscribe = session.subscribe(lambda data: loop.call_soon_threadsafe(queue.put_nowait, data))
    threading_done = loop.run_in_executor(None, session.closed.wait)
    threading_done.add_done_callback(lambda _: loop.call_soon_threadsafe(queue.put_nowait, None))

    async def pump() -> None:
        if backlog:
            await websocket.send_bytes(backlog)
        while (data := await queue.get()) is not None:
            await websocket.send_bytes(data)
        await websocket.close()

    sender = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                session.write(message["bytes"])
            elif message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "resize":
                    session.resize(int(payload.get("cols") or 120), int(payload.get("rows") or 36))
                elif payload.get("type") == "input":
                    session.write(str(payload.get("data") or "").encode("utf-8"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        unsubscribe()
        sender.cancel()
