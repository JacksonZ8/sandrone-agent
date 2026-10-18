import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import CharacterStage from "../stage/CharacterStage";
import { BackIcon, MenuIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./AppShell.module.css";
import { PARENT, type Route } from "./route";
import Sidebar from "./Sidebar";

const TITLES: Record<Route, string> = {
  chat: "SANDRONE",
  her: "她的资料",
  memory: "她记得的事",
  games: "游戏",
  wallpaper: "壁纸与玻璃",
  settings: "模型、微信与工具",
  usage: "API 用量",
};

interface Props {
  route: Route;
  navigate: (route: Route) => void;
  status: string;
  /** 后端可达 */
  online: boolean;
  children: ReactNode;
}

/** 她在“陪着看”时返回标题下的常驻提示（在看 X / 在看你玩 X），随时知道她能看到什么。 */
function useWatchingApp(active: boolean): string {
  const [app, setApp] = useState("");
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api.getCompanionStatus();
        if (!cancelled)
          setApp(
            status.state === "watching"
              ? `在看 ${status.app}`
              : status.state === "playing"
                ? `在看你玩 ${status.app}`
                : "",
          );
      } catch {
        if (!cancelled) setApp("");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active]);
  return active ? app : "";
}

/** 外壳：宽屏左侧常驻侧栏，窄屏顶栏菜单打开抽屉（同一份侧栏内容）。 */
export default function AppShell({ route, navigate, status, online, children }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const drawerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    drawerRef.current?.querySelector<HTMLElement>("[aria-current='page'], a")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const isChat = route === "chat";
  const watching = useWatchingApp(isChat);

  return (
    <div className={styles.shell}>
      <aside className={styles.rail} aria-label="主菜单">
        <Sidebar route={route} status={status} online={online} />
      </aside>
      <div className={styles.content}>
        <header className={styles.header}>
          {isChat ? (
            <button
              type="button"
              className={`${ui.iconButton} ${styles.narrowOnly}`}
              aria-label="菜单"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <MenuIcon size={20} />
            </button>
          ) : (
            <button
              type="button"
              className={`${ui.iconButton} ${styles.narrowOnly}`}
              aria-label="返回"
              onClick={() => navigate(PARENT[route])}
            >
              <BackIcon size={20} />
            </button>
          )}
          <div className={styles.title}>
            {isChat ? (
              <>
                <h1 className={styles.brand}>SANDRONE</h1>
                <div className={styles.status}>
                  桑多涅 · {status}
                  {watching && <span title="她能看到这个应用的窗口"> · {watching}</span>}
                </div>
              </>
            ) : (
              <h1 className={styles.pageTitle}>{TITLES[route]}</h1>
            )}
          </div>
          {isChat ? (
            <button
              type="button"
              className={`${styles.avatar} ${styles.narrowOnly}`}
              aria-label="她的资料"
              onClick={() => navigate("her")}
            >
              <CharacterStage size="avatar" label="" />
            </button>
          ) : (
            <span className={`${styles.balance} ${styles.narrowOnly}`} aria-hidden="true" />
          )}
        </header>

        <main className={isChat ? styles.main : `${styles.main} ${styles.scrollable}`}>
          {children}
        </main>
      </div>

      {menuOpen && (
        <div className={styles.drawerScrim} onClick={() => setMenuOpen(false)}>
          <nav
            ref={drawerRef}
            className={`${ui.sheet} ${styles.drawer}`}
            aria-label="主菜单"
            onClick={(event) => event.stopPropagation()}
          >
            <Sidebar
              route={route}
              status={status}
              online={online}
              onClose={() => setMenuOpen(false)}
              onNavigate={() => setMenuOpen(false)}
            />
          </nav>
        </div>
      )}
    </div>
  );
}
