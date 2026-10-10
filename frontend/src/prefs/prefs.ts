import { createContext, useContext } from "react";

export type WallpaperId = "mist" | "dusk" | "night" | "custom";
export type ReplyLength = "short" | "long";

export interface Prefs {
  wallpaper: WallpaperId;
  /** 自定义图片偏亮还是偏暗，决定沿用晨雾还是夜航的墨色 */
  customTone: "light" | "dark";
  /** 背景模糊，0–40 px */
  blur: number;
  /** 玻璃浓度，20–85 % */
  glass: number;
  replyLength: ReplyLength;
  /** 文字模式下是否保留括号里的动作描写 */
  actionText: boolean;
  /** 文字模式下每条回复是否自动合成并播放（本地 TTS 较慢，默认关） */
  autoplay: boolean;
}

export const DEFAULT_PREFS: Prefs = {
  wallpaper: "mist",
  customTone: "light",
  blur: 22,
  glass: 50,
  replyLength: "short",
  actionText: true,
  autoplay: false,
};

export const WALLPAPERS: { id: Exclude<WallpaperId, "custom">; name: string }[] = [
  { id: "mist", name: "晨雾" },
  { id: "dusk", name: "暮色" },
  { id: "night", name: "夜航" },
];

const STORAGE_KEY = "sandrone.prefs.v1";

export function loadPrefs(): Prefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<Prefs>) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // 隐私模式等情况下存不住，只影响下次打开时的偏好。
  }
}

export interface PrefsContextValue {
  prefs: Prefs;
  setPrefs: (patch: Partial<Prefs>) => void;
  /** 自定义壁纸的对象 URL；没有图片时为空 */
  customImage: string;
  setCustomImage: (file: File | null) => Promise<void>;
}

export const PrefsContext = createContext<PrefsContextValue | null>(null);

export function usePrefs(): PrefsContextValue {
  const value = useContext(PrefsContext);
  if (!value) throw new Error("usePrefs must be used inside PrefsProvider");
  return value;
}
