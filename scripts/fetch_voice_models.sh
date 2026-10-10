#!/usr/bin/env bash
# 下载实时语音通话所需的本地模型到 data/voice_models（不进 Git），并校验 SHA-256。
#   - X-ASR 流式中英识别（sherpa-onnx 版，Apache-2.0，480ms 分块，带标点，int8）
#   - Silero VAD
#   - Smart Turn v3.2（pipecat-ai，BSD-2）：听语气判断一句话是否说完
# 用法：scripts/fetch_voice_models.sh   （可用 SANDRONE_VOICE_MODEL_DIR 指定目录）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="${SANDRONE_VOICE_MODEL_DIR:-$ROOT_DIR/data/voice_models}"
BASE="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
ASR="sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05"

mkdir -p "$DEST"
cd "$DEST"

if [ ! -f "$ASR/encoder.int8.onnx" ]; then
  echo "下载 $ASR（约 134MB）…"
  curl -fSL -o "$ASR.tar.bz2" "$BASE/$ASR.tar.bz2"
  tar xjf "$ASR.tar.bz2"
  rm "$ASR.tar.bz2"
fi

if [ ! -f smart-turn-v3.2-cpu.onnx ]; then
  echo "下载 smart-turn-v3.2-cpu.onnx（约 9MB）…"
  curl -fSL -o smart-turn-v3.2-cpu.onnx "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"
fi

if [ ! -f silero_vad.onnx ]; then
  echo "下载 silero_vad.onnx…"
  curl -fSL -o silero_vad.onnx "$BASE/silero_vad.onnx"
fi

shasum -a 256 -c <<SUMS
9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6  silero_vad.onnx
2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f  smart-turn-v3.2-cpu.onnx
b818a60878b9aae978cbb8ad594acbd403d76d1af2e31ef4197c84e2dbdba27c  $ASR/tokens.txt
908596dcc137a73b95be908ca55e88caa1b3dbbe8027c171615f4b0609c5eb1e  $ASR/encoder.int8.onnx
a1cbc9eac2d5e3fb6617a218c67ad6daaa7f4e0fd225f08b2c22ab0413c8c257  $ASR/decoder.onnx
aedb7fa697b2ab43f20499826fff7c997eea7d67db77be97769aeeeb726e63b3  $ASR/joiner.int8.onnx
SUMS
echo "语音模型就绪：$DEST"
