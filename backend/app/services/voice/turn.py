from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlmodel import Session

from ...models import AgentTask, Message
from ..agent_tasks import propose_task, resolve_card_by_utterance, task_public
from ..chat_prompt import CHANNEL_HINTS, build_prompt_parts, layout_messages
from ..chat_service import _finish_assistant_message, get_recent_messages, next_turn_index
from ..llm_service import chat_completion_stream, resolve_thinking
from ..memory_service import estimate_importance, estimate_valence
from ..provider_service import get_config
from ..style_guard import recent_repetition_block
from ..tool_service import active_tool_schemas, execute_tool_call, get_tool_config
from .emotion import strip_emotion_tag

CHANNEL = "voice"
MAX_TOOL_ROUNDS = 3


class VoiceReply:
    """一轮通话回复的累积结果，供结束（或被打断）时写入时间线。"""

    def __init__(self) -> None:
        self.text = ""
        self.tool_traces: list[dict[str, Any]] = []
        self.memory_traces: list[dict[str, Any]] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.latency_ms = 0
        self.first_token_ms = 0
        self.emotion: str | None = None


async def stream_voice_reply(
    session: Session,
    user_text: str,
    reply: VoiceReply,
    *,
    style_context: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """流式生成通话回复：逐段产出 {"type": "delta"} 与 {"type": "tool_call"}，结果累积在 reply 里。

    用户这句话此时还没写入时间线（被打断合并时可能作废），所以手动追加到上下文末尾。
    委托子代理只生成任务卡；紧接着说“确认/取消”就直接处理（见 agent_tasks）。
    """
    config = get_config(session)
    recent = get_recent_messages(session, aligned=True)
    recent_ids = {message.id for message in recent if message.id is not None}
    variety = recent_repetition_block(
        (message.content for message in recent if message.role == "assistant"), user_text
    )
    task_context, decided = resolve_card_by_utterance(session, user_text, channel=CHANNEL)
    if decided is not None:
        yield {"type": "agent_task", "task": decided}
    extra = "\n\n".join(part for part in (CHANNEL_HINTS[CHANNEL], style_context, task_context, variety) if part)
    static_prompt, dynamic_prompt = build_prompt_parts(
        session, user_text, recent_ids, extra, memory_trace_collector=reply.memory_traces
    )
    history = [{"role": m.role, "content": m.content} for m in recent if m.role in ("user", "assistant")]
    llm_messages = layout_messages(static_prompt, dynamic_prompt, [*history, {"role": "user", "content": user_text}])

    tool_config = get_tool_config(session)
    tools = None if config.provider == "mock" else active_tool_schemas(tool_config)
    parts: list[str] = []
    for round_index in range(MAX_TOOL_ROUNDS + 1):
        done: dict[str, Any] = {}
        async for event in chat_completion_stream(
            session,
            config,
            llm_messages,
            purpose="voice",
            tools=tools if round_index < MAX_TOOL_ROUNDS else None,
            thinking=resolve_thinking(config.reply_thinking, user_text),
        ):
            if event["type"] == "delta":
                parts.append(event["text"])
                yield event
            else:
                done = event
        for key in reply.usage:
            reply.usage[key] += int(done.get(key) or 0)
        reply.latency_ms += int(done.get("latency_ms") or 0)
        if not reply.first_token_ms and done.get("first_token_ms"):
            reply.first_token_ms = int(done["first_token_ms"])
        calls = done.get("tool_calls") or []
        if not calls:
            break
        llm_messages.append(
            {
                "role": "assistant",
                "content": done.get("text") or "",
                "tool_calls": [
                    {
                        "id": call["id"] or f"call_{index}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                        },
                    }
                    for index, call in enumerate(calls)
                ],
            }
        )
        for index, call in enumerate(calls):
            yield {"type": "tool_call", "name": call["name"], "arguments": call["arguments"]}
            if call["name"] == "delegate_agent":
                outcome: dict[str, Any] = propose_task(session, call["arguments"], channel=CHANNEL)
                card = session.get(AgentTask, outcome["task_id"]) if outcome.get("ok") else None
                if card is not None:
                    yield {"type": "agent_task", "task": task_public(card)}
            else:
                outcome = await asyncio.to_thread(execute_tool_call, session, call["name"], call["arguments"])
            payload = json.dumps(outcome, ensure_ascii=False, default=str)
            reply.tool_traces.append(
                {"name": call["name"], "arguments": call["arguments"], "result_preview": payload[:2000]}
            )
            llm_messages.append(
                {"role": "tool", "tool_call_id": call["id"] or f"call_{index}", "content": payload}
            )
    reply.emotion, text = strip_emotion_tag("".join(parts))
    reply.text = text.strip()


async def save_voice_turn(
    session: Session,
    *,
    user_text: str,
    assistant_text: str,
    reply: VoiceReply,
    spoken_at: datetime,
    audio_path: str = "",
) -> dict[str, Any]:
    """把一轮通话写进唯一时间线；assistant_text 是她实际说出口的部分（被打断时会更短）。"""
    config = get_config(session)
    turn_index = next_turn_index(session)
    user_message = Message(
        channel=CHANNEL,
        turn_index=turn_index,
        role="user",
        content=user_text,
        created_at=spoken_at,
        importance=estimate_importance(user_text),
        valence=estimate_valence(user_text),
    )
    session.add(user_message)
    session.commit()
    session.refresh(user_message)
    result = {
        "text": assistant_text,
        "prompt_tokens": reply.usage["prompt_tokens"],
        "completion_tokens": reply.usage["completion_tokens"],
        "latency_ms": reply.latency_ms,
    }
    final = await _finish_assistant_message(
        session,
        config=config,
        result=result,
        thread_id="main",
        channel=CHANNEL,
        sender_id="owner",
        chat_id=CHANNEL,
        turn_index=turn_index,
        user_message=user_message,
        tool_traces=reply.tool_traces,
        memory_traces=reply.memory_traces,
    )
    if audio_path and final.get("assistant_message"):
        assistant = session.get(Message, final["assistant_message"]["id"])
        if assistant is not None:
            assistant.audio_path = audio_path
            session.add(assistant)
            session.commit()
            final["assistant_message"]["audio_path"] = audio_path
    return final
