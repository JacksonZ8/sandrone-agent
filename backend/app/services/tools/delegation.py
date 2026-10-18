from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlmodel import Session

from ...models import ToolConfig, utcnow
from . import agent_sandbox, agent_workspace
from .audit import _record_tool_call
from .config import get_tool_config
from .path_policy import _expand_path, _inside_allowlist, _parse_allowlist


AGENT_COMMANDS = {"claude", "codex", "opencode", "hermes", "ohdsh"}
AGENT_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "hermes": "Hermes", "ohdsh": "Oh-DSH"}
AGENT_ORDER = ("claude", "codex", "opencode", "hermes", "ohdsh")
# 走 ACP（Agent Client Protocol）的智能体：启动命令，以及只读/写入各切到它的哪个模式。
# 过程实时显示在任务卡上，它要权限时在卡上问用户；只读模式下改写/执行类请求直接拒绝。
ACP_AGENTS: dict[str, dict[str, Any]] = {
    "claude": {"command": ["claude-agent-acp"], "modes": {"readonly": "plan", "write": "default"}},
    "codex": {"command": ["codex-acp"], "modes": {"readonly": "read-only", "write": "workspace-write"}},
    "opencode": {"command": ["opencode", "acp"], "modes": {"readonly": "plan", "write": "build"}},
    "hermes": {"command": ["hermes-acp"], "modes": {}},
}
# 只有终端界面的智能体：在伪终端里跑，任务卡上是真终端，用户可以接手；命令名 -> 桌面版自带的启动器
TERMINAL_AGENTS = {"ohdsh": Path("/Applications/Oh-DSH Desktop.app/Contents/Resources/bin/ohdsh")}


AGENT_PERMISSIONS = ("readonly", "write")


# ChatGPT 桌面版自带的 codex 跟着 App 更新，和 App 用同一份配置与模型；codex-acp 通过 CODEX_PATH 用它
BUNDLED_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")


def agent_executable(command: str) -> str | None:
    """智能体的入口：终端类用桌面版自带的启动器，其余是 ACP 入口（适配器装在 ~/.local/bin 或 Homebrew 下）。"""
    if command in TERMINAL_AGENTS:
        bundled = TERMINAL_AGENTS[command]
        return str(bundled) if os.access(bundled, os.X_OK) else shutil.which(command)
    if command in ACP_AGENTS:
        name = ACP_AGENTS[command]["command"][0]
        return shutil.which(name) or shutil.which(name, path=agent_sandbox.SEARCH_PATH)
    return None


def acp_env(command: str = "", permission: str = "readonly") -> dict[str, str]:
    """ACP 适配器多是 Node 脚本：PATH 里要有 ~/.local/bin（node 在那里）。
    codex-acp 用 ChatGPT 自带的 codex，并带上我们的权限配置（私密路径禁读）。"""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([agent_sandbox.SEARCH_PATH, env.get("PATH", "")])
    if command == "codex":
        codex = os.getenv("SANDRONE_CODEX_BIN", "").strip() or (str(BUNDLED_CODEX) if os.access(BUNDLED_CODEX, os.X_OK) else "")
        if codex:
            env["CODEX_PATH"] = codex
        env["CODEX_CONFIG"] = json.dumps(agent_sandbox.codex_config(permission), ensure_ascii=False)
    return env


def _agent_cli_version(command: str) -> str:
    exe = agent_executable(command)
    if not exe:
        return ""
    if command in TERMINAL_AGENTS:
        return "桌面版自带的终端界面"
    return f"走 ACP（{Path(exe).name}）"


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
        "agents": available_agents(),
    }


def available_agents() -> list[dict[str, Any]]:
    """她能派活的智能体，以及这台电脑上装没装。"""
    return [
        {"id": command, "label": AGENT_LABELS[command], "available": agent_executable(command) is not None,
         "protocol": "terminal" if command in TERMINAL_AGENTS else "acp"}
        for command in AGENT_ORDER
    ]


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


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
        return {"ok": False, "command": command, "cwd": "", "message": f"这台电脑上没找到 {AGENT_LABELS[command]} 的接入程序，请先安装。"}

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
    live_task_id: int = 0,
    workspace_id: int = 0,
) -> dict[str, Any]:
    """派本机智能体在授权目录中执行任务：终端类在伪终端里跑，其余走 ACP。

    只读/写入切到智能体自己对应的模式（见 ACP_AGENTS）；Claude Code 与 OpenCode 另外放进 srt 沙盒。
    写入任务给了 workspace_id 且目录在 git 仓库里时，在独立工作区里改（见 agent_workspace），不直接改原目录。
    """
    target = resolve_agent_target(
        config, cwd_value, permission_override=permission_override, command_override=command_override
    )
    if not target["ok"]:
        return {"ok": False, "command": target["command"], "cwd": target["cwd"], "output": "", "duration_ms": 0, "message": target["message"]}
    command = target["command"]
    workdir, gitdir = target["cwd"], ""
    # 续接的任务即使是只读，也看同一个工作区里改过的样子
    if workspace_id and (target["permission"] == "write" or agent_workspace.exists(workspace_id)):
        try:
            workdir, gitdir = agent_workspace.prepare(workspace_id, target["cwd"])
        except (agent_workspace.WorkspaceError, OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "command": command, "cwd": target["cwd"], "output": "", "duration_ms": 0, "message": f"开独立工作区失败：{exc}"}
    if command in TERMINAL_AGENTS:
        from .terminal_agent import run_terminal_task

        result = run_terminal_task(
            [target["exe"]],
            prompt,
            workdir,
            task_id=live_task_id,
            timeout_seconds=config.agent_timeout_seconds,
            resume_session_id=resume_session_id,
            on_start=on_start,
        )
    else:
        from .acp import run_acp_task

        spec = ACP_AGENTS[command]
        args = [target["exe"], *spec["command"][1:]]
        with tempfile.TemporaryDirectory(prefix="sandrone_agent_") as tmp:
            if agent_sandbox.sandboxed(command):
                try:
                    args = agent_sandbox.wrap(command, args, workdir, Path(tmp), extra_write=[gitdir] if gitdir else None)
                except RuntimeError as exc:
                    return {"ok": False, "command": command, "cwd": target["cwd"], "output": "", "duration_ms": 0, "message": str(exc)}
            result = run_acp_task(
                args,
                prompt,
                workdir,
                task_id=live_task_id,
                readonly=target["permission"] == "readonly",
                resume_session_id=resume_session_id,
                timeout_seconds=config.agent_timeout_seconds,
                on_start=on_start,
                env=acp_env(command, target["permission"]),
                mode=spec["modes"].get(target["permission"], ""),
            )
    limit = config.agent_max_output_chars
    if len(result["output"]) > limit:
        result["output"] = result["output"][:limit] + "\n……[输出过长，已截断]"
    return {**result, "command": command, "cwd": target["cwd"]}


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
    live_task_id: int = 0,
    workspace_id: int = 0,
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
            live_task_id=live_task_id,
            workspace_id=workspace_id,
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
        raise ValueError(f"这台电脑上没找到 {AGENT_LABELS.get(config.agent_command, config.agent_command)} 的接入程序，请先安装或取消启用。")
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
