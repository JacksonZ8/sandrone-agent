import type { GameRole, GameRoleId } from "../types";
import ui from "../ui/ui.module.css";
import styles from "./GamesScreen.module.css";

export function RolePicker({
  roles,
  value,
  onChange,
}: {
  roles: GameRole[];
  value: GameRoleId | "";
  onChange: (role: GameRoleId) => void;
}) {
  return (
    <div className={styles.roles} role="radiogroup" aria-label="她的陪玩角色">
      {roles.map((role) => (
        <button
          key={role.id}
          type="button"
          role="radio"
          aria-checked={value === role.id}
          className={styles.role}
          onClick={() => onChange(role.id)}
        >
          <strong>{role.label}</strong>
          <span>{role.summary}</span>
        </button>
      ))}
    </div>
  );
}

export function AppPicker({
  running,
  value,
  onChange,
}: {
  running: string[];
  value: string[];
  onChange: (apps: string[]) => void;
}) {
  const choices = running.filter((name) => !value.includes(name));
  return (
    <div className={styles.apps}>
      {value.map((name) => (
        <button
          key={name}
          type="button"
          className={ui.pill}
          title="移除"
          onClick={() => onChange(value.filter((item) => item !== name))}
        >
          {name} ×
        </button>
      ))}
      <select
        value=""
        aria-label="游戏窗口对应的应用"
        onChange={(event) => event.target.value && onChange([...value, event.target.value])}
      >
        <option value="">{value.length ? "再加一个…" : "先打开游戏，再从这里选它…"}</option>
        {choices.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
