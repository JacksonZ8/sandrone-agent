"""游戏模式：结构化看屏、场次、开口时机、战报与存档卡，以及对话时的游戏上下文。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ...models import GameProfile, GameSession, MemoryExtractionRun, ScreenObservation, utcnow
from ..llm_service import chat_completion, resolve_thinking
from ..memory.common import _persona_text
from ..memory.extraction import _parse_llm_memories, _save_candidates
from ..provider_service import get_config
from .account import MILESTONE_EVENTS, add_milestone, list_milestones
from .knowledge import consolidate, knowledge_excerpt, save_source
from .profiles import ROLES, SAVE_FIELDS, load_save_card, merge_save_card, save_card_text
from .scope import SESSION_IDLE_MINUTES, active_session, as_utc, game_scope, mentioned_games

logger = logging.getLogger(__name__)

SCENES = {"combat", "result", "death", "menu", "loading", "map", "dialog", "other"}
EVENTS = {"victory", "boss_defeated", "death", "level_up", "unlock", "none"}
BIG_EVENTS = {"victory", "boss_defeated", "death"}
EVENT_LABELS = {"victory": "通关", "boss_defeated": "击败 Boss", "unlock": "解锁", "level_up": "升级"}
BREAK_SCENES = {"result", "death", "menu", "loading", "map"}
MIN_RECAP_OBSERVATIONS = 2
ACCURACY_RULE = "说到这个游戏的机制、数值、掉率时，只说有把握的；拿不准就直说，或用网页搜索查了再说并提一句出处，不要编。"
MAX_REMARK_CHARS = 120

GAME_VISION_PROMPT = (
    "你在帮一个陪伴角色看用户正在玩的游戏《{name}》{genre}。图里是游戏窗口。\n"
    "{hud}"
    "只输出 JSON：\n"
    '{{"scene": "combat|result|death|menu|loading|map|dialog|other 之一", '
    '"event": "victory|boss_defeated|death|level_up|unlock|none 之一（刚发生的事，看不出就 none）", '
    '"progress": "能看出的进度，如关卡、层数、章节、等级，不超过 20 字，看不出留空", '
    '"summary": "不超过 30 字，他此刻在做什么", '
    '"detail": "不超过 80 字，局面里值得留意的地方，如血量危险、关键选择、阵容；没有就留空"}}\n'
    "不要转录游戏内聊天和其他玩家的名字。"
)

GAME_REMARK_INSTRUCTIONS = (
    "## 陪他玩《{name}》\n"
    "你的陪玩角色：{role_label}。{role_prompt}\n"
    "上面“你们正在玩”的观察里最后一条是你刚看到的画面。现在由你决定要不要开口。\n"
    "- 多数时候不说也可以；说就是一两句短句，像坐在旁边的人。\n"
    "- 游戏机制、数值、掉率只说你有把握的，没把握就不说，绝不编造。\n"
    "- 不要提“截图”“观察”“系统”这些词。\n"
    '- 只输出 JSON：{{"say": true 或 false, "text": "要说的话"}}'
)
GAME_REMARK_TRIGGER = "[内部提示，不是对方说的话] 你刚看了一眼他的游戏画面。决定要不要开口，只输出要求的 JSON。"

RECAP_INSTRUCTIONS = (
    "\n\n你刚陪他玩完一场《{name}》（{duration}）。你的陪玩角色：{role_label}。\n"
    "他的账号档案（只属于他）：\n{card}\n\n这一场你看到的（按时间）：\n{timeline}\n\n"
    "请一次完成下面几件事，只输出 JSON：\n"
    '{{"recap": "用你自己的口吻对他说的一两句战报，提到这场的进展或名场面，不超过 100 字", '
    '"save_card": {{{card_fields}}}, '
    '"milestones": ["这场达成的里程碑，如“击败第三幕 Boss 时间吞噬者”，没有就空数组"], '
    '"knowledge": ["这场看到的、对所有玩家都成立的游戏规律或打法要点（不含他的任何个人信息），没有就空数组"], '
    '"memories": [{{"perspective": "user|self|shared", "type": "fact|preference|goal|event|opinion", '
    '"content": "值得长期记住的事", "importance": 0.0-1.0, "confidence": 0.0-1.0}}]}}\n'
    "- save_card 是他的账号档案，只写有新信息的字段（会覆盖原值，所以要写合并后的完整内容），没变化的字段留空串。\n"
    "- knowledge 会写进可分享的游戏知识文件：只写有把握的通用规律（如 Boss 的行动规律、机制），不写猜测，不写他的进度、阵容、习惯。\n"
    "- memories 只记这场里真正值得以后提起的：他的打法习惯和偏好（user）、你对这游戏或角色的看法（self，第一人称）、"
    "你们一起经历的高光或约定（shared，用“我们”）。没有就给空数组。不要记录临时的血量、位置。"
)


# ---------- 解析 ----------


def _json_object(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class GameState:
    scene: str
    event: str
    progress: str
    summary: str
    detail: str

    @property
    def notable(self) -> bool:
        return self.event in BIG_EVENTS or self.scene == "death"


def parse_game_state(raw: str) -> GameState | None:
    data = _json_object(raw)
    if data is None:
        return None
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    scene = str(data.get("scene") or "other").strip().lower()
    event = str(data.get("event") or "none").strip().lower()
    return GameState(
        scene=scene if scene in SCENES else "other",
        event=event if event in EVENTS else "none",
        progress=str(data.get("progress") or "").strip()[:40],
        summary=summary[:60],
        detail=str(data.get("detail") or "").strip()[:160],
    )


def parse_remark(raw: str) -> str:
    data = _json_object(raw)
    if data is None or data.get("say") is not True:
        return ""
    return str(data.get("text") or "").strip()[:MAX_REMARK_CHARS]


def vision_prompt(game: GameProfile) -> str:
    genre = f"（{game.genre}）" if game.genre else ""
    hud = f"屏幕上重点看：{game.hud_hints}\n" if game.hud_hints else ""
    return GAME_VISION_PROMPT.format(name=game.name, genre=genre, hud=hud)


# ---------- 场次 ----------


def touch_session(session: Session, game: GameProfile, now: datetime) -> GameSession:
    """同一个游戏 15 分钟内回来算同一场，否则开新的一场。"""
    now_utc = now.astimezone(timezone.utc)
    current = session.exec(
        select(GameSession)
        .where(GameSession.game_id == game.id, GameSession.ended_at.is_(None))  # type: ignore[union-attr]
        .order_by(GameSession.last_seen_at.desc())
    ).first()
    if current is not None and now_utc - (as_utc(current.last_seen_at) or now_utc) <= timedelta(minutes=SESSION_IDLE_MINUTES):
        current.last_seen_at = now_utc
    else:
        assert game.id is not None
        current = GameSession(game_id=game.id, started_at=now_utc, last_seen_at=now_utc)
    session.add(current)
    session.commit()
    session.refresh(current)
    return current


def session_observations(session: Session, session_id: int, limit: int = 40) -> list[ScreenObservation]:
    rows = session.exec(
        select(ScreenObservation)
        .where(ScreenObservation.session_id == session_id)
        .order_by(ScreenObservation.created_at.desc(), ScreenObservation.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def _hhmm(value: datetime | None) -> str:
    utc = as_utc(value)
    return utc.astimezone().strftime("%H:%M") if utc else ""


def _observation_line(row: ScreenObservation) -> str:
    parts = [row.summary]
    if row.progress:
        parts.append(f"进度 {row.progress}")
    if row.event and row.event != "none":
        parts.append(f"事件 {row.event}")
    if row.detail:
        parts.append(row.detail)
    return f"- {_hhmm(row.created_at)}［{row.scene or 'other'}］" + "；".join(parts)


# ---------- 开口 ----------


def may_speak(game: GameProfile, game_session: GameSession, state: GameState, now: datetime) -> bool:
    role = ROLES.get(game.role, ROLES["spectator"])
    if role.max_remarks <= 0 or state.scene == "combat":
        return False
    if game_session.remark_count >= role.max_remarks:
        return False
    last = as_utc(game_session.last_remark_at)
    if last is not None and now.astimezone(timezone.utc) - last < timedelta(minutes=role.gap_minutes):
        return False
    if role.big_events_only:
        return state.event in BIG_EVENTS
    return state.event in BIG_EVENTS or state.scene in BREAK_SCENES


async def compose_game_remark(session: Session, game: GameProfile) -> str:
    from ..chat_prompt import build_system_prompt
    from ..chat_service import get_recent_messages

    role = ROLES.get(game.role, ROLES["spectator"])
    config = get_config(session)
    recent = get_recent_messages(session, thread_id="main")
    recent_ids = {message.id for message in recent if message.id is not None}
    extra = GAME_REMARK_INSTRUCTIONS.format(name=game.name, role_label=role.label, role_prompt=role.prompt)
    # 用刚看到的局面做检索词，记忆和攻略手册都取与眼前局面相关的部分
    latest = session.exec(
        select(ScreenObservation)
        .where(ScreenObservation.game_id == game.id)
        .order_by(ScreenObservation.created_at.desc(), ScreenObservation.id.desc())
    ).first()
    query = " ".join(part for part in (game.name, latest.summary if latest else "", latest.detail if latest else "") if part)
    system_prompt = build_system_prompt(session, query, recent_ids, extra)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m.role, "content": m.content} for m in recent[-8:] if m.role in ("user", "assistant"))
    messages.append({"role": "user", "content": GAME_REMARK_TRIGGER})
    # 对话模型会先推理再作答，预算太小时推理就会用光额度、正文为空；按实际用量计费
    result = await chat_completion(
        session, config, messages, purpose="game_companion", max_tokens=1200, temperature=0.8, thinking=resolve_thinking(config.reply_thinking)
    )
    return parse_remark(result["text"])


def note_remark(session: Session, game_session: GameSession, now: datetime) -> None:
    game_session.remark_count += 1
    game_session.last_remark_at = now.astimezone(timezone.utc)
    session.add(game_session)
    session.commit()


# ---------- 战报 ----------


@dataclass(frozen=True)
class Recap:
    text: str
    save_card: dict[str, Any]
    memories: list[dict[str, Any]]
    milestones: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)


def _strings(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:200] for item in value if isinstance(item, str) and item.strip()][:limit]


def parse_recap(raw: str) -> Recap:
    data = _json_object(raw) or {}
    card = data.get("save_card") if isinstance(data.get("save_card"), dict) else {}
    memories = _parse_llm_memories(json.dumps({"memories": data.get("memories") or []}, ensure_ascii=False))
    return Recap(
        str(data.get("recap") or "").strip()[:200],
        card,
        memories,
        _strings(data.get("milestones"), 5),
        _strings(data.get("knowledge"), 8),
    )


def _duration(game_session: GameSession) -> str:
    start, end = as_utc(game_session.started_at), as_utc(game_session.last_seen_at)
    minutes = max(1, round(((end - start).total_seconds() if start and end else 0) / 60))
    return f"约 {minutes} 分钟"


async def write_recap(session: Session, game: GameProfile, game_session: GameSession) -> Recap | None:
    """结束一场：写战报，更新账号档案与里程碑，记下值得长期记住的事，把通用规律整理进游戏知识。"""
    rows = session_observations(session, game_session.id or 0)
    if len(rows) < MIN_RECAP_OBSERVATIONS:
        return None
    role = ROLES.get(game.role, ROLES["spectator"])
    card_fields = ", ".join(f'"{key}": "{label}"' for key, label in SAVE_FIELDS)
    system_prompt = _persona_text() + RECAP_INSTRUCTIONS.format(
        name=game.name,
        duration=_duration(game_session),
        role_label=role.label,
        card=save_card_text(game),
        timeline="\n".join(_observation_line(row) for row in rows),
        card_fields=card_fields,
    )
    config = get_config(session)
    result = await chat_completion(
        session,
        config,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": "写这一场的战报。"}],
        purpose="game_recap",
        max_tokens=2000,
        temperature=0.4,
    )
    recap = parse_recap(result["text"])
    if recap.save_card:
        merge_save_card(game, recap.save_card)
        game.updated_at = utcnow()
        session.add(game)
    game_session.recap = recap.text
    session.add(game_session)
    session.commit()
    if recap.memories:
        run = MemoryExtractionRun(source_message_ids="[]", status="pending", provider=config.provider, model=config.model)
        session.add(run)
        session.flush()
        assert game.id is not None
        _save_candidates(session, recap.memories, [], extraction_run=run, scope=game_scope(game.id))
        run.candidates_json = json.dumps(recap.memories, ensure_ascii=False)
        session.add(run)
        session.commit()
    for text in recap.milestones:
        add_milestone(session, game.id, text, kind="recap", session_id=game_session.id, at=game_session.last_seen_at)
    if recap.knowledge:
        source = save_source(game, "play", f"陪玩时的观察（{_duration(game_session)}）", "", "\n".join(f"- {item}" for item in recap.knowledge))
        try:
            await consolidate(session, game, [source])
        except Exception:  # noqa: BLE001 - 整理失败时资料仍在，下次整理时一并处理
            logger.exception("游戏知识整理失败（%s）", game.name)
    return recap


def record_event_milestone(session: Session, game: GameProfile, state: "GameState", session_id: int | None, now: datetime) -> None:
    """画面上出现大事件（打倒 Boss、通关、解锁、升级）时自动记一笔里程碑。"""
    if game.id is None or state.event not in MILESTONE_EVENTS:
        return
    detail = state.progress or state.summary
    add_milestone(session, game.id, f"{EVENT_LABELS.get(state.event, state.event)}：{detail}", kind=state.event, session_id=session_id, at=now)


def stale_sessions(session: Session, now: datetime) -> list[GameSession]:
    cutoff = now.astimezone(timezone.utc) - timedelta(minutes=SESSION_IDLE_MINUTES)
    rows = session.exec(
        select(GameSession).where(GameSession.ended_at.is_(None))  # type: ignore[union-attr]
    ).all()
    return [row for row in rows if (as_utc(row.last_seen_at) or cutoff) < cutoff]


# ---------- 对话上下文 ----------


def game_context_block(session: Session, query: str, now: datetime | None = None) -> str:
    """正在玩某个游戏、或这句话提到某个游戏时，告诉她账号档案、里程碑、本场所见、相关游戏知识与最近战报。"""
    now = now or datetime.now(timezone.utc)
    playing = active_session(session, now)
    games: list[GameProfile] = []
    if playing is not None:
        game = session.get(GameProfile, playing.game_id)
        if game is not None:
            games.append(game)
    for game in mentioned_games(session, query):
        if all(game.id != other.id for other in games):
            games.append(game)
    blocks = []
    for game in games[:2]:
        role = ROLES.get(game.role, ROLES["spectator"])
        lines = [
            f"## 《{game.name}》",
            f"你在这个游戏里的陪玩角色：{role.label}。",
            ACCURACY_RULE,
            "他的账号档案（只属于他，你在陪玩中维护）：",
            save_card_text(game),
        ]
        milestones = list_milestones(session, game.id or 0, limit=3)
        if milestones:
            lines.append("最近的里程碑：" + "；".join(f"{as_utc(row.at).astimezone().strftime('%m-%d')} {row.text}" for row in milestones))  # type: ignore[union-attr]
        if playing is not None and playing.game_id == game.id:
            lines[0] = f"## 你们正在玩《{game.name}》（这一场从 {_hhmm(playing.started_at)} 开始）"
            seen = session_observations(session, playing.id or 0, limit=5)
            if seen:
                lines.append("这一场你看到的：")
                lines.extend(_observation_line(row) for row in seen)
        excerpt = knowledge_excerpt(game, query)
        if excerpt:
            lines.append("游戏知识里相关的部分（你整理的通用资料，引用数值时可以提来源）：")
            lines.append(excerpt)
        recaps = session.exec(
            select(GameSession)
            .where(GameSession.game_id == game.id, GameSession.recap != "")
            .order_by(GameSession.started_at.desc())
            .limit(2)
        ).all()
        if recaps:
            lines.append("最近几场的战报：")
            lines.extend(f"- {as_utc(row.started_at).astimezone().strftime('%m-%d')}：{row.recap}" for row in recaps)  # type: ignore[union-attr]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


__all__ = [
    "GameState",
    "compose_game_remark",
    "game_context_block",
    "load_save_card",
    "may_speak",
    "note_remark",
    "parse_game_state",
    "stale_sessions",
    "touch_session",
    "vision_prompt",
    "write_recap",
]
