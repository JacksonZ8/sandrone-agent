from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from ..config import APP_NAME, APP_VERSION
from ..database import get_session
from ..models import Message, SemanticMemory
from ..schemas import AppState
from ..services.provider_service import get_agent_state, get_config
from ..services.tts_service import tts_status

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state", response_model=AppState)
def app_state(session: Session = Depends(get_session)) -> dict:
    message_count = session.exec(select(func.count(Message.id)).where(Message.thread_id == "main")).one()
    memory_count = session.exec(
        select(func.count(SemanticMemory.id)).where(SemanticMemory.status == "active")
    ).one()
    config = get_config(session)
    voice = tts_status()
    agent_state = get_agent_state(session)
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "message_count": int(message_count),
        "active_memory_count": int(memory_count),
        "has_api_key": bool(config.api_key),
        "provider": config.provider,
        "model": config.model,
        "voice_ready": voice["ready"],
        "voice_message": voice["message"],
        "agent_state": {
            "mood": agent_state.mood,
            "energy": agent_state.energy,
            "focus": agent_state.focus,
        },
    }
