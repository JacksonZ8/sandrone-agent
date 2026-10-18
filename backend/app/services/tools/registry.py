from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from ...models import ToolConfig
from .config import get_tool_config
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
            "description": "只读读取本机文本/代码文件；如果路径是目录，则列出目录内容。支持绝对路径、~、桌面/下载等别名，也可以只给文件名（在桌面、文稿、下载里找唯一匹配）。可读范围是用户主目录，资源库、隐藏文件夹和密钥类文件不能读。禁止写入。",
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
            "name": "forget_memory",
            "description": "用户明确要你忘掉、别再记得某件事时调用：相关的长期记忆不再被想起（数据库里保留，不物理删除），相关的旧对话也不再被想起。只在用户明确提出时使用，不要自己决定忘掉什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要忘掉的内容，用用户的原话或关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_memory",
            "description": "用户指出你记错了某件事时调用：把记错的那条长期记忆换成正确的。只在用户明确纠正时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "wrong": {"type": "string", "description": "记错的内容，用关键词或你原来的说法"},
                    "correct": {"type": "string", "description": "正确的内容，写成一条完整的记忆，例如“用户住在广州。”"},
                },
                "required": ["wrong", "correct"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at_screen",
            "description": "看一眼用户当前的电脑屏幕并回答他的问题（前台是游戏时只看游戏窗口，并带上这个游戏的档案和知识；认出没建档的游戏会自动加进游戏资料）。仅在用户明确要求看当前屏幕、界面、窗口或画面时使用。如果用户问的是桌面/目录里有哪些文件、文件内容，请优先使用 read_file。",
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
            "name": "watch_screen",
            "description": "用户明确要你一直看着屏幕时（如“这局陪我看着”“接下来看着我写代码”）开始“陪着看”：每隔几分钟看一次前台那个应用，多数时候安静，到时间自动结束；用户说“别看了”时停止。偶尔问一句屏幕上的东西用 look_at_screen 就够了。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "stop"], "description": "start 开始，stop 结束"},
                    "minutes": {"type": "integer", "description": "看多久（分钟），默认 60，最多 240"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_agent",
            "description": "把项目级、跨文件的代码/文档分析或修改交给这台电脑上的编程智能体（Claude Code、Codex、Hermes 或 Oh-DSH）。用户点名要哪个就用哪个。调用后只会生成一张任务卡，不会立刻执行、也不会马上有结果：用户确认后在后台执行，做完会通知。权限由用户配置决定：只读模式不写文件；写入模式可以修改授权目录内的文件。仅在单个 read_file 不足以完成任务时才使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "交给子代理的完整任务，包含目标、关注点、希望返回的格式；要保留用户要求的最终用途和约束，不要替子代理先做答"},
                    "title": {"type": "string", "description": "任务卡标题，十来个字的中文短语"},
                    "spoken_summary": {"type": "string", "description": "一句口语化的中文摘要，说清让谁做什么、在哪个项目、有什么重要约束；语音通话里会照着念给用户确认"},
                    "agent": {"type": "string", "enum": ["claude", "codex", "opencode", "hermes", "ohdsh"], "description": "交给哪个智能体：claude=Claude Code，codex=Codex（就是 ChatGPT 桌面版里的 Codex，派的任务在 ChatGPT 里也看得到；用户说让 ChatGPT 做也选它），opencode=OpenCode，hermes=Hermes，ohdsh=Oh-DSH（DeepSeek Harness）。用户没点名就不填，用设置里的默认。续接任务时忽略"},
                    "cwd": {"type": "string", "description": "要处理的项目目录；留空则使用设置中的第一个允许目录。续接任务或接着 Codex App 线程时忽略"},
                    "continue_task_id": {"type": "integer", "description": "接着之前某个子代理任务继续（同一个会话，记得上次做了什么）时填它的编号"},
                    "codex_thread": {"type": "string", "description": "用户明确要接着他在 Codex App 里的某个已有线程继续时，填线程标题里的关键词或线程 id；没提就不要填"}
                },
                "required": ["prompt", "title", "spoken_summary"],
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
    names = {"web_search", "web_fetch", "forget_memory", "correct_memory"}
    if config.files_enabled:
        names.add("read_file")
    if config.screen_enabled:
        names.update(("look_at_screen", "watch_screen"))
    if config.agent_enabled:
        names.add("delegate_agent")
    return [schema for schema in TOOL_SCHEMAS if schema["function"]["name"] in names]


def execute_tool_call(session: Session, name: str, arguments: dict[str, Any], *, channel: str = "web") -> dict[str, Any]:
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

    if name == "forget_memory":
        from ..memory.revision import forget_memories

        return forget_memories(session, str(arguments.get("query") or ""))

    if name == "correct_memory":
        from ..memory.revision import correct_memory

        return correct_memory(session, str(arguments.get("wrong") or ""), str(arguments.get("correct") or ""))

    if name == "look_at_screen":
        question = str(arguments.get("question") or "看看我现在的屏幕，告诉我当前在做什么。").strip()
        result = analyze_screen(session, question)
        if not result["ok"]:
            return {"ok": False, "error": result["message"]}
        outcome: dict[str, Any] = {"ok": True, "analysis": result["analysis"]}
        if result.get("game"):
            outcome["game"] = result["game"]
            if result.get("game_added"):
                outcome["note"] = f"第一次认出《{result['game']}》，已自动加进游戏资料；以后看到它会带上档案和知识。可以顺口告诉用户。"
        return outcome

    if name == "watch_screen":
        from ..screen_companion import start_watch, stop_watch

        if str(arguments.get("action") or "start") == "stop":
            return stop_watch(session)
        return start_watch(session, arguments.get("minutes"))

    if name == "delegate_agent":
        from ..agent_tasks import propose_task

        return propose_task(session, arguments, channel=channel)

    return {"ok": False, "error": f"未知工具：{name}"}
