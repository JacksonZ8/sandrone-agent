"""终端类智能体（Oh-DSH 的 TUI）：在伪终端里运行，桑多涅窗口里的终端面板实时显示，用户随时能接手打字。

她做的事只有两件：等界面就绪后把任务打进去、读结果；终端里出现的确认/审批交给用户在面板里回答，她不替用户按。
结果不从屏幕上猜，而是读 Oh-DSH 自己的会话记录（~/.ohdsh/sessions/<目录>/<会话>/session.jsonl.zstd）：
这一轮的 turn/end 出现就算做完，正文取这一轮最后一条带文字的 assistant/message。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import agent_live

logger = logging.getLogger(__name__)

OHDSH_HOME = Path.home() / ".ohdsh"
BUFFER_LIMIT = 400_000
READY_QUIET_SECONDS = 2.0   # 开场动画停下这么久算就绪
READY_MAX_SECONDS = 30
POLL_SECONDS = 1.5


class TerminalSession:
    """一个跑在伪终端里的进程，输出缓存并推给订阅者（终端面板）。"""

    def __init__(self, args: list[str], cwd: str, env: dict[str, str], *, cols: int = 120, rows: int = 36):
        self.master, slave = os.openpty()
        self.resize(cols, rows)
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.buffer = bytearray()
        self.last_output = time.monotonic()
        self.subscribers: set[Callable[[bytes], None]] = set()
        self.lock = threading.Lock()
        self.closed = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                break
            if not data:
                break
            with self.lock:
                self.buffer.extend(data)
                del self.buffer[:-BUFFER_LIMIT]
                self.last_output = time.monotonic()
                subscribers = list(self.subscribers)
            for callback in subscribers:
                try:
                    callback(data)
                except Exception:  # noqa: BLE001 - 面板断开不影响任务
                    pass
        self.closed.set()

    def write(self, data: bytes) -> None:
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", max(10, rows), max(20, cols), 0, 0))
        except OSError:
            pass

    def subscribe(self, callback: Callable[[bytes], None]) -> tuple[bytes, Callable[[], None]]:
        with self.lock:
            self.subscribers.add(callback)
            backlog = bytes(self.buffer)
        return backlog, lambda: self.subscribers.discard(callback)

    def wait_until_quiet(self, quiet: float, limit: float) -> None:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline and not self.closed.is_set():
            if time.monotonic() - self.last_output >= quiet:
                return
            time.sleep(0.2)

    def close(self) -> None:
        from .delegation import _kill_process_group

        if self.proc.poll() is None:
            _kill_process_group(self.proc)
        self.closed.wait(2)
        try:
            os.close(self.master)
        except OSError:
            pass


_SESSIONS: dict[int, TerminalSession] = {}


def terminal_for(task_id: int) -> TerminalSession | None:
    return _SESSIONS.get(task_id)


# --- Oh-DSH 的会话记录 ---


def read_events(path: Path) -> list[dict[str, Any]]:
    """解压并读出会话记录；文件正被写入时最后一帧可能不完整，读到哪算哪。"""
    zstdcat = shutil.which("zstdcat") or shutil.which("zstdcat", path="/opt/homebrew/bin:/usr/local/bin")
    if zstdcat is None:
        raise RuntimeError("找不到 zstd 命令，读不了 Oh-DSH 的会话记录（brew install zstd）。")
    result = subprocess.run([zstdcat, str(path)], capture_output=True, timeout=20)
    events = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def message_text(message: dict[str, Any]) -> str:
    parts = message.get("content") or []
    if isinstance(parts, str):
        return parts
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text")


def latest_turn(events: list[dict[str, Any]]) -> int:
    return max((int((e.get("data") or {}).get("turn") or 0) for e in events if e.get("type") == "turn/start"), default=0)


def finished_turn(events: list[dict[str, Any]], after_turn: int) -> tuple[bool, str, str]:
    """after_turn 之后有没有一轮结束：返回 (结束了, 结束原因, 这一轮最后的回复正文)。"""
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "turn/end" and int(data.get("turn") or 0) > after_turn:
            turn = int(data["turn"])
            texts = [
                message_text((e.get("data") or {}).get("message") or {})
                for e in events
                if e.get("type") == "assistant/message" and int((e.get("data") or {}).get("turn") or 0) == turn
            ]
            reason = str((data.get("reason") or {}).get("kind") or "")
            return True, reason, next((text for text in reversed(texts) if text.strip()), "")
    return False, "", ""


def find_session_file(root: Path, *, since: float, session_id: str = "") -> Path | None:
    """这次启动对应的会话记录：续接时按会话 id 找，新开时找启动后新建的那个。"""
    if not root.is_dir():
        return None
    files = list(root.glob("*/*/session.jsonl.zstd"))
    if session_id:
        return next((f for f in files if f.parent.name == session_id), None)
    fresh = [f for f in files if f.stat().st_mtime >= since - 1]
    return max(fresh, key=lambda f: f.stat().st_mtime) if fresh else None


def run_terminal_task(
    args: list[str],
    prompt: str,
    cwd: str,
    *,
    task_id: int,
    timeout_seconds: int = 600,
    resume_session_id: str = "",
    on_start: Callable[[subprocess.Popen], None] | None = None,
    sessions_root: Path | None = None,
    env: dict[str, str] | None = None,
    ready_quiet: float = READY_QUIET_SECONDS,
) -> dict[str, Any]:
    """在终端里跑一轮：返回与 run_delegate_agent 相同形状的结果。"""
    root = sessions_root or OHDSH_HOME / "sessions"
    started_wall, started = time.time(), time.monotonic()
    agent_live.start(task_id)
    full_args = [*args, "tui", "--inline", "--cwd", cwd] + (["--resume", resume_session_id] if resume_session_id else [])
    session = TerminalSession(full_args, cwd, {**(env or os.environ), "TERM": "xterm-256color"})
    _SESSIONS[task_id] = session
    if on_start:
        on_start(session.proc)

    def result(ok: bool, output: str, message: str, session_id: str) -> dict[str, Any]:
        agent_live.add_event(task_id, "status", "做完了" if ok else message[:200])
        return {"ok": ok, "output": output, "message": message, "agent_session_id": session_id,
                "duration_ms": int((time.monotonic() - started) * 1000)}

    try:
        agent_live.add_event(task_id, "status", "终端已打开，等它准备好")
        session.wait_until_quiet(ready_quiet, READY_MAX_SECONDS)
        if session.closed.is_set():
            return result(False, "", "Oh-DSH 启动后马上退出了，看看终端面板里的提示。", resume_session_id)
        record = None
        for _ in range(20):
            record = find_session_file(root, since=started_wall, session_id=resume_session_id)
            if record:
                break
            time.sleep(0.5)
        if record is None:
            return result(False, "", "没找到这次的 Oh-DSH 会话记录。", resume_session_id)
        session_id = record.parent.name
        before = latest_turn(read_events(record))
        # 终端里回车就是发送：多行任务压成一行再打进去
        session.write(" ".join(prompt.split()).encode("utf-8"))
        time.sleep(0.3)
        session.write(b"\r")
        agent_live.add_event(task_id, "status", "任务已经打进去了")
        deadline = started + timeout_seconds
        while time.monotonic() < deadline:
            if session.closed.is_set():
                return result(False, "", "Oh-DSH 在任务完成前退出了。", session_id)
            done, reason, text = finished_turn(read_events(record), before)
            if done:
                ok = reason in ("completed", "")
                return result(ok, text.strip(), "" if ok else f"Oh-DSH 这一轮没做完：{reason}", session_id)
            time.sleep(POLL_SECONDS)
        return result(False, "", f"超过 {timeout_seconds} 秒还没做完，已停止。", session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("终端智能体任务 #%s 出错", task_id)
        return result(False, "", f"出错了：{exc}", resume_session_id)
    finally:
        agent_live.finish(task_id)
        session.close()
        _SESSIONS.pop(task_id, None)
