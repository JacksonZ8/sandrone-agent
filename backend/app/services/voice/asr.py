from __future__ import annotations

import re
import threading
from collections import deque
from pathlib import Path
from typing import Any

from ...config import ASR_MODEL_NAME, ASR_NUM_THREADS, VOICE_MODEL_DIR

SAMPLE_RATE = 16000
FRAME_MS = 20

# 专有名词热词：识别器会偏向这些词（实测把“桑托涅/法杰欧”纠正为正确写法）。
DEFAULT_HOTWORDS = ("桑多涅", "法洁欧")
HOTWORDS_SCORE = 3.0
SENTENCE_END = ("。", "？", "！", "?", "!", "…")

_CJK = r"一-鿿　-〿＀-￯"


def normalize_text(text: str) -> str:
    """去掉两个中文字/中文标点之间多余的空格，中英之间与英文单词之间的空格保留。"""
    return re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", text.strip())


def _model_dir() -> Path:
    return VOICE_MODEL_DIR / ASR_MODEL_NAME


def _vad_path() -> Path:
    return VOICE_MODEL_DIR / "silero_vad.onnx"


def models_present() -> bool:
    model = _model_dir()
    needed = ("tokens.txt", "encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx")
    return _vad_path().is_file() and all((model / name).is_file() for name in needed)


def _hotword_line(word: str) -> str:
    # 模型的中文 token 是逐字的“▁字”：热词按字用空格隔开，英文单词保持整体。
    pieces = re.findall(rf"[{_CJK}]|[A-Za-z0-9']+", word)
    return " ".join(pieces)


def _prepare_hotwords() -> tuple[Path, Path]:
    """生成热词文件与 bpe 词表（模型只带 tokens.txt，热词编码需要词表）。"""
    VOICE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    words = list(DEFAULT_HOTWORDS)
    custom = VOICE_MODEL_DIR / "hotwords.txt"
    if custom.is_file():
        words += [line.strip() for line in custom.read_text(encoding="utf-8").splitlines() if line.strip()]
    hotwords = VOICE_MODEL_DIR / "hotwords.generated.txt"
    hotwords.write_text("\n".join(dict.fromkeys(_hotword_line(word) for word in words)) + "\n", encoding="utf-8")

    vocab = VOICE_MODEL_DIR / f"{ASR_MODEL_NAME}.bpe.vocab"
    if not vocab.is_file():
        lines = []
        for line in (_model_dir() / "tokens.txt").read_text(encoding="utf-8").splitlines():
            token, _, index = line.rpartition(" ")
            lines.append(f"{token}\t{-int(index)}")
        vocab.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return hotwords, vocab


