from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from sqlmodel import Session

from ..models import LLMConfig
from .usage.audit import record_api_call as _record_usage_call
from .usage.audit import sanitize_error


class LLMError(RuntimeError):
    """统一的 LLM 调用错误，前端会直接展示 message。"""


def _completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("Base URL 不能为空。")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _response_text(payload: dict[str, Any]) -> str:
    try:
        message = payload["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return ""
    content = str(message.get("content") or "").strip()
    if content:
        return content
    reasoning = str(message.get("reasoning_content") or "").strip()
    # 只在 reasoning 里看起来是结构化 JSON 时使用，避免把长段思考当最终回复。
    if reasoning and ('"memories"' in reasoning or reasoning.lstrip().startswith(("{", "["))):
        return reasoning
    return ""


MAX_RETRY_TOKENS = 4096
THINKING_LEVELS = ("off", "low", "high")
# auto：平时关掉推理（最快）；这句话像是要查资料、读文件、看屏幕时开到 low，免得该用工具时不用
_NEEDS_TOOLS = re.compile(
    r"查|搜|找一下|找找|最新|新闻|天气|价格|多少钱|攻略|怎么打|资料|网页|链接|https?://|读一下|读文件|文件|屏幕|看看我|看一下我|search|look up|google",
    re.I,
)


def resolve_thinking(setting: str, user_text: str = "") -> str:
    """把设置里的 auto 按这句话换成 off 或 low；其余档位原样返回。"""
    if setting == "auto":
        return "low" if _NEEDS_TOOLS.search(user_text or "") else "off"
    return setting
THINKING_MIN_TOKENS = 4096  # 开着推理时给推理留余量，避免用完额度正文为空（按实际用量计费）


def thinking_fields(config: LLMConfig, thinking: str | None) -> dict[str, Any]:
    """DeepSeek 的推理开关：off 关闭；low / high 开启并设定强度。其他服务商不加这些字段。

    只给对话、通话、看屏点评这类要快的调用传；后台整理类调用不传，保持模型默认。
    """
    if thinking not in THINKING_LEVELS or "deepseek" not in f"{config.provider} {config.base_url}".lower():
        return {}
    if thinking == "off":
        return {"thinking": {"type": "disabled"}}
    return {"thinking": {"type": "enabled"}, "reasoning_effort": thinking}


def _apply_thinking(payload: dict[str, Any], config: LLMConfig, thinking: str | None) -> None:
    fields = thinking_fields(config, thinking)
    payload.update(fields)
    if fields.get("thinking", {}).get("type") == "enabled":
        payload["max_tokens"] = max(int(payload.get("max_tokens") or 0), THINKING_MIN_TOKENS)


def _drop_thinking(payload: dict[str, Any], body: str) -> bool:
    """兼容接口不认推理参数时去掉重试。"""
    if "thinking" in payload and ("thinking" in body or "reasoning_effort" in body):
        payload.pop("thinking", None)
        payload.pop("reasoning_effort", None)
        return True
    return False


def _truncated_before_answer(payload: dict[str, Any]) -> bool:
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError):
        return False
    message = choice.get("message") or {}
    return (
        choice.get("finish_reason") == "length"
        and not str(message.get("content") or "").strip()
        and not message.get("tool_calls")
    )


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        raw_calls = payload["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return []
    result: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        arguments_raw = fn.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
        except json.JSONDecodeError:
            arguments = {}
        if name:
            result.append(
                {
                    "id": str(call.get("id") or ""),
                    "name": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }
            )
    return result


def _usage_fields(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _mock_reply(last_user_text: str) -> str:
    text = (last_user_text or "").strip()
    if not text:
        return "……没有说话。"
    if any(word in text for word in ("测试", "在吗", "连接")):
        return "链路正常。有什么要说的，现在可以说了。"
    if any(word in text for word in ("你好", "嗨", "早上好", "晚上好")):
        return "我在这里。"
    if any(word in text for word in ("我叫", "我是", "我的名字")):
        return "嗯，我记下了。之后我会用你告诉我的方式称呼你。"
    if any(word in text for word in ("喜欢", "讨厌", "偏好", "不喜欢")):
        return "这点我记下了。之后会按你的偏好来。"
    if "再见" in text:
        return "好。去忙吧，我在这里。"
    return "我听见了。这件事我先记下，你想继续的时候再接着谈。"


def record_api_call(
    session: Session,
    *,
    purpose: str,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    status: str = "ok",
    error: str = "",
) -> None:
    _record_usage_call(
        session,
        purpose=purpose,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        status=status,
        error=error,
    )


async def chat_completion(
    session: Session,
    config: LLMConfig,
    messages: list[dict[str, Any]],
    *,
    purpose: str = "chat",
    max_tokens: int | None = None,
    temperature: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    thinking: str | None = None,
) -> dict[str, Any]:
    """调用当前配置的 LLM。返回 {text, tool_calls, usage...}。thinking 见 thinking_fields。"""
    started = time.perf_counter()
    provider = config.provider
    model = config.model

    def audit(
        status: str,
        *,
        error: str = "",
        usage: dict[str, int] | None = None,
    ) -> int:
        latency = int((time.perf_counter() - started) * 1000)
        usage = usage or {}
        record_api_call(
            session,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=latency,
            status=status,
            error=error,
        )
        return latency

    if provider == "mock":
        await _sleep_short()
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        latency = audit("ok")
        return {
            "text": _mock_reply(last_user),
            "tool_calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_ms": latency,
        }

    if not config.api_key:
        message = "尚未配置 API Key。请先到「设置」中填写 DeepSeek 或兼容接口的 API Key。"
        audit("error", error=message)
        raise LLMError(message)

    try:
        url = _completions_url(config.base_url)
    except LLMError as exc:
        audit("error", error=str(exc))
        raise
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature if temperature is None else temperature,
        "max_tokens": config.max_tokens if max_tokens is None else max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _apply_thinking(payload, config, thinking)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            audit("timeout", error=str(exc))
            raise LLMError(f"请求超时（{config.timeout_seconds:.0f}s）。可以检查网络，或在设置中调大超时时间。") from exc
        except httpx.HTTPError as exc:
            safe_error = sanitize_error(str(exc))
            audit("error", error=safe_error)
            raise LLMError(f"无法连接到 LLM 服务：{safe_error}") from exc

        if response.status_code >= 400:
            body = sanitize_error(response.text[:500])
            if attempt == 0 and response.status_code == 400 and _drop_thinking(payload, body):
                continue
            audit("error", error=body)
            raise LLMError(f"LLM 请求失败（HTTP {response.status_code}）：{body}")

        try:
            data = response.json()
        except ValueError as exc:
            audit("error", error="响应不是合法 JSON")
            raise LLMError("LLM 返回了无法解析的内容。") from exc

        # 推理模型可能把额度全用在推理上、正文为空（finish_reason=length）：加大额度重试一次
        if attempt == 0 and _truncated_before_answer(data) and payload["max_tokens"] < MAX_RETRY_TOKENS:
            audit("error", error="推理用完额度、正文为空，已加大额度重试", usage=_usage_fields(data))
            payload["max_tokens"] = min(MAX_RETRY_TOKENS, max(2 * payload["max_tokens"], 2048))
            started = time.perf_counter()
            continue
        break

    text = _response_text(data)
    tool_calls = _tool_calls(data)
    usage = _usage_fields(data)
    latency = audit("ok", usage=usage)
    return {
        "text": text.strip(),
        "tool_calls": tool_calls,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "latency_ms": latency,
    }


async def _sleep_short() -> None:
    await asyncio.sleep(0.15)


def _merge_tool_call_delta(calls: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
    index = int(delta.get("index") or 0)
    call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if delta.get("id"):
        call["id"] = str(delta["id"])
    fn = delta.get("function") or {}
    if fn.get("name"):
        call["name"] += str(fn["name"])
    if fn.get("arguments"):
        call["arguments"] += str(fn["arguments"])


def _finished_tool_calls(calls: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in sorted(calls):
        call = calls[index]
        if not call["name"]:
            continue
        try:
            arguments = json.loads(call["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}
        result.append(
            {
                "id": call["id"],
                "name": call["name"],
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    return result


async def chat_completion_stream(
    session: Session,
    config: LLMConfig,
    messages: list[dict[str, Any]],
    *,
    purpose: str = "chat",
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    thinking: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式调用 LLM：先逐段产出 {"type": "delta", "text"}，最后产出一个 {"type": "done", ...}。

    done 里带完整 text、tool_calls、用量、总耗时 latency_ms 与首字耗时 first_token_ms。
    """
    started = time.perf_counter()
    provider = config.provider
    model = config.model

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    def audit(status: str, *, error: str = "", usage: dict[str, int] | None = None) -> int:
        usage = usage or {}
        latency = elapsed_ms()
        record_api_call(
            session,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=latency,
            status=status,
            error=error,
        )
        return latency

    if provider == "mock":
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = _mock_reply(last_user)
        # 通话要求回复开头带情绪标签时，模拟回复也照做，便于在没有 API Key 时检查表情链路
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "情绪标签" in str(system):
            reply = "【得意】" + reply
        first_token_ms = 0
        for index in range(0, len(reply), 4):
            await asyncio.sleep(0.03)
            if not first_token_ms:
                first_token_ms = elapsed_ms()
            yield {"type": "delta", "text": reply[index : index + 4]}
        latency = audit("ok")
        yield {
            "type": "done", "text": reply, "tool_calls": [], "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "latency_ms": latency,
            "first_token_ms": first_token_ms,
        }
        return

    if not config.api_key:
        message = "尚未配置 API Key。请先到「设置」中填写 DeepSeek 或兼容接口的 API Key。"
        audit("error", error=message)
        raise LLMError(message)
    try:
        url = _completions_url(config.base_url)
    except LLMError as exc:
        audit("error", error=str(exc))
        raise

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens if max_tokens is None else max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _apply_thinking(payload, config, thinking)
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}

    parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}
    first_token_ms = 0
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            for attempt in range(3):
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = sanitize_error((await response.aread()).decode("utf-8", "replace")[:500])
                        # 个别兼容接口不认 stream_options 或推理参数：去掉后重试，只是拿不到用量。
                        if attempt < 2 and response.status_code == 400 and "stream_options" in body and "stream_options" in payload:
                            payload.pop("stream_options", None)
                            continue
                        if attempt < 2 and response.status_code == 400 and _drop_thinking(payload, body):
                            continue
                        audit("error", error=body)
                        raise LLMError(f"LLM 请求失败（HTTP {response.status_code}）：{body}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = _usage_fields(chunk)
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            for call_delta in delta.get("tool_calls") or []:
                                _merge_tool_call_delta(calls, call_delta)
                            text = delta.get("content")
                            if text:
                                if not first_token_ms:
                                    first_token_ms = elapsed_ms()
                                parts.append(text)
                                yield {"type": "delta", "text": text}
                    break
    except httpx.TimeoutException as exc:
        audit("timeout", error=str(exc))
        raise LLMError(f"请求超时（{config.timeout_seconds:.0f}s）。") from exc
    except httpx.HTTPError as exc:
        safe_error = sanitize_error(str(exc))
        audit("error", error=safe_error)
        raise LLMError(f"无法连接到 LLM 服务：{safe_error}") from exc

    latency = audit("ok", usage=usage)
    yield {
        "type": "done",
        "text": "".join(parts).strip(),
        "tool_calls": _finished_tool_calls(calls),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "latency_ms": latency,
        "first_token_ms": first_token_ms,
    }
