import { useEffect, useState } from "react";
import { loadLive2DStatus } from "../live2d/status";
import { usePrefs } from "../prefs/prefs";
import CharacterStage from "./CharacterStage";
import { markLaunchSeen } from "./launch";
import styles from "./LaunchSplash.module.css";

const DURATION_MS = 2400;
const REDUCED_MS = 700;
const STATUS_WAIT_MS = 600; // 等素材状态的上限，超时就直接播 CSS 动画
// 片头最后约 1 秒是示意用的假对话界面；应用里直接淡出到真实界面
const FILM_END_S = 6.8;

type Mode = "pending" | "film" | "css";
interface Film {
  video: string;
  poster: string;
}

function greeting(hour: number): string {
  if (hour < 5) return "……这么晚还不睡？算了，进来吧。";
  if (hour < 11) return "早。我刚好算完一组数据。";
  if (hour < 14) return "……来了？午饭吃过没有。";
  if (hour < 18) return "来了？我刚好算完一组数据。";
  return "……来了？今天过得怎么样。";
}

/**
 * 启动动画：有开屏片头（data/character/films）时全屏播放片头（暮色壁纸用暖金“上弦剧场”，其余用蓝白“机械手稿”）；
 * 否则或减弱动态、片头加载失败时，播 CSS 的“发条上弦 → 她登场”。每个会话只播一次，轻点跳过。
 */
export default function LaunchSplash({ onDone }: { onDone: () => void }) {
  const { prefs } = usePrefs();
  const [leaving, setLeaving] = useState(false);
  const [mode, setMode] = useState<Mode>("pending");
  const [film, setFilm] = useState<Film | null>(null);
  const [text] = useState(() => greeting(new Date().getHours()));

  useEffect(() => {
    markLaunchSeen();
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const timer = window.setTimeout(() => setLeaving(true), REDUCED_MS);
      const show = window.setTimeout(() => setMode("css"), 0);
      return () => {
        window.clearTimeout(timer);
        window.clearTimeout(show);
      };
    }
    let cancelled = false;
    const fallback = window.setTimeout(
      () => !cancelled && setMode((m) => (m === "pending" ? "css" : m)),
      STATUS_WAIT_MS,
    );
    void loadLive2DStatus().then((status) => {
      if (cancelled) return;
      const films = status?.films ?? {};
      const chosen =
        (prefs.wallpaper === "dusk" ? films.theatre : films.manuscript) ??
        films.manuscript ??
        films.theatre;
      setFilm(chosen ?? null);
      setMode((m) => (m === "pending" ? (chosen ? "film" : "css") : m));
    });
    return () => {
      cancelled = true;
      window.clearTimeout(fallback);
    };
    // 只在挂载时决定一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (mode !== "css") return;
    const timer = window.setTimeout(() => setLeaving(true), DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [mode]);

  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(onDone, 320);
    return () => window.clearTimeout(timer);
  }, [leaving, onDone]);

  if (mode === "film" && film) {
    return (
      <div
        className={`${styles.splash} ${styles.film}`}
        data-leaving={leaving}
        role="button"
        tabIndex={0}
        aria-label="跳过开屏片头"
        onClick={() => setLeaving(true)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " " || event.key === "Escape")
            setLeaving(true);
        }}
      >
        <video
          className={styles.video}
          src={film.video}
          poster={film.poster}
          autoPlay
          muted
          playsInline
          preload="auto"
          onEnded={() => setLeaving(true)}
          onTimeUpdate={(event) => {
            if (event.currentTarget.currentTime >= FILM_END_S) setLeaving(true);
          }}
          onError={() => setMode("css")}
        />
        <div className={styles.skip}>轻点跳过</div>
      </div>
    );
  }

  return (
    <div
      className={styles.splash}
      data-leaving={leaving}
      data-pending={mode === "pending"}
      role="button"
      tabIndex={0}
      aria-label="跳过启动动画"
      onClick={() => setLeaving(true)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " " || event.key === "Escape") setLeaving(true);
      }}
    >
      <div className={styles.veil} />
      <div className={styles.dial}>
        <svg className={styles.rotor} viewBox="0 0 300 300" aria-hidden="true">
          <circle
            className={styles.ring}
            cx="150"
            cy="150"
            r="120"
            transform="rotate(-90 150 150)"
          />
        </svg>
        <div className={styles.seal} aria-hidden="true">
          桑
        </div>
        <div className={styles.rise}>
          <CharacterStage size="hero" label="" />
        </div>
      </div>
      <div className={styles.titles}>
        <div className={styles.name}>SANDRONE</div>
        <div className={styles.greet}>{text}</div>
      </div>
      <div className={styles.skip}>轻点跳过</div>
    </div>
  );
}
