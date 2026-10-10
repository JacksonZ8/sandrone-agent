import { useEffect, useState } from "react";

export interface EmotionAction {
  expression?: string;
  motion?: string;
}

export interface Framing {
  /** 相对“整身放进容器”的放大倍数 */
  zoom?: number;
  /** 视线落点：0 为模型顶部，1 为底部 */
  focusY?: number;
}

export interface Live2DMapping {
  model?: string;
  /** 通话情绪标签（平静/开心/得意/害羞/生气/惊讶/难过/担心）或她的心情 → 表情/动作 */
  emotions?: Record<string, EmotionAction>;
  /** 说话时播放的动作组 */
  speak_motion?: string;
  /** 口型参数名，Cubism 4 默认为 ParamMouthOpenY */
  mouth_param?: string;
  /** 口型灵敏度，音量 RMS 乘以该值后截到 0–1 */
  mouth_gain?: number;
  framing?: { avatar?: Framing; hero?: Framing; full?: Framing };
  error?: string;
}

export interface Live2DStatus {
  /** 逐帧母版素材包的 manifest 地址；没有 Cubism 模型时用它显示形象 */
  portrait_url: string | null;
  /** 开屏片头：theatre 暖金、manuscript 蓝白 */
  films: Partial<Record<"theatre" | "manuscript", { video: string; poster: string }>>;
  ready: boolean;
  core_present: boolean;
  core_url: string | null;
  models: string[];
  model_url: string | null;
  mapping: Live2DMapping;
  folder: string;
}

let cached: Promise<Live2DStatus | null> | null = null;

/** 只请求一次；失败时视为没有 Live2D，界面退回剪影 */
export function loadLive2DStatus(): Promise<Live2DStatus | null> {
  if (!cached) {
    cached = fetch("/api/live2d/status")
      .then((response) => (response.ok ? (response.json() as Promise<Live2DStatus>) : null))
      .catch(() => null);
  }
  return cached;
}

export function useLive2DStatus(): Live2DStatus | null {
  const [status, setStatus] = useState<Live2DStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    void loadLive2DStatus().then((value) => {
      if (!cancelled) setStatus(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return status;
}
