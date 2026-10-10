from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from ..config import CHARACTER_DIR, LIVE2D_DIR

router = APIRouter(prefix="/api/live2d", tags=["live2d"])

CORE_FILE = "live2dcubismcore.min.js"
MAPPING_FILE = "mapping.json"
MAX_DEPTH = 3


def _models(root: Path) -> list[str]:
    """找出目录下（最多三层）的 Cubism 3/4 模型入口文件，返回相对路径。"""
    if not root.is_dir():
        return []
    found: list[str] = []
    for path in sorted(root.rglob("*.model3.json")):
        relative = path.relative_to(root)
        if len(relative.parts) <= MAX_DEPTH + 1 and not any(part.startswith(".") for part in relative.parts):
            found.append(relative.as_posix())
    return found


def _mapping(root: Path) -> dict:
    path = root / MAPPING_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "mapping.json 不是合法 JSON。"}
    return data if isinstance(data, dict) else {}


@router.get("/status")
def live2d_status() -> dict:
    """前端据此决定显示 Live2D 还是占位剪影。mapping.json 可指定模型、情绪→表情/动作与口型参数。"""
    models = _models(LIVE2D_DIR)
    mapping = _mapping(LIVE2D_DIR)
    chosen = mapping.get("model") if isinstance(mapping.get("model"), str) else None
    if chosen not in models:
        chosen = models[0] if models else None
    core = (LIVE2D_DIR / CORE_FILE).is_file()
    portrait = (CHARACTER_DIR / "portrait" / "manifest.json").is_file()
    films = {
        key: {"video": f"/character/films/{key}.mp4", "poster": f"/character/films/{key}.jpg"}
        for key in ("theatre", "manuscript")
        if (CHARACTER_DIR / "films" / f"{key}.mp4").is_file()
    }
    return {
        # 开屏片头：theatre 暖金、manuscript 蓝白
        "films": films,
        # 逐帧母版素材包：没有 Cubism 模型时用它显示通话形象
        "portrait_url": "/character/portrait/manifest.json" if portrait else None,
        "ready": bool(core and chosen),
        "core_present": core,
        "core_url": f"/live2d/{CORE_FILE}" if core else None,
        "models": models,
        "model_url": f"/live2d/{chosen}" if chosen else None,
        "mapping": mapping,
        "folder": str(LIVE2D_DIR),
    }
