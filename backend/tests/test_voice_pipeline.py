from __future__ import annotations

import array
import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import LLMConfig, Message
from app.services.voice import call as call_module
from app.services.voice import turn as turn_module
from app.services.voice.asr import Transcriber, normalize_text
from app.services.voice.text import SentenceSplitter, spoken_text

FRAME = 320  # 20ms @ 16kHz


class FakeVad:
    """按帧脚本返回是否在说话：script 是每帧的 True/False。"""

    def __init__(self, script: list[bool]) -> None:
        self.script = script
        self.index = -1

    def accept_waveform(self, samples) -> None:
        self.index += 1

    def is_speech_detected(self) -> bool:
        return self.script[self.index] if self.index < len(self.script) else False

    def empty(self) -> bool:
        return True

    def pop(self) -> None:
        pass


class FakeStream:
    def __init__(self) -> None:
        self.frames = 0
        self.finished = False

    def accept_waveform(self, rate, samples) -> None:
        self.frames += 1

    def input_finished(self) -> None:
        self.finished = True


class FakeEngine:
    """每收到 N 帧就多认出一个字，用来驱动 Transcriber 与 VoiceCall。"""

    error = ""

    def __init__(self, script: list[bool], text: str, frames_per_char: int = 5) -> None:
        self.script = script
        self.text = text
        self.frames_per_char = frames_per_char

    def load(self) -> bool:
        return True

    def new_vad(self) -> FakeVad:
        return FakeVad(self.script)

    def new_stream(self) -> FakeStream:
        return FakeStream()

    def decode(self, stream: FakeStream) -> str:
        count = len(self.text) if stream.finished else stream.frames // self.frames_per_char
        return self.text[:count]


def speech_script(silence_before: int, speech: int, silence_after: int) -> list[bool]:
    return [False] * silence_before + [True] * speech + [False] * silence_after


class TurnFeatureTests(unittest.TestCase):
    def test_features_pad_short_audio_at_the_start(self) -> None:
        from app.services.voice.turn_detect import whisper_features

        import math

        tone = [0.3 * math.sin(2 * math.pi * 440 * index / 16000) for index in range(16000)]
        features = whisper_features(tone)
        self.assertEqual(features.shape, (1, 80, 800))
        # 不足 8 秒时在前面补零：有声音的只在最后 1 秒（约 100 帧）
        energy = features[0].mean(axis=0)
        self.assertGreater(energy[-50:].mean(), energy[:600].mean())
        self.assertLessEqual(float(features.max() - features.min()), 2.0 + 1e-6)


class TextTests(unittest.TestCase):
    def test_splitter_cuts_first_clause_early_and_keeps_actions_whole(self) -> None:
        splitter = SentenceSplitter()
        pieces: list[str] = []
        for delta in ["记性好", "是我的本事，不是因为你。（把草稿推回", "给你。）图三没标。", "去吃饭"]:
            pieces += splitter.feed(delta)
        pieces += splitter.flush()
        self.assertEqual(pieces, ["记性好是我的本事，", "不是因为你。", "图三没标。", "去吃饭"])

    def test_spoken_text_drops_actions_and_markdown(self) -> None:
        self.assertEqual(spoken_text("**好**，看[这里](http://x)。（笑）"), "好，看这里。")

    def test_normalize_keeps_english_spacing(self) -> None:
        self.assertEqual(normalize_text("桑多涅， 你好 today is 礼拜二"), "桑多涅，你好 today is 礼拜二")


class FakeTurnDetector:
    ready = True

    def __init__(self, probability: float) -> None:
        self.value = probability
        self.calls = 0

    def probability(self, samples) -> float:
        self.calls += 1
        return self.value


