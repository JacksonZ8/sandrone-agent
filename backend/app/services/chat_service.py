from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlmodel import Session, func, select

from ..config import PROMPTS_DIR, RECENT_MESSAGE_LIMIT
from ..database import engine
from ..models import Message, utcnow
from .games.play import game_context_block
from .llm_service import LLMError, chat_completion_stream, resolve_thinking
from .memory_service import build_memory_context_with_traces, estimate_importance, estimate_valence
from .message_service import delete_message
from .provider_service import get_agent_state, get_config
from .relationship_service import apply_conversation_affection, relationship_prompt_block
from .screen_companion import screen_context_block
from .style_guard import recent_repetition_block
from .tool_service import (
    AGENT_APPROVAL_TIMEOUT_SECONDS,
    active_tool_schemas,
    create_agent_approval,
    execute_tool_call,
    get_tool_config,
    run_tool_requests,
    wait_for_agent_approval,
)


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


HISTORY_STEP = 10  # 对话窗口的起点按 10 条对齐：窗口不会每轮都滑动，模型服务的前缀缓存才能命中历史


def get_recent_messages(
    session: Session,
    *,
    thread_id: str = "main",
    limit: int = RECENT_MESSAGE_LIMIT,
    aligned: bool = False,
) -> list[Message]:
    """最近的消息（按时间正序）。aligned=True 时多带至多 9 条，让窗口起点只在每 10 条时前移一次。"""
    if aligned:
        total = session.exec(select(func.count()).select_from(Message).where(Message.thread_id == thread_id)).one()
        offset = max(0, int(total) - limit)
        offset -= offset % HISTORY_STEP
        statement = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at, Message.id)
            .offset(offset)
        )
        return list(session.exec(statement).all())
    statement = (
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    rows = list(session.exec(statement).all())
    return list(reversed(rows))


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


def message_public(message: Message) -> dict[str, Any]:
    try:
        tool_calls = json.loads(message.tool_calls_json or "[]")
    except json.JSONDecodeError:
        tool_calls = []
    try:
        memory_traces = json.loads(message.memory_traces_json or "[]")
    except json.JSONDecodeError:
        memory_traces = []
    try:
        inner_state = json.loads(message.inner_state or "{}")
    except json.JSONDecodeError:
        inner_state = {}
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "channel": message.channel,
        "sender_id": message.sender_id,
        "chat_id": message.chat_id,
        "turn_index": message.turn_index,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "provider": message.provider,
        "model": message.model,
        "latency_ms": message.latency_ms,
        "audio_path": message.audio_path,
        "tool_calls": tool_calls if isinstance(tool_calls, list) else [],
        "memory_traces": memory_traces if isinstance(memory_traces, list) else [],
        # 她主动发起的消息（主动消息调度写入 inner_state.initiated）
        "initiated": bool(isinstance(inner_state, dict) and inner_state.get("initiated")),
    }


def next_turn_index(session: Session, *, thread_id: str = "main") -> int:
    max_turn = session.exec(select(func.max(Message.turn_index)).where(Message.thread_id == thread_id)).one()
    return int(max_turn or 0) + 1


