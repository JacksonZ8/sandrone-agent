import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import CharacterStage from "../stage/CharacterStage";
import { BackIcon, CloseIcon, MenuIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./AppShell.module.css";
import { PARENT, type Route } from "./route";

const TITLES: Record<Route, string> = {
  chat: "SANDRONE",
  her: "她的资料",
  memory: "她记得的事",
  games: "游戏",
  wallpaper: "壁纸与玻璃",
  settings: "模型、微信与工具",
  usage: "API 用量",
};

const NAV: { route: Route; label: string }[] = [
  { route: "chat", label: "对话" },
  { route: "her", label: "她的资料" },
  { route: "memory", label: "她记得的事" },
  { route: "games", label: "游戏" },
  { route: "wallpaper", label: "壁纸与玻璃" },
  { route: "settings", label: "模型、微信与工具" },
  { route: "usage", label: "API 用量" },
];

interface Props {
  route: Route;
  navigate: (route: Route) => void;
  status: string;
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

export default function AppShell({ route, navigate, status, children }: Props) {
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
      <header className={styles.header}>
        {isChat ? (
          <button
            type="button"
            className={ui.iconButton}
            aria-label="菜单"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <MenuIcon size={20} />
          </button>
        ) : (
          <button
            type="button"
            className={ui.iconButton}
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
            className={styles.avatar}
            aria-label="她的资料"
            onClick={() => navigate("her")}
          >
            <CharacterStage size="avatar" label="" />
          </button>
        ) : (
          <span className={styles.balance} aria-hidden="true" />
        )}
      </header>

      <main className={isChat ? styles.main : `${styles.main} ${styles.scrollable}`}>
        {children}
      </main>

      {menuOpen && (
        <div className={styles.drawerScrim} onClick={() => setMenuOpen(false)}>
          <nav
            ref={drawerRef}
            className={`${ui.sheet} ${styles.drawer}`}
            aria-label="主菜单"
            onClick={(event) => event.stopPropagation()}
          >
            <div className={styles.drawerHead}>
              <span className={styles.brand}>SANDRONE</span>
              <button
                type="button"
                className={ui.iconButton}
                aria-label="关闭菜单"
                onClick={() => setMenuOpen(false)}
              >
                <CloseIcon />
              </button>
            </div>
            {NAV.map((item) => (
              <a
                key={item.route}
                href={`#/${item.route}`}
                className={styles.navItem}
                aria-current={route === item.route ? "page" : undefined}
                onClick={() => setMenuOpen(false)}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      )}
    </div>
  );
}
