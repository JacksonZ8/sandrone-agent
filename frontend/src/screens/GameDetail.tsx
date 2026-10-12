import { useEffect, useState } from "react";
import { api } from "../api";
import type { Game, GameMilestone, GameRole, GameSaveCard, GameSession, Memory } from "../types";
import GameKnowledgePanel from "./GameKnowledgePanel";
import { AppPicker, RolePicker } from "./GamePickers";
import { errorText, splitNames } from "./gameHelpers";
import ui from "../ui/ui.module.css";
import styles from "./GamesScreen.module.css";

const SAVE_FIELDS: { key: keyof GameSaveCard; label: string; placeholder: string }[] = [
  { key: "progress", label: "进度", placeholder: "如：第 3 幕 / 进阶 7 / 主线第 4 章" },
  {
    key: "roster",
    label: "拥有的角色/卡牌/装备",
    placeholder: "如：铁甲战士、静默猎手；五星角色……",
  },
  { key: "build", label: "当前配队/卡组/流派", placeholder: "如：力量战、雷国队" },
  { key: "resources", label: "资源与养成", placeholder: "如：原石 8000、角色等级、命座" },
  { key: "goals", label: "目标", placeholder: "如：通关进阶 10" },
  { key: "struggles", label: "卡关点", placeholder: "如：总在时间吞噬者那里翻车" },
  { key: "preferences", label: "偏好", placeholder: "如：不想被剧透、不爱抄作业" },
  { key: "notes", label: "备注", placeholder: "" },
];

const PERSPECTIVE_LABELS: Record<string, string> = {
  user: "关于你",
  self: "关于她",
  shared: "我们之间",
};

const INTERVALS = [1, 2, 3, 5];

