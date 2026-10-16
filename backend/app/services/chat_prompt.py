"""系统提示的拼装：固定部分（人设 + 工具原则）与每轮变化部分（时间、关系、看屏/游戏上下文、渠道与偏好、记忆），
以及它们在消息列表里的位置（前缀缓存友好）。网页、微信、通话、看屏点评与主动消息共用。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from ..config import PROMPTS_DIR
from .agent_tasks import agent_tasks_block
from .games.play import game_context_block
from .memory_service import build_memory_context_with_traces
from .relationship_service import relationship_prompt_block
from .screen_companion import screen_context_block
from .user_emotion import user_emotion_block


CHANNEL_HINTS = {
    "wechat_dm": (
        "## 当前渠道\n"
        "对方正在用微信和你说话。像发微信一样回复：一两句短句，口语化，不用 Markdown、列表或标题。"
        "时间线里也有你们在网页上的对话，可以自然接着聊。"
    ),
    "voice": (
        "## 当前渠道\n"
        "你们正在实时语音通话，你说的每个字都会被念出来：像打电话一样说话，口语、短句，"
        "不用 Markdown、列表、链接或表情。对方的话来自语音识别，个别字可能听错，按语境理解即可。"
    ),
}


def reply_style_block(
    *,
    reply_length: str | None = None,
    action_text: bool | None = None,
    voice_mode: bool = False,
) -> str:
    """网页端按用户偏好给出本轮的回复长度与动作描写要求。"""
    lines: list[str] = []
    if reply_length == "short":
        lines.append("这一轮回复控制在一到三句，说完就停。")
    elif reply_length == "long":
        lines.append("这一轮可以多说一些，把想法讲完整；仍然是说话的口吻，不写报告或清单。")
    if voice_mode:
        lines.append(
            "这一轮的回复会被合成语音念出来：只写说出口的话，不写括号里的动作、神态或舞台说明，"
            "不用 Markdown、列表、链接或表情符号。"
        )
    elif action_text is False:
        lines.append("不要写括号里的动作、神态或舞台说明，只写说出口的话。")
    elif action_text is True:
        lines.append("可以偶尔用全角括号单独成段写一个简短的动作或神态，不是每轮都要写。")
    if not lines:
        return ""
    return "## 本轮回复要求\n" + "\n".join(f"- {line}" for line in lines)


def _persona_text() -> str:
    path = PROMPTS_DIR / "sandrone_persona.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "你是桑多涅，愚人众执行官第七席「木偶」。冷静、骄傲、理性，擅长机械与数理。"


TOOL_PRINCIPLES = (
            "## 工具使用原则\n"
            "- 用户问文件、目录、桌面有哪些内容、代码内容：优先 read_file。\n"
            "- 需要最新信息或外部资料：先 web_search 找来源，摘要不够时用 web_fetch 读具体网页；要读几个网页就在同一轮里一起调用（会并行执行）；回答里说出用到的来源，不要把没读过的网页当依据。\n"
            "- 只有用户明确要看当前屏幕、窗口、界面或画面：才使用 look_at_screen（看一眼）；明确要你一直看着时才用 watch_screen（陪着看）。\n"
            "- 只有任务涉及整个项目、多个文件交叉分析或复杂推理、单次 read_file 不够时，才使用 delegate_agent；它较慢、会消耗额外额度。\n"
            "- delegate_agent 只生成任务卡：用户确认后才在后台执行，做完会另行通知。调用后请用户确认，不要说已经在做或已经做完。\n"
            "- 用户问子代理任务的进度或结果时，按“子代理任务”里的记录回答；要接着某个任务做就填 continue_task_id。\n"
            "- 只有用户明确要接着他在 Codex App 里的某个线程做时，才填 codex_thread。\n"
            "- 用户明确要你忘掉某件事时用 forget_memory；指出你记错了时用 correct_memory。做完简短确认，不要复述要忘掉的细节。"
)


def build_prompt_parts(
    session: Session,
    query: str,
    recent_message_ids: set[int],
    extra_context: str = "",
    memory_trace_collector: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """返回 (固定部分, 这一轮会变的部分)：固定的人设与工具原则在前，时间、关系、看屏/游戏上下文、渠道与记忆在后。"""
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y年%m月%d日 %H:%M")
    weekday = "一二三四五六日"[now.weekday()]
    static = "\n\n".join((_persona_text(), TOOL_PRINCIPLES))
    parts = [f"现在是 {stamp}，星期{weekday}。", relationship_prompt_block(session), user_emotion_block(session)]
    for block in (screen_context_block(session), game_context_block(session, query), agent_tasks_block(session)):
        if block:
            parts.append(block)
    if extra_context:
        parts.append(extra_context)
    memory_context, traces = build_memory_context_with_traces(session, query, recent_message_ids)
    if memory_trace_collector is not None:
        memory_trace_collector.extend(traces)
    if memory_context:
        parts.append(memory_context)
    return static, "\n\n".join(part for part in parts if part)


def build_system_prompt(
    session: Session,
    query: str,
    recent_message_ids: set[int],
    extra_context: str = "",
    memory_trace_collector: list[dict[str, Any]] | None = None,
) -> str:
    static, dynamic = build_prompt_parts(session, query, recent_message_ids, extra_context, memory_trace_collector)
    return f"{static}\n\n{dynamic}"


def layout_messages(static: str, dynamic: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[固定的人设与规则] + 历史 + [这一轮会变的上下文] + 最新一句。

    固定部分和历史放在前面、每轮都在变的时间、关系、看屏/游戏上下文与记忆放到最后，
    模型服务的前缀缓存才能命中大部分输入，首字更快、也更便宜。
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": static}]
    if history and history[-1]["role"] == "user":
        messages.extend(history[:-1])
        messages.append({"role": "system", "content": dynamic})
        messages.append(history[-1])
    else:
        messages.extend(history)
        messages.append({"role": "system", "content": dynamic})
    return messages
