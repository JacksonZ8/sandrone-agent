// macOS App 原生壳（WKWebView）与网页之间的消息。浏览器与手机 PWA 里没有原生壳，这里都返回 false。
//
// 对话窗口：点「通话」时请原生壳接通——它会隐藏对话窗口与桌宠，弹出透明的桌面通话窗口。
// 通话窗口：加载 /?overlay=call；挂断、拖动、打开对话窗口都交给原生壳。
// 原生壳在通话结束后对对话窗口派发 sandrone:call-ended，让时间线立刻读到通话里的几轮对话。

export type NativeMessage =
  | { type: "call" }
  | { type: "hangup" }
  | { type: "open-chat" }
  | { type: "move"; dx: number; dy: number }
  | { type: "move-end" };

interface NativeHandler {
  postMessage(message: NativeMessage): void;
}

/** 需要原生壳回话的请求（走 sandroneReply，postMessage 返回 Promise） */
export type NativeRequest = { type: "pickFolder" };

interface NativeReplyHandler {
  postMessage(message: NativeRequest): Promise<unknown>;
}

declare global {
  interface Window {
    webkit?: { messageHandlers?: { sandrone?: NativeHandler; sandroneReply?: NativeReplyHandler } };
    /** 原生壳在页面加载前注入：callWindow 表示它能弹出桌面通话窗口 */
    sandroneNative?: { callWindow?: boolean };
  }
}

export const CALL_ENDED_EVENT = "sandrone:call-ended";

/** 发给原生壳；没有原生壳时返回 false，由调用方走网页里的做法 */
export function postNative(message: NativeMessage): boolean {
  const handler = window.webkit?.messageHandlers?.sandrone;
  if (!handler) return false;
  handler.postMessage(message);
  return true;
}

/** 在 macOS App 里：通话交给桌面通话窗口 */
export function nativeCallWindow(): boolean {
  return Boolean(window.sandroneNative?.callWindow && window.webkit?.messageHandlers?.sandrone);
}

/** 这个页面是桌面通话窗口（/?overlay=call） */
export function isCallOverlay(): boolean {
  return new URLSearchParams(window.location.search).get("overlay") === "call";
}

/** 在 macOS App 里能弹出系统的“选择文件夹”面板 */
export function canPickFolder(): boolean {
  return Boolean(window.webkit?.messageHandlers?.sandroneReply);
}

/** 弹出系统面板选一个文件夹，返回绝对路径；取消或不在 App 里时返回 null */
export async function pickFolder(): Promise<string | null> {
  const handler = window.webkit?.messageHandlers?.sandroneReply;
  if (!handler) return null;
  const result = await handler.postMessage({ type: "pickFolder" });
  return typeof result === "string" && result ? result : null;
}
