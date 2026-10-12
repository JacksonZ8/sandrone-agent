#!/usr/bin/env bash
# 下载记忆检索用的中文向量模型到 data/models（不进 Git），并校验 SHA-256。
#   - BAAI bge-small-zh-v1.5（MIT），Xenova 转换的 ONNX 版，约 95MB
# 没有这个模型时记忆检索退回纯分词匹配，其余功能不受影响。
# 用法：scripts/fetch_memory_model.sh   （可用 SANDRONE_EMBEDDING_MODEL_DIR 指定目录）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="${SANDRONE_EMBEDDING_MODEL_DIR:-$ROOT_DIR/data/models/bge-small-zh-v1.5}"
REVISION="75c43b069aac4d136ba6bc1122f995fedcfd2781"
BASE="https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/$REVISION"

mkdir -p "$DEST"
cd "$DEST"

if [ ! -f model.onnx ]; then
  echo "下载 bge-small-zh-v1.5 model.onnx（约 95MB）…"
  curl -fSL -o model.onnx.partial "$BASE/onnx/model.onnx"
  mv model.onnx.partial model.onnx
fi
[ -f vocab.txt ] || curl -fSL -o vocab.txt "$BASE/vocab.txt"
[ -f tokenizer_config.json ] || curl -fSL -o tokenizer_config.json "$BASE/tokenizer_config.json"

shasum -a 256 -c <<SUMS
69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a  model.onnx
SUMS
echo "完成：$DEST"
