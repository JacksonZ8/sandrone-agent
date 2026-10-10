// Live2D 运行时按需加载：Cubism Core 由用户放在 data/live2d（受 Live2D 许可约束，不随项目分发），
// pixi.js 与 pixi-live2d-display 拆成独立分包，没有模型时不会下载。
type Runtime = {
  PIXI: typeof import("pixi.js");
  Live2DModel: typeof import("pixi-live2d-display").Live2DModel;
};

let runtime: Promise<Runtime> | null = null;

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-live2d-core="${src}"]`)) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.dataset.live2dCore = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`无法加载 Cubism Core：${src}`));
    document.head.appendChild(script);
  });
}

export function loadLive2DRuntime(coreUrl: string): Promise<Runtime> {
  if (!runtime) {
    runtime = (async () => {
      await loadScript(coreUrl);
      const PIXI = await import("pixi.js");
      // pixi-live2d-display 0.4 通过全局 PIXI 找 Ticker 驱动模型更新
      (window as unknown as { PIXI: typeof PIXI }).PIXI = PIXI;
      const { Live2DModel } = await import("pixi-live2d-display/cubism4");
      Live2DModel.registerTicker(PIXI.Ticker);
      return { PIXI, Live2DModel };
    })();
    runtime.catch(() => {
      runtime = null;
    });
  }
  return runtime;
}
