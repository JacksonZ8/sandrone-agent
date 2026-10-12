from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..database import engine
from ..models import Message, ProactiveConfig, ProactiveEvent, utcnow
from .chat_prompt import CHANNEL_HINTS, build_system_prompt
from .chat_service import get_recent_messages, next_turn_index
from .llm_service import chat_completion
from .provider_service import get_agent_state, get_config
from .style_guard import recent_repetition_block
from . import push_service
from .usage.audit import sanitize_error

logger = logging.getLogger(__name__)

TICK_SECONDS = 300
MIN_SLOT_GAP_MINUTES = 90
MAX_TEXT_CHARS = 300

PROACTIVE_INSTRUCTIONS = (
    "## 主动联系\n"
    "现在没有人在和你说话，轮到你决定要不要主动给他发一条微信。\n"
    "- 像真人一样发：可以是你此刻在做或想到的事、之前没聊完的话题、你记得的他的安排或状态的后续、一句不经意的关心。\n"
    "- 不要每次都问候，不要解释自己为什么发消息，不要提系统、提示、定时或“主动联系”这些词。\n"
    "- 结合现在的时间和你们最近的对话；如果此刻没有真心想说的，就不发。"
)
REPLY_FORMAT = '- 只输出 JSON：{"send": true 或 false, "text": "要发的话，1 到 3 句短句"}'


def proactive_instructions() -> str:
    return PROACTIVE_INSTRUCTIONS + "\n" + REPLY_FORMAT


PROACTIVE_TRIGGER = "[内部提示，不是对方说的话] 现在由你决定要不要主动给他发微信。只输出要求的 JSON。"


def get_proactive_config(session: Session) -> ProactiveConfig:
    config = session.get(ProactiveConfig, 1)
    if config is None:
        config = ProactiveConfig(id=1)
        session.add(config)
        try:
            session.commit()
        except IntegrityError:
            # 页面请求与调度器可能同时播种同一行：输的一方读取已创建的那行。
            session.rollback()
            return session.get(ProactiveConfig, 1)
        session.refresh(config)
    return config


def parse_hhmm(value: str) -> time:
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (value or "").strip())
    if not match:
        raise ValueError("时间格式应为 HH:MM。")
    return time(int(match.group(1)), int(match.group(2)))


def in_quiet_hours(moment: datetime, quiet_start: time, quiet_end: time) -> bool:
    current = moment.time()
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= current < quiet_end
    return current >= quiet_start or current < quiet_end


def plan_slots(
    start: datetime,
    count: int,
    quiet_start: time,
    quiet_end: time,
    rng: random.Random,
) -> list[datetime]:
    """在 start 到当天结束之间、安静时段以外，随机挑 count 个彼此间隔足够的时刻。"""
    day_end = start.replace(hour=23, minute=59, second=0, microsecond=0)
    candidates: list[datetime] = []
    moment = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while moment <= day_end:
        if not in_quiet_hours(moment, quiet_start, quiet_end):
            candidates.append(moment)
        moment += timedelta(minutes=5)
    chosen: list[datetime] = []
    for candidate in rng.sample(candidates, len(candidates)):
        if all(abs(candidate - other) >= timedelta(minutes=MIN_SLOT_GAP_MINUTES) for other in chosen):
            chosen.append(candidate)
        if len(chosen) >= count:
            break
    return sorted(chosen)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class ProactiveDraft:
    send: bool
    text: str = ""


DECLINED = ProactiveDraft(send=False)


def parse_proactive_reply(raw: str) -> ProactiveDraft:
    raw = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return DECLINED
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return DECLINED
    if not isinstance(data, dict) or data.get("send") is not True:
        return DECLINED
    text = str(data.get("text") or "").strip()
    if not text:
        return DECLINED
    return ProactiveDraft(send=True, text=text[:MAX_TEXT_CHARS])


