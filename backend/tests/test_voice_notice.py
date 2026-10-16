from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest.mock import patch

from app.services.voice import call as call_module
from app.services.voice.call import ACTIVE_CALLS, VoiceCall, announce_to_calls


class FakeSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.audio = 0

    async def send_text(self, text: str) -> None:
        self.events.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio += len(data)


async def fake_tts(text: str):
    yield 32000, b"\x00\x00" * 320


def types(socket: FakeSocket) -> list[str]:
    return [event["type"] for event in socket.events]


class VoiceNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patches = [
            patch.object(call_module, "stream_tts_pcm", side_effect=fake_tts),
            patch.object(call_module, "NOTICE_POLL_S", 0.01),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        ACTIVE_CALLS.clear()

    def _call(self) -> tuple[VoiceCall, FakeSocket]:
        socket = FakeSocket()
        voice_call = VoiceCall(socket, asr=object(), turn_detector=None)  # type: ignore[arg-type]
        ACTIVE_CALLS.add(voice_call)
        return voice_call, socket

    def test_idle_call_speaks_the_notice_and_syncs_the_timeline(self) -> None:
        async def scenario() -> None:
            voice_call, socket = self._call()
            message = {"id": 7, "role": "assistant", "content": "「查依赖」那边做完了"}
            self.assertEqual(await announce_to_calls("对了，「查依赖」那边做完了，要听结果吗？", message), 1)
            await voice_call.notice_waiter
            await voice_call.turn_task
            self.assertEqual(
                types(socket),
                ["state", "sentence", "audio_begin", "state", "audio_end", "turn_done"],
            )
            self.assertEqual(socket.events[1]["text"], "对了，「查依赖」那边做完了，要听结果吗？")
            self.assertEqual(socket.events[-1]["assistant_message"], message)
            self.assertIsNone(socket.events[-1]["user_message"])
            self.assertGreater(socket.audio, 0)

        asyncio.run(scenario())

    def test_waits_while_she_is_speaking_and_keeps_only_the_newest(self) -> None:
        async def scenario() -> None:
            voice_call, socket = self._call()
            voice_call.audible_until = time.monotonic() + 0.1  # 她还在说上一句
            voice_call.queue_notice("旧的通知")
            voice_call.queue_notice("新的通知")
            await asyncio.sleep(0.03)
            self.assertEqual(socket.events, [])  # 说话时不插嘴
            await voice_call.notice_waiter
            await voice_call.turn_task
            spoken = [event["text"] for event in socket.events if event["type"] == "sentence"]
            self.assertEqual(spoken, ["新的通知"])

        asyncio.run(scenario())

    def test_closed_calls_are_skipped(self) -> None:
        async def scenario() -> None:
            voice_call, socket = self._call()
            voice_call.closed = True
            self.assertEqual(await announce_to_calls("没人听"), 0)
            self.assertEqual(socket.events, [])

        asyncio.run(scenario())

    def test_notice_can_be_interrupted(self) -> None:
        async def slow_tts(text: str):
            yield 32000, b"\x00\x00" * 320
            await asyncio.sleep(5)
            yield 32000, b"\x00\x00" * 320

        async def scenario() -> None:
            voice_call, socket = self._call()
            with patch.object(call_module, "stream_tts_pcm", side_effect=slow_tts):
                voice_call.queue_notice("一句很长的通知")
                await voice_call.notice_waiter
                await asyncio.sleep(0.02)
                await voice_call.interrupt("speech")
            self.assertIn("interrupt", types(socket))
            self.assertNotIn("turn_done", types(socket))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
