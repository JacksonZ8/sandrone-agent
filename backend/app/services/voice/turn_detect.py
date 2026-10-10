from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config import VOICE_MODEL_DIR

SAMPLE_RATE = 16000
WINDOW_SECONDS = 8
N_FFT = 400
HOP = 160
N_MELS = 80
MODEL_NAME = "smart-turn-v3.2-cpu.onnx"


def model_path() -> Path:
    return VOICE_MODEL_DIR / MODEL_NAME


def _hz_to_mel(freq: Any) -> Any:
    import numpy as np

    freq = np.asarray(freq, dtype=np.float64)
    mel = 3.0 * freq / 200.0
    log_region = freq >= 1000.0
    mel = np.where(log_region, 15.0 + np.log(np.maximum(freq, 1e-10) / 1000.0) * (27.0 / np.log(6.4)), mel)
    return mel


def _mel_to_hz(mel: Any) -> Any:
    import numpy as np

    mel = np.asarray(mel, dtype=np.float64)
    freq = 200.0 * mel / 3.0
    log_region = mel >= 15.0
    return np.where(log_region, 1000.0 * np.exp(np.log(6.4) * (mel - 15.0) / 27.0), freq)


@lru_cache(maxsize=1)
def _mel_filters() -> Any:
    """Slaney 刻度、Slaney 归一化的 80 维三角滤波器组，与 Whisper 特征提取一致。"""
    import numpy as np

    fft_freqs = np.linspace(0, SAMPLE_RATE // 2, 1 + N_FFT // 2)
    mel_points = np.linspace(_hz_to_mel(0.0), _hz_to_mel(8000.0), N_MELS + 2)
    filter_freqs = _mel_to_hz(mel_points)
    filter_diff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / filter_diff[:-1]
    up = slopes[:, 2:] / filter_diff[1:]
    filters = np.maximum(0.0, np.minimum(down, up))
    filters *= 2.0 / (filter_freqs[2 : N_MELS + 2] - filter_freqs[:N_MELS])
    return filters  # (201, 80)


def whisper_features(samples: Any) -> Any:
    """复现 WhisperFeatureExtractor(chunk_length=8, do_normalize=True) 的 log-mel 特征，形状 (1, 80, 800)。

    与 Smart Turn 参考实现相同：取最后 8 秒，不足时在前面补零，再整体做零均值单位方差归一。
    """
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32)
    total = WINDOW_SECONDS * SAMPLE_RATE
    audio = audio[-total:] if len(audio) > total else np.pad(audio, (total - len(audio), 0))
    audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)

    padded = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")
    frames = 1 + (len(padded) - N_FFT) // HOP
    index = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    window = np.hanning(N_FFT + 1)[:-1].astype(np.float64)
    spectrum = np.abs(np.fft.rfft(padded[index] * window, n=N_FFT)) ** 2  # (frames, 201)
    mel = np.maximum(spectrum @ _mel_filters(), 1e-10).T  # (80, frames)
    log_spec = np.log10(mel)[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec[None, :, :].astype(np.float32)


class TurnDetector:
    """Smart Turn v3（BSD-2，约 8MB）：听最后 8 秒的语气判断这句话是否已经说完。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any = None
        self.error = ""

    @property
    def ready(self) -> bool:
        return self._session is not None

    def load(self) -> bool:
        with self._lock:
            if self._session is not None:
                return True
            if not model_path().is_file():
                self.error = "语义断句模型未下载：请运行 scripts/fetch_voice_models.sh。"
                return False
            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.inter_op_num_threads = 1
                options.intra_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(str(model_path()), sess_options=options)
                self.probability([0.0] * SAMPLE_RATE)  # 预热：首次推理约 300ms，之后约 60ms
                self.error = ""
                return True
            except Exception as exc:  # noqa: BLE001
                self.error = f"语义断句模型加载失败：{exc}"
                return False

    def probability(self, samples: Any) -> float:
        """返回“这句话已经说完”的概率。"""
        outputs = self._session.run(None, {"input_features": whisper_features(samples)})
        return float(outputs[0].reshape(-1)[0])


TURN_DETECTOR = TurnDetector()
