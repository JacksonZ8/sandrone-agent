"""派出去的智能体在沙盒里运行：用 Anthropic 的 sandbox-runtime（srt，macOS 上是系统自带的 Seatbelt），不用容器。

- 读：默认可读，但私密目录（SSH/云账号/GPG/gh 凭据）和桑多涅自己的数据目录、.env 读不到。
- 写：只有任务目录、临时目录和这个智能体自己的状态目录。
- 网络：默认全禁，只放行这个智能体要连的服务（经 srt 的代理按域名放行）。

Codex 不放进 srt：Seatbelt 不能嵌套（嵌套会让它执行命令全部失败）。它用自己的系统沙盒，由我们经 CODEX_CONFIG 给它一个
权限配置（permission profile，Codex 的测试版功能）：在只读或工作区写入的基础上，同一份私密路径一律禁读。
OpenCode 的 ACP 模式要在本机起服务，必须放行本机端口；macOS 上这同时允许它连本机任意端口（包括桑多涅的后端），是已知缺口。
沙盒没装时这些智能体不运行（不退回无沙盒）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...config import DATA_DIR, PROJECT_ROOT

HOME = Path.home()
SEARCH_PATH = os.pathsep.join([str(HOME / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"])

DENY_READ = [
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/.docker", "~/.kube",
    str(DATA_DIR), str(PROJECT_ROOT / ".env"),
]

# 常见模型服务：用户在 OpenCode 里可能配了其中任何一家
LLM_HOSTS = [
    "api.openai.com", "api.anthropic.com", "api.deepseek.com", "openrouter.ai", "*.openrouter.ai",
    "generativelanguage.googleapis.com", "api.moonshot.cn", "open.bigmodel.cn", "dashscope.aliyuncs.com",
    "api.groq.com", "api.mistral.ai", "api.x.ai",
]

PROFILES: dict[str, dict[str, Any]] = {
    "claude": {
        "domains": ["anthropic.com", "*.anthropic.com", "claude.ai", "*.claude.ai", "claude.com", "*.claude.com"],
        "state": ["~/.claude", "~/.claude.json", "~/.claude.json.lock", "~/Library/Caches/claude-cli-nodejs"],
        "local_binding": False,
    },
    "opencode": {
        "domains": ["opencode.ai", "*.opencode.ai", "models.dev", "*.models.dev", *LLM_HOSTS],
        "state": ["~/.local/share/opencode", "~/.local/state/opencode", "~/.cache/opencode", "~/.config/opencode"],
        "local_binding": True,
    },
}


def srt_executable() -> str | None:
    return shutil.which("srt") or shutil.which("srt", path=SEARCH_PATH)


def sandboxed(agent: str) -> bool:
    return agent in PROFILES


def settings(agent: str, cwd: str, extra_write: list[str] | None = None) -> dict[str, Any]:
    profile = PROFILES[agent]
    temp = os.path.realpath(tempfile.gettempdir())
    return {
        "network": {
            "allowedDomains": list(profile["domains"]),
            "deniedDomains": [],
            "allowLocalBinding": bool(profile["local_binding"]),
        },
        "filesystem": {
            "denyRead": list(DENY_READ),
            "allowWrite": [cwd, *(extra_write or []), *profile["state"], "/tmp", "/private/tmp", temp],
            "denyWrite": [str(PROJECT_ROOT / ".env")],
        },
    }


def wrap(agent: str, args: list[str], cwd: str, settings_dir: Path, *, extra_write: list[str] | None = None) -> list[str]:
    """返回放进沙盒后的命令；设置写在 settings_dir 里（任务结束后由调用方删掉）。"""
    srt = srt_executable()
    if srt is None:
        raise RuntimeError("沙盒（sandbox-runtime）没装好，为了安全没有运行。可以 npm install -g @anthropic-ai/sandbox-runtime。")
    path = settings_dir / f"srt-{agent}.json"
    path.write_text(json.dumps(settings(agent, cwd, extra_write), ensure_ascii=False, indent=1), encoding="utf-8")
    return [srt, "-s", str(path), *args]


def codex_config(permission: str) -> dict[str, Any]:
    """给 codex-acp 的 CODEX_CONFIG：只读基于 :read-only，写入基于 :workspace，私密路径禁读。"""
    deny = {path: "deny" for path in DENY_READ}
    return {
        "default_permissions": "sandrone",
        "permissions": {"sandrone": {"extends": ":read-only" if permission == "readonly" else ":workspace", "filesystem": deny}},
    }


def describe(agent: str) -> str:
    """任务卡上的一句话。"""
    if agent == "codex":
        return "Codex 在它自己的系统沙盒里运行：读不到 ~/.ssh 等私密目录和桑多涅自己的数据；写入模式也只能改任务目录，联网或写别处要先问你。"
    if not sandboxed(agent):
        return ""
    text = "在沙盒里运行：读不到 ~/.ssh 等私密目录和桑多涅自己的数据，只能写任务目录，只能连它自己的服务。"
    if PROFILES[agent]["local_binding"]:
        text += "（它需要本机端口，所以还能连到本机的其他服务。）"
    return text
