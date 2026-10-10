from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session

from ...models import ToolConfig, utcnow
from ..provider_service import get_config
from .audit import _record_tool_call
from .config import get_tool_config
from .windows import MAC_DESKTOP


SCREEN_MAX_SIDE = 1280


BROWSER_PROCESS_NAMES = {
    "Google Chrome",
    "Safari",
    "Microsoft Edge",
    "Arc",
    "Firefox",
    "Brave Browser",
    "Vivaldi",
    "Chromium",
}


def screen_tool_public(config: ToolConfig) -> dict[str, Any]:
    key = config.vision_api_key or ""
    return {
        "screen_enabled": config.screen_enabled,
        "vision_base_url": config.vision_base_url,
        "vision_model": config.vision_model,
        "has_vision_api_key": bool(key),
        "vision_api_key_masked": (key[:3] + "••••" + key[-4:]) if len(key) > 8 else ("••••" if key else ""),
    }


def _frontmost_app_name() -> str:
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _set_app_visible(name: str, visible: bool) -> None:
    flag = "true" if visible else "false"
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "System Events" to set visible of process "{name}" to {flag}',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass


def _capture_screen() -> Path:
    """截取当前屏幕。默认不隐藏任何窗口；如需隐藏浏览器可设置 SANDRONE_SCREEN_HIDE_BROWSER=1。"""
    raw = Path(tempfile.gettempdir()) / f"sandrone_screen_{time.time_ns()}.png"
    resized = raw.with_suffix(".jpg")
    hide_browser = os.getenv("SANDRONE_SCREEN_HIDE_BROWSER", "0") == "1"
    front_app = _frontmost_app_name() if hide_browser else ""
    hidden_app = front_app if front_app in BROWSER_PROCESS_NAMES else ""
    if hidden_app:
        _set_app_visible(hidden_app, False)
        time.sleep(0.5)

    try:
        result = subprocess.run(
            ["screencapture", "-x", str(raw)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        if hidden_app:
            _set_app_visible(hidden_app, True)
            time.sleep(0.25)

    if result.returncode != 0 or not raw.exists():
        tail = (result.stderr or "").strip()
        raise RuntimeError(f"截屏失败：{tail or '未生成截图'}。请确认已授予终端「屏幕录制」权限。")

    try:
        subprocess.run(
            ["sips", "-Z", str(SCREEN_MAX_SIDE), "-s", "format", "jpeg", "-s", "formatOptions", "80", str(raw), "--out", str(resized)],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        raw.unlink(missing_ok=True)
        return resized
    except Exception:
        return raw


DEFAULT_VISION_MODEL = "deepseek-v4-flash-vision-exp"


def vision_settings(session: Session, config: ToolConfig) -> tuple[str, str, str]:
    """视觉模型的 (base_url, model, api_key)；留空时复用对话模型的地址与 Key。"""
    llm_config = get_config(session)
    base_url = (config.vision_base_url or llm_config.base_url or "https://api.deepseek.com").strip()
    model = (config.vision_model or DEFAULT_VISION_MODEL).strip()
    api_key = (config.vision_api_key or llm_config.api_key).strip()
    return base_url, model, api_key


def call_vision(
    session: Session,
    config: ToolConfig,
    prompt: str,
    image_bytes: bytes,
    *,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """把一张 JPEG 和提示词发给视觉模型，返回 {ok, analysis, message}。"""
    base_url, model, api_key = vision_settings(session, config)
    if not api_key:
        return {"ok": False, "analysis": "", "message": "没有可用的视觉模型 API Key。可以在设置里填写，或留空复用聊天模型的 DeepSeek Key。"}
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = httpx.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=90.0)
    if response.status_code >= 400:
        return {"ok": False, "analysis": "", "message": f"视觉模型返回 {response.status_code}：{response.text[:300]}"}
    data = response.json()
    message = (data.get("choices") or [{}])[0].get("message", {}) or {}
    analysis = str(message.get("content") or message.get("reasoning_content") or "").strip()
    if not analysis:
        return {"ok": False, "analysis": "", "message": "视觉模型返回为空。"}
    return {"ok": True, "analysis": analysis, "message": "屏幕分析完成。"}


def _capture_for_help(session: Session) -> tuple[bytes | None, str]:
    """求助时的单次截图：设了“陪你用电脑”应用名单时只截名单内最前面的窗口，否则沿用整屏截图。"""
    from ..screen_companion import allowed_apps

    apps = allowed_apps(session)
    if not apps:
        image_path = _capture_screen()
        try:
            return image_path.read_bytes(), ""
        finally:
            image_path.unlink(missing_ok=True)
    window = MAC_DESKTOP.snapshot().front_window(apps)
    if window is None:
        return None, "屏幕上没有允许她看的应用窗口。可以在设置的“陪你用电脑”里把应用加入名单。"
    return MAC_DESKTOP.capture(window.id).jpeg, ""


def _analyze_screen(
    session: Session,
    question: str,
    config: ToolConfig,
) -> dict[str, Any]:
    if not config.screen_enabled:
        return {"ok": False, "analysis": "", "message": "屏幕观察未开启。请先在设置里开启。"}
    if not vision_settings(session, config)[2]:
        return {"ok": False, "analysis": "", "message": "没有可用的视觉模型 API Key。可以在设置里填写，或留空复用聊天模型的 DeepSeek Key。"}

    image_bytes, refusal = _capture_for_help(session)
    if image_bytes is None:
        return {"ok": False, "analysis": "", "message": refusal}
    prompt = (
        "你是桑多涅，正在看用户的电脑屏幕，目的是帮助用户处理当前工作。"
        "请用简体中文简洁描述：屏幕上当前是什么应用/页面、用户在做什么、有哪些值得注意的问题。"
        f"用户的问题：{question}"
    )
    return call_vision(session, config, prompt, image_bytes)


def analyze_screen(session: Session, question: str = "看看我现在的屏幕，告诉我当前在做什么。") -> dict[str, Any]:
    started = time.monotonic()
    config = get_tool_config(session)
    model = (config.vision_model or DEFAULT_VISION_MODEL).strip()
    try:
        result = _analyze_screen(session, question, config)
    except httpx.TimeoutException as exc:
        _record_tool_call(
            session,
            "screen_analysis",
            "vision",
            model,
            "timeout",
            str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        _record_tool_call(
            session,
            "screen_analysis",
            "vision",
            model,
            "error",
            str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    status = "ok" if result["ok"] else ("empty" if "返回为空" in result["message"] else "error")
    _record_tool_call(
        session,
        "screen_analysis",
        "vision",
        model,
        status,
        "" if result["ok"] else result["message"],
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def update_screen_config(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    config = get_tool_config(session)
    if data.get("screen_enabled") is not None:
        config.screen_enabled = bool(data["screen_enabled"])
    for field in ("vision_base_url", "vision_model"):
        if data.get(field) is not None:
            setattr(config, field, str(data[field]).strip())
    if data.get("vision_api_key") is not None:
        config.vision_api_key = str(data["vision_api_key"]).strip()
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return screen_tool_public(config)
