// 单张母版的“类 Live2D”网格形变：把原画铺在网格上，按姿态移动网格顶点（不改像素），
// 得到转头、点头、歪头、呼吸与发梢摆动。纯函数，不碰 DOM / WebGL，便于测试。
// 坐标都是原画像素（0..size，y 向下）。

export interface Ellipse {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
}

/** 原画上的关键区域：从素材包的眨眼与嘴部贴片位置推算 */
export interface RigLayout {
  size: number;
  /** 头部（含刘海与头饰）：上半椭圆一直延伸到画面顶部 */
  head: Ellipse;
  /** 脖子：头部绕它歪头，身体从这里往下呼吸 */
  neckY: number;
}

export interface RigPose {
  /** 左右转头，-1..1（正值转向画面右侧） */
  yaw: number;
  /** 抬头低头，-1..1（正值低头） */
  pitch: number;
  /** 歪头，弧度（正值顺时针） */
  roll: number;
  /** 呼吸，-1..1 */
  breath: number;
  /** 发梢与缎带的横向摆动，原画像素 */
  hair: number;
  /** 整个人上下起伏（开心时的轻跳），原画像素，负值向上 */
  bob: number;
  /** 上身轻微左右倾，弧度近似；画面底边不动 */
  lean: number;
}

export const REST_POSE: RigPose = {
  yaw: 0,
  pitch: 0,
  roll: 0,
  breath: 0,
  hair: 0,
  bob: 0,
  lean: 0,
};

