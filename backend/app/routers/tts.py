from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..schemas import TTSStatus
from ..services.tts_service import synthesize_tts, tts_status

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    message_id: int | None = None


class TTSResponse(BaseModel):
    audio_path: str
    audio_url: str
    voice_id: str
    duration_sec: float
    cached: bool
    text: str


@router.get("/status", response_model=TTSStatus)
def read_tts_status() -> dict:
    return tts_status()


@router.post("", response_model=TTSResponse)
async def synthesize(request: TTSRequest) -> dict:
    try:
        return await asyncio.to_thread(
            synthesize_tts,
            request.text,
            message_id=request.message_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
