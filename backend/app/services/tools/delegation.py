from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import Session

from ...models import ToolConfig, utcnow
from .audit import _record_tool_call
from .config import get_tool_config
from .path_policy import _expand_path, _inside_allowlist, _parse_allowlist


AGENT_COMMANDS = {"claude", "codex"}


AGENT_PERMISSIONS = ("readonly", "write")


AGENT_APPROVAL_TIMEOUT_SECONDS = int(os.getenv("SANDRONE_AGENT_APPROVAL_TIMEOUT", "180"))


@dataclass
class _PendingAgentApproval:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None


PENDING_AGENT_APPROVALS: dict[str, _PendingAgentApproval] = {}


def create_agent_approval() -> str:
    """登记一次待确认的写入请求，返回 request_id。"""
    request_id = uuid.uuid4().hex
    PENDING_AGENT_APPROVALS[request_id] = _PendingAgentApproval()
    return request_id


async def wait_for_agent_approval(request_id: str) -> bool | None:
    """等待用户在网页上确认；True/False 为决定，None 表示请求不存在。"""
    pending = PENDING_AGENT_APPROVALS.get(request_id)
    if pending is None:
        return None
    try:
        await asyncio.wait_for(pending.event.wait(), timeout=AGENT_APPROVAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        pending.approved = False
    finally:
        PENDING_AGENT_APPROVALS.pop(request_id, None)
    return pending.approved


async def resolve_agent_approval(request_id: str, approved: bool) -> bool:
    pending = PENDING_AGENT_APPROVALS.get(request_id)
    if pending is None or pending.event.is_set():
        return False
    pending.approved = bool(approved)
    pending.event.set()
    return True


def _agent_cli_version(command: str) -> str:
    exe = shutil.which(command)
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
        "cli_available": shutil.which(command) is not None,
        "cli_version": _agent_cli_version(command),
    }


def _build_agent_args(
    command: str,
    exe: str,
    prompt: str,
    cwd: str,
    permission: str,
    budget: str,
) -> list[str]:
    if command == "claude":
        mode = "plan" if permission == "readonly" else "acceptEdits"
        return [
            exe,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--permission-mode",
            mode,
            "--max-budget-usd",
            budget,
            "--no-session-persistence",
            "--add-dir",
            cwd,
        ]
    # codex：exec 非交互模式默认不询问审批，只把最终回复写到 stdout，活动日志在 stderr。
    sandbox = "read-only" if permission == "readonly" else "workspace-write"
    return [
        exe,
        "exec",
        prompt,
        "--sandbox",
        sandbox,
        "--cd",
        cwd,
        "--skip-git-repo-check",
        "--ephemeral",
    ]


def _run_delegate_agent(
    session: Session,
    prompt: str,
    cwd_value: str,
    config: ToolConfig,
    *,
    permission_override: str | None = None,
) -> dict[str, Any]:
    """派本地 CLI 子代理在授权目录中执行任务。

    只读模式：claude plan / codex read-only，不写文件。
    写入模式：claude acceptEdits / codex workspace-write，仅限授权工作目录，由用户显式开启。
    """
    if not config.agent_enabled:
        return {"ok": False, "command": "", "cwd": "", "output": "", "duration_ms": 0, "message": "子代理功能未开启。请先在设置里开启并配置允许访问的目录。"}

    command = (config.agent_command or "claude").strip() or "claude"
    if command not in AGENT_COMMANDS:
        return {"ok": False, "command": command, "cwd": "", "output": "", "duration_ms": 0, "message": f"不支持的子代理命令：{command}。当前只允许 {', '.join(sorted(AGENT_COMMANDS))}。"}

    if permission_override is not None and permission_override not in AGENT_PERMISSIONS:
        raise ValueError("子代理权限覆盖只能是 readonly 或 write。")
    permission = permission_override or (
        config.agent_permission if config.agent_permission in AGENT_PERMISSIONS else "readonly"
    )
    exe = shutil.which(command)
    if not exe:
        return {"ok": False, "command": command, "cwd": "", "output": "", "duration_ms": 0, "message": f"找不到 {command} CLI。请先安装并确保它已加入 PATH。"}

    allowlist = _parse_allowlist(config.agent_allowlist)
    if not allowlist:
        return {"ok": False, "command": command, "cwd": "", "output": "", "duration_ms": 0, "message": "子代理没有配置允许访问的目录。"}

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
            return {"ok": False, "command": command, "cwd": cwd_value, "output": "", "duration_ms": 0, "message": f"目录不存在。当前允许的目录：\n{names}"}
        cwd = resolved
    else:
        cwd = allowlist[0]

    if not _inside_allowlist(cwd, allowlist):
        names = "\n".join(str(root) for root in allowlist)
        return {"ok": False, "command": command, "cwd": str(cwd), "output": "", "duration_ms": 0, "message": f"目录不在允许列表内。当前允许的目录：\n{names}"}

    budget = f"{max(0.1, config.agent_max_budget_usd):.2f}"
    args = _build_agent_args(command, exe, prompt.strip(), str(cwd), permission, budget)
    started = time.monotonic()
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=max(30, config.agent_timeout_seconds),
        )
    except subprocess.TimeoutExpired:
        latency = int((time.monotonic() - started) * 1000)
        return {"ok": False, "command": command, "cwd": str(cwd), "output": "", "duration_ms": latency, "message": f"子代理执行超过 {config.agent_timeout_seconds} 秒，已中止。"}
    except Exception as exc:
        latency = int((time.monotonic() - started) * 1000)
        return {"ok": False, "command": command, "cwd": str(cwd), "output": "", "duration_ms": latency, "message": f"子代理启动失败：{exc}"}

    latency = int((time.monotonic() - started) * 1000)
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    output = stdout
    if result.returncode != 0:
        tail = f"{stdout}\n\n--- stderr ---\n{stderr}".strip()[-3000:]
        return {"ok": False, "command": command, "cwd": str(cwd), "output": tail, "duration_ms": latency, "message": f"子代理返回退出码 {result.returncode}。"}
    if not output:
        # codex 会把活动日志放在 stderr，最终回答放在 stdout；若 stdout 为空则视为没有内容。
        return {"ok": False, "command": command, "cwd": str(cwd), "output": "", "duration_ms": latency, "message": "子代理没有返回内容。"}

    truncated = output[: config.agent_max_output_chars]
    if len(output) > config.agent_max_output_chars:
        truncated += "\n……[子代理输出过长，已截断]"
    return {"ok": True, "command": command, "cwd": str(cwd), "output": truncated, "duration_ms": latency, "message": "子代理任务完成。"}


def run_delegate_agent(
    session: Session,
    prompt: str,
    cwd_value: str,
    config: ToolConfig,
    *,
    permission_override: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    command = (config.agent_command or "claude").strip() or "claude"
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
    if config.agent_enabled and not shutil.which(config.agent_command):
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
