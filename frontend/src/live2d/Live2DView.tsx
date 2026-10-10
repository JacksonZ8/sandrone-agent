import { useEffect, useRef } from "react";
import { loadLive2DRuntime } from "./runtime";
import type { Framing, Live2DMapping } from "./status";

type Live2DModelInstance = import("pixi-live2d-display").Live2DModel;

interface Props {
  coreUrl: string;
  modelUrl: string;
  mapping: Live2DMapping;
  size: "avatar" | "hero" | "full";
  /** 情绪标签或心情名，按 mapping.emotions 映射成表情/动作 */
  emotion?: string | null;
  speaking?: boolean;
  /** 她当前说话音量（0–1 左右），用于口型 */
  level?: () => number;
  onError: (message: string) => void;
}

const DEFAULT_FRAMING: Record<Props["size"], Required<Framing>> = {
  avatar: { zoom: 2.4, focusY: 0.2 },
  hero: { zoom: 1, focusY: 0.5 },
  full: { zoom: 1, focusY: 0.5 },
};

/** 一个 Live2D 画布：透明背景，按容器大小取景，表情/动作/口型由属性驱动。 */
export default function Live2DView({
  coreUrl,
  modelUrl,
  mapping,
  size,
  emotion,
  speaking = false,
  level,
  onError,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const modelRef = useRef<Live2DModelInstance | null>(null);
  const levelRef = useRef(level);
  const errorRef = useRef(onError);

  useEffect(() => {
    levelRef.current = level;
    errorRef.current = onError;
  }, [level, onError]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let cleanup = () => {};

    (async () => {
      try {
        const { PIXI, Live2DModel } = await loadLive2DRuntime(coreUrl);
        if (disposed) return;
        const app = new PIXI.Application({
          backgroundAlpha: 0,
          antialias: true,
          autoDensity: true,
          resolution: Math.min(window.devicePixelRatio || 1, 2),
          resizeTo: host,
        });
        // 头像尺寸不需要满帧，省电
        if (size === "avatar") app.ticker.maxFPS = 24;
        host.appendChild(app.view);
        const model = await Live2DModel.from(modelUrl, { autoInteract: false });
        if (disposed) {
          model.destroy();
          app.destroy(true);
          return;
        }
        modelRef.current = model;
        app.stage.addChild(model);

        const framing = { ...DEFAULT_FRAMING[size], ...(mapping.framing?.[size] ?? {}) };
        const layout = () => {
          const width = host.clientWidth;
          const height = host.clientHeight;
          if (!width || !height) return;
          const base = Math.min(
            width / model.internalModel.width,
            height / model.internalModel.height,
          );
          const scale = base * framing.zoom;
          model.scale.set(scale);
          const modelWidth = model.internalModel.width * scale;
          const modelHeight = model.internalModel.height * scale;
          model.x = (width - modelWidth) / 2;
          model.y =
            framing.zoom === 1 ? height - modelHeight : height / 2 - modelHeight * framing.focusY;
        };
        layout();
        const observer = new ResizeObserver(layout);
        observer.observe(host);

        // 口型：每帧在动作更新之后覆盖嘴部参数
        const mouth = mapping.mouth_param || "ParamMouthOpenY";
        const gain = mapping.mouth_gain ?? 6;
        const applyMouth = () => {
          const read = levelRef.current;
          if (!read) return;
          const value = Math.max(0, Math.min(1, read() * gain));
          const core = model.internalModel.coreModel as {
            setParameterValueById: (id: string, value: number) => void;
          };
          core.setParameterValueById(mouth, value);
        };
        model.internalModel.on("beforeModelUpdate", applyMouth);

        cleanup = () => {
          observer.disconnect();
          model.internalModel.off("beforeModelUpdate", applyMouth);
          modelRef.current = null;
          app.destroy(true, { children: true });
        };
      } catch (caught) {
        if (!disposed) errorRef.current(caught instanceof Error ? caught.message : String(caught));
      }
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [coreUrl, mapping, modelUrl, size]);

  useEffect(() => {
    const model = modelRef.current;
    if (!model || !emotion) return;
    const action = mapping.emotions?.[emotion];
    if (action?.expression) void model.expression(action.expression);
    if (action?.motion) void model.motion(action.motion);
  }, [emotion, mapping]);

  useEffect(() => {
    const model = modelRef.current;
    if (model && speaking && mapping.speak_motion) void model.motion(mapping.speak_motion);
  }, [speaking, mapping]);

  return <div ref={hostRef} style={{ position: "absolute", inset: 0 }} />;
}
