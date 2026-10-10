# 形象素材（可选）

仓库**不包含任何角色形象素材**。没有素材时，头像、启动画面与通话全屏显示剪影，所有功能照常可用。显示优先级：Cubism Live2D 模型（见 [`live2d.md`](live2d.md)）> 逐帧形象包 > 剪影。

素材放在 `data/character/`（不进 Git），后端以 `/character/` 静态提供；可用 `SANDRONE_CHARACTER_DIR` 改位置。请只使用你自己有权使用的图像。

## 逐帧形象包

```text
data/character/portrait/
├── manifest.json
├── master-0.webp …            # 每种表情一张完整母版（同尺寸，透明背景）
├── mouth-0.webp …             # 每种表情的张嘴贴片
└── blink-half.webp, blink-closed.webp   # 平静表情的半闭/全闭眼贴片
```

`manifest.json`：

```json
{
  "version": 1,
  "size": 1254,
  "emotions": {
    "平静": {"master": "master-0.webp", "mouth": {"src": "mouth-0.webp", "x": 560, "y": 610, "w": 140, "h": 95, "cx": 630, "cy": 660}}
  },
  "blink": {
    "half": {"src": "blink-half.webp", "x": 440, "y": 500, "w": 380, "h": 140},
    "closed": {"src": "blink-closed.webp", "x": 440, "y": 500, "w": 380, "h": 140}
  },
  "framing": {"avatar": {"x": 400, "y": 300, "w": 460, "h": 460}, "full": {"x": 0, "y": 0, "w": 1254, "h": 1254}}
}
```

- 表情名对应通话中的情绪标签：平静、开心、得意、害羞、生气、惊讶、难过、担心；缺少的按平静显示。
- 贴片坐标以母版像素为单位；说话时嘴部贴片按音量纵向展开，眨眼只用于平静表情，其余像素不动。呼吸与轻微摆动在画布上连续绘制。

## 开屏片头

`data/character/films/` 下放 `theatre.mp4`/`theatre.jpg`（暮色壁纸使用）与 `manuscript.mp4`/`manuscript.jpg`（其余壁纸）。播到 6.8 秒淡出，轻点跳过；没有片头、减弱动态或加载失败时播 CSS 动画。
