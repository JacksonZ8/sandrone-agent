from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from ...config import PROMPTS_DIR
from ...models import AgentDiary, AgentState, SemanticMemory, utcnow

MEMORY_TYPES = ("fact", "preference", "relationship", "project", "event")
PERSPECTIVES = ("user", "self", "shared")

try:
    import jieba

    jieba.setLogLevel(60)
    _JIEBA_READY = True
except Exception:  # pragma: no cover
    _JIEBA_READY = False


def tokenize(text: str) -> frozenset[str]:
    """分词结果按文本缓存：每轮都要给全部记忆和近 800 条旧对话分词，内容不变就不重复算。"""
    return _tokenize((text or "").lower())


@lru_cache(maxsize=8192)
def _tokenize(text: str) -> frozenset[str]:
    if not text:
        return frozenset()
    if _JIEBA_READY:
        tokens = set(jieba.lcut(text))
    else:
        tokens = {text[i : i + 2] for i in range(max(0, len(text) - 1))}
    tokens.discard(" ")
    return frozenset(t for t in tokens if t and t.strip())


def warm_up() -> None:
    """后台预载 jieba 词典（约 0.4 秒），免得重启后第一条消息等它。"""
    if _JIEBA_READY:
        jieba.initialize()


def estimate_importance(text: str) -> float:
    """轻量规则评估：显式记忆请求、偏好表达和个人信息优先。"""
    text = text or ""
    score = 0.20
    if any(k in text for k in ("记住", "别忘了", "以后都", "以后要", "我叫", "我的名字", "你可以叫我")):
        score += 0.55
    if any(k in text for k in ("喜欢", "不喜欢", "讨厌", "偏好", "希望", "不要", "别")):
        score += 0.25
    if any(k in text for k in ("项目", "目标", "计划", "工作", "实验", "研究")):
        score += 0.15
    if len(text) >= 40:
        score += 0.10
    return min(score, 1.0)


def estimate_valence(text: str) -> float:
    text = (text or "").replace("不喜欢", "负面").replace("不太喜欢", "负面")
    positive = sum(text.count(w) for w in ("喜欢", "开心", "高兴", "谢谢", "不错", "很好", "期待", "愿意"))
    negative = sum(text.count(w) for w in ("负面", "讨厌", "烦", "累", "难过", "生气", "失望", "不要", "害怕", "焦虑"))
    if positive or negative:
        return max(-1.0, min(1.0, (positive - negative) * 0.35))
    return 0.0


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _age_days(value: datetime | None) -> float:
    value = _dt(value)
    if value is None:
        return 365.0
    return max((utcnow() - value).total_seconds() / 86400.0, 0.0)


def _age_hours(value: datetime | None) -> float:
    return _age_days(value) * 24.0


def _persona_text() -> str:
    path = PROMPTS_DIR / "sandrone_persona.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "你是桑多涅，愚人众执行官第七席「木偶」。"


def _memory_to_dict(m: SemanticMemory) -> dict[str, Any]:
    try:
        sources = json.loads(m.source_message_ids or "[]")
    except json.JSONDecodeError:
        sources = []
    return {
        "id": m.id,
        "perspective": m.perspective,
        "type": m.type,
        "content": m.content,
        "source_message_ids": sources,
        "confidence": m.confidence,
        "importance": m.importance,
        "valence": m.valence,
        "strength": m.strength,
        "access_count": m.access_count,
        "last_accessed_at": m.last_accessed_at,
        "status": m.status,
        "notes": m.notes,
        "scope": m.scope,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


def _diary_to_public(d: AgentDiary) -> dict[str, Any]:
    return {
        "id": d.id,
        "entry_type": d.entry_type,
        "summary": d.summary,
        "mood_before": d.mood_before,
        "mood_after": d.mood_after,
        "created_at": d.created_at,
    }


def _agent_state_to_public(state: AgentState) -> dict[str, Any]:
    return {
        "mood": state.mood,
        "energy": state.energy,
        "focus": state.focus,
    }


def _token_similarity(a: str, b: str) -> float:
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
