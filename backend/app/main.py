from __future__ import annotations

import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_NAME, APP_VERSION, AUDIO_CACHE_DIR, CHARACTER_DIR, FRONTEND_DIST, LIVE2D_DIR
from .database import init_db
from .routers import chat, companion, games, live2d, memories, proactive, providers, push, state, tools, tts, usage, voice, wechat
from .security import LocalRequestGuard
from .services.memory.common import warm_up as warm_up_tokenizer
from .services.proactive_service import PROACTIVE_SCHEDULER
from .services.screen_companion import COMPANION
from .services.tts_service import start_tts_api_background, stop_tts_api
from .services.voice.asr import start_asr_background
from .services.wechat_service import shutdown_wechat, startup_wechat

# PWA 清单需要正确的类型；sw.js 与清单不能被长期缓存，否则手机上更新不了
mimetypes.add_type("application/manifest+json", ".webmanifest")
NO_CACHE_FILES = {"sw.js", "manifest.webmanifest"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await startup_wechat()
    start_tts_api_background()
    start_asr_background()
    threading.Thread(target=warm_up_tokenizer, daemon=True).start()
    PROACTIVE_SCHEDULER.start()
    COMPANION.start()
    yield
    await COMPANION.stop()
    await PROACTIVE_SCHEDULER.stop()
    await shutdown_wechat()
    stop_tts_api()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(LocalRequestGuard)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(providers.router)
app.include_router(chat.router)
app.include_router(memories.router)
app.include_router(state.router)
app.include_router(tools.router)
app.include_router(tts.router)
app.include_router(wechat.router)
app.include_router(proactive.router)
app.include_router(usage.router)
app.include_router(voice.router)
app.include_router(live2d.router)
app.include_router(push.router)
app.include_router(companion.router)
app.include_router(games.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


@app.get("/")
def root():
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").is_file():
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-store"})
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_CACHE_DIR), name="audio")

LIVE2D_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/live2d", StaticFiles(directory=LIVE2D_DIR), name="live2d")

CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/character", StaticFiles(directory=CHARACTER_DIR), name="character")

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
            headers = {"Cache-Control": "no-cache"} if full_path in NO_CACHE_FILES else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-store"})
