import { useRef, useState } from "react";
import { usePrefs, WALLPAPERS } from "../prefs/prefs";
import { PlusIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./WallpaperScreen.module.css";

export default function WallpaperScreen() {
  const { prefs, setPrefs, customImage, setCustomImage } = usePrefs();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState("");

  async function pick(file: File | undefined) {
    if (!file) return;
    setError("");
    try {
      await setCustomImage(file);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "图片读取失败。");
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.previewArea}>
        <div className={`${ui.card} ${ui.lifted} ${styles.preview}`} aria-label="预览">
          <div className={ui.hint}>预览</div>
          <div>这里清静，适合一个人做点研究。……你来的话，也不算打扰。</div>
        </div>
      </div>

      <section className={`${ui.sheet} ${styles.sheet}`}>
        <div role="radiogroup" aria-label="壁纸" className={styles.papers}>
          {WALLPAPERS.map((paper) => (
            <button
              key={paper.id}
              type="button"
              role="radio"
              aria-checked={prefs.wallpaper === paper.id}
              className={styles.paper}
              onClick={() => setPrefs({ wallpaper: paper.id })}
            >
              <span className={styles.swatch} data-wallpaper={paper.id} />
              {paper.name}
            </button>
          ))}
          {customImage ? (
            <button
              type="button"
              role="radio"
              aria-checked={prefs.wallpaper === "custom"}
              className={styles.paper}
              onClick={() => setPrefs({ wallpaper: "custom" })}
            >
              <span
                className={styles.swatch}
                style={{ background: `url("${customImage}") center / cover` }}
              />
              我的图片
            </button>
          ) : (
            <button type="button" className={styles.paper} onClick={() => fileRef.current?.click()}>
              <span className={`${styles.swatch} ${styles.add}`}>
                <PlusIcon size={20} />
              </span>
              我的图片
            </button>
          )}
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(event) => {
              void pick(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>

        {customImage && (
          <div className={styles.customActions}>
            <button type="button" className={ui.pill} onClick={() => fileRef.current?.click()}>
              换一张图片
            </button>
            <button
              type="button"
              className={`${ui.pill} ${ui.danger}`}
              onClick={() => void setCustomImage(null)}
            >
              移除我的图片
            </button>
          </div>
        )}

        <label className={styles.slider}>
          <span className={styles.sliderHead}>
            <span>背景模糊</span>
            <span className={ui.hint}>{prefs.blur}</span>
          </span>
          <input
            type="range"
            min={0}
            max={40}
            value={prefs.blur}
            onChange={(event) => setPrefs({ blur: Number(event.target.value) })}
          />
        </label>
        <label className={styles.slider}>
          <span className={styles.sliderHead}>
            <span>玻璃浓度</span>
            <span className={ui.hint}>{prefs.glass}%</span>
          </span>
          <input
            type="range"
            min={20}
            max={85}
            value={prefs.glass}
            onChange={(event) => setPrefs({ glass: Number(event.target.value) })}
          />
        </label>
        {error && <div className={ui.error}>{error}</div>}
        <p className={ui.hint}>
          文字太难看清时，把玻璃浓度调高；自定义图片只保存在这台设备的浏览器里。
        </p>
      </section>
    </div>
  );
}
