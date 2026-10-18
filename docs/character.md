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

有形象包时，通话形象是**类 Live2D 的网格形变**（`frontend/src/character/rig.ts`、`RigView.tsx`）：表情母版铺在 40×40 的 WebGL 网格上，只移动网格顶点、不改像素——

- 视线跟随鼠标的转头与抬头（脸中心移动多、头部边缘移动少，形成球面转动的错觉；触屏或鼠标不动时自己慢慢四处看）；
- 说话时随音量轻点头；你开口时微微侧头倾听；
- 情绪动作：开心轻跳、得意抬下巴、害羞低头侧脸、生气摇头、惊讶一缩、难过低头、担心歪头；
- 呼吸（肩膀与胸口上提）、发梢与缎带比头慢半拍地甩动；
- 嘴部与眨眼贴片跟着同一套形变。头部区域由眨眼与嘴部贴片的位置推算，换素材包不用改代码；画面底边始终不动。

WebGL 不可用时退回 2D 画法；头像仍用 2D 画法。它不是 `.moc3`：单张原画没有被遮挡的部分，转头幅度只能很小；有正式 Cubism 模型时放进 `data/live2d/` 会自动优先使用。

## 开屏片头

`data/character/films/` 下放 `theatre.mp4`/`theatre.jpg`（暮色壁纸使用）与 `manuscript.mp4`/`manuscript.jpg`（其余壁纸）。播到 6.8 秒淡出，轻点跳过；没有片头、减弱动态或加载失败时播 CSS 动画。