async def compose_proactive_message(session: Session) -> ProactiveDraft:
    """让她基于人设、记忆、时间与最近对话，决定是否主动说话以及说什么。"""
    config = get_config(session)
    recent = get_recent_messages(session, thread_id="main")
    recent_ids = {message.id for message in recent if message.id is not None}
    recent_user_text = " ".join(m.content for m in recent if m.role == "user")[-500:]
    extra = "\n\n".join(
        part
        for part in (
            CHANNEL_HINTS["wechat_dm"],
            recent_repetition_block((m.content for m in recent if m.role == "assistant")),
            proactive_instructions(),
        )
        if part
    )
    system_prompt = build_system_prompt(session, recent_user_text, recent_ids, extra)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": m.role, "content": m.content} for m in recent[-20:] if m.role in ("user", "assistant")
    )
    messages.append({"role": "user", "content": PROACTIVE_TRIGGER})
    result = await chat_completion(
        session, config, messages, purpose="proactive", max_tokens=300, temperature=0.9
    )
    return parse_proactive_reply(result["text"])


def record_event(session: Session, status: str, reason: str = "", message_id: int | None = None) -> ProactiveEvent:
    event = ProactiveEvent(status=status, reason=reason[:300], message_id=message_id)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def _skip_reason(session: Session, config: ProactiveConfig, now_local: datetime) -> str:
    if in_quiet_hours(now_local, parse_hhmm(config.quiet_start), parse_hhmm(config.quiet_end)):
        return "quiet_hours"
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    sent_today = session.exec(
        select(ProactiveEvent).where(
            ProactiveEvent.status == "sent", ProactiveEvent.created_at >= day_start
        )
    ).all()
    if len(sent_today) >= config.daily_max:
        return "daily_limit"

    last_user = session.exec(
        select(Message)
        .where(Message.thread_id == "main", Message.role == "user")
        .order_by(Message.created_at.desc(), Message.id.desc())
    ).first()
    last_user_at = _as_utc(last_user.created_at) if last_user else None
    if last_user_at and now_local.astimezone(timezone.utc) - last_user_at < timedelta(
        minutes=config.min_idle_minutes
    ):
        return "recently_chatted"

    last_sent = session.exec(
        select(ProactiveEvent)
        .where(ProactiveEvent.status == "sent")
        .order_by(ProactiveEvent.created_at.desc(), ProactiveEvent.id.desc())
    ).first()
    last_sent_at = _as_utc(last_sent.created_at) if last_sent else None
    if last_sent_at and (last_user_at is None or last_sent_at > last_user_at):
        return "awaiting_reply"
    return ""


async def deliver_proactive_message(session: Session, *, target: "WeChatTarget") -> ProactiveEvent:
    """生成并发送一条主动消息，写入 main 时间线与决策日志。"""
    try:
        draft = await compose_proactive_message(session)
    except Exception as exc:  # noqa: BLE001
        return record_event(session, "failed", f"compose: {sanitize_error(str(exc), limit=200)}")
    if not draft.send:
        return record_event(session, "declined", "她这次选择不发")
    return await send_to_owner(session, target=target, text=draft.text)


