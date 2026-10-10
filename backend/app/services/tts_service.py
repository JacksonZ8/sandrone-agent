from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import (
    AUDIO_CACHE_DIR,
    GPT_SOVITS_DIR,
    GPT_SOVITS_MPLCONFIGDIR,
    GPT_SOVITS_PYTHON,
    TTS_API_BASE_URL,
    TTS_API_CONFIG_PATH,
    TTS_ASSETS_DIR,
    TTS_MODE,
)
from ..database import engine
from .usage.audit import record_api_call
from sqlmodel import Session

_manifest_path = TTS_ASSETS_DIR / "configs" / "sandrone_tts_config.json"
_generate_lock = threading.Lock()
_api_lock = threading.Lock()
_api_process: subprocess.Popen | None = None


def _load_manifest() -> dict[str, Any]:
    if not _manifest_path.exists():
        return {}
    try:
        return json.loads(_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _weight_paths() -> tuple[Path, Path, Path, Path]:
    manifest = _load_manifest()
    weights = manifest.get("weights") or {}
    reference = manifest.get("reference") or {}
    gpt_path = TTS_ASSETS_DIR / weights.get("gpt", "")
    sovits_path = TTS_ASSETS_DIR / weights.get("sovits", "")
    ref_audio = TTS_ASSETS_DIR / reference.get("audio", "")
    ref_text = TTS_ASSETS_DIR / "reference" / "ref_text.txt"
    return gpt_path, sovits_path, ref_audio, ref_text


def _write_api_config() -> None:
    gpt_path, sovits_path, _, _ = _weight_paths()
    bert = GPT_SOVITS_DIR / "GPT_SoVITS" / "pretrained_models" / "chinese-roberta-wwm-ext-large"
    hubert = GPT_SOVITS_DIR / "GPT_SoVITS" / "pretrained_models" / "chinese-hubert-base"
    config = {
        "custom": {
            "bert_base_path": str(bert),
            "cnhuhbert_base_path": str(hubert),
            "device": "cpu",
            "is_half": False,
            "t2s_weights_path": str(gpt_path),
            "version": "v2",
            "vits_weights_path": str(sovits_path),
        },
        "v2": {
            "bert_base_path": str(bert),
            "cnhuhbert_base_path": str(hubert),
            "device": "cpu",
            "is_half": False,
            "t2s_weights_path": str(gpt_path),
            "version": "v2",
            "vits_weights_path": str(sovits_path),
        },
    }
    TTS_API_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TTS_API_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _api_url_parts() -> tuple[str, int]:
    parsed = urlparse(TTS_API_BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9881
    return host, port


def _api_env() -> dict[str, str]:
    env = dict(os.environ)
    env["MPLCONFIGDIR"] = str(GPT_SOVITS_MPLCONFIGDIR)
    env["PYTHONPATH"] = f"{GPT_SOVITS_DIR}:{GPT_SOVITS_DIR / 'GPT_SoVITS'}"
    return env


def _api_health() -> bool:
    try:
        response = httpx.get(f"{TTS_API_BASE_URL.rstrip('/')}/openapi.json", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


def _start_api_process() -> None:
    global _api_process
    if _api_process is not None and _api_process.poll() is None:
        return
    _write_api_config()
    host, port = _api_url_parts()
    command = [
        str(GPT_SOVITS_PYTHON),
        str(GPT_SOVITS_DIR / "api_v2.py"),
        "-a",
        host,
        "-p",
        str(port),
        "-c",
        str(TTS_API_CONFIG_PATH),
    ]
    log_path = TTS_API_CONFIG_PATH.parent / "tts_api.log"
    log_file = open(log_path, "a", encoding="utf-8")
    _api_process = subprocess.Popen(
        command,
        cwd=str(GPT_SOVITS_DIR),
        env=_api_env(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def ensure_tts_api(timeout_seconds: float = 180.0) -> bool:
    if TTS_MODE != "api":
        return False
    with _api_lock:
        if _api_health():
            return True
        _start_api_process()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _api_health():
                return True
            if _api_process is not None and _api_process.poll() is not None:
                return False
            time.sleep(1.0)
        return False


def stop_tts_api() -> None:
    global _api_process
    if _api_process is not None and _api_process.poll() is None:
        _api_process.terminate()
        try:
            _api_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _api_process.kill()
    _api_process = None


_prewarm_started = False
_prewarm_done = False


def _prewarm_worker() -> None:
    global _prewarm_done
    try:
        if not ensure_tts_api(timeout_seconds=240):
            return
        gpt_path, sovits_path, ref_audio, ref_text_path = _weight_paths()
        httpx.post(
            f"{TTS_API_BASE_URL.rstrip('/')}/tts",
            json={
                "text": "你好。",
                "text_lang": "zh",
                "ref_audio_path": str(ref_audio),
                "prompt_text": ref_text_path.read_text(encoding="utf-8").strip(),
                "prompt_lang": "zh",
                "text_split_method": "cut5",
                "batch_size": 1,
                "media_type": "wav",
                "streaming_mode": False,
            },
            timeout=180.0,
        )
    except Exception:
        pass
    finally:
        _prewarm_done = True


def start_tts_api_background() -> None:
    global _prewarm_started
    if TTS_MODE != "api" or _prewarm_started:
        return
    _prewarm_started = True
    thread = threading.Thread(target=_prewarm_worker, daemon=True)
    thread.start()



def tts_status() -> dict[str, Any]:
    manifest = _load_manifest()
    gpt_path, sovits_path, ref_audio, ref_text = _weight_paths()

    if TTS_MODE == "mock":
        ready = True
        message = "TTS 处于 Mock 模式：用提示音代替真实合成。"
    elif TTS_MODE == "api":
        ready = _api_health()
        message = "常驻 TTS API 已就绪。" if ready else "常驻 TTS API 尚未启动，第一次合成时会自动启动。"
    else:
        cli_path = GPT_SOVITS_DIR / "GPT_SoVITS" / "inference_cli.py"
        missing = []
        if not GPT_SOVITS_PYTHON.exists():
            missing.append("GPT-SoVITS Python")
        if not cli_path.exists():
            missing.append("inference_cli.py")
        if not gpt_path.exists():
            missing.append("GPT 权重")
        if not sovits_path.exists():
            missing.append("SoVITS 权重")
        if not ref_audio.exists():
            missing.append("参考音频")
        if not ref_text.exists():
            missing.append("参考文本")
        ready = not missing
        message = "TTS CLI 已就绪。" if ready else "TTS 未就绪：" + "、".join(missing)

    return {
        "ready": ready,
        "voice_id": manifest.get("voice_id", "sandrone_v1_e12_e8"),
        "engine": manifest.get("engine", "GPT-SoVITS"),
        "message": message,
        "mode": TTS_MODE,
    }


def _audio_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return round(frames / rate, 2) if rate else 0.0
    except Exception:
        return 0.0


MOCK_RATE = 32000


def _mock_pcm(text: str) -> bytes:
    """Mock 模式的合成音：按字数给出一段轻声提示音（不依赖任何人声样本）。"""
    seconds = min(4.0, 0.4 + 0.12 * len(text or ""))
    frames = int(MOCK_RATE * seconds)
    return b"".join(
        int(1200 * math.sin(2 * math.pi * 440 * i / MOCK_RATE) * (1 if (i // 1600) % 4 else 0)).to_bytes(2, "little", signed=True)
        for i in range(frames)
    )


def _mock_synthesize(text: str, target: Path) -> None:
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(MOCK_RATE)
        wav.writeframes(_mock_pcm(text))


def _cli_synthesize(text: str, target: Path) -> None:
    gpt_path, sovits_path, ref_audio, ref_text_path = _weight_paths()
    cli_path = GPT_SOVITS_DIR / "GPT_SoVITS" / "inference_cli.py"
    work_dir = Path("/tmp") / f"sandrone_tts_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    target_txt = work_dir / "target.txt"
    target_txt.write_text(text, encoding="utf-8")

    env = _api_env()
    env["gpt_path"] = str(gpt_path)
    env["sovits_path"] = str(sovits_path)
    command = [
        str(GPT_SOVITS_PYTHON),
        str(cli_path),
        "--gpt_model",
        str(gpt_path),
        "--sovits_model",
        str(sovits_path),
        "--ref_audio",
        str(ref_audio),
        "--ref_text",
        str(ref_text_path),
        "--ref_language",
        "中文",
        "--target_text",
        str(target_txt),
        "--target_language",
        "中文",
        "--output_path",
        str(work_dir),
    ]
    result = subprocess.run(
        command,
        cwd=str(GPT_SOVITS_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output_wav = work_dir / "output.wav"
    if result.returncode != 0 or not output_wav.exists():
        tail = (result.stderr or result.stdout or "")[-800:]
        raise RuntimeError(f"GPT-SoVITS 合成失败：{tail}")
    shutil.move(str(output_wav), str(target))
    shutil.rmtree(work_dir, ignore_errors=True)


_LATIN = re.compile(r"[A-Za-z]")
# GPT-SoVITS 处理中文里夹的英文单词时要用 NLTK 的英文词性标注数据；本机缺这份数据时合成服务会中途断开，
# 而且断开前可能已经送出半句声音，无法安全重试。所以第一次遇到英文时先用极短样本探测一次，结果记住：
# 不可用就让含英文的句子直接用 all_zh（按中文规则念，不再崩）。进程重启后重新探测。
_english_g2p_broken = False
_english_probe: bool | None = None  # None=未探测，True=英文可用，False=不可用
_PROBE_TEXT = "OK。"


def _text_lang(text: str) -> str:
    return "all_zh" if _english_g2p_broken and _LATIN.search(text) else "zh"


def _probe_english(payload: dict[str, Any]) -> bool:
    """同步探测英文发音是否可用（非流式、约一秒），只在第一次遇到英文时调用。"""
    global _english_probe, _english_g2p_broken
    probe = {**payload, "text": _PROBE_TEXT, "text_lang": "zh", "streaming_mode": False}
    try:
        response = httpx.post(f"{TTS_API_BASE_URL.rstrip('/')}/tts", json=probe, timeout=60.0)
        ok = response.status_code == 200 and len(response.content) > 44
    except httpx.HTTPError:
        ok = False
    _english_probe = ok
    _english_g2p_broken = not ok
    return ok


def _mark_english_failure(text: str) -> bool:
    """含英文的句子合成失败时标记并返回 True，调用方可用 all_zh 重试。"""
    global _english_g2p_broken
    if _LATIN.search(text) and not _english_g2p_broken:
        _english_g2p_broken = True
        return True
    return False


def _api_synthesize(text: str, target: Path) -> None:
    if not ensure_tts_api():
        raise RuntimeError("常驻 TTS API 启动失败，请检查 data/tts_api.log。")
    _, _, ref_audio, ref_text_path = _weight_paths()
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": str(ref_audio),
        "prompt_text": ref_text_path.read_text(encoding="utf-8").strip(),
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": 1.0,
    }
    if _english_probe is None and _LATIN.search(text):
        _probe_english(payload)
    payload["text_lang"] = _text_lang(text)
    try:
        response = httpx.post(f"{TTS_API_BASE_URL.rstrip('/')}/tts", json=payload, timeout=300.0)
        failed = response.status_code != 200
    except httpx.HTTPError:
        failed, response = True, None
    if failed and _mark_english_failure(text):
        payload["text_lang"] = "all_zh"
        response = httpx.post(f"{TTS_API_BASE_URL.rstrip('/')}/tts", json=payload, timeout=300.0)
    if response is None:
        raise RuntimeError("TTS API 连接中断。")
    if response.status_code != 200:
        raise RuntimeError(f"TTS API 返回 {response.status_code}: {response.text[:300]}")
    if not response.content:
        raise RuntimeError("TTS API 返回了空音频。")
    target.write_bytes(response.content)


def _synthesize_tts(text: str, *, message_id: int | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("合成文本不能为空。")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    target = AUDIO_CACHE_DIR / f"{digest}.wav"

    cached = target.exists()
    started = time.monotonic()
    if not cached:
        with _generate_lock:
            if not target.exists():
                if TTS_MODE == "mock":
                    _mock_synthesize(text, target)
                elif TTS_MODE == "api":
                    _api_synthesize(text, target)
                elif TTS_MODE == "cli":
                    _cli_synthesize(text, target)
                else:
                    raise RuntimeError(f"未知 TTS_MODE：{TTS_MODE}")
    elapsed_ms = int((time.monotonic() - started) * 1000)

    duration = _audio_duration(target)

    with Session(engine) as session:
        if message_id is not None:
            from ..models import Message

            message = session.get(Message, message_id)
            if message is not None:
                message.audio_path = f"/audio/{target.name}"
                session.add(message)
                session.commit()

    return {
        "audio_path": f"/audio/{target.name}",
        "audio_url": f"/audio/{target.name}",
        "voice_id": "sandrone_v1_e12_e8",
        "duration_sec": duration,
        "cached": cached,
        "latency_ms": elapsed_ms,
        "text": text[:200],
    }


def synthesize_tts(text: str, *, message_id: int | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = _synthesize_tts(text, message_id=message_id)
    except Exception as exc:
        status = "timeout" if isinstance(
            exc,
            (httpx.TimeoutException, subprocess.TimeoutExpired, TimeoutError),
        ) else "error"
        with Session(engine) as session:
            record_api_call(
                session,
                purpose="tts",
                provider="local-gpt-sovits",
                model="sandrone_v1_e12_e8",
                latency_ms=int((time.monotonic() - started) * 1000),
                status=status,
                error=str(exc),
            )
        raise
    with Session(engine) as session:
        record_api_call(
            session,
            purpose="tts",
            provider="local-gpt-sovits",
            model=str(result.get("voice_id") or "sandrone_v1_e12_e8"),
            latency_ms=int(result.get("latency_ms") or 0),
            status="ok",
        )
    return result


STREAM_SAMPLE_RATE = 32000


def _parse_wav_header(header: bytes) -> int:
    """从流式 WAV 头里取采样率；GPT-SoVITS v2 为 32kHz 单声道 16bit。"""
    if len(header) >= 28 and header[:4] == b"RIFF":
        return int.from_bytes(header[24:28], "little") or STREAM_SAMPLE_RATE
    return STREAM_SAMPLE_RATE


async def stream_tts_pcm(text: str) -> AsyncIterator[tuple[int, bytes]]:
    """流式合成一段话，逐块产出 (采样率, 16bit 单声道 PCM)。

    api 模式用 GPT-SoVITS 的 streaming_mode=3（本机实测首段可播放音频约 0.75–0.85 秒）。
    """
    text = (text or "").strip()
    if not text:
        return
    if TTS_MODE == "mock":
        rate, pcm = MOCK_RATE, _mock_pcm(text)
        step = rate * 2 // 10
        for index in range(0, len(pcm), step):
            await asyncio.sleep(0.02)
            yield rate, pcm[index : index + step]
        return
    if TTS_MODE != "api":
        raise RuntimeError("流式合成只支持 api 或 mock 模式。")

    if not await asyncio.to_thread(ensure_tts_api):
        raise RuntimeError("常驻 TTS API 启动失败，请检查 data/tts_api.log。")
    _, _, ref_audio, ref_text_path = _weight_paths()
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": str(ref_audio),
        "prompt_text": ref_text_path.read_text(encoding="utf-8").strip(),
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": 3,
        "speed_factor": 1.0,
    }
    if _english_probe is None and _LATIN.search(text):
        await asyncio.to_thread(_probe_english, payload)
    for attempt in range(2):
        payload["text_lang"] = _text_lang(text)
        header = b""
        rate = STREAM_SAMPLE_RATE
        remainder = b""
        yielded = False
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", f"{TTS_API_BASE_URL.rstrip('/')}/tts", json=payload
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread())[:300]
                        raise RuntimeError(f"TTS API 返回 {response.status_code}: {body!r}")
                    async for chunk in response.aiter_bytes():
                        if len(header) < 44:
                            need = 44 - len(header)
                            header += chunk[:need]
                            chunk = chunk[need:]
                            if len(header) == 44:
                                rate = _parse_wav_header(header)
                        data = remainder + chunk
                        cut = len(data) - len(data) % 2
                        remainder = data[cut:]
                        if cut:
                            yielded = True
                            yield rate, data[:cut]
            return
        except (httpx.HTTPError, RuntimeError):
            # 已经送出声音就不能重来（会重复念）；只在还没出声时对含英文的句子改用 all_zh 重试一次
            if attempt == 0 and not yielded and _mark_english_failure(text):
                continue
            raise


def save_wav(pcm: bytes, rate: int, text: str) -> str:
    """把一段通话里她说的话存进音频缓存，返回 /audio 路径（键与 synthesize_tts 一致，重听可直接复用）。"""
    digest = hashlib.sha1(text.strip().encode("utf-8")).hexdigest()
    target = AUDIO_CACHE_DIR / f"{digest}.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return f"/audio/{target.name}"
