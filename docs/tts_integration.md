# TTS 语音 v1

## 当前链路

```text
桑多涅文字回复
  -> 前端勾选「语音回复」
  -> POST /api/tts { text, message_id }
  -> 本地 GPT-SoVITS（默认常驻 api_v2，端口 9881）
  -> data/audio_cache/<sha1>.wav
  -> /audio/<hash>.wav 播放
```

`SANDRONE_TTS_MODE` 决定合成方式：

- `api`（默认）：后端启动时在后台拉起 GPT-SoVITS `api_v2.py` 并预热，日志写入 `data/tts_api.log`；
- `cli`：每次调用 `inference_cli.py`，启动慢但不常驻；
- `mock`：用提示音代替真实合成，测试与无 GPT-SoVITS 时联调使用。

## 关键参数

- GPT 权重：`assets/tts/weights/gpt.ckpt`（自备，见 `assets/tts/README.md`）
- SoVITS 权重：`assets/tts/weights/sovits.pth`（自备）
- 参考音频：`assets/tts/reference/reference.wav`
- 参考文本：`assets/tts/reference/ref_text.txt`
- 采样率：32kHz，单声道

## 缓存

- 相同文本只合成一次，按 SHA1 缓存。
- 合成失败不影响文字聊天，前端会显示错误。
- Mock 模式：`SANDRONE_TTS_MODE=mock`，用提示音代替真实合成。

## 待办

- 流式合成，进一步降低首句延迟。
- 情绪化语音：根据好感度和上下文调整参考音频或韵律。
