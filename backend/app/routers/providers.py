from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..database import get_session
from ..models import LLMConfig
from ..schemas import (
    LLMConfigPublic,
    LLMConfigUpdate,
    LLMTestRequest,
    LLMTestResponse,
    PROVIDER_PRESETS,
)
from ..services.llm_service import LLMError, chat_completion
from ..services.provider_service import (
    apply_config_update,
    config_to_public,
    get_config,
)

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/provider-presets")
def provider_presets() -> dict:
    return {"presets": PROVIDER_PRESETS}


@router.get("/llm-config", response_model=LLMConfigPublic)
def read_llm_config(session: Session = Depends(get_session)) -> dict:
    return config_to_public(get_config(session))


@router.put("/llm-config", response_model=LLMConfigPublic)
def update_llm_config(
    update: LLMConfigUpdate,
    session: Session = Depends(get_session),
) -> dict:
    config = get_config(session)
    data = update.model_dump(exclude_unset=True)
    if data.get("provider") is not None and data["provider"] not in PROVIDER_PRESETS:
        raise HTTPException(status_code=422, detail="未知的 Provider。")
    apply_config_update(session, config, update)
    return config_to_public(config)


@router.post("/llm-config/test", response_model=LLMTestResponse)
async def test_llm_config(
    payload: LLMTestRequest,
    session: Session = Depends(get_session),
) -> dict:
    saved = get_config(session)
    draft = payload.model_dump(exclude_unset=True)

    # api_key=None 时使用已保存的 Key，便于“只改模型后测试”
    if draft.get("api_key") is None:
        draft.pop("api_key", None)

    temp = LLMConfig(
        id=saved.id,
        provider=draft.get("provider", saved.provider),
        base_url=draft.get("base_url", saved.base_url),
        model=draft.get("model", saved.model),
        api_key=draft.get("api_key", saved.api_key),
        temperature=draft.get("temperature", saved.temperature),
        max_tokens=draft.get("max_tokens", saved.max_tokens),
        timeout_seconds=draft.get("timeout_seconds", saved.timeout_seconds),
    )
    if temp.provider not in PROVIDER_PRESETS:
        raise HTTPException(status_code=422, detail="未知的 Provider。")

    try:
        result = await chat_completion(
            session,
            temp,
            [{"role": "user", "content": "你好。请只回复四个字以内。"}],
            purpose="test",
            max_tokens=16,
        )
    except LLMError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "provider": temp.provider,
            "model": temp.model,
            "latency_ms": 0,
            "reply_preview": "",
        }

    return {
        "ok": True,
        "message": "连接成功。",
        "provider": temp.provider,
        "model": temp.model,
        "latency_ms": result["latency_ms"],
        "reply_preview": result["text"][:120],
    }
