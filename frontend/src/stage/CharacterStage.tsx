import { lazy, Suspense, useState } from "react";
import { useLive2DStatus } from "../live2d/status";
import styles from "./CharacterStage.module.css";

// pixi 与 Live2D 运行时较大，只有放了模型才会加载这个分包
const Live2DView = lazy(() => import("../live2d/Live2DView"));
const PortraitView = lazy(() => import("../character/PortraitView"));
const RigView = lazy(() => import("../character/RigView"));

interface Props {
  /** avatar：圆形头像；hero：启动动画；full：通话全屏 */
  size: "avatar" | "hero" | "full";
  speaking?: boolean;
  /** 你在说话（通话里她在听） */
  listening?: boolean;
  /** 情绪标签或心情名，交给 Live2D 映射成表情/动作 */
  emotion?: string | null;
  /** 她当前说话音量，用于口型 */
  level?: () => number;
  /** 传空字符串表示纯装饰（外层已有可读名称） */
  label?: string;
}

/**
 * 角色舞台：优先 Cubism Live2D（data/live2d 有 Core 与模型时）；否则用逐帧母版素材包
 * （data/character/portrait，表情/口型/眨眼局部贴片）——大尺寸铺在网格上做类 Live2D 形变，
 * 头像或 WebGL 不可用时用 2D 画法；都没有或加载失败时显示剪影占位。
 */
export default function CharacterStage({
  size,
  speaking = false,
  listening = false,
  emotion,
  level,
  label = "她的形象",
}: Props) {
  const status = useLive2DStatus();
  const [failed, setFailed] = useState("");
  const [portraitFailed, setPortraitFailed] = useState("");
  const [rigFailed, setRigFailed] = useState("");
  const live = Boolean(status?.ready && status.core_url && status.model_url && !failed);
  // 没有 Cubism 模型时用逐帧母版素材包：大尺寸用网格形变（WebGL），头像与 WebGL 失败时用 2D 画法
  const portrait = !live && Boolean(status?.portrait_url && !portraitFailed);
  const rig = portrait && size !== "avatar" && !rigFailed;

  return (
    <div
      className={`${styles.stage} ${styles[size]}`}
      data-speaking={speaking}
      data-live2d={live || portrait}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      title={failed || portraitFailed ? `形象加载失败：${failed || portraitFailed}` : undefined}
    >
      {live && status ? (
        <Suspense fallback={null}>
          <Live2DView
            coreUrl={status.core_url!}
            modelUrl={status.model_url!}
            mapping={status.mapping}
            size={size}
            emotion={emotion}
            speaking={speaking}
            level={level}
            onError={setFailed}
          />
        </Suspense>
      ) : rig && status ? (
        <Suspense fallback={null}>
          <RigView
            manifestUrl={status.portrait_url!}
            size={size}
            emotion={emotion}
            speaking={speaking}
            listening={listening}
            level={level}
            onError={setRigFailed}
          />
        </Suspense>
      ) : portrait && status ? (
        <Suspense fallback={null}>
          <PortraitView
            manifestUrl={status.portrait_url!}
            size={size}
            emotion={emotion}
            speaking={speaking}
            level={level}
            onError={setPortraitFailed}
          />
        </Suspense>
      ) : (
        <div className={styles.figure}>
          <span className={styles.head} />
          <span className={styles.body} />
        </div>
      )}
    </div>
  );
}
