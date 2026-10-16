"""看一眼时的游戏场景：认出正在玩的游戏，带上档案与知识回答；没建过档的游戏自动加进游戏资料。

游戏不是单独的功能，而是看屏时自动识别的场景：
- 前台应用对得上某个游戏档案：只截这个游戏的窗口，提示里带上账号档案和相关游戏知识；
- 对不上时，让视觉模型顺带判断这是不是游戏画面；是的话按游戏名建一个档案（标记为自动识别，默认不写战报）；
- 看过之后这一场算“正在玩”，接下来十几分钟的对话会带上这个游戏的上下文。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlmodel import Session

from ...models import GameProfile, ScreenObservation
from ..tools.screen import BROWSER_PROCESS_NAMES
from .knowledge import knowledge_excerpt
from .play import ACCURACY_RULE, touch_session
from .profiles import create_game, list_games, save_card_text
from .scope import app_names

MAX_GAME_NAME = 40

LOOK_PROMPT = (
    "你是桑多涅，正在看用户的电脑屏幕，帮他处理眼前的事。用户的问题：{question}\n"
    "先回答他的问题；他没问具体的，就简洁说明屏幕上是什么、他在做什么、有什么值得注意。\n"
    "另外判断画面是不是一款电子游戏正在运行的游戏画面（游戏网站、视频、直播都不算）："
    "是的话写出游戏名，否则留空。\n"
    '只输出 JSON：{{"answer": "给用户的回答", "game": "游戏名或空字符串"}}'
)

GAME_LOOK_PROMPT = (
    "你是桑多涅，正在看用户玩《{name}》的画面。用户的问题：{question}\n"
    "{accuracy}\n"
    "他的账号档案：\n{card}\n"
    "{knowledge}"
    "直接回答他的问题：看清画面上的选项和局面，给出具体建议和理由；看不清或拿不准就直说。"
)


def look_prompt(question: str) -> str:
    return LOOK_PROMPT.format(question=question)


def game_look_prompt(game: GameProfile, question: str) -> str:
    excerpt = knowledge_excerpt(game, question)
    knowledge = f"游戏知识里相关的部分：\n{excerpt}\n" if excerpt else ""
    return GAME_LOOK_PROMPT.format(
        name=game.name, question=question, accuracy=ACCURACY_RULE, card=save_card_text(game), knowledge=knowledge
    )


def parse_look_reply(raw: str) -> tuple[str, str]:
    """视觉模型的回复 -> (回答, 认出的游戏名)。不是 JSON 时整段当回答。"""
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and str(data.get("answer") or "").strip():
            name = str(data.get("game") or "").strip().strip("《》「」\"'")[:MAX_GAME_NAME]
            return str(data["answer"]).strip(), name
    return text, ""


def game_by_name(session: Session, name: str) -> GameProfile | None:
    lowered = name.lower()
    for game in list_games(session):
        if game.name.lower() == lowered:
            return game
    return None


def adopt_detected_game(session: Session, name: str, app: str) -> tuple[GameProfile | None, bool]:
    """看屏认出的游戏：已有同名档案就把这个应用记到它名下；没有就新建一个。返回 (档案, 是否新建)。"""
    name = (name or "").strip()[:MAX_GAME_NAME]
    app = (app or "").strip()
    # 浏览器里的“游戏”多半是网页、视频或直播，不按浏览器的应用名建档
    if not name or not app or app in BROWSER_PROCESS_NAMES:
        return None, False
    existing = game_by_name(session, name)
    if existing is not None:
        apps = app_names(existing)
        if app not in apps:
            existing.app_names = json.dumps(sorted({*apps, app}), ensure_ascii=False)
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing, False
    game = create_game(session, {"name": name, "app_names": [app], "role": "spectator", "recap_enabled": False})
    game.auto_detected = True
    session.add(game)
    session.commit()
    session.refresh(game)
    return game, True


def note_game_look(session: Session, game: GameProfile, answer: str, now: datetime | None = None) -> None:
    """看一眼也算在玩：续上这一场，并记下看到了什么，接下来的对话会带上这个游戏的上下文。"""
    now = now or datetime.now(timezone.utc)
    game_session = touch_session(session, game, now)
    session.add(
        ScreenObservation(
            created_at=now.astimezone(timezone.utc),
            app=game.name,
            summary=answer.strip().splitlines()[0][:60] if answer.strip() else "看了一眼画面",
            detail="",
            notable=False,
            game_id=game.id,
            session_id=game_session.id,
        )
    )
    session.commit()
