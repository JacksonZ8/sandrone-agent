from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from ..models import AffectionEvent, Message, RelationshipState, utcnow


def get_relationship_state(session: Session) -> RelationshipState:
    state = session.get(RelationshipState, 1)
    if state is None:
        state = RelationshipState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def affection_phase(value: float) -> dict[str, Any]:
    if value < 0.25:
        return {
            "label": "戒备",
            "guidance": "保持距离，嘴上不饶人，但不会真的伤害对方。",
        }
    if value < 0.45:
        return {
            "label": "观察",
            "guidance": "愿意继续接触，关心会包装成嫌弃和顺便。",
        }
    if value < 0.60:
        return {
            "label": "开始习惯",
            "guidance": "已经习惯对方存在，会主动留意他的状态；被戳穿时先否认，再小声补一句。",
        }
    if value < 0.75:
        return {
            "label": "明显在意",
            "guidance": "嘴上依旧很硬，但行动藏不住；会找借口靠近，也会在对方需要时出现。",
        }
    if value < 0.90:
        return {
            "label": "重要的人",
            "guidance": "对方已经是少数被放进内圈的人。关心更直接，偶尔承认“我确实在意”。",
        }
    return {
        "label": "不愿失去",
        "guidance": "非常信任和依赖对方。可以自然表达关心，但仍保留骄傲和傲娇，不说肉麻话。",
    }


def relationship_prompt_block(session: Session) -> str:
    state = get_relationship_state(session)
    phase = affection_phase(state.affection)
    return (
        "## 当前关系状态（真实存在，但不要向用户报数字）\n"
        f"- 好感度：{state.affection:.0%}，阶段：{phase['label']}。\n"
        f"- 你此刻对待用户的方式：{phase['guidance']}"
    )


def _score_text(text: str) -> tuple[float, list[str]]:
    text = text or ""
    normalized = text.replace("不喜欢", "【偏好】").replace("不太喜欢", "【偏好】")
    reasons: list[str] = []
    delta = 0.0

    negative_terms = [
        ("讨厌你", -0.04),
        ("别管我", -0.03),
        ("不想理你", -0.03),
        ("烦死了", -0.02),
        ("闭嘴", -0.04),
        ("滚", -0.05),
    ]
    for term, weight in negative_terms:
        if term in normalized:
            delta += weight
            if "用户说了重话" not in reasons:
                reasons.append("用户说了重话")

    positive_terms = [
        ("你真好", 0.03),
        ("想你了", 0.03),
        ("谢谢", 0.02),
        ("辛苦了", 0.02),
        ("喜欢", 0.015),
        ("开心", 0.015),
        ("陪我", 0.02),
        ("有你在", 0.025),
        ("真厉害", 0.02),
    ]
    positive_hits = 0
    for term, weight in positive_terms:
        if term in normalized:
            delta += weight
            positive_hits += 1
    if positive_hits:
        reasons.append("用户表达了正向情绪")

    care_terms = ("你累不累", "你怎么样", "注意休息", "照顾好自己", "别太累")
    if any(term in normalized for term in care_terms):
        delta += 0.025
        reasons.append("用户主动关心桑多涅")

    if "我们" in normalized or "项目" in normalized:
        delta += 0.008
        reasons.append("用户把桑多涅算进自己的事里")

    if len(text) >= 30:
        delta += 0.01
        reasons.append("用户愿意说更多自己的事")
    elif len(text) >= 12:
        delta += 0.005

    if delta == 0 and len(text) >= 2:
        delta += 0.002
        reasons.append("普通但持续的对话")

    delta = max(-0.05, min(0.05, round(delta, 4)))
    return delta, reasons or ["对话本身"]


def apply_conversation_affection(
    session: Session,
    *,
    user_message_id: int,
    user_text: str,
) -> AffectionEvent | None:
    state = get_relationship_state(session)
    delta, reasons = _score_text(user_text)
    before = state.affection
    after = max(0.0, min(1.0, round(before + delta, 4)))

    if abs(after - before) < 0.0001:
        return None

    state.affection = after
    if delta > 0:
        state.familiarity = min(1.0, round(state.familiarity + 0.004, 4))
        state.trust = min(1.0, round(state.trust + 0.003, 4))
    elif delta < 0:
        state.trust = max(0.0, round(state.trust - 0.002, 4))
    state.updated_at = utcnow()
    session.add(state)

    event = AffectionEvent(
        source_message_id=user_message_id,
        delta=delta,
        level_before=before,
        level_after=after,
        reason="；".join(reasons),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def relationship_public(session: Session) -> dict[str, Any]:
    state = get_relationship_state(session)
    phase = affection_phase(state.affection)
    events = list(
        session.exec(
            select(AffectionEvent).order_by(AffectionEvent.created_at.desc(), AffectionEvent.id.desc()).limit(10)
        ).all()
    )
    return {
        "affection": state.affection,
        "familiarity": state.familiarity,
        "trust": state.trust,
        "phase": phase["label"],
        "guidance": phase["guidance"],
        "updated_at": state.updated_at,
        "events": [
            {
                "id": e.id,
                "delta": e.delta,
                "level_before": e.level_before,
                "level_after": e.level_after,
                "reason": e.reason,
                "created_at": e.created_at,
            }
            for e in reversed(events)
        ],
    }
