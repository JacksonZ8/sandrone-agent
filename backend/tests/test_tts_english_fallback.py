from __future__ import annotations

import asyncio
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.services import tts_service


def _wav_header(rate: int = 32000) -> bytes:
    return b"RIFF" + b"\0" * 20 + struct.pack("<I", rate) + b"\0" * 16


class EnglishFallbackTests(unittest.TestCase):
    """GPT-SoVITS 缺英文词性数据时，含英文单词的句子会让合成服务中途断开（2026-10-09 通话实测）。"""

    def setUp(self) -> None:
        self.langs: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.langs.append(body["text_lang"])
            if body["text_lang"] == "zh" and any(ch.isascii() and ch.isalpha() for ch in body["text"]):
                raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")
            return httpx.Response(200, content=_wav_header() + b"\x01\x00" * 100)

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient

        self.patches = [
            patch.object(tts_service, "TTS_MODE", "api"),
            patch.object(tts_service, "ensure_tts_api", lambda *a, **k: True),
            patch.object(tts_service, "_weight_paths", self._weights),
            patch.object(tts_service, "_english_g2p_broken", False),
            patch.object(tts_service, "_english_probe", None),
            patch.object(tts_service.httpx, "post", self._probe),
            patch.object(tts_service.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()

    def _probe(self, url, json=None, timeout=None):  # type: ignore[no-untyped-def]
        # 模拟缺 NLTK 数据：英文探测失败
        self.langs.append("probe")
        return httpx.Response(500, content=b"LookupError")

    @staticmethod
    def _weights():
        ref_text = Path(__file__).parent / "test_tts_english_fallback.py"
        return None, None, Path("ref.wav"), ref_text

    def _collect(self, text: str) -> bytes:
        async def run() -> bytes:
            return b"".join([pcm async for _, pcm in tts_service.stream_tts_pcm(text)])

        return asyncio.run(run())

    def test_english_probe_once_then_all_zh(self) -> None:
        self.assertEqual(len(self._collect("干掉每层Boss，撑到塔顶。")), 200)
        self.assertEqual(self.langs, ["probe", "all_zh"])
        self.assertTrue(tts_service._english_g2p_broken)
        self._collect("下一层还有Boss。")
        self.assertEqual(self.langs, ["probe", "all_zh", "all_zh"])

    def test_unexpected_failure_before_audio_retries_with_all_zh(self) -> None:
        with patch.object(tts_service, "_english_probe", True):
            self.assertEqual(len(self._collect("干掉每层Boss。")), 200)
        self.assertEqual(self.langs, ["zh", "all_zh"])

    def test_pure_chinese_keeps_zh_without_probe(self) -> None:
        self._collect("撑到塔顶。")
        self.assertEqual(self.langs, ["zh"])
        self.assertFalse(tts_service._english_g2p_broken)


if __name__ == "__main__":
    unittest.main()
