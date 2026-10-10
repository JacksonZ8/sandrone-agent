import { api } from "./api";

export type PushSupport = "supported" | "needs-install" | "unsupported";

/** iPhone 上只有“添加到主屏幕”的 App（iOS 16.4+）能收推送；电脑浏览器需要 HTTPS 或 localhost。 */
export function pushSupport(): PushSupport {
  if (
    !("serviceWorker" in navigator) ||
    !("PushManager" in window) ||
    !("Notification" in window)
  ) {
    const ios = /iPhone|iPad/.test(navigator.userAgent);
    return ios ? "needs-install" : "unsupported";
  }
  return "supported";
}

function keyBytes(base64url: string): ArrayBuffer {
  const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0)).buffer;
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== "supported") return null;
  const registration = await navigator.serviceWorker.getRegistration();
  return (await registration?.pushManager.getSubscription()) ?? null;
}

export async function enablePush(): Promise<void> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("没有通知权限：请在系统设置里允许通知。");
  const registration = await navigator.serviceWorker.ready;
  const { public_key } = await api.getPushKey();
  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyBytes(public_key),
    }));
  await api.subscribePush(subscription.toJSON());
}

export async function disablePush(): Promise<void> {
  const subscription = await currentSubscription();
  if (!subscription) return;
  await api.unsubscribePush(subscription.endpoint);
  await subscription.unsubscribe();
}
