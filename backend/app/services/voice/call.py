from __future__ import annotations

import array
import asyncio
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from sqlmodel import Session

from ...database import engine
from ..chat_prompt import reply_style_block
from ..llm_service import LLMError
from ..memory_service import run_extraction_background
from ..tts_service import save_wav, stream_tts_pcm
from .asr import ASR_ENGINE, SAMPLE_RATE, AsrEngine, Transcriber
from .emotion import EMOTION_BLOCK, EmotionTagParser
from .turn_detect import TURN_DETECTOR, TurnDetector
from .text import SentenceSplitter
from .turn import VoiceReply, save_voice_turn, stream_voice_reply

logger = logging.getLogger(__name__)

# 用户开口打断她：识别出至少这么多个字才算（避免咳嗽、回声误触发）。
BARGE_IN_MIN_CHARS = 2
# 她说话时听到这些单字应答只当附和，不打断。
BACKCHANNEL = {"嗯", "啊", "哦", "噢", "好", "对", "是", "嗯嗯", "哈"}
# 她开口不到这么久就被你接着说的话打断：多半是把停顿误判成说完，撤回她这半句并把两段话合并。
MERGE_WINDOW_S = 2.0
# 说完后超过这么久还没有第一段音频，就先垫一句语气词。
FILLER_DELAY_S = 1.2
FILLERS = ("嗯……", "让我想想。", "唔……")

_filler_cache: dict[str, tuple[int, bytes]] = {}


def _meaningful(text: str) -> str:
    return re.sub(r"[\s，。？！、,.?!…]+", "", text or "")


def _join_utterances(prefix: str, text: str) -> str:
    """把被误判断开的两段话接起来，中间补一个逗号。"""
    if not prefix:
        return text
    if prefix.endswith(("，", "。", "？", "！", ",", "?", "!", "…", "、")):
        return prefix + text
    return f"{prefix}，{text}"


def pcm16_to_float(data: bytes) -> list[float]:
    samples = array.array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    return [value / 32768.0 for value in samples]


async def _prepare_fillers() -> None:
    for text in FILLERS:
        if text in _filler_cache:
            continue
        try:
            rate, pcm = 0, bytearray()
            async for rate, chunk in stream_tts_pcm(text):
                pcm += chunk
            if pcm:
                _filler_cache[text] = (rate, bytes(pcm))
        except Exception:  # noqa: BLE001
            logger.debug("filler synthesis failed", exc_info=True)
            return


