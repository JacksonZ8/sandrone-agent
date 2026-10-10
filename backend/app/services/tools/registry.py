from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from ...models import ToolConfig
from .config import get_tool_config
from .delegation import run_delegate_agent
from .files import read_file_with_audit
from .screen import analyze_screen
from .search import search_web_with_audit
from .web import web_fetch_sync


MAX_PROMPT_CHARS = 24_000


FILE_COMMAND_RE = re.compile(r"^(?:读(?:一下|取)?文件|看看文件|查看文件|读取|read file)\s*[:：]?\s*(.+)$", re.I)


SEARCH_COMMAND_RE = re.compile(r"^(?:搜索|搜一下|查一下|帮我搜|search)\s*[:：]?\s*(.+)$", re.I)


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "只读读取本机文本/代码文件；如果路径是目录，则列出目录内容。支持绝对路径、~、桌面/下载等别名，也可以只给文件名（白名单内唯一匹配）。禁止写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的绝对路径，例如 /Users/you/Documents/notes.md"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，只返回标题、链接与摘要。需要最新信息或外部资料时先用它找来源；摘要不够回答时，再用 web_fetch 读具体网页。回答里要说出用到的来源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，具体一些（名称、版本、年份）"},
                    "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "只看这些网站，如 [\"wiki.biligame.com\"]"},
                    "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "排除这些网站"},
                    "max_results": {"type": "integer", "description": "最多返回条数，默认 5", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "读取一个网页并只依据它回答你给的问题（网页先转成 Markdown，再由模型提取答案）。用于读 web_search 找到的具体页面。跳转到别的网站时会返回新地址，需要的话用新地址再调用一次。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整网址"},
                    "prompt": {"type": "string", "description": "想从这个网页知道什么，越具体越好"},
                },
                "required": ["url", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at_screen",
            "description": "截取用户当前电脑屏幕（设了“陪你用电脑”应用名单时只截名单内最前面的窗口）并用视觉模型分析。仅在用户明确要求看当前屏幕、当前界面、窗口或视觉内容时使用。如果用户问的是桌面/目录里有哪些文件、文件内容，请优先使用 read_file。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "关于屏幕内容的具体问题，可选"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_agent",
            "description": "派一个本地 CLI 子代理（Claude Code 或 Codex）在授权目录里做项目级、跨文件的代码/文档分析与修改，并返回文字摘要。权限由用户配置决定：只读模式不写文件；写入模式允许修改授权工作目录内的文件，但每次调用都必须先经过用户在网页上确认。仅在单个 read_file 不足以完成任务时才使用；任务复杂、涉及整个目录或需要深入推理时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "交给子代理的完整任务，包含目标、关注点、希望返回的格式"},
                    "cwd": {"type": "string", "description": "要分析的项目目录；留空则使用设置中的第一个允许目录"}
                },
                "required": ["prompt"],
            },
        },
    },
]


def run_tool_requests(session: Session, text: str) -> str:
    config = get_tool_config(session)
    blocks: list[str] = []

    file_match = FILE_COMMAND_RE.match(text.strip())
    if file_match:
        result = read_file_with_audit(session, file_match.group(1), config)
        if result["ok"]:
            content = result["content"]
            if len(content) > MAX_PROMPT_CHARS:
                content = content[:MAX_PROMPT_CHARS] + "\n……[内容过长，已截断]"
            blocks.append(
                "## 文件工具结果\n"
                f"路径：{result['path']}\n"
                "以下是文件内容，你可以直接回答用户关于这份文件的问题：\n"
                "```text\n" + content + "\n```"
            )
        else:
            blocks.append(f"## 文件工具结果\n读取失败：{result['message']}")

    search_match = SEARCH_COMMAND_RE.match(text.strip())
    if search_match:
        query = search_match.group(1).strip()
        try:
            results = search_web_with_audit(session, query, config)
            if results:
                lines = [f"- {r['title']}\n  {r['snippet']}\n  {r['url']}" for r in results]
                blocks.append(
                    "## 网页搜索结果\n"
                    f"查询：{query}\n"
                    + "\n".join(lines)
                )
            else:
                blocks.append("## 网页搜索结果\n没有找到相关结果。")
        except Exception as exc:
            blocks.append(f"## 网页搜索结果\n搜索失败：{exc}")

    return "\n\n".join(blocks)


def active_tool_schemas(config: ToolConfig) -> list[dict[str, Any]]:
    """只把当前已开启的工具定义交给模型，关闭的能力不进入工具列表。"""
    names = {"web_search", "web_fetch"}
    if config.files_enabled:
        names.add("read_file")
    if config.screen_enabled:
        names.add("look_at_screen")
    if config.agent_enabled:
        names.add("delegate_agent")
    return [schema for schema in TOOL_SCHEMAS if schema["function"]["name"] in names]


def execute_tool_call(session: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    config = get_tool_config(session)
    if name == "read_file":
        path = str(arguments.get("path") or "").strip()
        result = read_file_with_audit(session, path, config)
        if result["ok"]:
            content = result["content"]
            if len(content) > MAX_PROMPT_CHARS:
                content = content[:MAX_PROMPT_CHARS] + "\n……[内容过长，已截断]"
            preview = result["path"]
            return {"ok": True, "preview": preview, "content": content}
        return {"ok": False, "error": result["message"]}

    if name == "web_search":
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "搜索关键词为空。"}
        limit = int(arguments.get("max_results") or 5)
        limit = max(1, min(10, limit))
        def domains(key: str) -> list[str]:
            value = arguments.get(key) or []
            return [str(item) for item in value] if isinstance(value, list) else []

        try:
            results = search_web_with_audit(
                session,
                query,
                config,
                limit=limit,
                allowed_domains=domains("allowed_domains"),
                blocked_domains=domains("blocked_domains"),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "results": results}

    if name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        prompt = str(arguments.get("prompt") or "").strip() or "这个网页的主要内容是什么？"
        if not url:
            return {"ok": False, "error": "网址为空。"}
        try:
            return web_fetch_sync(session, url, prompt)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc)[:300]}

    if name == "look_at_screen":
        question = str(arguments.get("question") or "看看我现在的屏幕，告诉我当前在做什么。").strip()
        result = analyze_screen(session, question)
        if result["ok"]:
            return {"ok": True, "analysis": result["analysis"]}
        return {"ok": False, "error": result["message"]}

    if name == "delegate_agent":
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "子代理任务为空。"}
        cwd = str(arguments.get("cwd") or "").strip()
        result = run_delegate_agent(session, prompt, cwd, config)
        if result["ok"]:
            preview = f"{result['cwd']} · {prompt[:80]}"
            return {"ok": True, "preview": preview, "output": result["output"], "duration_ms": result["duration_ms"]}
        return {"ok": False, "error": result["message"]}

    return {"ok": False, "error": f"未知工具：{name}"}
