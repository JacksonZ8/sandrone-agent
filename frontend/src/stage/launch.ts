const SEEN_KEY = "sandrone.launch.seen";

/** 启动动画每个浏览器会话只播一次 */
export function shouldShowLaunch(): boolean {
  try {
    return window.sessionStorage.getItem(SEEN_KEY) !== "1";
  } catch {
    return true;
  }
}

export function markLaunchSeen(): void {
  try {
    window.sessionStorage.setItem(SEEN_KEY, "1");
  } catch {
    // 存不住只会让下次再播一次
  }
}