class VoiceCall:
    """一路实时通话。

    上行：浏览器 16kHz 单声道 PCM16（二进制帧）与控制消息（JSON）。
    下行：状态/字幕/计时（JSON）与她的声音（二进制 PCM16，采样率见 audio_begin）。
    """

    def __init__(
        self,
        websocket: WebSocket,
        asr: AsrEngine = ASR_ENGINE,
        turn_detector: TurnDetector | None = TURN_DETECTOR,
    ) -> None:
        self.ws = websocket
        self.asr = asr
        self.turn_detector = turn_detector
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-asr")
        self.transcriber: Transcriber | None = None
        self.send_lock = asyncio.Lock()
        self.options: dict[str, Any] = {"reply_length": "short", "fillers": True}
        self.turn_task: asyncio.Task | None = None
        self.turn_id = 0
        self.audio_started = False
        self.audible_until = 0.0
        self.pending_prefix = ""
        self.interrupt_reason = ""
        self.muted = False
        self.closed = False

    # ---------- 收发 ----------

    async def send(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))

    async def send_audio(self, pcm: bytes) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.ws.send_bytes(pcm)

    async def run(self) -> None:
        await self.ws.accept()
        try:
            if not await asyncio.to_thread(self.asr.load):
                await self.send({"type": "error", "message": self.asr.error, "fatal": True})
                return
            # 语义断句模型可选：加载失败时退回静音规则
            if self.turn_detector is not None:
                await asyncio.to_thread(self.turn_detector.load)
            self.transcriber = Transcriber(self.asr, turn_detector=self.turn_detector)
            await self.send({"type": "ready", "sample_rate": SAMPLE_RATE})
            await self.send({"type": "state", "state": "listening"})
            filler_task = asyncio.create_task(_prepare_fillers())
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    await self.on_audio(message["bytes"])
                elif message.get("text"):
                    await self.on_control(json.loads(message["text"]))
            filler_task.cancel()
        except Exception:  # noqa: BLE001
            logger.exception("voice call failed")
        finally:
            self.closed = True
            await self.cancel_turn()
            self.executor.shutdown(wait=False, cancel_futures=True)

    async def on_control(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "start":
            if message.get("reply_length") in ("short", "long"):
                self.options["reply_length"] = message["reply_length"]
            self.options["fillers"] = bool(message.get("fillers", True))
        elif kind == "playback_drained":
            self.audible_until = 0.0
            if not self.turn_running:
                await self.send({"type": "state", "state": "listening"})
        elif kind == "interrupt":
            await self.interrupt("tap")
        elif kind == "mute":
            self.muted = bool(message.get("muted"))
            if self.muted and self.transcriber and self.transcriber.active:
                self.transcriber.finish()

    # ---------- 听 ----------

    @property
    def turn_running(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    @property
    def she_is_busy(self) -> bool:
        return self.turn_running or time.monotonic() < self.audible_until

    async def on_audio(self, data: bytes) -> None:
        if self.muted or self.transcriber is None:
            return
        samples = pcm16_to_float(data)
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(self.executor, self.transcriber.feed, samples)
        for kind, text in events:
            if kind == "speech_start":
                await self.send({"type": "user_speech", "speaking": True})
            elif kind == "partial":
                await self.send({"type": "partial", "text": text})
                if self.she_is_busy and len(_meaningful(text)) >= BARGE_IN_MIN_CHARS:
                    await self.interrupt("speech")
            elif kind == "final":
                await self.on_final(text)

    async def on_final(self, text: str) -> None:
        await self.send({"type": "user_speech", "speaking": False})
        words = _meaningful(text)
        if not words:
            await self.send({"type": "final", "text": ""})
            return
        if self.she_is_busy and words in BACKCHANNEL:
            return
        if self.she_is_busy:
            await self.interrupt("speech")
        user_text = _join_utterances(self.pending_prefix, text)
        self.pending_prefix = ""
        await self.send({"type": "final", "text": user_text})
        self.turn_id += 1
        self.turn_task = asyncio.create_task(self.run_turn(self.turn_id, user_text, time.monotonic()))

    # ---------- 打断 ----------

    async def cancel_turn(self) -> None:
        task = self.turn_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def interrupt(self, reason: str) -> None:
        was_audible = self.she_is_busy
        self.interrupt_reason = reason
        await self.cancel_turn()
        self.audible_until = 0.0
        if was_audible:
            await self.send({"type": "interrupt", "turn": self.turn_id})
            await self.send({"type": "state", "state": "listening"})

    # ---------- 说 ----------

    async def run_turn(self, turn_id: int, user_text: str, ended_at: float) -> None:
        spoken_at = datetime.now(timezone.utc)
        reply = VoiceReply()
        splitter = SentenceSplitter()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        spoken: list[str] = []
        pcm_all = bytearray()
        state: dict[str, Any] = {
            "rate": 0,
            "audio_started": False,
            "first_audio_ms": 0,
            "real_audio": False,
            "real_audio_at": 0.0,
        }
        audio_seconds = 0.0
        await self.send({"type": "state", "state": "thinking", "turn": turn_id})

        async def begin_audio(rate: int) -> None:
            if not state["audio_started"]:
                state["audio_started"] = True
                state["first_audio_ms"] = int((time.monotonic() - ended_at) * 1000)
                await self.send({"type": "audio_begin", "turn": turn_id, "sample_rate": rate})
                await self.send({"type": "state", "state": "speaking", "turn": turn_id})
            elif rate != state["rate"] and state["rate"]:
                await self.send({"type": "audio_begin", "turn": turn_id, "sample_rate": rate})
            state["rate"] = rate

        async def push_audio(rate: int, pcm: bytes) -> None:
            nonlocal audio_seconds
            await begin_audio(rate)
            audio_seconds += len(pcm) / 2 / rate
            self.audible_until = max(self.audible_until, time.monotonic()) + len(pcm) / 2 / rate
            await self.send_audio(pcm)

        async def produce(session: Session) -> None:
            style = reply_style_block(reply_length=self.options["reply_length"], voice_mode=True)
            tags = EmotionTagParser()
            async for event in stream_voice_reply(
                session, user_text, reply, style_context=f"{style}\n\n{EMOTION_BLOCK}"
            ):
                if event["type"] == "delta":
                    emotion, text = tags.feed(event["text"])
                    if emotion:
                        await self.send({"type": "emotion", "turn": turn_id, "name": emotion})
                    for piece in splitter.feed(text):
                        await queue.put(piece)
                elif event["type"] == "tool_call":
                    await self.send({"type": "tool", "name": event["name"]})
            for piece in splitter.feed(tags.flush()) + splitter.flush():
                await queue.put(piece)
            await queue.put(None)

        async def speak() -> None:
            while (piece := await queue.get()) is not None:
                await self.send({"type": "sentence", "turn": turn_id, "text": piece})
                try:
                    async for rate, pcm in stream_tts_pcm(piece):
                        if not state["real_audio"]:
                            state["real_audio"] = True
                            state["real_audio_at"] = time.monotonic()
                        pcm_all.extend(pcm)
                        await push_audio(rate, pcm)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    # 一句合成失败只跳过这句的声音，字幕已发出，后面的句子照常念
                    logger.warning("tts failed for one sentence; skipping its audio", exc_info=True)
                    await self.send({"type": "tts_skipped", "turn": turn_id, "text": piece})
                spoken.append(piece)

        async def filler() -> None:
            await asyncio.sleep(FILLER_DELAY_S)
            if state["audio_started"] or not self.options["fillers"] or not _filler_cache:
                return
            text = random.choice(list(_filler_cache))
            rate, pcm = _filler_cache[text]
            await push_audio(rate, pcm)

        with Session(engine) as session:
            filler_task = asyncio.create_task(filler())
            try:
                producer = asyncio.create_task(produce(session))
                try:
                    await asyncio.gather(producer, speak())
                except BaseException:
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)
                    raise
                filler_task.cancel()
                final = await save_voice_turn(
                    session,
                    user_text=user_text,
                    assistant_text=reply.text,
                    reply=reply,
                    spoken_at=spoken_at,
                    audio_path=save_wav(bytes(pcm_all), state["rate"], "".join(spoken)) if pcm_all else "",
                )
                await self.send({"type": "audio_end", "turn": turn_id})
                await self._finish(final, reply, state, audio_seconds)
            except asyncio.CancelledError:
                filler_task.cancel()
                heard_s = time.monotonic() - state["real_audio_at"] if state["real_audio"] else 0.0
                resumed = self.interrupt_reason == "speech" and heard_s < MERGE_WINDOW_S
                if state["real_audio"] and spoken and not resumed:
                    # 她已经开口：把说出口的部分记进时间线，标明被打断。
                    final = await save_voice_turn(
                        session,
                        user_text=user_text,
                        assistant_text="".join(spoken) + "……",
                        reply=reply,
                        spoken_at=spoken_at,
                    )
                    await self._finish(final, reply, state, audio_seconds, interrupted=True)
                else:
                    # 还没开口、或刚开口就被你接着说的话打断：多半是停顿被误判成说完，
                    # 撤回她这半句，把这句并进下一句。
                    self.pending_prefix = user_text
                    await self.send({"type": "retract", "turn": turn_id})
                raise
            except LLMError as exc:
                filler_task.cancel()
                await self.send({"type": "error", "message": str(exc)})
                await self.send({"type": "state", "state": "listening"})
            except Exception as exc:  # noqa: BLE001
                filler_task.cancel()
                logger.exception("voice turn failed")
                await self.send({"type": "error", "message": f"通话回复失败：{exc}"})
                await self.send({"type": "state", "state": "listening"})

    async def _finish(
        self,
        final: dict[str, Any],
        reply: VoiceReply,
        state: dict[str, Any],
        audio_seconds: float,
        *,
        interrupted: bool = False,
    ) -> None:
        user, assistant = final.get("user_message"), final.get("assistant_message")
        if user and assistant:
            asyncio.get_running_loop().run_in_executor(
                None, run_extraction_background, user["id"], assistant["id"]
            )
        await self.send(
            {
                "type": "turn_done",
                "interrupted": interrupted,
                "user_message": user,
                "assistant_message": assistant,
                "timings": {
                    "end_silence_ms": self.transcriber.last_end_silence_ms if self.transcriber else 0,
                    "turn_probability": self.transcriber.last_turn_probability if self.transcriber else None,
                    "llm_first_token_ms": reply.first_token_ms,
                    "first_audio_ms": state["first_audio_ms"],
                    "audio_seconds": round(audio_seconds, 2),
                },
            }
        )
