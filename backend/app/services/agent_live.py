"""正在执行的子代理任务的实时过程（给任务卡上的实时面板看），以及它向用户要的权限。

只存在内存里：后端重启后过程就没了（任务本身会被记为 interrupted）。
权限请求永远交给用户决定，她不会替用户点“允许”；只读模式下改文件、删文件、执行命令这类请求直接拒绝。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

MAX_EVENTS = 300
MAX_TEXT = 20000
PERMISSION_TIMEOUT_SECONDS = 600
WRITE_KINDS = {"edit", "delete", "move", "execute"}


@dataclass
class PendingPermission:
    request_id: str
    title: str
    kind: str
    options: list[dict[str, str]]
    created_at: float = field(default_factory=time.time)
    answer: str | None = None  # 选中的 optionId；None 表示还没答（超时或停止时按拒绝处理）
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class LiveTask:
    events: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""  # 智能体这一轮回复的正文（流式累积）
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan: list[dict[str, str]] = field(default_factory=list)
    pending: PendingPermission | None = None
    updated_at: float = field(default_factory=time.time)


_LIVE: dict[int, LiveTask] = {}
_LOCK = threading.Lock()


def start(task_id: int) -> LiveTask:
    with _LOCK:
        live = _LIVE[task_id] = LiveTask()
        return live


def get(task_id: int) -> LiveTask | None:
    return _LIVE.get(task_id)


def finish(task_id: int) -> None:
    """任务结束：还没答的权限请求按拒绝处理，过程保留到下次同一任务开始（面板还能看到最后的样子）。"""
    live = _LIVE.get(task_id)
    if live and live.pending and not live.pending.done.is_set():
        live.pending.done.set()


def add_event(task_id: int, kind: str, text: str, **extra: Any) -> None:
    live = _LIVE.get(task_id)
    if live is None:
        return
    with _LOCK:
        live.events.append({"kind": kind, "text": text[:500], "at": time.time(), **extra})
        del live.events[:-MAX_EVENTS]
        live.updated_at = time.time()


def add_text(task_id: int, chunk: str) -> None:
    live = _LIVE.get(task_id)
    if live is None:
        return
    with _LOCK:
        live.text = (live.text + chunk)[-MAX_TEXT:]
        live.updated_at = time.time()


def update_tool(task_id: int, tool_id: str, **fields: Any) -> None:
    live = _LIVE.get(task_id)
    if live is None:
        return
    with _LOCK:
        tool = live.tools.setdefault(tool_id, {"id": tool_id, "title": "", "kind": "", "status": "pending"})
        tool.update({key: value for key, value in fields.items() if value})
        live.updated_at = time.time()


def set_plan(task_id: int, entries: list[dict[str, str]]) -> None:
    live = _LIVE.get(task_id)
    if live is None:
        return
    with _LOCK:
        live.plan = [{"content": str(e.get("content", ""))[:200], "status": str(e.get("status", ""))} for e in entries][:30]
        live.updated_at = time.time()


def ask_permission(task_id: int, pending: PendingPermission, *, timeout: float | None = None) -> str | None:
    """挂起等用户在任务卡上选一个选项；返回 optionId，超时或任务停止返回 None。"""
    live = _LIVE.get(task_id)
    if live is None:
        return None
    live.pending = pending
    add_event(task_id, "permission", f"等你决定：{pending.title}")
    pending.done.wait(PERMISSION_TIMEOUT_SECONDS if timeout is None else timeout)
    live.pending = None
    return pending.answer


def answer_permission(task_id: int, option_id: str) -> bool:
    live = _LIVE.get(task_id)
    pending = live.pending if live else None
    if pending is None or pending.done.is_set():
        return False
    if option_id not in {option["optionId"] for option in pending.options}:
        return False
    pending.answer = option_id
    pending.done.set()
    return True


def public(task_id: int) -> dict[str, Any] | None:
    live = _LIVE.get(task_id)
    if live is None:
        return None
    with _LOCK:
        pending = live.pending
        return {
            "text": live.text[-4000:],
            "events": live.events[-60:],
            "tools": list(live.tools.values())[-40:],
            "plan": live.plan,
            "pending_permission": None if pending is None else {
                "title": pending.title,
                "kind": pending.kind,
                "options": pending.options,
            },
            "updated_at": live.updated_at,
        }
