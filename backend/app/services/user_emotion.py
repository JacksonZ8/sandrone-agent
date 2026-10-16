"""对方（用户）的情绪：和她自己的心情分开记。

- 每条用户消息记一次观察，存在 Message.emotion_tags：{"label", "confidence", "source"}。
  来源优先用后台记忆提取那次模型调用顺带给出的判断（不额外调用模型）；那次没跑或看不出来时，按明显用词粗判。
- “对方最近的情绪”取 12 小时内最近一次观察，置信度按 3 小时半衰期衰减，太弱就当没有。
- 只作为背景进入提示，不改写历史消息，也不替代她自己的心情（AgentState.mood）。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ..models import Message, utcnow

USER_EMOTIONS = ("平静", "开心", "兴奋", "难过", "生气", "焦虑", "疲惫", "惊讶")
WINDOW_HOURS = 12
HALF_LIFE_HOURS = 3.0
MIN_CONFIDENCE = 0.25
RULE_CONFIDENCE = 0.45

# 只认明显的说法；前面紧跟“不/没/别”的不算（“不累”不是累）
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("难过", ("难过", "伤心", "想哭", "哭了", "失落", "低落", "心累", "emo")),
    ("焦虑", ("焦虑", "紧张", "担心", "好慌", "心慌", "压力好大", "压力大", "睡不着")),
    ("生气", ("生气", "气死", "烦死", "好烦", "火大", "无语")),
    ("疲惫", ("好累", "太累", "累死", "累了", "很累", "有点累", "好困", "困了", "没力气", "熬夜", "疲惫")),
    ("兴奋", ("激动", "兴奋", "迫不及待")),
    ("开心", ("开心", "高兴", "太好了", "哈哈", "好耶", "好棒")),
    ("惊讶", ("居然", "竟然", "没想到", "震惊")),
)
_NEGATION = ("不", "没", "别")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def rule_emotion(text: str) -> dict[str, Any] | None:
    """按明显用词粗判；拿不准就返回 None。"""
    text = (text or "").lower()
    for label, words in _RULES:
        for word in words:
            for match in re.finditer(re.escape(word), text):
                if match.start() > 0 and text[match.start() - 1] in _NEGATION:
                    continue
                return {"label": label, "confidence": RULE_CONFIDENCE, "source": "rule"}
    return None


def parse_llm_emotion(data: Any) -> dict[str, Any] | None:
    """记忆提取 JSON 里的 user_emotion 字段：{"label": 八选一, "confidence": 0-1} 或 null。"""
    if not isinstance(data, dict):
        return None
    value = data.get("user_emotion")
    if not isinstance(value, dict) or value.get("label") not in USER_EMOTIONS:
        return None
    try:
        confidence = min(max(float(value.get("confidence", 0.6)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.6
    return {"label": value["label"], "confidence": round(confidence, 2), "source": "llm"}


def record_user_emotion(session: Session, message: Message, emotion: dict[str, Any] | None) -> None:
    if emotion is None or message.role != "user":
        return
    message.emotion_tags = json.dumps(emotion, ensure_ascii=False)
    session.add(message)
    session.commit()


def _observation(message: Message) -> dict[str, Any] | None:
    try:
        value = json.loads(message.emotion_tags or "[]")
    except (TypeError, ValueError):
        return None
    if isinstance(value, dict) and value.get("label") in USER_EMOTIONS:
        return value
    return None


def current_user_emotion(session: Session, *, now: datetime | None = None) -> dict[str, Any] | None:
    """对方最近的情绪：12 小时内最近一次观察，置信度随时间衰减。"""
    now = now or utcnow()
    since = now - timedelta(hours=WINDOW_HOURS)
    rows = session.exec(
        select(Message)
        .where(Message.role == "user", Message.created_at >= since, Message.emotion_tags != "[]")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(20)
    ).all()
    for message in rows:
        observation = _observation(message)
        if observation is None:
            continue
        hours = max((now - _aware(message.created_at)).total_seconds() / 3600.0, 0.0)
        confidence = float(observation.get("confidence") or 0.0) * math.pow(0.5, hours / HALF_LIFE_HOURS)
        if confidence < MIN_CONFIDENCE:
            return None
        return {
            "label": observation["label"],
            "confidence": round(confidence, 2),
            "source": observation.get("source", ""),
            "observed_at": _aware(message.created_at),
        }
    return None


def _ago(observed_at: datetime, now: datetime) -> str:
    minutes = (now - observed_at).total_seconds() / 60.0
    if minutes < 15:
        return "刚才"
    if minutes < 60:
        return f"约 {int(minutes)} 分钟前"
    return f"约 {int(minutes // 60)} 小时前"


def user_emotion_block(session: Session) -> str:
    emotion = current_user_emotion(session)
    if emotion is None or emotion["label"] == "平静":
        return ""
    basis = "从对方的话推测" if emotion["source"] == "llm" else "从对方的用词粗略判断"
    return (
        "## 对方最近的情绪（推测，只作背景）\n"
        f"{_ago(emotion['observed_at'], utcnow())}，对方看起来{emotion['label']}（{basis}）。"
        "体会着回应就好，不必点破，也可能已经过去了；你有自己的心情，不要被带着一起" + emotion["label"] + "。"
    )
