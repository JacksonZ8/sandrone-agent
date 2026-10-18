import { useEffect, useRef } from "react";
import { CALM, ease, FaceClock } from "./expression";
import { loadPortraitPack, type LoadedPack, type PatchInfo } from "./portraitPack";

interface Props {
  manifestUrl: string;
  size: "avatar" | "hero" | "full";
  /** 情绪标签；不在素材包里的（如心情“专注”）按平静显示 */
  emotion?: string | null;
  speaking?: boolean;
  /** 她当前说话音量（RMS，约 0–0.3），驱动张嘴 */
  level?: () => number;
  onError: (message: string) => void;
}

/**
 * 逐帧母版形象：表情只换整张母版（对齐后短暂过渡），说话只换嘴部贴片，眨眼只换双眼贴片，
 * 其余像素始终不动；呼吸与轻微摆动按屏幕刷新连续绘制。
 */
export default function PortraitView({
  manifestUrl,
  size,
  emotion,
  speaking = false,
  level,
  onError,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const props = useRef({ emotion, speaking, level });
  const errorRef = useRef(onError);

  useEffect(() => {
    props.current = { emotion, speaking, level };
    errorRef.current = onError;
  }, [emotion, speaking, level, onError]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let frame = 0;
    let disposed = false;
    let pack: LoadedPack | null = null;

    const clock = new FaceClock(performance.now());
    let lastDraw = 0;
    const minInterval = size === "avatar" ? 1000 / 24 : 0;

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2.5);
      const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
      const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const drawPatch = (
      context: CanvasRenderingContext2D,
      patch: PatchInfo,
      alpha: number,
      scaleY = 1,
    ) => {
      const image = pack?.image(patch.src);
      if (!image || alpha <= 0) return;
      context.globalAlpha = Math.min(1, alpha);
      if (scaleY === 1) {
        context.drawImage(image, patch.x, patch.y, patch.w, patch.h);
      } else {
        const cy = patch.cy ?? patch.y + patch.h / 2;
        context.save();
        context.translate(0, cy);
        context.scale(1, scaleY);
        context.drawImage(image, patch.x, patch.y - cy, patch.w, patch.h);
        context.restore();
      }
    };

    const drawEmotion = (
      context: CanvasRenderingContext2D,
      name: string,
      alpha: number,
      open: number,
      blink: number,
    ) => {
      if (!pack) return;
      const entry = pack.manifest.emotions[name] ?? pack.manifest.emotions[CALM];
      const master = pack.image(entry.master);
      if (!master || alpha <= 0) return;
      context.globalAlpha = alpha;
      context.drawImage(master, 0, 0);
      // 张嘴：贴片按开口程度纵向展开，开口很小时淡入，避免在闭嘴和张嘴之间来回跳
      if (open > 0.02)
        drawPatch(context, entry.mouth, alpha * Math.min(1, open * 2.2), 0.45 + 0.55 * open);
      // 眨眼只用于平静：半闭贴片先出现，再过渡到全闭
      if (name === CALM && blink > 0) {
        drawPatch(context, pack.manifest.blink.half, alpha * Math.min(1, blink * 2));
        drawPatch(context, pack.manifest.blink.closed, alpha * Math.max(0, blink * 2 - 1));
      }
    };

    const render = (now: number) => {
      frame = requestAnimationFrame(render);
      // 页面在后台时暂停绘制省电，但至少画出第一帧
      if (!pack || (document.hidden && lastDraw > 0)) return;
      if (minInterval && now - lastDraw < minInterval) return;
      lastDraw = now;
      const context = canvas.getContext("2d");
      if (!context) return;
      const { emotion: wanted, speaking: talking, level: read } = props.current;
      const manifest = pack.manifest;

      const face = clock.step(
        now,
        wanted,
        talking,
        talking && read ? read() : 0,
        (name) => Boolean(manifest.emotions[name]),
        (name) => Boolean(pack?.image(manifest.emotions[name].master)),
      );
      const mouth = face.mouth;

      // 取景：头像只看脸，其它显示整格并贴底
      const crop = size === "avatar" ? manifest.framing.avatar : manifest.framing.full;
      const scale =
        size === "avatar"
          ? Math.max(canvas.width / crop.w, canvas.height / crop.h)
          : Math.min(canvas.width / crop.w, canvas.height / crop.h);
      const offsetX = (canvas.width - crop.w * scale) / 2 - crop.x * scale;
      const offsetY =
        size === "avatar"
          ? (canvas.height - crop.h * scale) / 2 - crop.y * scale
          : canvas.height - (crop.y + crop.h) * scale;

      // 呼吸与轻微摆动：以画面底部为支点，幅度很小
      const t = now / 1000;
      const breath = Math.sin(t * 1.3);
      const sway = Math.sin(t * 0.43) * 0.0035 + (talking ? Math.sin(t * 2.1) * mouth * 0.004 : 0);

      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      const pivotX = offsetX + (manifest.size / 2) * scale;
      const pivotY = offsetY + manifest.size * scale;
      context.translate(pivotX, pivotY);
      context.rotate(sway);
      context.scale(1, 1 + breath * 0.0035);
      context.translate(-pivotX, -pivotY);
      context.translate(offsetX, offsetY);
      context.scale(scale, scale);

      if (face.progress < 1) {
        drawEmotion(context, face.previous, 1, mouth, 0);
        drawEmotion(context, face.shown, ease(face.progress), mouth, 0);
      } else {
        drawEmotion(context, face.shown, 1, mouth, face.blink);
      }
      context.globalAlpha = 1;
    };

    loadPortraitPack(manifestUrl).then(
      (loaded) => {
        if (disposed) return;
        pack = loaded;
        frame = requestAnimationFrame(render);
      },
      (caught: unknown) => {
        if (!disposed) errorRef.current(caught instanceof Error ? caught.message : String(caught));
      },
    );

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [manifestUrl, size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    />
  );
}
