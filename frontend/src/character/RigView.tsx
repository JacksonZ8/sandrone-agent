import { useEffect, useRef } from "react";
import { CALM, ease, FaceClock } from "./expression";
import { loadPortraitPack, type LoadedPack, type PatchInfo } from "./portraitPack";
import {
  deformMesh,
  emotionPose,
  gridMesh,
  layoutFromPatches,
  REST_POSE,
  type Mesh,
  type RigLayout,
  type RigPose,
} from "./rig";

interface Props {
  manifestUrl: string;
  size: "hero" | "full";
  /** 情绪标签；不在素材包里的按平静显示 */
  emotion?: string | null;
  speaking?: boolean;
  /** 你在说话：她微微侧头、看向你 */
  listening?: boolean;
  /** 她当前说话音量（RMS，约 0–0.3），驱动张嘴 */
  level?: () => number;
  onError: (message: string) => void;
}

/** 原生壳（macOS 桌面通话窗口）用它推送全局鼠标位置：detail 为页面坐标 {x, y} */
export const POINTER_EVENT = "sandrone:pointer";
const IDLE_GAZE_MS = 4000; // 鼠标这么久不动就自己四处看看

const VERT = `
attribute vec2 aPos;
attribute vec2 aUV;
uniform vec4 uView;
varying vec2 vUV;
void main() {
  vUV = aUV;
  gl_Position = vec4(aPos * uView.xy + uView.zw, 0.0, 1.0);
}`;

const FRAG = `
precision mediump float;
uniform sampler2D uTex;
uniform float uAlpha;
varying vec2 vUV;
void main() {
  gl_FragColor = texture2D(uTex, vUV) * uAlpha;
}`;

/** 一块要画的网格：原画坐标的矩形 + 它的顶点缓存 */
interface Part {
  rect: { x: number; y: number; w: number; h: number };
  mesh: Mesh;
  positions: Float32Array;
  posBuffer: WebGLBuffer;
  uvBuffer: WebGLBuffer;
  indexBuffer: WebGLBuffer;
}

function approach(current: number, target: number, rate: number, dt: number): number {
  return current + (target - current) * (1 - Math.exp(-rate * dt));
}

/**
 * 类 Live2D 的通话形象：一张表情母版铺在 40×40 的网格上，按姿态移动网格顶点——
 * 视线跟随鼠标的转头与抬头、说话时轻点头、情绪动作、呼吸、发梢摆动；嘴部与眨眼贴片跟着同一套形变。
 * 不改动原画像素；WebGL 不可用时由外层退回 2D 画法。
 */
