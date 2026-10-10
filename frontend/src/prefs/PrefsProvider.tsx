import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { loadPrefs, PrefsContext, savePrefs, type Prefs } from "./prefs";
import {
  clearCustomWallpaper,
  imageTone,
  readCustomWallpaper,
  writeCustomWallpaper,
} from "./wallpaperStore";

export default function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const [customImage, setCustomImageUrl] = useState("");

  const setPrefs = useCallback((patch: Partial<Prefs>) => {
    setPrefsState((current) => {
      const next = { ...current, ...patch };
      savePrefs(next);
      return next;
    });
  }, []);

  useEffect(() => {
    let url = "";
    readCustomWallpaper()
      .then((blob) => {
        if (!blob) return;
        url = URL.createObjectURL(blob);
        setCustomImageUrl(url);
      })
      .catch(() => undefined);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, []);

  const setCustomImage = useCallback(
    async (file: File | null) => {
      if (!file) {
        await clearCustomWallpaper();
        setCustomImageUrl("");
        setPrefs({ wallpaper: "mist" });
        return;
      }
      const tone = await imageTone(file);
      await writeCustomWallpaper(file);
      setCustomImageUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return URL.createObjectURL(file);
      });
      setPrefs({ wallpaper: "custom", customTone: tone });
    },
    [setPrefs],
  );

  // 把壁纸与玻璃参数写到根元素上，整页的设计变量随之切换。
  const useCustom = prefs.wallpaper === "custom" && Boolean(customImage);
  useEffect(() => {
    const root = document.documentElement;
    const base = useCustom
      ? prefs.customTone === "dark"
        ? "night"
        : "mist"
      : prefs.wallpaper === "custom"
        ? "mist"
        : prefs.wallpaper;
    root.dataset.wallpaper = base;
    root.dataset.wallpaperCustom = String(useCustom);
    root.style.setProperty("--wall-image", useCustom ? `url("${customImage}")` : "none");
    root.style.setProperty("--glass-alpha", String(prefs.glass / 100));
    root.style.setProperty("--glass-blur", `${prefs.blur}px`);
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute(
      "content",
      base === "night" ? "#1b2333" : base === "dusk" ? "#f1dccb" : "#dce6ec",
    );
  }, [customImage, prefs.blur, prefs.customTone, prefs.glass, prefs.wallpaper, useCustom]);

  const value = useMemo(
    () => ({ prefs, setPrefs, customImage, setCustomImage }),
    [customImage, prefs, setCustomImage, setPrefs],
  );

  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}