class AsrEngine:
    """进程内只加载一次的识别器与 VAD 配置；加载约 2 秒，放在后台线程里预热。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recognizer: Any = None
        self._vad_config: Any = None
        self.error = ""

    @property
    def ready(self) -> bool:
        return self._recognizer is not None

    def load(self) -> bool:
        with self._lock:
            if self._recognizer is not None:
                return True
            if not models_present():
                self.error = "语音识别模型未下载：请运行 scripts/fetch_voice_models.sh。"
                return False
            try:
                import sherpa_onnx

                model = _model_dir()
                hotwords, vocab = _prepare_hotwords()
                self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=str(model / "tokens.txt"),
                    encoder=str(model / "encoder.int8.onnx"),
                    decoder=str(model / "decoder.onnx"),
                    joiner=str(model / "joiner.int8.onnx"),
                    num_threads=ASR_NUM_THREADS,
                    sample_rate=SAMPLE_RATE,
                    feature_dim=80,
                    decoding_method="modified_beam_search",
                    max_active_paths=4,
                    hotwords_file=str(hotwords),
                    hotwords_score=HOTWORDS_SCORE,
                    modeling_unit="bpe",
                    bpe_vocab=str(vocab),
                    provider="cpu",
                )
                self._vad_config = sherpa_onnx.VadModelConfig(
                    silero_vad=sherpa_onnx.SileroVadModelConfig(
                        model=str(_vad_path()),
                        threshold=0.5,
                        min_silence_duration=0.15,
                        min_speech_duration=0.15,
                        window_size=512,
                        max_speech_duration=30,
                    ),
                    sample_rate=SAMPLE_RATE,
                    num_threads=1,
                )
                self.error = ""
                return True
            except Exception as exc:  # noqa: BLE001
                self._recognizer = None
                self.error = f"语音识别模型加载失败：{exc}"
                return False

    def new_stream(self) -> Any:
        return self._recognizer.create_stream()

    def new_vad(self) -> Any:
        import sherpa_onnx

        return sherpa_onnx.VoiceActivityDetector(self._vad_config, buffer_size_in_seconds=30)

    def decode(self, stream: Any) -> str:
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return normalize_text(self._recognizer.get_result(stream))


ASR_ENGINE = AsrEngine()


def start_asr_background() -> None:
    """后台预热识别器与语义断句模型（各约 2 秒 / 0.3 秒），第一通电话不用等加载。"""
    if not models_present():
        return

    def warm() -> None:
        from .turn_detect import TURN_DETECTOR

        ASR_ENGINE.load()
        TURN_DETECTOR.load()

    threading.Thread(target=warm, daemon=True).start()


class Transcriber:
    """一路麦克风音频 → 说话开始 / 实时字幕 / 一句话说完。

    VAD 决定何时开口、何时停下；只把说话片段（含 0.5 秒前置缓冲）送进识别器。
    判断说完：有语义断句模型（Smart Turn）时，静音到 check_points_ms 的各个时刻听一次语气，
    认为说完就立刻结束，否则最多等到 max_wait_ms；没有模型时退回静音规则
    （句末已有句号/问号用 punct_silence_ms，否则 end_silence_ms）。
    """

    def __init__(
        self,
        engine: AsrEngine,
        *,
        turn_detector: Any = None,
        end_silence_ms: int = 700,
        punct_silence_ms: int = 400,
        check_points_ms: tuple[int, ...] = (200, 500, 900),
        max_wait_ms: int = 1500,
        complete_threshold: float = 0.5,
        punct_threshold: float = 0.3,
        preroll_ms: int = 500,
        max_utterance_ms: int = 30000,
    ) -> None:
        self.engine = engine
        self.turn_detector = turn_detector if turn_detector is not None and turn_detector.ready else None
        self.check_points_ms = check_points_ms
        self.max_wait_ms = max_wait_ms
        self.complete_threshold = complete_threshold
        self.punct_threshold = punct_threshold
        self.audio: list[list[float]] = []
        self.audio_samples = 0
        self.last_turn_probability: float | None = None
        self.vad = engine.new_vad()
        self.end_silence_ms = end_silence_ms
        self.punct_silence_ms = punct_silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.preroll: deque[list[float]] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.stream: Any = None
        self.silence_ms = 0
        self.utterance_ms = 0
        self.text = ""
        # 上一句实际等了多久静音才判定说完（句末有标点时更短）
        self.last_end_silence_ms = 0

    @property
    def active(self) -> bool:
        return self.stream is not None

    def feed(self, samples: list[float]) -> list[tuple[str, str]]:
        """送入一帧 16kHz 浮点采样，返回事件列表：speech_start / partial / final。"""
        events: list[tuple[str, str]] = []
        self.vad.accept_waveform(samples)
        speaking = self.vad.is_speech_detected()
        while not self.vad.empty():
            self.vad.pop()

        if self.stream is None:
            self.preroll.append(samples)
            if not speaking:
                return events
            self.stream = self.engine.new_stream()
            for chunk in self.preroll:
                self.stream.accept_waveform(SAMPLE_RATE, chunk)
                self._keep(chunk)
            self.preroll.clear()
            self.silence_ms = 0
            self.utterance_ms = 0
            self.text = ""
            events.append(("speech_start", ""))
        else:
            self.stream.accept_waveform(SAMPLE_RATE, samples)
            self._keep(samples)
            self.utterance_ms += FRAME_MS
            self.silence_ms = 0 if speaking else self.silence_ms + FRAME_MS

        text = self.engine.decode(self.stream)
        if text != self.text:
            self.text = text
            events.append(("partial", text))

        if self._turn_complete() or self.utterance_ms >= self.max_utterance_ms:
            self.last_end_silence_ms = self.silence_ms
            events.append(("final", self.finish()))
        return events

    def _keep(self, samples: list[float]) -> None:
        """保留本句最后 8 秒音频给语义断句模型。"""
        self.audio.append(samples)
        self.audio_samples += len(samples)
        while self.audio and self.audio_samples - len(self.audio[0]) >= 8 * SAMPLE_RATE:
            self.audio_samples -= len(self.audio.pop(0))

    def _turn_complete(self) -> bool:
        if self.silence_ms == 0:
            return False
        if self.turn_detector is None:
            needed = self.punct_silence_ms if self.text.endswith(SENTENCE_END) else self.end_silence_ms
            return self.silence_ms >= needed
        if self.silence_ms >= self.max_wait_ms:
            return True
        if self.silence_ms not in self.check_points_ms or not self.text:
            return False
        audio = [value for chunk in self.audio for value in chunk]
        probability = self.turn_detector.probability(audio)
        self.last_turn_probability = probability
        threshold = self.punct_threshold if self.text.endswith(SENTENCE_END) else self.complete_threshold
        return probability >= threshold

    def finish(self) -> str:
        """补一秒静音让识别器吐出最后几个字（实测约 50ms），返回整句并复位。"""
        stream, self.stream = self.stream, None
        if stream is None:
            return ""
        stream.accept_waveform(SAMPLE_RATE, [0.0] * SAMPLE_RATE)
        stream.input_finished()
        text = self.engine.decode(stream)
        self.audio = []
        self.audio_samples = 0
        self.text = ""
        self.silence_ms = 0
        self.utterance_ms = 0
        return text