export default function RigView({
  manifestUrl,
  size,
  emotion,
  speaking = false,
  listening = false,
  level,
  onError,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const props = useRef({ emotion, speaking, listening, level });
  const errorRef = useRef(onError);

  useEffect(() => {
    props.current = { emotion, speaking, listening, level };
    errorRef.current = onError;
  }, [emotion, speaking, listening, level, onError]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: true,
    });
    if (!gl) {
      errorRef.current("当前环境不支持 WebGL");
      return;
    }

    let frame = 0;
    let disposed = false;
    let pack: LoadedPack | null = null;
    let layout: RigLayout | null = null;
    const textures = new Map<string, WebGLTexture>();
    const parts = new Map<string, Part>();

    // 着色器
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type)!;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(shader) || "着色器编译失败");
      return shader;
    };
    const program = gl.createProgram()!;
    try {
      gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(program) || "着色器链接失败");
    } catch (caught) {
      errorRef.current(caught instanceof Error ? caught.message : String(caught));
      return;
    }
    gl.useProgram(program);
    const aPos = gl.getAttribLocation(program, "aPos");
    const aUV = gl.getAttribLocation(program, "aUV");
    const uView = gl.getUniformLocation(program, "uView");
    const uAlpha = gl.getUniformLocation(program, "uAlpha");
    gl.enableVertexAttribArray(aPos);
    gl.enableVertexAttribArray(aUV);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);

    const texture = (name: string): WebGLTexture | null => {
      const cached = textures.get(name);
      if (cached) return cached;
      const image = pack?.image(name);
      if (!image) return null;
      const created = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, created);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      textures.set(name, created);
      return created;
    };

    const part = (key: string, rect: Part["rect"], cells: number): Part => {
      const cached = parts.get(key);
      if (cached) return cached;
      const mesh = gridMesh(rect, cells, cells);
      const uvBuffer = gl.createBuffer()!;
      gl.bindBuffer(gl.ARRAY_BUFFER, uvBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, mesh.uv, gl.STATIC_DRAW);
      const indexBuffer = gl.createBuffer()!;
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
      const created: Part = {
        rect,
        mesh,
        positions: new Float32Array(mesh.source.length),
        posBuffer: gl.createBuffer()!,
        uvBuffer,
        indexBuffer,
      };
      parts.set(key, created);
      return created;
    };

    const draw = (
      piece: Part,
      image: string,
      alpha: number,
      pose: RigPose,
      scaleY = 1,
      pivotY = 0,
    ) => {
      const tex = alpha > 0 ? texture(image) : null;
      if (!tex || !layout) return;
      deformMesh(piece.mesh, pose, layout, piece.positions, scaleY, pivotY);
      gl.bindBuffer(gl.ARRAY_BUFFER, piece.posBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, piece.positions, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, piece.uvBuffer);
      gl.vertexAttribPointer(aUV, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, piece.indexBuffer);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1f(uAlpha, Math.min(1, alpha));
      gl.drawElements(gl.TRIANGLES, piece.mesh.indices.length, gl.UNSIGNED_SHORT, 0);
    };

    const patch = (info: PatchInfo, cells: number) =>
      part(`${info.src}`, { x: info.x, y: info.y, w: info.w, h: info.h }, cells);

    // 视线：鼠标相对她脸部的位置；原生壳可以推送全局鼠标位置
    let pointer: { x: number; y: number } | null = null;
    let pointerAt = -Infinity;
    const onPointer = (x: number, y: number) => {
      pointer = { x, y };
      pointerAt = performance.now();
    };
    const onMove = (event: PointerEvent) => {
      if (event.pointerType === "mouse") onPointer(event.clientX, event.clientY);
    };
    const onNative = (event: Event) => {
      const detail = (event as CustomEvent<{ x: number; y: number }>).detail;
      if (detail && Number.isFinite(detail.x) && Number.isFinite(detail.y))
        onPointer(detail.x, detail.y);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener(POINTER_EVENT, onNative);

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
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

    const clock = new FaceClock(performance.now());
    const pose: RigPose = { ...REST_POSE };
    let hairLag = 0;
    let last = performance.now();
    const wander = Math.random() * 100;

    const render = (now: number) => {
      frame = requestAnimationFrame(render);
      if (!pack || !layout) return;
      // 页面在后台时不画，省电
      if (document.hidden) {
        last = now;
        return;
      }
      const dt = Math.min(0.1, Math.max(0.001, (now - last) / 1000));
      last = now;
      const { emotion: wanted, speaking: talking, listening: hearing, level: read } = props.current;
      const manifest = pack.manifest;
      const face = clock.step(
        now,
        wanted,
        talking,
        talking && read ? read() : 0,
        (name) => Boolean(manifest.emotions[name]),
        (name) => Boolean(pack?.image(manifest.emotions[name].master)),
      );

      // 取景：整张原画放进画布并贴底
      const crop = manifest.framing.full;
      const scale = Math.min(canvas.width / crop.w, canvas.height / crop.h);
      const offsetX = (canvas.width - crop.w * scale) / 2 - crop.x * scale;
      const offsetY = canvas.height - (crop.y + crop.h) * scale;

      // 目标姿态：视线 + 情绪动作 + 说话点头 + 倾听侧头
      const t = now / 1000;
      let gazeX: number;
      let gazeY: number;
      if (pointer && now - pointerAt < IDLE_GAZE_MS) {
        const rect = canvas.getBoundingClientRect();
        const ratio = rect.width / canvas.width;
        const faceX = rect.left + (offsetX + layout.head.cx * scale) * ratio;
        const faceY = rect.top + (offsetY + layout.head.cy * scale) * ratio;
        const reach = Math.max(window.innerWidth, window.innerHeight) * 0.5;
        gazeX = Math.max(-1, Math.min(1, (pointer.x - faceX) / reach));
        gazeY = Math.max(-1, Math.min(1, (pointer.y - faceY) / reach));
      } else {
        // 没有鼠标（触屏或很久没动）：慢慢地四处看看
        gazeX = Math.sin(t * 0.21 + wander) * 0.35 + Math.sin(t * 0.53 + wander * 2) * 0.12;
        gazeY = Math.sin(t * 0.17 + wander * 3) * 0.15;
      }
      const motion = emotionPose(face.shown, face.since, t);
      const target = {
        yaw: gazeX * 0.75 + (motion.yaw ?? 0) + (hearing ? 0.1 : 0),
        pitch:
          gazeY * 0.55 +
          (motion.pitch ?? 0) +
          (hearing ? 0.12 : 0) +
          (talking ? Math.sin(t * 7.3) * face.mouth * 0.22 : 0),
        roll: (motion.roll ?? 0) + (hearing ? 0.035 : 0) + gazeX * 0.02,
        bob: motion.bob ?? 0,
      };
      pose.yaw = approach(pose.yaw, target.yaw, 5, dt);
      pose.pitch = approach(pose.pitch, target.pitch, 5, dt);
      pose.roll = approach(pose.roll, target.roll, 4, dt);
      pose.bob = approach(pose.bob, target.bob, 14, dt);
      pose.breath = Math.sin(t * 1.3);
      pose.lean =
        Math.sin(t * 0.43) * 0.0025 + (talking ? Math.sin(t * 2.1) * face.mouth * 0.003 : 0);
      // 发梢比头慢半拍：跟着转头的速度往反方向甩，再自己轻轻摆
      hairLag = approach(hairLag, pose.yaw, 2.2, dt);
      pose.hair = (hairLag - pose.yaw) * 26 + Math.sin(t * 1.6) * 1.4;

      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform4f(
        uView,
        (2 * scale) / canvas.width,
        (-2 * scale) / canvas.height,
        (2 * offsetX) / canvas.width - 1,
        1 - (2 * offsetY) / canvas.height,
      );
      const edge = manifest.size;

      const body = part("master", { x: 0, y: 0, w: edge, h: edge }, 40);
      const drawEmotion = (name: string, alpha: number, blink: number) => {
        const entry = manifest.emotions[name] ?? manifest.emotions[CALM];
        draw(body, entry.master, alpha, pose);
        const mouth = entry.mouth;
        if (face.mouth > 0.02) {
          const cy = mouth.cy ?? mouth.y + mouth.h / 2;
          draw(
            patch(mouth, 6),
            mouth.src,
            alpha * Math.min(1, face.mouth * 2.2),
            pose,
            0.45 + 0.55 * face.mouth,
            cy,
          );
        }
        if (name === CALM && blink > 0) {
          draw(
            patch(manifest.blink.half, 8),
            manifest.blink.half.src,
            alpha * Math.min(1, blink * 2),
            pose,
          );
          draw(
            patch(manifest.blink.closed, 8),
            manifest.blink.closed.src,
            alpha * Math.max(0, blink * 2 - 1),
            pose,
          );
        }
      };
      if (face.progress < 1) {
        drawEmotion(face.previous, 1, 0);
        drawEmotion(face.shown, ease(face.progress), 0);
      } else {
        drawEmotion(face.shown, 1, face.blink);
      }
    };

    loadPortraitPack(manifestUrl).then(
      (loaded) => {
        if (disposed) return;
        pack = loaded;
        const calm = loaded.manifest.emotions[CALM];
        layout = layoutFromPatches(loaded.manifest.size, loaded.manifest.blink.half, calm.mouth);
        frame = requestAnimationFrame(render);
      },
      (caught: unknown) => {
        if (!disposed) errorRef.current(caught instanceof Error ? caught.message : String(caught));
      },
    );

    const onLost = (event: Event) => {
      event.preventDefault();
      errorRef.current("WebGL 上下文丢失");
    };
    canvas.addEventListener("webglcontextlost", onLost);

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener(POINTER_EVENT, onNative);
      canvas.removeEventListener("webglcontextlost", onLost);
      textures.forEach((value) => gl.deleteTexture(value));
      parts.forEach((value) => {
        gl.deleteBuffer(value.posBuffer);
        gl.deleteBuffer(value.uvBuffer);
        gl.deleteBuffer(value.indexBuffer);
      });
      gl.deleteProgram(program);
    };
  }, [manifestUrl, size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    />
  );
}
