"""测试用的流式模型替身：替换 chat_completion_stream，记录每次调用的消息。"""

from __future__ import annotations

from typing import Any


def reply(text: str, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "text": text, "tool_calls": tool_calls or [], "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "latency_ms": 1, "first_token_ms": 1,
    }


class FakeStream:
    """每次调用依次取一个结果（dict 回复或要抛出的异常），最后一个会被重复使用。"""

    def __init__(self, *results: dict[str, Any] | BaseException) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, session, config, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]

        async def generate():
            if isinstance(result, BaseException):
                raise result
            if result["text"]:
                yield {"type": "delta", "text": result["text"]}
            yield {"type": "done", **result}

        return generate()

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.calls[-1]["messages"]

    @property
    def system_text(self) -> str:
        """最后一次调用里全部 system 消息（固定部分 + 这一轮的上下文）。"""
        return "\n\n".join(m["content"] for m in self.messages if m["role"] == "system")
