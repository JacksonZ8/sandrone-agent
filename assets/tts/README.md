# 语音（GPT-SoVITS）素材

仓库**不包含任何音色**：没有模型权重、参考音频或样本。要让她用声音说话，需要你自备一套自己有权使用的 GPT-SoVITS v2 音色：

```text
assets/tts/
├── configs/sandrone_tts_config.json   # 文件名与参考音频语言的清单（可按需修改）
├── weights/gpt.ckpt                   # GPT 语义模型
├── weights/sovits.pth                 # SoVITS 声学模型
└── reference/
    ├── reference.wav                  # 3–10 秒参考音频
    └── ref_text.txt                   # 参考音频对应的文字
```

`weights/` 与 `reference/` 下的文件都不进 Git。

运行方式（在项目根 `.env` 中设置）：

- `SANDRONE_TTS_MODE=api`（默认）：后端按需在 `127.0.0.1:9881` 拉起 GPT-SoVITS 的 `api_v2.py`，需要 `SANDRONE_GPT_SOVITS_DIR` 指向你的 GPT-SoVITS 目录（默认是项目旁边的 `../GPT-SoVITS`）。
- `SANDRONE_TTS_MODE=mock`：不做真实合成，用提示音代替，用于没有 GPT-SoVITS 时联调界面与通话流程。

句子里夹英文时，GPT-SoVITS 需要 NLTK 的 `averaged_perceptron_tagger_eng` 数据；缺少时后端会自动改用中文读法，不会中断。
