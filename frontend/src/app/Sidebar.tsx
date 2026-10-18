import type { ComponentType, SVGProps } from "react";
import HerAvatar from "../character/HerAvatar";
import {
  BookIcon,
  ChartIcon,
  ChatIcon,
  CloseIcon,
  GamepadIcon,
  ImageIcon,
  SlidersIcon,
  UserIcon,
} from "../ui/icons";
import ui from "../ui/ui.module.css";
import type { Route } from "./route";
import styles from "./Sidebar.module.css";

type Icon = ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;

const GROUPS: { title: string; items: { route: Route; label: string; icon: Icon }[] }[] = [
  {
    title: "她",
    items: [
      { route: "chat", label: "对话", icon: ChatIcon },
      { route: "her", label: "她的资料", icon: UserIcon },
      { route: "memory", label: "她记得的事", icon: BookIcon },
      { route: "games", label: "游戏", icon: GamepadIcon },
    ],
  },
  {
    title: "设置",
    items: [
      { route: "wallpaper", label: "壁纸与玻璃", icon: ImageIcon },
      { route: "settings", label: "模型、微信与工具", icon: SlidersIcon },
      { route: "usage", label: "API 用量", icon: ChartIcon },
    ],
  },
];

interface Props {
  route: Route;
  /** 顶栏状态文字：她的心情或连接问题 */
  status: string;
  online: boolean;
  /** 抽屉里显示关闭按钮；常驻侧栏不显示 */
  onClose?: () => void;
  onNavigate?: () => void;
}

/** 侧栏内容：她的名片、分组导航与连接状态。宽屏常驻在左侧，窄屏放进抽屉。 */
export default function Sidebar({ route, status, online, onClose, onNavigate }: Props) {
  return (
    <div className={styles.sidebar}>
      <div className={styles.head}>
        <a href="#/her" className={styles.identity} onClick={onNavigate}>
          <HerAvatar className={styles.avatar} />
          <span className={styles.who}>
            <span className={styles.name}>桑多涅</span>
            <span className={styles.status}>{status}</span>
          </span>
        </a>
        {onClose && (
          <button type="button" className={ui.iconButton} aria-label="关闭菜单" onClick={onClose}>
            <CloseIcon />
          </button>
        )}
      </div>

      {GROUPS.map((group) => (
        <section key={group.title} className={styles.group} aria-label={group.title}>
          <h2 className={styles.groupTitle}>{group.title}</h2>
          {group.items.map(({ route: target, label, icon: ItemIcon }) => (
            <a
              key={target}
              href={`#/${target}`}
              className={styles.item}
              aria-current={route === target ? "page" : undefined}
              onClick={onNavigate}
            >
              <ItemIcon size={18} />
              <span>{label}</span>
            </a>
          ))}
        </section>
      ))}

      <div className={styles.foot}>
        <span className={styles.brand}>SANDRONE</span>
        <span className={styles.link} data-online={online}>
          {online ? "已连接" : "未连接"}
        </span>
      </div>
    </div>
  );
}
