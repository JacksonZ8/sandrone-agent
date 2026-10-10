from __future__ import annotations

import re

# 通话里她的神态交给 Live2D：回复开头带一个情绪标签，服务器去掉后作为表情/动作事件发给前端。
EMOTIONS = ("平静", "开心", "得意", "害羞", "生气", "惊讶", "难过", "担心")

EMOTION_BLOCK = (
    "## 情绪标签（不会被念出来）\n"
    f"- 回复的最开头先写一个情绪标签，只能从这些里选：{'、'.join(f'【{name}】' for name in EMOTIONS)}，"
    "例如“【得意】记性好是我的本事。”。整轮只写这一个标签，后面不要再写。"
)

_TAG = re.compile(r"^\s*[【\[]([^】\]]{1,4})[】\]]\s*")
_MAX_PROBE = 8


def strip_emotion_tag(text: str) -> tuple[str | None, str]:
    """从整段回复里取出开头的情绪标签，返回 (情绪, 去掉标签的文本)。未知标签也会去掉但不返回情绪。"""
    match = _TAG.match(text or "")
    if not match:
        return None, text
    name = match.group(1).strip()
    return (name if name in EMOTIONS else None), text[match.end() :]


class EmotionTagParser:
    """流式版：先攒住开头几个字判断有没有标签，确定后把正文原样放行。"""

    def __init__(self) -> None:
        self.buffer = ""
        self.decided = False

    def feed(self, delta: str) -> tuple[str | None, str]:
        if self.decided:
            return None, delta
        self.buffer += delta
        probe = self.buffer.lstrip()
        if not probe:
            return None, ""
        if probe[0] not in "【[":
            return self._release(None, self.buffer)
        if re.search(r"[】\]]", probe):
            emotion, rest = strip_emotion_tag(self.buffer)
            return self._release(emotion, rest)
        if len(probe) > _MAX_PROBE:
            return self._release(None, self.buffer)
        return None, ""

    def flush(self) -> str:
        if self.decided:
            return ""
        return self._release(None, self.buffer)[1]

    def _release(self, emotion: str | None, text: str) -> tuple[str | None, str]:
        self.decided = True
        self.buffer = ""
        return emotion, text