interface PatchRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 由素材包里双眼贴片与嘴部贴片的位置推算头部椭圆与脖子位置 */
export function layoutFromPatches(size: number, eyes: PatchRect, mouth: PatchRect): RigLayout {
  const cx = eyes.x + eyes.w / 2;
  const eyeY = eyes.y + eyes.h / 2;
  const mouthY = mouth.y + mouth.h / 2;
  const face = mouthY - eyeY;
  return {
    size,
    head: { cx, cy: eyeY - face * 0.4, rx: eyes.w * 0.86, ry: face * 3.4 },
    neckY: mouthY + face * 1.3,
  };
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

export function smoothstep(edge0: number, edge1: number, value: number): number {
  const t = clamp((value - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

/** 头部权重：椭圆内为 1，边缘柔和衰减；上半部延伸到画面顶部，脖子以下归零 */
export function headWeight(x: number, y: number, layout: RigLayout): number {
  const { cx, cy, rx, ry } = layout.head;
  const up = Math.max(cy, 1);
  const dy = y < cy ? (y - cy) / up : (y - cy) / (ry * 0.62);
  const dx = (x - cx) / rx;
  const d = Math.sqrt(dx * dx + dy * dy);
  const neck =
    1 - smoothstep(layout.neckY - layout.size * 0.06, layout.neckY + layout.size * 0.03, y);
  return (1 - smoothstep(0.8, 1.3, d)) * neck;
}

// 幅度（相对原画边长）：转头时脸中心横移约 2.4%，边缘几乎不动，形成球面转动的错觉
const YAW_SPAN = 0.024;
const PITCH_SPAN = 0.016;
const BREATH_SPAN = 0.005;

/** 把原画上的一点按姿态移到新位置 */
export function deform(x: number, y: number, pose: RigPose, layout: RigLayout): [number, number] {
  const { size, head, neckY } = layout;
  const w = headWeight(x, y, layout);
  let px = x;
  let py = y;

  if (w > 0) {
    // 球面转动：越靠近脸中心移动越多，再加一点整体平移
    const u = clamp((x - head.cx) / head.rx, -1, 1);
    const v = clamp((y - head.cy) / (head.ry * 0.8), -1, 1);
    px += w * pose.yaw * size * YAW_SPAN * (0.35 + 0.65 * (1 - u * u));
    py += w * pose.pitch * size * PITCH_SPAN * (0.3 + 0.7 * (1 - v * v));
    // 歪头：绕脖子旋转
    if (pose.roll) {
      const angle = pose.roll * w;
      const ox = px - head.cx;
      const oy = py - neckY;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      px = head.cx + ox * cos - oy * sin;
      py = neckY + ox * sin + oy * cos;
    }
  }

  // 呼吸：肩膀与胸口轻微上提、略微变宽，越往下越弱，画面底边不动
  const body = smoothstep(neckY - size * 0.05, neckY + size * 0.08, y);
  if (body > 0 && pose.breath) {
    const fade = 1 - smoothstep(neckY + size * 0.1, size, y);
    py -= body * fade * pose.breath * size * BREATH_SPAN;
    px += (x - head.cx) * body * pose.breath * 0.004;
  }

  // 发梢与缎带：头部两侧、脸以下的区域横向摆动，越往下越明显，画面底部收住
  if (pose.hair) {
    const side = smoothstep(head.rx * 0.5, head.rx * 1.05, Math.abs(x - head.cx));
    const below =
      smoothstep(head.cy, head.cy + size * 0.2, y) * (1 - smoothstep(size * 0.8, size, y));
    px += side * below * pose.hair * (0.5 + 0.5 * smoothstep(head.cy, size * 0.75, y));
  }

  // 整体起伏：脖子以上全量，往下渐弱到底边为零，底边不离开画框
  if (pose.bob) py += pose.bob * (1 - smoothstep(neckY, size, y));
  if (pose.lean) px += pose.lean * (size - y);

  return [px, py];
}

export interface Mesh {
  /** 原画坐标的网格顶点（x, y 交替） */
  source: Float32Array;
  /** 纹理坐标 0..1 */
  uv: Float32Array;
  indices: Uint16Array;
}

/** 覆盖矩形 rect 的规则网格，cols×rows 个格子 */
export function gridMesh(rect: PatchRect, cols: number, rows: number): Mesh {
  const count = (cols + 1) * (rows + 1);
  const source = new Float32Array(count * 2);
  const uv = new Float32Array(count * 2);
  for (let j = 0; j <= rows; j += 1) {
    for (let i = 0; i <= cols; i += 1) {
      const k = (j * (cols + 1) + i) * 2;
      uv[k] = i / cols;
      uv[k + 1] = j / rows;
      source[k] = rect.x + rect.w * uv[k];
      source[k + 1] = rect.y + rect.h * uv[k + 1];
    }
  }
  const indices = new Uint16Array(cols * rows * 6);
  let n = 0;
  for (let j = 0; j < rows; j += 1) {
    for (let i = 0; i < cols; i += 1) {
      const a = j * (cols + 1) + i;
      const b = a + 1;
      const c = a + cols + 1;
      const d = c + 1;
      indices.set([a, b, c, b, d, c], n);
      n += 6;
    }
  }
  return { source, uv, indices };
}

/**
 * 按姿态算出网格顶点的新位置，写进 out。
 * scaleY / pivotY 用于嘴部贴片：先在原画坐标里绕嘴部中心纵向伸缩，再跟着头部一起形变。
 */
export function deformMesh(
  mesh: Mesh,
  pose: RigPose,
  layout: RigLayout,
  out: Float32Array,
  scaleY = 1,
  pivotY = 0,
): Float32Array {
  const { source } = mesh;
  for (let k = 0; k < source.length; k += 2) {
    const sy = scaleY === 1 ? source[k + 1] : pivotY + (source[k + 1] - pivotY) * scaleY;
    const [x, y] = deform(source[k], sy, pose, layout);
    out[k] = x;
    out[k + 1] = y;
  }
  return out;
}

/** 每个三角形的有向面积里最小的一个；为正说明网格没有翻折 */
export function minTriangleArea(positions: Float32Array, indices: Uint16Array): number {
  let min = Infinity;
  for (let n = 0; n < indices.length; n += 3) {
    const a = indices[n] * 2;
    const b = indices[n + 1] * 2;
    const c = indices[n + 2] * 2;
    const area =
      (positions[b] - positions[a]) * (positions[c + 1] - positions[a + 1]) -
      (positions[c] - positions[a]) * (positions[b + 1] - positions[a + 1]);
    min = Math.min(min, area);
  }
  return min;
}

/** 情绪带来的姿态偏移：since 为换到这个情绪后的秒数，t 为全局秒数 */
export function emotionPose(emotion: string, since: number, t: number): Partial<RigPose> {
  const settle = Math.exp(-since * 2.5);
  switch (emotion) {
    case "开心":
      return { roll: 0.03, pitch: -0.1, bob: -Math.abs(Math.sin(since * 9)) * 7 * settle };
    case "得意":
      return { pitch: -0.35, roll: -0.04, yaw: 0.15 };
    case "害羞":
      return { pitch: 0.45, roll: 0.06, yaw: -0.3 };
    case "生气":
      return { pitch: 0.15, yaw: Math.sin(since * 22) * 0.35 * settle };
    case "惊讶":
      return { pitch: -0.4, bob: -10 * settle };
    case "难过":
      return { pitch: 0.55, roll: -0.03, bob: 3 };
    case "担心":
      return { pitch: 0.2, roll: 0.045 + Math.sin(t * 0.8) * 0.01 };
    default:
      return {};
  }
}
