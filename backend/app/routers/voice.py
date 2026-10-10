from __future__ import annotations

from fastapi import APIRouter, WebSocket

from ..services.voice.asr import ASR_ENGINE, models_present
from ..services.voice.call import VoiceCall
from ..services.voice.turn_detect import TURN_DETECTOR

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/status")
def voice_status() -> dict:
    return {
        "models_present": models_present(),
        "asr_ready": ASR_ENGINE.ready,
        "turn_detector_ready": TURN_DETECTOR.ready,
        "error": ASR_ENGINE.error or TURN_DETECTOR.error,
    }


@router.websocket("/ws")
async def voice_ws(websocket: WebSocket) -> None:
    # 来源校验在 LocalRequestGuard 里完成：只接受本机 Host 与本机 Origin。
    await VoiceCall(websocket).run()
