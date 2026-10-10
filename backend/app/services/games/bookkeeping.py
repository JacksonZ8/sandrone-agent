"""对话后的游戏记账（后台）：聊到某个游戏时，由她决定要不要更新账号档案、记里程碑、把通用知识记进知识文件。

原来这是对话里的两个工具，每次调用都要多跑一整轮模型（实测一轮从 4 秒变成 16 秒）；放到回复之后的
后台任务里，回复不再等它。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlmodel import Session

from ...models import GameProfile, Message, utcnow
from ..llm_service import chat_completion
from ..provider_service import get_config
from .account import add_milestone
from .knowledge import note_knowledge
from .profiles import SAVE_FIELDS, merge_save_card, save_card_text
from .scope import message_scope

PROMPT = """你是桑多涅，刚和他聊完一句关于《{name}》的话。判断这轮对话里有没有需要记下来的东西，只输出 JSON：
{{"save_card": {{{fields}}},
 "milestone": "他这轮明确说出的、值得记的里程碑（如“首次通关进阶 9”），没有就空串",
 "knowledge": [{{"text": "对所有玩家都成立的游戏规则、机制或高手打法要点，不含他的任何个人信息", "source_url": "依据的网页地址，没有就空串"}}]}}

规则：
- save_card 是他的账号档案，只写他这轮**明确说出**的变化（进度、新拿到的角色/卡牌/装备、配队、资源、目标、卡关点、偏好）；要写合并后的完整内容（会覆盖原值），没变化的字段留空串。不要猜。
- knowledge 只收有把握的通用知识：读过的网页里的结论（附网址），或公认的规则；没有就空数组。你们的闲聊、他的打法习惯不算。
- 大多数时候什么都不用记，全部留空即可。

他现在的账号档案：
{card}

他说：{user}
你说：{reply}
{sources}"""


def _json_object(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _fetched_urls(message: Message | None) -> list[str]:
    if message is None:
        return []
    try:
        traces = json.loads(message.tool_calls_json or "[]")
    except json.JSONDecodeError:
        return []
    urls = []
    for trace in traces if isinstance(traces, list) else []:
        if isinstance(trace, dict) and trace.get("name") == "web_fetch":
            url = str((trace.get("arguments") or {}).get("url") or "")
            if url:
                urls.append(url)
    return urls[:5]


def apply_bookkeeping(session: Session, game: GameProfile, data: dict[str, Any]) -> dict[str, Any]:
    """把模型的决定落到账号档案、里程碑与知识文件；返回做了什么。"""
    assert game.id is not None
    allowed = {key for key, _ in SAVE_FIELDS}
    card = data.get("save_card") if isinstance(data.get("save_card"), dict) else {}
    updates = {key: str(value) for key, value in card.items() if key in allowed and str(value).strip()}
    if updates:
        merge_save_card(game, updates)
        game.updated_at = utcnow()
        session.add(game)
        session.commit()
    milestone = str(data.get("milestone") or "").strip()
    added = add_milestone(session, game.id, milestone, kind="chat") if milestone else None
    noted = 0
    for item in data.get("knowledge") or []:
        if isinstance(item, dict) and len(str(item.get("text") or "").strip()) >= 6:
            note_knowledge(game, str(item["text"]).strip(), str(item.get("source_url") or "").strip())
            noted += 1
    return {"updated": sorted(updates), "milestone": added is not None, "knowledge": noted}


def run_game_bookkeeping(session: Session, user_message: Message, assistant_message: Message | None) -> dict[str, Any] | None:
    """只在这轮对话属于某个游戏（正在玩或提到了它）时调用模型；在后台任务里同步执行。"""
    scope = message_scope(session, user_message.content, user_message.created_at)
    if not scope:
        return None
    game = session.get(GameProfile, int(scope.split(":", 1)[1]))
    if game is None:
        return None
    config = get_config(session)
    if config.provider == "mock":
        return None
    urls = _fetched_urls(assistant_message)
    prompt = PROMPT.format(
        name=game.name,
        fields=", ".join(f'"{key}": "{label}"' for key, label in SAVE_FIELDS),
        card=save_card_text(game),
        user=user_message.content[:1500],
        reply=(assistant_message.content if assistant_message else "")[:1500],
        sources=("你这轮读过的网页：" + "、".join(urls)) if urls else "",
    )
    result = asyncio.run(
        chat_completion(
            session,
            config,
            [{"role": "user", "content": prompt}],
            purpose="game_bookkeeping",
            max_tokens=4096,
            thinking="low",
        )
    )
    data = _json_object(result["text"])
    return apply_bookkeeping(session, game, data) if data else None
