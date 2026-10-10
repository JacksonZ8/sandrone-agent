import { useCallback, useSyncExternalStore } from "react";

export type Route = "chat" | "her" | "memory" | "games" | "wallpaper" | "settings" | "usage";

const ROUTES: Route[] = ["chat", "her", "memory", "games", "wallpaper", "settings", "usage"];

/** 二级页返回时去的上一层 */
export const PARENT: Record<Route, Route> = {
  chat: "chat",
  her: "chat",
  memory: "her",
  games: "her",
  wallpaper: "her",
  settings: "her",
  usage: "her",
};

function current(): Route {
  const name = window.location.hash.replace(/^#\/?/, "") as Route;
  return ROUTES.includes(name) ? name : "chat";
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

export function useRoute(): [Route, (next: Route) => void] {
  const route = useSyncExternalStore(subscribe, current);
  const navigate = useCallback((next: Route) => {
    window.location.hash = `/${next}`;
  }, []);
  return [route, navigate];
}
