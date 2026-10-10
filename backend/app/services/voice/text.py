from __future__ import annotations

import re

_HARD_END = "。！？!?…\n"
_SOFT_END = "，、；;：:,"
_ACTION = re.compile(r"[（(][^（()）]*[）)]")
_MARKDOWN = re.compile(r"[*_`#>~|]+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def spoken_text(text: str) -> str:
    """念出来的部分：去掉括号里的动作描写、Markdown 记号和链接地址。"""
    text = _LINK.sub(r"\1", text)
    text = _ACTION.sub("", text)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_words(text: str) -> bool:
    return bool(re.search(r"[一-鿿A-Za-z0-9]", text))


class SentenceSplitter:
    """把 LLM 的流式输出切成适合逐句合成的片段。

    第一段越早送出，她越早开口：第一段遇到逗号且已有 6 个字就切；之后按句号切，
    过长（超过 40 字）时退到逗号切，避免一段合成太久。括号里的动作描写不会被切开。
    """

    def __init__(self, *, first_min_chars: int = 6, max_chars: int = 40) -> None:
        self.buffer = ""
        self.first_min_chars = first_min_chars
        self.max_chars = max_chars
        self.emitted = 0

    def _in_action(self) -> bool:
        return self.buffer.count("（") + self.buffer.count("(") > self.buffer.count("）") + self.buffer.count(")")

    def feed(self, delta: str) -> list[str]:
        pieces: list[str] = []
        for char in delta:
            self.buffer += char
            if self._in_action():
                continue
            length = len(spoken_text(self.buffer))
            hard = char in _HARD_END
            soft = char in _SOFT_END and (
                (self.emitted == 0 and length >= self.first_min_chars) or length >= self.max_chars
            )
            if hard or soft:
                piece = self._take()
                if piece:
                    pieces.append(piece)
        return pieces

    def flush(self) -> list[str]:
        piece = self._take()
        return [piece] if piece else []

    def _take(self) -> str:
        raw, self.buffer = self.buffer, ""
        piece = spoken_text(raw)
        if not _has_words(piece):
            return ""
        self.emitted += 1
        return piece
