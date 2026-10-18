// 表情、口型与眨眼的时间状态：逐帧母版（2D 画布）与网格形变（WebGL）两种画法共用。

export const CALM = "平静";
const SWITCH_MS = 220; // 换表情的过渡时长
const RETURN_TO_CALM_MS = 6000; // 带情绪说完后多久回到平静
const BLINK_MS = 150;

export interface FaceFrame {
  /** 当前表情与过渡前的表情；progress < 1 时两张母版叠着过渡 */
  shown: string;
  previous: string;
  progress: number;
  /** 当前表情持续了多久（秒），驱动情绪动作的衰减 */
  since: number;
  /** 张嘴程度 0..1 */
  mouth: number;
  /** 闭眼程度 0..1 */
  blink: number;
}

export function ease(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
}

export class FaceClock {
  private shown = CALM;
  private previous = CALM;
  private switchedAt = -Infinity;
  private lastEmotionAt = 0;
  private lastWanted: string | null | undefined;
  private mouth = 0;
  private nextBlink: number;
  private blinkStart = -Infinity;
  private doubleBlink = false;

  constructor(now: number) {
    this.nextBlink = now + 1500 + Math.random() * 2500;
  }

  /**
   * known：素材包里有没有这个表情；ready：它的母版是否已经加载好（没加载好先不换，避免闪空）。
   * level：她的实时音量（RMS，约 0–0.3）。
   */
  step(
    now: number,
    wanted: string | null | undefined,
    talking: boolean,
    level: number,
    known: (name: string) => boolean,
    ready: (name: string) => boolean,
  ): FaceFrame {
    // 目标表情：带情绪说完后停留一会儿再回到平静；素材包没有的名字按平静处理
    let target = wanted && known(wanted) ? wanted : CALM;
    if (wanted !== this.lastWanted || talking) {
      this.lastWanted = wanted;
      this.lastEmotionAt = now;
    }
    if (target !== CALM && now - this.lastEmotionAt > RETURN_TO_CALM_MS) target = CALM;
    if (target !== this.shown && ready(target)) {
      this.previous = this.shown;
      this.shown = target;
      this.switchedAt = now;
    }

    // 口型：快开慢合，说话停止时自然收口
    const raw = talking ? Math.min(1, level * 5) : 0;
    this.mouth += (raw - this.mouth) * (raw > this.mouth ? 0.55 : 0.25);

    // 眨眼：2–6 秒一次，偶尔连眨两下
    let blink = 0;
    if (now >= this.nextBlink) {
      this.blinkStart = now;
      this.doubleBlink = Math.random() < 0.18;
      this.nextBlink = now + 2000 + Math.random() * 4000;
    }
    const sinceBlink = now - this.blinkStart;
    const blinkSpan = this.doubleBlink ? BLINK_MS * 2.4 : BLINK_MS;
    if (sinceBlink < blinkSpan) {
      const local = this.doubleBlink
        ? (sinceBlink % (BLINK_MS * 1.2)) / BLINK_MS
        : sinceBlink / BLINK_MS;
      blink = local <= 1 ? Math.sin(Math.PI * Math.min(1, local)) : 0;
    }

    return {
      shown: this.shown,
      previous: this.previous,
      progress: Math.min(1, (now - this.switchedAt) / SWITCH_MS),
      since: Math.max(0, (now - this.switchedAt) / 1000),
      mouth: this.mouth,
      blink,
    };
  }
}