class TranscriberTests(unittest.TestCase):
    def feed_all(self, transcriber: Transcriber, frames: int) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        for _ in range(frames):
            events += transcriber.feed([0.0] * FRAME)
        return events

    def test_endpoint_after_trailing_silence_with_preroll(self) -> None:
        script = speech_script(10, 30, 40)
        transcriber = Transcriber(FakeEngine(script, "今天好累"), end_silence_ms=700)
        events = self.feed_all(transcriber, len(script))
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "speech_start")
        self.assertIn("partial", kinds)
        self.assertEqual(events[-1], ("final", "今天好累"))
        # 700ms 静音 = 35 帧：在第 10 + 30 + 35 帧左右给出 final，而不是更早。
        self.assertEqual(kinds.count("final"), 1)

    def test_sentence_final_punctuation_ends_sooner(self) -> None:
        script = speech_script(0, 20, 60)
        slow = Transcriber(FakeEngine(script, "在吗"), end_silence_ms=900, punct_silence_ms=300)
        fast = Transcriber(FakeEngine(script, "在吗？"), end_silence_ms=900, punct_silence_ms=300)

        def final_frame(transcriber: Transcriber) -> int:
            for index in range(len(script)):
                if any(kind == "final" for kind, _ in transcriber.feed([0.0] * FRAME)):
                    return index
            return -1

        self.assertLess(final_frame(fast), final_frame(slow))

    def _final_silence_ms(self, transcriber: Transcriber, script: list[bool]) -> int:
        for _ in script:
            if any(kind == "final" for kind, _ in transcriber.feed([0.0] * FRAME)):
                return transcriber.last_end_silence_ms
        return -1

    def test_smart_turn_ends_early_when_complete(self) -> None:
        script = speech_script(0, 20, 100)
        detector = FakeTurnDetector(0.9)
        transcriber = Transcriber(FakeEngine(script, "今天好累"), turn_detector=detector)
        self.assertEqual(self._final_silence_ms(transcriber, script), 200)
        self.assertEqual(detector.calls, 1)
        self.assertEqual(transcriber.last_turn_probability, 0.9)

    def test_smart_turn_waits_when_incomplete(self) -> None:
        script = speech_script(0, 20, 100)
        detector = FakeTurnDetector(0.1)
        transcriber = Transcriber(FakeEngine(script, "我想说的是"), turn_detector=detector)
        self.assertEqual(self._final_silence_ms(transcriber, script), 1500)
        self.assertEqual(detector.calls, 3)

    def test_sentence_punctuation_lowers_the_bar(self) -> None:
        script = speech_script(0, 20, 100)
        transcriber = Transcriber(FakeEngine(script, "在吗？"), turn_detector=FakeTurnDetector(0.35))
        self.assertEqual(self._final_silence_ms(transcriber, script), 200)

    def test_silence_only_produces_nothing(self) -> None:
        transcriber = Transcriber(FakeEngine([False] * 50, "噪音"))
        self.assertEqual(self.feed_all(transcriber, 50), [])


def _pcm_frame() -> bytes:
    return array.array("h", [0] * FRAME).tobytes()


class VoiceCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(LLMConfig(provider="mock"))
            session.commit()
        self.patches = [
            patch.object(call_module, "engine", self.engine),
            patch.object(call_module, "run_extraction_background", lambda *args: None),
            patch.object(call_module, "save_wav", lambda pcm, rate, text: "/audio/test.wav"),
            patch.object(call_module, "_prepare_fillers", self._no_fillers),
            patch.dict(os.environ, {"SANDRONE_TTS_MODE": "mock"}),
        ]
        for item in self.patches:
            item.start()

    async def _no_fillers(self) -> None:
        return None

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def _client(self, fake: FakeEngine) -> TestClient:
        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await call_module.VoiceCall(websocket, asr=fake, turn_detector=None).run()

        return TestClient(app)

    def _receive_until(self, ws, wanted: str, limit: int = 400) -> list[dict]:
        seen: list[dict] = []
        for _ in range(limit):
            message = ws.receive()
            if message.get("text"):
                payload = json.loads(message["text"])
                seen.append(payload)
                if payload["type"] == wanted:
                    return seen
        self.fail(f"没有等到 {wanted}：{[item['type'] for item in seen]}")

    def test_full_turn_streams_audio_and_saves_timeline(self) -> None:
        script = speech_script(5, 25, 45)
        fake = FakeEngine(script, "你好")
        with self._client(fake) as client, client.websocket_connect("/ws") as ws:
            self._receive_until(ws, "ready")
            ws.send_text(json.dumps({"type": "start", "reply_length": "short"}))
            for _ in script:
                ws.send_bytes(_pcm_frame())
            events = self._receive_until(ws, "turn_done")
        kinds = [event["type"] for event in events]
        self.assertIn("final", kinds)
        self.assertIn("audio_begin", kinds)
        self.assertIn("sentence", kinds)
        done = events[-1]
        self.assertFalse(done["interrupted"])
        self.assertEqual(done["user_message"]["content"], "你好")
        self.assertEqual(done["user_message"]["channel"], "voice")
        self.assertEqual(done["assistant_message"]["content"], "我在这里。")
        self.assertGreaterEqual(done["timings"]["first_audio_ms"], 0)
        with Session(self.engine) as session:
            rows = session.exec(select(Message).order_by(Message.id)).all()
        self.assertEqual([row.role for row in rows], ["user", "assistant"])
        self.assertEqual(rows[1].audio_path, "/audio/test.wav")

    def test_user_speaking_again_interrupts_and_merges_unspoken_turn(self) -> None:
        # 第一句说完后马上又开口：她还没出声就被打断，两句并成一句。
        script = speech_script(2, 15, 40) + [True] * 25 + [False] * 45
        fake = FakeEngine(script, "在吗", frames_per_char=4)
        slow_reply = turn_module.stream_voice_reply

        async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
            import asyncio

            await asyncio.sleep(5)
            async for event in slow_reply(*args, **kwargs):
                yield event

        with patch.object(call_module, "stream_voice_reply", slow):
            with self._client(fake) as client, client.websocket_connect("/ws") as ws:
                self._receive_until(ws, "ready")
                for _ in script[:57]:
                    ws.send_bytes(_pcm_frame())
                self._receive_until(ws, "final")
                with patch.object(call_module, "stream_voice_reply", slow_reply):
                    for _ in script[57:]:
                        ws.send_bytes(_pcm_frame())
                    events = self._receive_until(ws, "turn_done")
        finals = [event["text"] for event in events if event["type"] == "final"]
        self.assertIn("在吗，在吗", finals)
        self.assertEqual(events[-1]["user_message"]["content"], "在吗，在吗")

    def test_failed_sentence_audio_is_skipped_not_fatal(self) -> None:
        script = speech_script(5, 25, 45)
        fake = FakeEngine(script, "你好")

        async def flaky(text):  # type: ignore[no-untyped-def]
            raise RuntimeError("TTS API 连接中断。")
            yield  # pragma: no cover

        with patch.object(call_module, "stream_tts_pcm", flaky):
            with self._client(fake) as client, client.websocket_connect("/ws") as ws:
                self._receive_until(ws, "ready")
                for _ in script:
                    ws.send_bytes(_pcm_frame())
                events = self._receive_until(ws, "turn_done")
        kinds = [event["type"] for event in events]
        self.assertIn("tts_skipped", kinds)
        self.assertNotIn("error", kinds)
        self.assertEqual(events[-1]["assistant_message"]["content"], "我在这里。")

    def test_resuming_right_after_she_starts_retracts_and_merges(self) -> None:
        script = speech_script(2, 15, 40) + [True] * 25 + [False] * 45
        fake = FakeEngine(script, "在吗", frames_per_char=4)
        with self._client(fake) as client, client.websocket_connect("/ws") as ws:
            self._receive_until(ws, "ready")
            for _ in script[:57]:
                ws.send_bytes(_pcm_frame())
            self._receive_until(ws, "audio_begin")
            for _ in script[57:]:
                ws.send_bytes(_pcm_frame())
            events = self._receive_until(ws, "turn_done")
        kinds = [event["type"] for event in events]
        self.assertIn("retract", kinds)
        self.assertEqual(events[-1]["user_message"]["content"], "在吗，在吗")
        with Session(self.engine) as session:
            rows = session.exec(select(Message).order_by(Message.id)).all()
        # 撤回的半句没有进时间线：只有合并后的一问一答
        self.assertEqual([(row.role, row.content) for row in rows][0], ("user", "在吗，在吗"))
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
