/** 生产构建里注册 Service Worker（开发服务器下不注册，避免缓存干扰热更新）。 */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // 非安全上下文（既不是 localhost 也不是 HTTPS）时注册会失败，网页照常可用
    });
  });
}