TOOL_PRINCIPLES = (
            "## 工具使用原则\n"
            "- 用户问文件、目录、桌面有哪些内容、代码内容：优先 read_file。\n"
            "- 需要最新信息或外部资料：先 web_search 找来源，摘要不够时用 web_fetch 读具体网页；要读几个网页就在同一轮里一起调用（会并行执行）；回答里说出用到的来源，不要把没读过的网页当依据。\n"
            "- 只有用户明确要看当前屏幕、窗口、界面或视觉内容：才使用 look_at_screen。\n"
            "- 只有任务涉及整个项目、多个文件交叉分析或复杂推理、单次 read_file 不够时，才使用 delegate_agent；它较慢、会消耗额外额度。\n"
            "- delegate_agent 的权限由用户在设置中决定：只读模式只能看，写入模式可以改授权目录内的文件；写入模式调用后网页会弹出确认，等用户决定即可。\n"
            "- 无论哪种模式，都要在最终回复里说明子代理实际做了什么。"
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
    parts = [f"现在是 {stamp}，星期{weekday}。", relationship_prompt_block(session)]
    for block in (screen_context_block(session), game_context_block(session, query)):
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


async def _finish_assistant_message(
    session: Session,
    *,
    config: Any,
    result: dict[str, Any],
    thread_id: str,
    channel: str,
    sender_id: str,
    chat_id: str,
    turn_index: int,
    user_message: Message,
    tool_traces: list[dict[str, Any]],
    memory_traces: list[dict[str, Any]] | None = None,
    apply_affection: bool = True,
) -> dict[str, Any]:
    state = get_agent_state(session)
    assistant_message = Message(
        thread_id=thread_id,
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        turn_index=turn_index,
        role="assistant",
        content=result["text"],
        provider=config.provider,
        model=config.model,
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        latency_ms=result["latency_ms"],
        inner_state=json.dumps(
            {"mood": state.mood, "energy": state.energy, "focus": state.focus},
            ensure_ascii=False,
        ),
        tool_calls_json=json.dumps(tool_traces, ensure_ascii=False),
        memory_traces_json=json.dumps(memory_traces or [], ensure_ascii=False),
    )
    session.add(assistant_message)
    state.energy = max(0.05, round(state.energy - 0.02, 3))
    state.focus = "和用户对话"
    state.updated_at = utcnow()
    session.add(state)
    session.commit()
    session.refresh(assistant_message)

    if apply_affection and channel in ("web", "wechat_dm", "voice"):
        apply_conversation_affection(
            session,
            user_message_id=user_message.id,
            user_text=user_message.content,
        )

    return {
        "user_message": message_public(user_message),
        "assistant_message": message_public(assistant_message),
        "tool_calls": tool_traces,
        "error": "",
    }


def _run_tool_isolated(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """在工作线程里用独立的数据库会话执行一个工具（并行执行时不能共用会话）。"""
    with Session(engine) as session:
        try:
            return execute_tool_call(session, name, arguments)
        except Exception as exc:  # noqa: BLE001 - 单个工具失败不影响同轮其他工具
            return {"ok": False, "error": str(exc)[:300]}


async def _stream_round(
    session: Session,
    config: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    thinking: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式跑一轮模型：先逐段产出 {"type": "delta"}，最后产出 {"type": "round_done", "result": ...}。

    这一轮如果最后是去调工具，前端要把已经显示的半截文字清掉（delta_reset）。
    """
    shown = False
    result: dict[str, Any] = {}
    async for event in chat_completion_stream(
        session, config, messages, purpose="chat", tools=tools, thinking=thinking
    ):
        if event["type"] == "delta":
            shown = True
            yield {"type": "delta", "text": event["text"]}
        elif event["type"] == "done":
            result = event
    if shown and result.get("tool_calls"):
        yield {"type": "delta_reset"}
    yield {"type": "round_done", "result": result}


async def create_chat_turn_stream(
    session: Session,
    content: str,
    *,
    thread_id: str = "main",
    channel: str = "web",
    sender_id: str = "owner",
    chat_id: str = "web",
    style_context: str = "",
    existing_user_message: Message | None = None,
    replace_message_id: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """一轮对话。

    重新生成时传入 existing_user_message（沿用那条用户发言、不重复计算好感）和
    replace_message_id（旧回复不进上下文，新回复成功后才删除它）。
    """
    config = get_config(session)

    if existing_user_message is not None:
        user_message = existing_user_message
        turn_index = user_message.turn_index
        content = user_message.content
    else:
        turn_index = next_turn_index(session, thread_id=thread_id)
        user_message = Message(
            thread_id=thread_id,
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            turn_index=turn_index,
            role="user",
            content=content.strip(),
            importance=estimate_importance(content),
            valence=estimate_valence(content),
        )
        session.add(user_message)
        session.commit()
        session.refresh(user_message)

    try:
        recent = [
            message
            for message in get_recent_messages(session, thread_id=thread_id, aligned=True)
            if message.id != replace_message_id
        ]
        recent_ids = {message.id for message in recent if message.id is not None}
        tool_traces: list[dict[str, Any]] = []
        memory_traces: list[dict[str, Any]] = []
        tool_context = run_tool_requests(session, content) if config.provider == "mock" else ""
        variety_context = recent_repetition_block(
            (message.content for message in recent if message.role == "assistant"),
            content,
        )
        channel_context = CHANNEL_HINTS.get(channel, "")
        extra_context = "\n\n".join(
            part for part in (channel_context, style_context, tool_context, variety_context) if part
        )
        static_prompt, dynamic_prompt = build_prompt_parts(
            session,
            content,
            recent_ids,
            extra_context,
            memory_trace_collector=memory_traces,
        )
        llm_messages = layout_messages(
            static_prompt,
            dynamic_prompt,
            [{"role": m.role, "content": m.content} for m in recent if m.role in ("user", "assistant")],
        )

        tool_config = get_tool_config(session)
        tool_schemas = (
            None if config.provider == "mock" else active_tool_schemas(tool_config)
        )
        yield {"type": "thinking", "message": "桑多涅正在回忆和整理上下文…"}
        thinking = resolve_thinking(config.reply_thinking, content)

        result: dict[str, Any] = {}
        async for event in _stream_round(session, config, llm_messages, tools=tool_schemas, thinking=thinking):
            if event["type"] == "round_done":
                result = event["result"]
            else:
                yield event

        # Agent loop：模型决定调工具 -> 执行 -> 结果回填 -> 再推理
        for _ in range(4):
            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                break

            assistant_tool_message: dict[str, Any] = {
                "role": "assistant",
                "content": result.get("text") or "",
                "tool_calls": [
                    {
                        "id": call.get("id") or f"call_{idx}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for idx, call in enumerate(tool_calls)
                ],
            }
            llm_messages.append(assistant_tool_message)

            def needs_approval(call: dict[str, Any]) -> bool:
                return call["name"] == "delegate_agent" and tool_config.agent_permission == "write"

            # 只读工具（搜索、读网页、读文件、看屏幕）同一轮里并行执行；需要确认的写入仍逐个处理
            parallel = [call for call in tool_calls if not needs_approval(call)]
            for call in parallel:
                yield {"type": "tool_call", "name": call["name"], "arguments": call.get("arguments") or {}}
            parallel_outcomes = await asyncio.gather(
                *(asyncio.to_thread(_run_tool_isolated, call["name"], call.get("arguments") or {}) for call in parallel)
            )
            outcomes_by_call = {id(call): outcome for call, outcome in zip(parallel, parallel_outcomes)}

            for idx, call in enumerate(tool_calls):
                arguments = call.get("arguments") or {}
                outcome: dict[str, Any]

                if id(call) in outcomes_by_call:
                    outcome = outcomes_by_call[id(call)]
                elif channel == "web":
                    request_id = create_agent_approval()
                    yield {
                        "type": "tool_call",
                        "name": call["name"],
                        "arguments": arguments,
                        "requires_confirmation": True,
                        "request_id": request_id,
                        "confirmation_timeout_seconds": AGENT_APPROVAL_TIMEOUT_SECONDS,
                    }
                    approved = await wait_for_agent_approval(request_id)
                    yield {
                        "type": "approval_result",
                        "request_id": request_id,
                        "approved": bool(approved),
                    }
                    if approved is True:
                        outcome = await asyncio.to_thread(
                            execute_tool_call,
                            session,
                            call["name"],
                            arguments,
                        )
                    else:
                        reason = (
                            "用户拒绝了这次写入。"
                            if approved is False
                            else "写入确认请求已失效或等待超时，本次未执行。"
                        )
                        outcome = {"ok": False, "error": reason}
                else:
                    yield {"type": "tool_call", "name": call["name"], "arguments": arguments}
                    outcome = {"ok": False, "error": "写入模式需要用户在网页端确认；当前渠道不支持，请改用网页发起。"}

                content_payload = json.dumps(outcome, ensure_ascii=False, default=str)
                tool_traces.append(
                    {
                        "name": call["name"],
                        "arguments": arguments,
                        "result_preview": content_payload[:2000],
                    }
                )
                yield {
                    "type": "tool_result",
                    "name": call["name"],
                    "ok": bool(outcome.get("ok")),
                    "preview": content_payload[:800],
                }
                llm_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{idx}",
                        "content": content_payload,
                    }
                )

            yield {"type": "thinking", "message": "桑多涅正在根据工具结果继续思考…"}
            async for event in _stream_round(session, config, llm_messages, tools=tool_schemas, thinking=thinking):
                if event["type"] == "round_done":
                    result = event["result"]
                else:
                    yield event

        if not (result.get("text") or "").strip():
            yield {"type": "thinking", "message": "桑多涅正在整理最终回复…"}
            async for event in _stream_round(session, config, llm_messages, tools=None, thinking=thinking):
                if event["type"] == "round_done":
                    result = event["result"]
                else:
                    yield event

        final = await _finish_assistant_message(
            session,
            config=config,
            result=result,
            thread_id=thread_id,
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            turn_index=turn_index,
            user_message=user_message,
            tool_traces=tool_traces,
            memory_traces=memory_traces,
            apply_affection=existing_user_message is None,
        )
        if replace_message_id is not None:
            delete_message(session, replace_message_id)
            final["replaced_message_id"] = replace_message_id
        yield {"type": "final", **final}
    except LLMError as exc:
        session.refresh(user_message)
        yield {
            "type": "error",
            "message": str(exc),
            "user_message": message_public(user_message),
        }
    except Exception as exc:  # noqa: BLE001
        session.refresh(user_message)
        yield {
            "type": "error",
            "message": f"对话服务发生异常：{exc}",
            "user_message": message_public(user_message),
        }


async def create_chat_turn(
    session: Session,
    content: str,
    *,
    thread_id: str = "main",
    channel: str = "web",
    sender_id: str = "owner",
    chat_id: str = "web",
    style_context: str = "",
) -> dict[str, Any]:
    final_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    async for event in create_chat_turn_stream(
        session,
        content,
        thread_id=thread_id,
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        style_context=style_context,
    ):
        if event["type"] == "final":
            final_payload = event
        elif event["type"] == "error":
            error_payload = event

    if final_payload is not None:
        return final_payload
    return {
        "user_message": error_payload["user_message"] if error_payload else None,
        "assistant_message": None,
        "tool_calls": [],
        "error": error_payload["message"] if error_payload else "对话未完成。",
    }