function when(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function minutes(session: GameSession): number {
  const start = new Date(session.started_at).getTime();
  // 结束时间包含判定离开前的等待，时长按最后一次看到游戏算
  const end = new Date(session.last_seen_at).getTime();
  return Math.max(1, Math.round((end - start) / 60000));
}

/** 一个游戏的详情：档案与陪玩角色、账号档案与里程碑、可分享的游戏知识、战报与这个游戏的记忆。 */
export default function GameDetail({
  game,
  roles,
  running,
  onChanged,
  onDelete,
}: {
  game: Game;
  roles: GameRole[];
  running: string[];
  onChanged: (game: Game) => void;
  onDelete: () => void;
}) {
  const [card, setCard] = useState<GameSaveCard>(game.save_card);
  const [aliasText, setAliasText] = useState(game.aliases.join("、"));
  const [sessions, setSessions] = useState<GameSession[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [milestones, setMilestones] = useState<GameMilestone[]>([]);
  const [milestoneDraft, setMilestoneDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.getGameSessions(game.id),
      api.getGameMemories(game.id),
      api.getGameMilestones(game.id),
    ]).then(
      ([nextSessions, nextMemories, nextMilestones]) => {
        if (cancelled) return;
        setSessions(nextSessions);
        setMemories(nextMemories);
        setMilestones(nextMilestones);
      },
      (caught: unknown) => !cancelled && setError(errorText(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [game.id]);

  async function save(data: Parameters<typeof api.updateGame>[1], message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateGame(game.id, data);
      onChanged(next);
      setCard(next.save_card);
      setNotice(message);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.detail}>
      <section>
        <h3>她的陪玩角色</h3>
        <RolePicker
          roles={roles}
          value={game.role}
          onChange={(role) => void save({ role }, "已换角色。")}
        />
      </section>

      <section className={styles.grid}>
        <label className={styles.field}>
          <span>游戏中多久看一次</span>
          <select
            value={game.interval_minutes}
            onChange={(event) =>
              void save({ interval_minutes: Number(event.target.value) }, "已保存。")
            }
          >
            {INTERVALS.map((value) => (
              <option key={value} value={value}>
                每 {value} 分钟
              </option>
            ))}
          </select>
        </label>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={game.recap_enabled}
            onChange={(event) => void save({ recap_enabled: event.target.checked }, "已保存。")}
          />
          玩完发一段战报
        </label>
        <div className={styles.field}>
          <span>游戏窗口对应的应用</span>
          <AppPicker
            running={running}
            value={game.app_names}
            onChange={(apps) => apps.length && void save({ app_names: apps }, "已保存。")}
          />
        </div>
        <label className={styles.field}>
          <span>聊天里的叫法（别名）</span>
          <input
            value={aliasText}
            placeholder="如：尖塔、STS"
            onChange={(event) => setAliasText(event.target.value)}
            onBlur={() =>
              splitNames(aliasText).join("、") !== game.aliases.join("、") &&
              void save({ aliases: splitNames(aliasText) }, "别名已保存。")
            }
          />
        </label>
      </section>

      <section>
        <h3>账号档案（只在本机）</h3>
        <p className={ui.hint}>
          你在这个游戏里的情况，只存在本机数据库，不会写进可分享的知识文件。主要由她维护：每玩完一场、或你在聊天里提到变化时，她会自己更新；你也可以直接改。
        </p>
        <div className={styles.card}>
          {SAVE_FIELDS.map((field) => (
            <label key={field.key} className={styles.field}>
              <span>{field.label}</span>
              <textarea
                rows={2}
                value={card[field.key]}
                placeholder={field.placeholder}
                maxLength={300}
                onChange={(event) => setCard({ ...card, [field.key]: event.target.value })}
              />
            </label>
          ))}
        </div>
        <div className={styles.actions}>
          <button
            type="button"
            className={`${ui.pill} ${ui.primary}`}
            disabled={busy}
            onClick={() => void save({ save_card: card }, "账号档案已保存。")}
          >
            保存账号档案
          </button>
        </div>
        <h3>里程碑</h3>
        {milestones.length === 0 ? (
          <p className={ui.hint}>还没有。打倒 Boss、通关、解锁这些大事她会自己记下。</p>
        ) : (
          <ul className={styles.sessions}>
            {milestones.map((row) => (
              <li key={row.id} className={styles.milestone}>
                <span className={styles.meta}>{when(row.at)}</span> {row.text}
                <button
                  type="button"
                  className={`${ui.pill} ${ui.danger}`}
                  onClick={() =>
                    void api
                      .deleteGameMilestone(game.id, row.id)
                      .then(() => setMilestones(milestones.filter((item) => item.id !== row.id)))
                      .catch((caught: unknown) => setError(errorText(caught)))
                  }
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className={styles.apps}>
          <input
            value={milestoneDraft}
            maxLength={120}
            placeholder="手动记一条，如：抽到了芙宁娜"
            onChange={(event) => setMilestoneDraft(event.target.value)}
          />
          <button
            type="button"
            className={ui.pill}
            disabled={!milestoneDraft.trim()}
            onClick={() =>
              void api
                .addGameMilestone(game.id, milestoneDraft.trim())
                .then((row) => {
                  setMilestones([row, ...milestones]);
                  setMilestoneDraft("");
                })
                .catch((caught: unknown) => setError(errorText(caught)))
            }
          >
            记下
          </button>
        </div>
      </section>

      <GameKnowledgePanel gameId={game.id} gameName={game.name} />

      <section>
        <h3>最近的场次</h3>
        {sessions.length === 0 ? (
          <p className={ui.hint}>还没有一起玩过。打开游戏后她会自己开始陪着。</p>
        ) : (
          <ul className={styles.sessions}>
            {sessions.map((session) => (
              <li key={session.id}>
                <div className={styles.meta}>
                  {when(session.started_at)} · 约 {minutes(session)} 分钟
                  {session.ended_at ? "" : " · 进行中"}
                  {session.remark_count ? ` · 她说了 ${session.remark_count} 句` : ""}
                </div>
                {session.recap && <div>{session.recap}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3>她关于这个游戏的记忆</h3>
        {memories.length === 0 ? (
          <p className={ui.hint}>还没有。玩过几场、聊过这个游戏后会慢慢沉淀下来。</p>
        ) : (
          <ul className={styles.sessions}>
            {memories.map((memory) => (
              <li key={memory.id}>
                <span className={ui.tag}>{PERSPECTIVE_LABELS[memory.perspective]}</span>{" "}
                {memory.content}
              </li>
            ))}
          </ul>
        )}
        <p className={ui.hint}>「关于你」的记忆可以在“她记得的事”里修改或忘掉。</p>
      </section>

      {error && (
        <div className={ui.error} role="alert">
          {error}
        </div>
      )}
      {notice && <p className={ui.hint}>{notice}</p>}
      <div className={styles.actions}>
        <button type="button" className={`${ui.pill} ${ui.danger}`} onClick={onDelete}>
          删除这个游戏
        </button>
      </div>
    </div>
  );
}
