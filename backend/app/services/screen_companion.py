"""陪你用电脑（M14）：只读、低频地看名单内应用的窗口，多数时候安静，偶尔说一句。

每隔几分钟检查一次：前台应用在名单里、人没有离开、窗口内容确实变了，才截这一个窗口交给视觉模型概括；
概括（不含截图）存 7 天，进入她和你对话时的上下文。是否开口由她按人设决定，并受开口间隔限制。
截图只在内存里，不落盘；首次开启需要用户在设置页同意截图外发给视觉模型。不做任何键鼠操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from ..database import engine
from ..models import GameProfile, GameSession, Message, ScreenCompanionConfig, ScreenObservation, utcnow
from .games.play import (
    compose_game_remark,
    may_speak as may_speak_in_game,
    note_remark,
    parse_game_state,
    record_event_milestone,
    stale_sessions,
    touch_session,
    vision_prompt as game_vision_prompt,
    write_recap,
)
from .games.profiles import list_games
from .games.scope import app_names as game_app_names
from .llm_service import chat_completion, resolve_thinking
from .provider_service import get_agent_state, get_config
from .tools.audit import _record_tool_call
from .tools.config import get_tool_config
from .tools.screen import call_vision, vision_settings
from .tools.windows import MAC_DESKTOP, MacDesktop, WindowImage, hash_distance
from .usage.audit import sanitize_error

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
AWAY_SECONDS = 300  # 键鼠空闲超过这么久视为离开，不看
GAME_AWAY_SECONDS = 900  # 游戏可能用手柄或在看过场，放宽到 15 分钟
SAME_SCREEN_DISTANCE = 5  # 差值哈希距离不超过这个值视为画面没变，不调视觉模型
RECENT_CHAT_MINUTES = 10  # 刚聊过就不另外开口，看到的内容照样进入对话上下文
RETENTION_DAYS = 7
CONTEXT_MINUTES = 30
MAX_REMARK_CHARS = 120

VISION_PROMPT = (
    "你在帮一个陪伴角色了解用户此刻在电脑上做什么。图里是用户正在用的一个应用窗口（{app}）。\n"
    "只输出 JSON："
    '{{"summary": "不超过 30 字，他在做什么，例如：在 VS Code 里改 Python 测试", '
    '"detail": "不超过 80 字，值得留意的地方，如报错、卡住的迹象、进度；没有就留空", '
    '"notable": true 或 false（有报错、明显卡住、完成了什么等值得关心的事才为 true）}}\n'
    "不要转录聊天内容、邮件正文、密码、证件号、银行卡号等私密信息原文，只做概括。"
)

COMPANION_INSTRUCTIONS = (
    "## 陪他用电脑\n"
    "上面“你最近看到他在电脑上做的事”里最后一条是你刚看到的。"
    "现在由你决定要不要开口说一句。多数时候安静陪着就好，不要实况解说他在做什么。\n"
    "- 值得开口的时候：看到报错或他明显卡住了、同一件事做了很久、完成了什么、太晚了还在忙。\n"
    "- 说的话像坐在旁边的人随口一句：一两句短句，可以给具体的小建议，不要长篇指导。\n"
    "- 不要复述私密内容，不要提“截图”“系统”“观察记录”这些词。\n"
    '- 只输出 JSON：{"say": true 或 false, "text": "要说的话"}'
)
COMPANION_TRIGGER = "[内部提示，不是对方说的话] 你刚看了一眼他的屏幕。决定要不要开口，只输出要求的 JSON。"


# ---------- 配置 ----------


def get_companion_config(session: Session) -> ScreenCompanionConfig:
    config = session.get(ScreenCompanionConfig, 1)
    if config is None:
        config = ScreenCompanionConfig(id=1)
        session.add(config)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            config = session.get(ScreenCompanionConfig, 1)
            assert config is not None
            return config
        session.refresh(config)
    return config


def _app_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def allowed_apps(session: Session) -> set[str]:
    return set(_app_list(get_companion_config(session).allowed_apps))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def companion_config_public(session: Session, config: ScreenCompanionConfig) -> dict[str, Any]:
    tool_config = get_tool_config(session)
    base_url, model, api_key = vision_settings(session, tool_config)
    return {
        "enabled": config.enabled,
        "allowed_apps": _app_list(config.allowed_apps),
        "interval_minutes": config.interval_minutes,
        "remark_gap_minutes": config.remark_gap_minutes,
        "notify": config.notify,
        "consented": config.consent_at is not None,
        "paused_until": _as_utc(config.paused_until),
        "vision_base_url": base_url,
        "vision_model": model,
        "has_vision_key": bool(api_key),
        "updated_at": config.updated_at,
    }


def update_companion_config(session: Session, data: dict[str, Any]) -> ScreenCompanionConfig:
    config = get_companion_config(session)
    if data.get("consent") is True and config.consent_at is None:
        config.consent_at = utcnow()
    if data.get("enabled") is True and config.consent_at is None:
        raise ValueError("开启前需要先同意把名单内应用的窗口截图发给视觉模型。")
    if data.get("allowed_apps") is not None:
        apps = sorted({str(app).strip() for app in data["allowed_apps"] if str(app).strip()})
        config.allowed_apps = json.dumps(apps, ensure_ascii=False)
    for name in ("enabled", "interval_minutes", "remark_gap_minutes", "notify"):
        if data.get(name) is not None:
            setattr(config, name, data[name])
    if "paused_until" in data:
        config.paused_until = data["paused_until"]
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    COMPANION.last_capture_at = None  # 设置变化后下一次检查立即生效
    return config


# ---------- 观察记录 ----------


def observation_public(row: ScreenObservation) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": _as_utc(row.created_at),
        "app": row.app,
        "summary": row.summary,
        "detail": row.detail,
        "notable": row.notable,
        "message_id": row.message_id,
    }


def list_observations(session: Session, limit: int = 20) -> list[ScreenObservation]:
    return list(
        session.exec(
            select(ScreenObservation)
            .order_by(ScreenObservation.created_at.desc(), ScreenObservation.id.desc())
            .limit(limit)
        ).all()
    )


def clear_observations(session: Session) -> int:
    result = session.exec(delete(ScreenObservation))
    session.commit()
    return int(result.rowcount or 0)


def _prune(session: Session, now: datetime) -> None:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=RETENTION_DAYS)
    session.exec(delete(ScreenObservation).where(ScreenObservation.created_at < cutoff))


def _local_hhmm(value: datetime | None) -> str:
    utc = _as_utc(value)
    return utc.astimezone().strftime("%H:%M") if utc else ""


def _observation_lines(rows: list[ScreenObservation]) -> str:
    lines = []
    for row in reversed(rows):
        text = row.summary + (f"；{row.detail}" if row.detail else "")
        lines.append(f"- {_local_hhmm(row.created_at)} {row.app}：{text}")
    return "\n".join(lines)


def screen_context_block(session: Session, now: datetime | None = None) -> str:
    """对话时给她的上下文：开着陪伴模式且最近半小时看到过什么时，告诉她看到了什么。"""
    config = session.get(ScreenCompanionConfig, 1)
    if config is None or not config.enabled:
        return ""
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(minutes=CONTEXT_MINUTES)
    # 游戏里的观察由游戏上下文（存档卡、本场所见）单独给出
    rows = session.exec(
        select(ScreenObservation)
        .where(ScreenObservation.game_id.is_(None))  # type: ignore[union-attr]
        .order_by(ScreenObservation.created_at.desc(), ScreenObservation.id.desc())
        .limit(3)
    ).all()
    rows = [row for row in rows if (_as_utc(row.created_at) or cutoff) >= cutoff]
    if not rows:
        return ""
    return (
        "## 你最近看到他在电脑上做的事\n"
        "他开着“陪你用电脑”，你能看到名单内应用的窗口（只读）。需要时可以自然地提起，不必每次都提。\n"
        + _observation_lines(rows)
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
class SeenWindow:
    summary: str
    detail: str
    notable: bool


def parse_vision_reply(raw: str) -> SeenWindow | None:
    data = _json_object(raw)
    if data is None:
        text = (raw or "").strip()
        return SeenWindow(text[:60], "", False) if text else None
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    return SeenWindow(summary[:60], str(data.get("detail") or "").strip()[:160], data.get("notable") is True)


def parse_remark(raw: str) -> str:
    data = _json_object(raw)
    if data is None or data.get("say") is not True:
        return ""
    return str(data.get("text") or "").strip()[:MAX_REMARK_CHARS]


# ---------- 观察与开口 ----------


async def vision_text(session: Session, prompt: str, image: WindowImage, *, purpose: str) -> str:
    """把窗口截图交给视觉模型，记录一次调用（用量页可见），返回原始回复。"""
    tool_config = get_tool_config(session)
    model = vision_settings(session, tool_config)[1]
    started = asyncio.get_running_loop().time()
    try:
        # 视觉模型有时先推理再输出 JSON，预算太小会截断在推理里
        result = await asyncio.to_thread(call_vision, session, tool_config, prompt, image.jpeg, max_tokens=1000)
    except Exception as exc:  # noqa: BLE001
        _record_tool_call(session, purpose, "vision", model, "error", sanitize_error(str(exc), limit=200))
        raise
    latency = int((asyncio.get_running_loop().time() - started) * 1000)
    if not result["ok"]:
        _record_tool_call(session, purpose, "vision", model, "error", result["message"][:200], latency_ms=latency)
        raise RuntimeError(result["message"])
    _record_tool_call(session, purpose, "vision", model, "ok", latency_ms=latency)
    return result["analysis"]


async def describe_window(session: Session, app: str, image: WindowImage) -> SeenWindow | None:
    raw = await vision_text(session, VISION_PROMPT.format(app=app), image, purpose="screen_companion")
    return parse_vision_reply(raw)


async def compose_remark(session: Session) -> str:
    from .chat_prompt import build_system_prompt
    from .chat_service import get_recent_messages

    config = get_config(session)
    recent = get_recent_messages(session, thread_id="main")
    recent_ids = {message.id for message in recent if message.id is not None}
    latest = " ".join(row.summary for row in list_observations(session, limit=3))
    system_prompt = build_system_prompt(session, latest, recent_ids, COMPANION_INSTRUCTIONS)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m.role, "content": m.content} for m in recent[-12:] if m.role in ("user", "assistant"))
    messages.append({"role": "user", "content": COMPANION_TRIGGER})
    # 预算太小时模型的推理会用光额度、正文为空
    result = await chat_completion(
        session, config, messages, purpose="screen_companion", max_tokens=800, temperature=0.8, thinking=resolve_thinking(config.reply_thinking)
    )
    return parse_remark(result["text"])


def _write_remark(session: Session, text: str) -> Message:
    from .chat_service import next_turn_index

    config = get_config(session)
    state = get_agent_state(session)
    message = Message(
        thread_id="main",
        channel="web",
        turn_index=next_turn_index(session, thread_id="main"),
        role="assistant",
        content=text,
        provider=config.provider,
        model=config.model,
        inner_state=json.dumps(
            {"mood": state.mood, "energy": state.energy, "focus": state.focus, "initiated": True, "screen": True},
            ensure_ascii=False,
        ),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def _minutes_since_user_spoke(session: Session, now: datetime) -> float | None:
    last = session.exec(
        select(Message)
        .where(Message.thread_id == "main", Message.role == "user")
        .order_by(Message.created_at.desc(), Message.id.desc())
    ).first()
    at = _as_utc(last.created_at) if last else None
    return (now.astimezone(timezone.utc) - at).total_seconds() / 60 if at else None


def _last_remark_at(session: Session) -> datetime | None:
    row = session.exec(
        select(ScreenObservation)
        .where(ScreenObservation.message_id.is_not(None))  # type: ignore[union-attr]
        .order_by(ScreenObservation.created_at.desc(), ScreenObservation.id.desc())
    ).first()
    return _as_utc(row.created_at) if row else None


def _may_speak(session: Session, config: ScreenCompanionConfig, now: datetime) -> bool:
    if config.remark_gap_minutes <= 0:
        return False
    since_user = _minutes_since_user_spoke(session, now)
    if since_user is not None and since_user < RECENT_CHAT_MINUTES:
        return False
    last = _last_remark_at(session)
    return last is None or now.astimezone(timezone.utc) - last >= timedelta(minutes=config.remark_gap_minutes)


# ---------- 调度 ----------


@dataclass
class CompanionStatus:
    state: str = "off"  # off / paused / not_allowed / away / no_window / watching / playing / error
    app: str = ""  # 只在名单内应用时填写
    checked_at: datetime | None = None
    seen_at: datetime | None = None
    message: str = ""


class ScreenCompanion:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        desktop: MacDesktop | Any = MAC_DESKTOP,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.desktop = desktop
        self.status = CompanionStatus()
        self.last_capture_at: datetime | None = None
        self.last_app = ""
        self.last_fingerprint: int | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _set(self, state: str, app: str = "", message: str = "") -> str:
        self.status.state, self.status.app, self.status.message = state, app, message
        self.status.checked_at = self.clock()
        return state

    async def tick(self, *, force: bool = False) -> str:
        async with self._lock:
            return await self._tick(force)

    async def _tick(self, force: bool) -> str:
        now = self.clock()
        with Session(engine) as session:
            # 离开游戏 15 分钟的场次先收尾（写战报），与是否开启、是否暂停无关
            await self._close_game_sessions(session, now)
            config = get_companion_config(session)
            if not config.enabled or config.consent_at is None:
                return self._set("off")
            paused = _as_utc(config.paused_until)
            if paused and paused > now.astimezone(timezone.utc):
                return self._set("paused")
            games = {name: game for game in list_games(session) for name in game_app_names(game)}
            apps = set(_app_list(config.allowed_apps)) | set(games)

            snapshot = await asyncio.to_thread(self.desktop.snapshot)
            if force:
                # 手动“看一眼”时前台通常是网页本身：改看名单内最前面的窗口
                window = snapshot.front_window(apps)
                if window is None:
                    return self._set("no_window", message="屏幕上没有名单内应用的窗口。")
                app = window.app
            else:
                if snapshot.app not in apps:
                    # 名单外的应用连名字都不记录
                    return self._set("not_allowed")
                app = snapshot.app
                window = snapshot.front_window()
            game = games.get(app)
            interval = game.interval_minutes if game else config.interval_minutes
            if (
                not force
                and self.last_capture_at is not None
                and now - self.last_capture_at < timedelta(minutes=max(1, interval))
            ):
                return "waiting"
            if not force:
                idle = await asyncio.to_thread(self.desktop.idle_seconds)
                if idle is not None and idle >= (GAME_AWAY_SECONDS if game else AWAY_SECONDS):
                    return self._set("away", app)
                if window is None:
                    return self._set("no_window", app)

            if not vision_settings(session, get_tool_config(session))[2]:
                return self._set("error", app, "没有可用的视觉模型 Key，没有截图。")
            game_session = touch_session(session, game, now) if game else None
            self.last_capture_at = now
            try:
                image = await asyncio.to_thread(self.desktop.capture, window.id)
            except Exception as exc:  # noqa: BLE001
                return self._set("error", app, sanitize_error(str(exc), limit=200))
            if (
                not force
                and app == self.last_app
                and self.last_fingerprint is not None
                and hash_distance(image.fingerprint, self.last_fingerprint) <= SAME_SCREEN_DISTANCE
            ):
                self._set("playing" if game else "watching", app)
                return "unchanged"
            self.last_app, self.last_fingerprint = app, image.fingerprint

            if game is not None and game_session is not None:
                return await self._observe_game(session, config, game, game_session, image, now)
            try:
                seen = await describe_window(session, app, image)
            except Exception as exc:  # noqa: BLE001
                return self._set("error", app, sanitize_error(str(exc), limit=200))
            if seen is None:
                return self._set("watching", app)
            row = ScreenObservation(
                created_at=now.astimezone(timezone.utc),
                app=app,
                summary=seen.summary,
                detail=seen.detail,
                notable=seen.notable,
            )
            session.add(row)
            _prune(session, now)
            session.commit()
            session.refresh(row)
            self._set("watching", app)
            self.status.seen_at = now

            if not _may_speak(session, config, now):
                return "observed"
            try:
                text = await compose_remark(session)
            except Exception as exc:  # noqa: BLE001
                self.status.message = sanitize_error(str(exc), limit=200)
                return "observed"
            if not text:
                return "observed"
            await self._speak(session, config, row, text)
            return "spoke"

    async def _speak(self, session: Session, config: ScreenCompanionConfig, row: ScreenObservation | None, text: str) -> Message:
        message = _write_remark(session, text)
        if row is not None:
            row.message_id = message.id
            session.add(row)
            session.commit()
        if config.notify:
            try:
                await asyncio.to_thread(self.desktop.notify, "桑多涅", text)
            except Exception:  # noqa: BLE001
                logger.warning("系统通知发送失败", exc_info=True)
        return message

    async def _observe_game(
        self,
        session: Session,
        config: ScreenCompanionConfig,
        game: GameProfile,
        game_session: GameSession,
        image: WindowImage,
        now: datetime,
    ) -> str:
        """游戏模式：按档案看屏得到结构化局面，只在角色允许的时机开口。"""
        try:
            raw = await vision_text(session, game_vision_prompt(game), image, purpose="screen_companion_game")
        except Exception as exc:  # noqa: BLE001
            return self._set("error", game.name, sanitize_error(str(exc), limit=200))
        state = parse_game_state(raw)
        if state is None:
            return self._set("playing", game.name)
        row = ScreenObservation(
            created_at=now.astimezone(timezone.utc),
            app=game.name,
            summary=state.summary,
            detail=state.detail,
            notable=state.notable,
            game_id=game.id,
            session_id=game_session.id,
            scene=state.scene,
            event=state.event,
            progress=state.progress,
        )
        session.add(row)
        _prune(session, now)
        session.commit()
        session.refresh(row)
        record_event_milestone(session, game, state, game_session.id, now)
        self._set("playing", game.name)
        self.status.seen_at = now

        since_user = _minutes_since_user_spoke(session, now)
        if not may_speak_in_game(game, game_session, state, now) or (
            since_user is not None and since_user < RECENT_CHAT_MINUTES
        ):
            return "observed"
        try:
            text = await compose_game_remark(session, game)
        except Exception as exc:  # noqa: BLE001
            self.status.message = sanitize_error(str(exc), limit=200)
            return "observed"
        if not text:
            return "observed"
        await self._speak(session, config, row, text)
        note_remark(session, game_session, now)
        return "spoke"

    async def _close_game_sessions(self, session: Session, now: datetime) -> None:
        for game_session in stale_sessions(session, now):
            game = session.get(GameProfile, game_session.game_id)
            game_session.ended_at = now.astimezone(timezone.utc)
            session.add(game_session)
            session.commit()
            if game is None:
                continue
            try:
                recap = await write_recap(session, game, game_session)
            except Exception as exc:  # noqa: BLE001
                logger.exception("战报生成失败（%s）", game.name)
                self.status.message = f"战报生成失败：{sanitize_error(str(exc), limit=160)}"
                continue
            if recap is None or not recap.text or not game.recap_enabled:
                continue
            since_user = _minutes_since_user_spoke(session, now)
            if since_user is not None and since_user < RECENT_CHAT_MINUTES:
                continue
            config = get_companion_config(session)
            message = await self._speak(session, config, None, recap.text)
            game_session.message_id = message.id
            session.add(game_session)
            session.commit()

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("看屏陪伴检查出错")
                self._set("error", message=sanitize_error(str(exc), limit=200))
            await asyncio.sleep(TICK_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


COMPANION = ScreenCompanion()


def companion_status_public() -> dict[str, Any]:
    status = COMPANION.status
    return {
        "state": status.state,
        "app": status.app,
        "checked_at": status.checked_at,
        "seen_at": status.seen_at,
        "message": status.message,
    }
