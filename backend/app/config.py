from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
AUDIO_CACHE_DIR = DATA_DIR / "audio_cache"
ASSETS_DIR = PROJECT_ROOT / "assets"
TTS_ASSETS_DIR = ASSETS_DIR / "tts"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTS_MODE = os.getenv("SANDRONE_TTS_MODE", "api")  # api / cli / mock
# GPT-SoVITS 源码目录（自备），默认放在项目旁边
GPT_SOVITS_DIR = Path(os.getenv("SANDRONE_GPT_SOVITS_DIR", str(PROJECT_ROOT.parent / "GPT-SoVITS")))
GPT_SOVITS_PYTHON = Path(
    os.getenv("SANDRONE_GPT_SOVITS_PYTHON", str(GPT_SOVITS_DIR / ".venv-gpt-sovits/bin/python"))
)
GPT_SOVITS_MPLCONFIGDIR = Path(
    os.getenv(
        "SANDRONE_GPT_SOVITS_MPLCONFIGDIR",
        str(DATA_DIR / "matplotlib_cache"),
    )
)
TTS_API_BASE_URL = os.getenv("SANDRONE_TTS_API_BASE_URL", "http://127.0.0.1:9881")
TTS_API_CONFIG_PATH = DATA_DIR / "tts_infer_sandrone.yaml"

# 记忆检索用的中文向量模型（由 scripts/fetch_memory_model.sh 下载，不进 Git；缺失时只用分词匹配）
EMBEDDING_MODEL_DIR = Path(
    os.getenv("SANDRONE_EMBEDDING_MODEL_DIR", str(DATA_DIR / "models" / "bge-small-zh-v1.5"))
)

# 实时语音：sherpa-onnx 流式识别与 VAD 模型（由 scripts/fetch_voice_models.sh 下载，不进 Git）
VOICE_MODEL_DIR = Path(os.getenv("SANDRONE_VOICE_MODEL_DIR", str(DATA_DIR / "voice_models")))
ASR_MODEL_NAME = os.getenv(
    "SANDRONE_ASR_MODEL",
    "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05",
)
ASR_NUM_THREADS = int(os.getenv("SANDRONE_ASR_THREADS", "2"))

# Live2D：用户自备的模型与 Cubism Core（受 Live2D 许可约束，不进 Git），由 /live2d 静态提供
LIVE2D_DIR = Path(os.getenv("SANDRONE_LIVE2D_DIR", str(DATA_DIR / "live2d")))
# 通话形象素材包（自备，格式见 docs/character.md，不进 Git），由 /character 静态提供
CHARACTER_DIR = Path(os.getenv("SANDRONE_CHARACTER_DIR", str(DATA_DIR / "character")))
# 每个游戏的原始资料与本机备份（不进 Git）
GAMES_DIR = Path(os.getenv("SANDRONE_GAMES_DIR", str(DATA_DIR / "games")))
# 每个游戏一份可分享的游戏知识（规则与高手打法，不含个人信息），随仓库进 Git
KNOWLEDGE_DIR = Path(os.getenv("SANDRONE_KNOWLEDGE_DIR", str(PROJECT_ROOT / "knowledge" / "games")))


DATABASE_URL = os.getenv("SANDRONE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'sandrone_agent.db'}")
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

RECENT_MESSAGE_LIMIT = int(os.getenv("SANDRONE_RECENT_MESSAGE_LIMIT", "40"))
EPISODIC_RECALL_LIMIT = int(os.getenv("SANDRONE_EPISODIC_RECALL_LIMIT", "4"))
SEMANTIC_MEMORY_LIMIT = int(os.getenv("SANDRONE_SEMANTIC_MEMORY_LIMIT", "10"))

APP_NAME = "Sandrone Agent"
APP_VERSION = "0.1.0"
