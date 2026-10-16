from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlmodel import Session

from ...models import ToolConfig, utcnow
from .audit import _record_tool_call
from .config import get_tool_config
from .path_policy import _expand_path, _inside_allowlist, _parse_allowlist


AGENT_COMMANDS = {"claude", "codex"}


AGENT_PERMISSIONS = ("readonly", "write")


# ChatGPT 桌面版自带的 codex 跟着 App 更新，和 App 用同一份配置与模型；PATH 里的 Homebrew 版可能落后、跑不了新模型
BUNDLED_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")


def agent_executable(command: str) -> str | None:
    """子代理 CLI 的可执行文件：codex 优先 SANDRONE_CODEX_BIN，其次桌面版自带的，最后 PATH。"""
    if command == "codex":
        override = os.getenv("SANDRONE_CODEX_BIN", "").strip()
        if override:
            return override if os.access(override, os.X_OK) else None
        if os.access(BUNDLED_CODEX, os.X_OK):
            return str(BUNDLED_CODEX)
    return shutil.which(command)


def _agent_cli_version(command: str) -> str:
    exe = agent_executable(command)
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        first_line = (result.stdout or result.stderr or "").strip().splitlines()
        return first_line[0][:120] if first_line else exe
    except Exception as exc:
        return f"{exe}（无法获取版本：{exc}）"


def agent_tool_public(config: ToolConfig) -> dict[str, Any]:
    try:
        allowlist = json.loads(config.agent_allowlist or "[]")
    except json.JSONDecodeError:
        allowlist = []
    command = (config.agent_command or "claude").strip() or "claude"
    permission = config.agent_permission if config.agent_permission in AGENT_PERMISSIONS else "readonly"
    return {
        "agent_enabled": config.agent_enabled,
        "agent_command": command,
        "agent_permission": permission,
        "agent_allowlist": [str(x) for x in allowlist],
        "agent_timeout_seconds": config.agent_timeout_seconds,
        "agent_max_output_chars": config.agent_max_output_chars,
        "agent_max_budget_usd": config.agent_max_budget_usd,
        "cli_available": agent_executable(command) is not None,
        "cli_version": _agent_cli_version(command),
    }