async def send_to_owner(
    session: Session,
    *,
    target: "WeChatTarget",
    text: str,
    note: str = "",
) -> ProactiveEvent:
    """发送一条文字；成功后写入 main 时间线。"""
    try:
        await target.send(target.owner_id, text)
    except Exception as exc:  # noqa: BLE001
        return record_event(session, "failed", f"send: {sanitize_error(str(exc), limit=200)}")
    if target.channel == "wechat_dm":
        # 手机 App 订阅了推送时同步提醒一次；推送失败不影响微信已经发出的消息
        try:
            await push_service.notify(session, title="桑多涅", body=text)
        except Exception:  # noqa: BLE001
            logger.exception("主动消息的手机推送失败")

    config = get_config(session)
    state = get_agent_state(session)
    message = Message(
        thread_id="main",
        channel=target.channel,
        sender_id=target.owner_id,
        chat_id=target.owner_id,
        turn_index=next_turn_index(session, thread_id="main"),
        role="assistant",
        content=text,
        provider=config.provider,
        model=config.model,
        inner_state=json.dumps(
            {"mood": state.mood, "energy": state.energy, "focus": state.focus, "initiated": True},
            ensure_ascii=False,
        ),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return record_event(session, "sent", note, message.id)


@dataclass
class WeChatTarget:
    owner_id: str
    send: Callable[[str, str], Any]
    # 写入时间线的渠道：wechat_dm 为微信，web 为只推到手机 App
    channel: str = "wechat_dm"


async def _push_only_send(owner_id: str, text: str) -> None:
    with Session(engine) as session:
        if await push_service.notify(session, title="桑多涅", body=text) == 0:
            raise RuntimeError("没有可用的推送订阅。")


def default_wechat_target() -> WeChatTarget | None:
    """微信可用时发微信；否则若手机 App 订阅了推送，就只推送（消息照常进入网页时间线）。"""
    from .wechat_service import get_runtime, get_wechat_account

    with Session(engine) as session:
        account = get_wechat_account(session)
        if not account.enabled or not account.token or not account.owner_user_id:
            if push_service.has_subscriptions(session):
                return WeChatTarget(owner_id="owner", send=_push_only_send, channel="web")
            return None
        owner_id = account.owner_user_id
    runtime = get_runtime()
    return WeChatTarget(
        owner_id=owner_id,
        send=runtime.send_text,
    )


class ProactiveScheduler:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
        target_factory: Callable[[], WeChatTarget | None] = default_wechat_target,
    ) -> None:
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.rng = rng or random.Random()
        self.target_factory = target_factory
        self.plan_day: date | None = None
        self.slots: list[datetime] = []
        self._task: asyncio.Task | None = None

    def _ensure_plan(self, now: datetime, config: ProactiveConfig) -> None:
        if self.plan_day == now.date():
            return
        self.plan_day = now.date()
        count = self.rng.randint(1, max(1, config.daily_max))
        # 只规划从现在起的剩余时段：停机错过的时刻不补发。
        self.slots = plan_slots(
            now, count, parse_hhmm(config.quiet_start), parse_hhmm(config.quiet_end), self.rng
        )

    async def tick(self) -> str:
        now = self.clock()
        with Session(engine) as session:
            config = get_proactive_config(session)
            if not config.enabled:
                return "disabled"
            self._ensure_plan(now, config)
            due = [slot for slot in self.slots if slot <= now]
            if not due:
                return "waiting"
            # 一次只处理一个时刻；同时到期的其余时刻作废，避免停顿后连发。
            self.slots = [slot for slot in self.slots if slot > now]

            reason = _skip_reason(session, config, now)
            if reason:
                record_event(session, "skipped", reason)
                return reason
            target = self.target_factory()
            if target is None:
                record_event(session, "skipped", "wechat_unavailable")
                return "wechat_unavailable"
            event = await deliver_proactive_message(session, target=target)
            return event.status

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("主动消息调度出错")
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


PROACTIVE_SCHEDULER = ProactiveScheduler()


def proactive_config_public(config: ProactiveConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "daily_max": config.daily_max,
        "quiet_start": config.quiet_start,
        "quiet_end": config.quiet_end,
        "min_idle_minutes": config.min_idle_minutes,
        "updated_at": config.updated_at,
    }


def update_proactive_config(session: Session, data: dict[str, Any]) -> ProactiveConfig:
    config = get_proactive_config(session)
    for name in ("quiet_start", "quiet_end"):
        if data.get(name) is not None:
            parse_hhmm(data[name])
    for name in ("enabled", "daily_max", "quiet_start", "quiet_end", "min_idle_minutes"):
        if data.get(name) is not None:
            setattr(config, name, data[name])
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    # 节奏变化后当天重新规划。
    PROACTIVE_SCHEDULER.plan_day = None
    return config


def list_proactive_events(session: Session, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.exec(
        select(ProactiveEvent).order_by(ProactiveEvent.created_at.desc(), ProactiveEvent.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "status": row.status,
            "reason": row.reason,
            "message_id": row.message_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]
