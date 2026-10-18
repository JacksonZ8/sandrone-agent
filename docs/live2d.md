# Live2D 形象接入

她的 Live2D 形象出现在三处：顶栏头像与「她的资料」头像、启动动画、语音通话全屏。没有 Cubism 模型时用逐帧母版做类 Live2D 的网格形变（见 [`character.md`](character.md)），都没有时显示剪影占位。放好素材后刷新页面即可。

## 需要你自己准备的文件

都放进 `data/live2d/`（不进 Git，后端以 `/live2d/` 静态提供；可用环境变量 `SANDRONE_LIVE2D_DIR` 改位置）：

1. **Cubism Core**：`live2dcubismcore.min.js`。从 Live2D 官网下载 Cubism SDK for Web，阅读并同意其许可后，把 `Core/live2dcubismcore.min.js` 复制到 `data/live2d/`。它是 Live2D 的专有运行库，项目不随附、也不代为下载。
2. **模型**：一个 Cubism 3/4 模型文件夹（含 `*.model3.json`、`.moc3`、贴图，以及可选的 `motions/`、`expressions/`），例如 `data/live2d/sandrone/sandrone.model3.json`。只使用你有权使用的模型（约稿或获得授权），仅限本地非商业用途。

## 可选的 `data/live2d/mapping.json`

```json
{
  "model": "sandrone/sandrone.model3.json",
  "emotions": {
    "平静": { "expression": "normal" },
    "开心": { "expression": "smile", "motion": "Happy" },
    "得意": { "expression": "smug" },
    "害羞": { "expression": "blush" },
    "生气": { "expression": "angry", "motion": "Angry" },
    "惊讶": { "expression": "surprised" },
    "难过": { "expression": "sad" },
    "担心": { "expression": "worried" },
    "专注": { "expression": "normal" }
  },
  "speak_motion": "Talk",
  "mouth_param": "ParamMouthOpenY",
  "mouth_gain": 6,
  "framing": {
    "avatar": { "zoom": 2.4, "focusY": 0.2 },
    "full": { "zoom": 1, "focusY": 0.5 }
  }
}
```

- `model`：目录里有多个模型时选哪一个；不写则用找到的第一个。
- `emotions`：通话时她每句回复开头会带一个情绪标签（平静/开心/得意/害羞/生气/惊讶/难过/担心），标签不会被念出来，而是按这里映射成表情（`expressions` 里的名字）和动作组（`motions` 里的组名）。「她的资料」头像也会按她当前的心情（如「专注」）查这张表。
- `speak_motion`：她开口时播放的动作组（可选）。
- `mouth_param` / `mouth_gain`：口型参数名与灵敏度，口型跟随她的实时音量。
- `framing`：各位置的取景；`zoom` 是相对整身的放大倍数，`focusY` 是视线落点（0 顶部、1 底部）。

「她的资料」页的「Live2D 形象」一行会显示当前状态（缺 Core、缺模型或已加载）。