def _build_agent_args(
    command: str,
    exe: str,
    prompt: str,
    cwd: str,
    permission: str,
    budget: str,
    *,
    resume_session_id: str = "",
    new_session_id: str = "",
    output_file: str = "",
) -> list[str]:
    """拼子代理命令。会话都会保存下来，之后能用 resume_session_id 接着同一个会话继续。"""
    if command == "claude":
        mode = "plan" if permission == "readonly" else "acceptEdits"
        args = [
            exe,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            mode,
            "--max-budget-usd",
            budget,
            "--add-dir",
            cwd,
        ]
        if resume_session_id:
            args += ["--resume", resume_session_id]
        elif new_session_id:
            args += ["--session-id", new_session_id]
        return args
    # codex：exec 非交互、不询问审批；--json 把事件逐行写到 stdout，最终回复另写到 -o 文件
    sandbox = "read-only" if permission == "readonly" else "workspace-write"
    if resume_session_id:
        # resume 不接受 --sandbox/--cd：沙箱用配置覆盖，目录沿用线程原来的
        args = [exe, "exec", "resume", resume_session_id, "--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{sandbox}"']
    else:
        args = [exe, "exec", "--json", "--sandbox", sandbox, "--cd", cwd, "--skip-git-repo-check"]
    if output_file:
        args += ["-o", output_file]
    return [*args, prompt]


def _nested_message(text: str) -> str:
    """codex 的错误常是一段 JSON 字符串，取里面最深的 message。"""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text
    while isinstance(data, dict):
        inner = data.get("error") if isinstance(data.get("error"), dict) else None
        if inner is None:
            return str(data.get("message") or text)
        data = inner
    return text


def parse_codex_output(stdout: str, last_message: str = "") -> tuple[str, str, str]:
    """从 codex --json 事件流里取 (最终回复, thread_id, 错误)。"""
    thread_id = ""
    reply = ""
    error = ""
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = str(event.get("thread_id") or thread_id)
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                reply = str(item["text"])
        elif kind == "turn.failed":
            error = _nested_message(str((event.get("error") or {}).get("message") or "子代理任务失败。"))
        elif kind == "error" and not error:
            error = _nested_message(str(event.get("message") or ""))
    final = (last_message or "").strip() or reply.strip()
    return final, thread_id, "" if final and not error else error


def parse_claude_output(stdout: str) -> tuple[str, str, str]:
    """从 claude --output-format json 的结果里取 (最终回复, session_id, 错误)；不是 JSON 时整段当回复。"""
    text = (stdout or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text, "", ""
    if isinstance(data, list):
        data = next((item for item in reversed(data) if isinstance(item, dict) and item.get("type") == "result"), {})
    if not isinstance(data, dict):
        return text, "", ""
    result = str(data.get("result") or "").strip()
    session_id = str(data.get("session_id") or "")
    if data.get("is_error"):
        return "", session_id, result or str(data.get("subtype") or "子代理任务失败。")
    return result, session_id, ""


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _run_process(
    args: list[str],
    cwd: str,
    timeout: int,
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> tuple[int, str, str]:
    """运行子代理并等它结束；stdin 关闭（codex 否则会等输入），超时连同它拉起的子进程一起结束。"""
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    if on_start is not None:
        on_start(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.communicate()
        raise
    return proc.returncode, stdout or "", stderr or ""


def resolve_agent_target(
    config: ToolConfig,
    cwd_value: str,
    *,
    permission_override: str | None = None,
    command_override: str | None = None,
) -> dict[str, Any]:
    """检查子代理是否可用并解析工作目录；返回 {"ok": True, command, exe, cwd, permission} 或带 message 的失败。"""
    if not config.agent_enabled:
        return {"ok": False, "command": "", "cwd": "", "message": "子代理功能未开启。请先在设置里开启并配置允许访问的目录。"}

    command = (command_override or config.agent_command or "claude").strip() or "claude"
    if command not in AGENT_COMMANDS:
        return {"ok": False, "command": command, "cwd": "", "message": f"不支持的子代理命令：{command}。当前只允许 {', '.join(sorted(AGENT_COMMANDS))}。"}

    if permission_override is not None and permission_override not in AGENT_PERMISSIONS:
        raise ValueError("子代理权限覆盖只能是 readonly 或 write。")
    permission = permission_override or (
        config.agent_permission if config.agent_permission in AGENT_PERMISSIONS else "readonly"
    )
    exe = agent_executable(command)
    if not exe:
        return {"ok": False, "command": command, "cwd": "", "message": f"找不到 {command} CLI。请先安装并确保它已加入 PATH。"}

    allowlist = _parse_allowlist(config.agent_allowlist)
    if not allowlist:
        return {"ok": False, "command": command, "cwd": "", "message": "子代理没有配置允许访问的目录。"}

    cwd: Path
    if cwd_value.strip():
        raw = _expand_path(cwd_value)
        candidates = [raw]
        if not raw.is_absolute():
            candidates.extend([Path.cwd() / raw, Path.home() / raw])
        resolved: Path | None = None
        for candidate in candidates:
            try:
                candidate_resolved = candidate.resolve()
            except Exception:
                continue
            if candidate_resolved.exists() and candidate_resolved.is_dir():
                resolved = candidate_resolved
                break
        if resolved is None:
            names = "\n".join(str(root) for root in allowlist)
            return {"ok": False, "command": command, "cwd": cwd_value, "message": f"目录不存在。当前允许的目录：\n{names}"}
        cwd = resolved
    else:
        cwd = allowlist[0]

    if not _inside_allowlist(cwd, allowlist):
        names = "\n".join(str(root) for root in allowlist)
        return {"ok": False, "command": command, "cwd": str(cwd), "message": f"目录不在允许列表内。当前允许的目录：\n{names}"}
    return {"ok": True, "command": command, "exe": exe, "cwd": str(cwd), "permission": permission}


def execute_agent(
    target: dict[str, Any],
    prompt: str,
    config: ToolConfig,
    *,
    resume_session_id: str = "",
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> dict[str, Any]:
    """在已解析的目录里跑一次子代理；结果带 agent_session_id，之后可以接着这个会话继续。"""
    command, cwd = target["command"], target["cwd"]
    budget = f"{max(0.1, config.agent_max_budget_usd):.2f}"
    new_session_id = "" if resume_session_id or command != "claude" else str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="sandrone_agent_") as tmp:
        output_file = str(Path(tmp) / "last_message.txt") if command == "codex" else ""
        args = _build_agent_args(
            command,
            target["exe"],
            prompt.strip(),
            cwd,
            target["permission"],
            budget,
            resume_session_id=resume_session_id,
            new_session_id=new_session_id,
            output_file=output_file,
        )
        started = time.monotonic()
        base = {"command": command, "cwd": cwd, "agent_session_id": resume_session_id or new_session_id}
        try:
            returncode, stdout, stderr = _run_process(args, cwd, max(30, config.agent_timeout_seconds), on_start)
        except subprocess.TimeoutExpired:
            latency = int((time.monotonic() - started) * 1000)
            return {**base, "ok": False, "output": "", "duration_ms": latency, "message": f"子代理执行超过 {config.agent_timeout_seconds} 秒，已中止。"}
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            return {**base, "ok": False, "output": "", "duration_ms": latency, "message": f"子代理启动失败：{exc}"}
        latency = int((time.monotonic() - started) * 1000)
        last_message = ""
        if output_file and Path(output_file).exists():
            last_message = Path(output_file).read_text(encoding="utf-8", errors="replace")

    if command == "codex":
        output, session_id, error = parse_codex_output(stdout, last_message)
    else:
        output, session_id, error = parse_claude_output(stdout)
    base["agent_session_id"] = session_id or base["agent_session_id"]
    if returncode < 0:
        return {**base, "ok": False, "output": "", "duration_ms": latency, "message": "子代理已被停止。"}
    if returncode != 0 or error:
        detail = error or f"{stdout.strip()}\n\n--- stderr ---\n{stderr.strip()}".strip()[-3000:]
        return {**base, "ok": False, "output": detail[-3000:], "duration_ms": latency, "message": f"子代理返回退出码 {returncode}。" if returncode else "子代理任务失败。"}
    if not output:
        return {**base, "ok": False, "output": "", "duration_ms": latency, "message": "子代理没有返回内容。"}

    truncated = output[: config.agent_max_output_chars]
    if len(output) > config.agent_max_output_chars:
        truncated += "\n……[子代理输出过长，已截断]"
    return {**base, "ok": True, "output": truncated, "duration_ms": latency, "message": "子代理任务完成。"}


def _run_delegate_agent(
    session: Session,
    prompt: str,
    cwd_value: str,
    config: ToolConfig,
    *,
    permission_override: str | None = None,
    command_override: str | None = None,
    resume_session_id: str = "",
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> dict[str, Any]:
    """派本地 CLI 子代理在授权目录中执行任务。

    只读模式：claude plan / codex read-only，不写文件。
    写入模式：claude acceptEdits / codex workspace-write，仅限授权工作目录，由用户显式开启。
    """
    target = resolve_agent_target(
        config, cwd_value, permission_override=permission_override, command_override=command_override
    )
    if not target["ok"]:
        return {"ok": False, "command": target["command"], "cwd": target["cwd"], "output": "", "duration_ms": 0, "message": target["message"]}
    return execute_agent(target, prompt, config, resume_session_id=resume_session_id, on_start=on_start)


def run_delegate_agent(
    session: Session,
    prompt: str,
    cwd_value: str,
    config: ToolConfig,
    *,
    permission_override: str | None = None,
    command_override: str | None = None,
    resume_session_id: str = "",
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    command = (command_override or config.agent_command or "claude").strip() or "claude"
    permission = permission_override or (
        config.agent_permission if config.agent_permission in AGENT_PERMISSIONS else "readonly"
    )
    try:
        result = _run_delegate_agent(
            session,
            prompt,
            cwd_value,
            config,
            permission_override=permission_override,
            command_override=command_override,
            resume_session_id=resume_session_id,
            on_start=on_start,
        )
    except Exception as exc:
        _record_tool_call(
            session,
            "delegate_agent",
            command,
            permission,
            "error",
            str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    message = str(result.get("message") or "")
    if result.get("ok"):
        status = "ok"
    elif "超过" in message and "秒" in message:
        status = "timeout"
    elif "没有返回内容" in message:
        status = "empty"
    else:
        status = "error"
    _record_tool_call(
        session,
        "delegate_agent",
        command,
        permission,
        status,
        "" if result.get("ok") else message,
        latency_ms=int(result.get("duration_ms") or (time.monotonic() - started) * 1000),
    )
    return result


def update_agent_config(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    config = get_tool_config(session)
    if data.get("agent_enabled") is not None:
        config.agent_enabled = bool(data["agent_enabled"])
    if data.get("agent_command") is not None:
        command = str(data["agent_command"]).strip()
        if command and command not in AGENT_COMMANDS:
            raise ValueError(f"不支持的子代理命令：{command}。当前只允许 {', '.join(sorted(AGENT_COMMANDS))}。")
        if command:
            config.agent_command = command
    if data.get("agent_permission") is not None:
        permission = str(data["agent_permission"]).strip()
        if permission not in AGENT_PERMISSIONS:
            raise ValueError("子代理权限只能是 readonly 或 write。")
        config.agent_permission = permission
    if config.agent_enabled and not agent_executable(config.agent_command):
        raise ValueError(f"找不到 {config.agent_command} CLI。请先安装并确保它已加入 PATH，或取消启用。")
    if data.get("agent_allowlist") is not None:
        cleaned: list[str] = []
        for item in data["agent_allowlist"]:
            value = str(item).strip()
            if not value:
                continue
            if not value.startswith("~") and not value.startswith("/"):
                raise ValueError(f"目录必须是绝对路径或 ~ 开头：{value}")
            cleaned.append(value)
        config.agent_allowlist = json.dumps(cleaned, ensure_ascii=False)
    if data.get("agent_timeout_seconds") is not None:
        config.agent_timeout_seconds = int(data["agent_timeout_seconds"])
    if data.get("agent_max_output_chars") is not None:
        config.agent_max_output_chars = int(data["agent_max_output_chars"])
    if data.get("agent_max_budget_usd") is not None:
        config.agent_max_budget_usd = float(data["agent_max_budget_usd"])
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return agent_tool_public(config)
