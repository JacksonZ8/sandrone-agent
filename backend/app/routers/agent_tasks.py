from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..database import get_session
from ..models import AgentTask
from ..schemas import AgentTaskActionResponse, AgentTaskPublic, AgentTaskThreadUpdate, CodexThreadPublic
from ..services.agent_tasks import cancel_task, confirm_task, list_tasks, set_task_thread, task_public
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
