from __future__ import annotations

from sqlmodel import Session, select

from ..models import AgentState, LLMConfig, RelationshipState, utcnow
from ..schemas import LLMConfigUpdate, PROVIDER_PRESETS


def seed_default_config(session: Session) -> LLMConfig:
    config = session.get(LLMConfig, 1)
    if config is None:
        config = LLMConfig(id=1)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def seed_default_state(session: Session) -> RelationshipState:
    state = session.get(RelationshipState, 1)
    if state is None:
        state = RelationshipState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def seed_agent_state(session: Session) -> AgentState:
    state = session.get(AgentState, 1)
    if state is None:
        state = AgentState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def get_agent_state(session: Session) -> AgentState:
    return seed_agent_state(session)


def get_config(session: Session) -> LLMConfig:
    return seed_default_config(session)


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "••••"
    return f"{api_key[:3]}••••••••{api_key[-4:]}"


def apply_config_update(session: Session, config: LLMConfig, update: LLMConfigUpdate) -> LLMConfig:
    data = update.model_dump(exclude_unset=True)
    if "api_key" in data and data["api_key"] is None:
        data.pop("api_key")  # None 表示保留现有 Key
    for field, value in data.items():
        if value is not None:
            setattr(config, field, value)
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def config_to_public(config: LLMConfig) -> dict:
    preset = PROVIDER_PRESETS.get(config.provider, PROVIDER_PRESETS["openai_compatible"])
    return {
        "provider": config.provider,
        "provider_label": preset["label"],
        "base_url": config.base_url,
        "model": config.model,
        "has_api_key": bool(config.api_key),
        "api_key_masked": mask_api_key(config.api_key),
        "api_key_required": bool(preset["api_key_required"]),
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "reply_thinking": config.reply_thinking,
        "updated_at": config.updated_at,
    }


def get_llm_config_snapshot(config: LLMConfig) -> dict:
    return {
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "api_key": config.api_key,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "reply_thinking": config.reply_thinking,
    }
