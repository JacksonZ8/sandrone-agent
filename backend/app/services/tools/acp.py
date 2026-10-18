"""ACP（Agent Client Protocol）客户端：用同一套标准协议驱动本机的编程智能体（Claude Code、Codex、OpenCode、Hermes）。

stdio 上的 JSON-RPC 2.0：initialize → session/new（或 session/load 接着旧会话）→ session/prompt，
智能体用 session/update 推送过程（回复正文、工具调用、计划），用 session/request_permission 要权限。
过程写进 agent_live 给任务卡上的实时面板；权限交给用户（只读模式下改写/执行类直接拒绝），从不替用户允许。
我们不提供文件与终端能力（clientCapabilities 全 false），智能体用它自己的工具。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable

from .. import agent_live

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
REQUEST_TIMEOUT = 60
STDERR_TAIL = 4000


class AcpError(RuntimeError):
    pass


class AcpClient:
    """一个智能体进程上的一次 ACP 会话。"""

    def __init__(self, args: list[str], cwd: str, *, task_id: int, readonly: bool, env: dict[str, str] | None = None):
        self.task_id = task_id
        self.readonly = readonly
        self.cwd = cwd
        self.proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,  # 停止时整组结束
        )
        self.session_id = ""
        self.loading = False  # session/load 回放历史时不记进实时过程
        self._next_id = 0
        self._waiters: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=200)
        self._closed = threading.Event()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # --- 传输 ---

    def _send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False)
        with self._write_lock:
            if self.proc.stdin is None or self.proc.poll() is not None:
                raise AcpError("智能体进程已经退出。" + self.stderr_tail())
            self.proc.stdin.write(data + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any], *, timeout: float = REQUEST_TIMEOUT) -> Any:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            waiter = {"event": threading.Event(), "result": None, "error": None}
            self._waiters[request_id] = waiter
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        finished = waiter["event"].wait(timeout)
        with self._lock:
            self._waiters.pop(request_id, None)
        if not finished:
            raise AcpError(f"{method} 超时（{int(timeout)} 秒）。")
        if waiter["error"] is not None:
            error = waiter["error"]
            details = (error.get("data") or {}).get("details") if isinstance(error.get("data"), dict) else error.get("data")
            # 智能体把真正的原因放在 data.details 里（例如“还没配置模型”），message 往往只是 Internal error
            raise AcpError(f"{method} 失败：{details or error.get('message') or error}")
        return waiter["result"]

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _respond(self, request_id: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        try:
            self._send(message)
        except AcpError:
            pass

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            self._dispatch_lines()
        except (OSError, ValueError):  # close() 关掉了管道
            pass
        self._closed.set()
        with self._lock:
            for waiter in self._waiters.values():
                waiter["error"] = {"message": "智能体进程已经退出。" + self.stderr_tail()}
                waiter["event"].set()

    def _dispatch_lines(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # 有的智能体会往 stdout 打日志
            if "method" in message and "id" in message:
                threading.Thread(target=self._handle_request, args=(message,), daemon=True).start()
            elif "method" in message:
                self._handle_notification(message)
            elif "id" in message:
                with self._lock:
                    waiter = self._waiters.get(message["id"])
                if waiter is not None:
                    waiter["result"] = message.get("result")
                    waiter["error"] = message.get("error")
                    waiter["event"].set()

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for line in self.proc.stderr:
                self._stderr.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> str:
        text = "\n".join(self._stderr)[-STDERR_TAIL:]
        return f"\n{text}" if text else ""

    # --- 智能体发来的东西 ---

    def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") != "session/update" or self.loading:
            return
        update = (message.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                agent_live.add_text(self.task_id, str(content.get("text") or ""))
        elif kind == "tool_call":
            tool_id = str(update.get("toolCallId") or "")
            title = str(update.get("title") or update.get("kind") or "工具")
            agent_live.update_tool(self.task_id, tool_id, title=title, kind=str(update.get("kind") or ""),
                                   status=str(update.get("status") or "pending"))
            agent_live.add_event(self.task_id, "tool", title, tool_id=tool_id)
        elif kind == "tool_call_update":
            tool_id = str(update.get("toolCallId") or "")
            agent_live.update_tool(self.task_id, tool_id, title=str(update.get("title") or ""),
                                   status=str(update.get("status") or ""))
        elif kind == "plan":
            agent_live.set_plan(self.task_id, list(update.get("entries") or []))

    def _handle_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        if method != "session/request_permission":
            # 没有声明文件/终端能力，这些请求一律不支持
            self._respond(message["id"], error={"code": -32601, "message": f"client does not support {method}"})
            return
        tool = params.get("toolCall") or {}
        options = [
            {"optionId": str(o.get("optionId")), "name": str(o.get("name") or o.get("optionId")), "kind": str(o.get("kind") or "")}
            for o in params.get("options") or []
            if o.get("optionId")
        ]
        title = str(tool.get("title") or tool.get("kind") or "一个操作")
        kind = str(tool.get("kind") or "")
        reject = next((o["optionId"] for o in options if o["kind"].startswith("reject")), None)
        if self.readonly and kind in agent_live.WRITE_KINDS:
            agent_live.add_event(self.task_id, "denied", f"只读模式，已拒绝：{title}")
            choice = reject
        else:
            choice = agent_live.ask_permission(
                self.task_id, agent_live.PendingPermission(str(message["id"]), title, kind, options)
            )
            if choice is None:
                agent_live.add_event(self.task_id, "denied", f"没有得到你的允许，已拒绝：{title}")
        if choice is None:
            self._respond(message["id"], result={"outcome": {"outcome": "cancelled"}})
        else:
            self._respond(message["id"], result={"outcome": {"outcome": "selected", "optionId": choice}})

    # --- 流程 ---

    def start_session(self, resume_session_id: str = "", *, mode: str = "") -> str:
        info = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        })
        capabilities = (info or {}).get("agentCapabilities") or {}
        if resume_session_id and capabilities.get("loadSession"):
            self.loading = True
            try:
                result = self.request("session/load", {"sessionId": resume_session_id, "cwd": self.cwd, "mcpServers": []},
                                      timeout=120)
                self.session_id = resume_session_id
            finally:
                self.loading = False
        else:
            result = self.request("session/new", {"cwd": self.cwd, "mcpServers": []})
            self.session_id = str((result or {}).get("sessionId") or "")
        if not self.session_id:
            raise AcpError("智能体没有返回会话。")
        if mode:
            self.set_mode(mode, result or {})
        return self.session_id

    def set_mode(self, mode: str, session_info: dict[str, Any]) -> None:
        """切到要求的模式（只读/写入对应的那个）；智能体没有这个模式就失败，不带着默认权限继续跑。"""
        option = next((o for o in session_info.get("configOptions") or [] if o.get("id") == "mode"), None)
        if option is None and not session_info.get("modes"):
            # 续接旧会话时有的智能体不回模式列表：直接设置，设不上就失败
            try:
                self.request("session/set_config_option", {"sessionId": self.session_id, "configId": "mode", "value": mode})
            except AcpError:
                self.request("session/set_mode", {"sessionId": self.session_id, "modeId": mode})
            return
        if option is not None:
            values = {str(choice.get("value")) for choice in option.get("options") or []}
            if mode not in values:
                raise AcpError(f"智能体没有“{mode}”模式，为了安全没有继续。")
            if option.get("currentValue") != mode:
                self.request("session/set_config_option", {"sessionId": self.session_id, "configId": "mode", "value": mode})
            return
        modes = session_info.get("modes") or {}
        if mode not in {str(m.get("id")) for m in modes.get("availableModes") or []}:
            raise AcpError(f"智能体没有“{mode}”模式，为了安全没有继续。")
        if modes.get("currentModeId") != mode:
            self.request("session/set_mode", {"sessionId": self.session_id, "modeId": mode})

    def prompt(self, text: str, *, timeout: float) -> str:
        result = self.request(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )
        return str((result or {}).get("stopReason") or "")

    def cancel(self) -> None:
        if self.session_id:
            try:
                self.notify("session/cancel", {"sessionId": self.session_id})
            except AcpError:
                pass

    def close(self) -> None:
        from .delegation import _kill_process_group

        if self.proc.poll() is None:
            _kill_process_group(self.proc)
        self._closed.wait(2)  # 等读取线程读完最后的输出
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def run_acp_task(
    args: list[str],
    prompt: str,
    cwd: str,
    *,
    task_id: int,
    readonly: bool,
    resume_session_id: str = "",
    timeout_seconds: int = 600,
    on_start: Callable[[subprocess.Popen], None] | None = None,
    env: dict[str, str] | None = None,
    mode: str = "",
) -> dict[str, Any]:
    """跑完一轮任务：返回与 run_delegate_agent 相同形状的结果（ok/output/message/duration_ms/agent_session_id）。"""
    started = time.monotonic()
    agent_live.start(task_id)
    client: AcpClient | None = None
    try:
        client = AcpClient(args, cwd, task_id=task_id, readonly=readonly, env=env or dict(os.environ))
        if on_start:
            on_start(client.proc)
        agent_live.add_event(task_id, "status", "已连上，开始干活")
        session_id = client.start_session(resume_session_id, mode=mode)
        stop_reason = client.prompt(prompt, timeout=timeout_seconds)
        live = agent_live.get(task_id)
        output = (live.text if live else "").strip()
        ok = stop_reason in ("end_turn", "max_tokens", "max_turn_requests")
        denied = any(event["kind"] == "denied" for event in (live.events if live else []))
        message = "" if ok else {
            "refusal": "智能体拒绝了这个任务。",
            "cancelled": "它要做的操作被拒绝了，这一轮就停了。" if denied else "任务被取消了。",
        }.get(stop_reason, f"智能体结束原因：{stop_reason or '未知'}")
        agent_live.add_event(task_id, "status", "做完了" if ok else message)
        return {
            "ok": ok,
            "output": output,
            "message": message,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "agent_session_id": session_id,
        }
    except AcpError as exc:
        if client is not None:
            client.cancel()
        agent_live.add_event(task_id, "status", f"出错了：{str(exc)[:200]}")
        return {
            "ok": False,
            "output": (agent_live.get(task_id).text if agent_live.get(task_id) else "").strip(),
            "message": str(exc)[:1200],
            "duration_ms": int((time.monotonic() - started) * 1000),
            "agent_session_id": client.session_id if client else "",
        }
    finally:
        agent_live.finish(task_id)
        if client is not None:
            client.close()
