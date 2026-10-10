import { useEffect, useState } from "react";
import { api } from "../api";
import type { Route } from "../app/route";
import type {
  CompanionConfig,
  Game,
  GameDraft,
  GameRole,
  GameRoleId,
  GameSaveCard,
  GameMilestone,
  GameSession,
  Memory,
} from "../types";
import ConfirmDialog from "../ui/ConfirmDialog";
import GameKnowledgePanel from "./GameKnowledgePanel";
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

const EMPTY_DRAFT = {
  name: "",
  app_names: [] as string[],
  aliases: [] as string[],
  genre: "",
  hud_hints: "",
  role: "" as GameRoleId | "",
  interval_minutes: 2,
  recap_enabled: true,
};

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

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

function splitNames(text: string): string[] {
  return text
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter((item) => item.length >= 2);
}

function minutes(session: GameSession): number {
  const start = new Date(session.started_at).getTime();
  // 结束时间包含判定离开前的等待，时长按最后一次看到游戏算
  const end = new Date(session.last_seen_at).getTime();
  return Math.max(1, Math.round((end - start) / 60000));
}

function RolePicker({
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

function AppPicker({
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

function GameDetail({
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

/** 陪你玩游戏（M15）：游戏档案、陪玩角色、账号档案与里程碑、可分享的游戏知识、战报与这个游戏的记忆。 */
export default function GamesScreen({ navigate }: { navigate: (route: Route) => void }) {
  const [games, setGames] = useState<Game[] | null>(null);
  const [roles, setRoles] = useState<GameRole[]>([]);
  const [running, setRunning] = useState<string[]>([]);
  const [companion, setCompanion] = useState<CompanionConfig | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [aliasDraft, setAliasDraft] = useState("");
  const [deleting, setDeleting] = useState<Game | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getGames(), api.getGameRoles(), api.getCompanionConfig()]).then(
      ([nextGames, nextRoles, nextCompanion]) => {
        if (cancelled) return;
        setGames(nextGames);
        setRoles(nextRoles);
        setCompanion(nextCompanion);
      },
      (caught: unknown) => !cancelled && setError(errorText(caught)),
    );
    api.getRunningApps().then(
      (apps) => !cancelled && setRunning(apps),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function add() {
    if (!draft.role) return;
    setBusy(true);
    setError("");
    try {
      const game = await api.createGame({
        ...draft,
        aliases: splitNames(aliasDraft),
        role: draft.role,
      } as GameDraft);
      setGames((current) =>
        [...(current ?? []), game].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setDraft(EMPTY_DRAFT);
      setAliasDraft("");
      setAdding(false);
      setOpenId(game.id);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove(game: Game) {
    setBusy(true);
    try {
      await api.deleteGame(game.id);
      setGames((current) => (current ?? []).filter((item) => item.id !== game.id));
      setDeleting(null);
      setOpenId(null);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const ready = companion?.enabled && companion.consented;
  const roleLabel = (id: GameRoleId) => roles.find((role) => role.id === id)?.label ?? id;

  return (
    <div className={styles.page}>
      <p className={styles.lead}>
        你玩游戏时，她按你选的角色陪着：战斗里不出声，只在结算、阵亡、菜单这些间隙说话；玩完写战报，记住你每个游戏的进度和打法。
      </p>
      {companion && !ready && (
        <div className={`${ui.card} ${styles.notice}`}>
          需要先在设置里开启「陪你用电脑」并同意截图外发，她才看得到游戏画面。
          <button type="button" className={ui.pill} onClick={() => navigate("settings")}>
            去设置
          </button>
        </div>
      )}
      {error && (
        <div className={ui.error} role="alert">
          {error}
        </div>
      )}

      {games === null && <p className={ui.hint}>正在读取…</p>}
      {games?.length === 0 && !adding && (
        <p className={ui.hint}>还没有游戏。先打开你要玩的游戏，再点下面的“添加游戏”。</p>
      )}

      {games?.map((game) => (
        <article key={game.id} className={`${ui.card} ${styles.game}`}>
          <button
            type="button"
            className={styles.head}
            aria-expanded={openId === game.id}
            onClick={() => setOpenId(openId === game.id ? null : game.id)}
          >
            <span className={styles.name}>{game.name}</span>
            <span className={ui.tagAccent}>{roleLabel(game.role)}</span>
            <span className={styles.progress}>
              {game.save_card.progress || game.app_names.join("、")}
            </span>
          </button>
          {openId === game.id && (
            <GameDetail
              game={game}
              roles={roles}
              running={running}
              onChanged={(next) =>
                setGames((current) =>
                  (current ?? []).map((item) => (item.id === next.id ? next : item)),
                )
              }
              onDelete={() => setDeleting(game)}
            />
          )}
        </article>
      ))}

      {adding ? (
        <section className={`${ui.card} ${styles.detail}`}>
          <h3>添加游戏</h3>
          <label className={styles.field}>
            <span>游戏名</span>
            <input
              value={draft.name}
              maxLength={60}
              placeholder="如：杀戮尖塔"
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </label>
          <div className={styles.field}>
            <span>游戏窗口对应的应用</span>
            <AppPicker
              running={running}
              value={draft.app_names}
              onChange={(apps) => setDraft({ ...draft, app_names: apps })}
            />
          </div>
          <label className={styles.field}>
            <span>聊天里的叫法（可选）</span>
            <input
              value={aliasDraft}
              placeholder="如：尖塔、STS——聊天提到这些词时她会想起这个游戏"
              onChange={(event) => setAliasDraft(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span>类型（可选）</span>
            <input
              value={draft.genre}
              maxLength={60}
              placeholder="如：卡牌肉鸽、开放世界、动作"
              onChange={(event) => setDraft({ ...draft, genre: event.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span>屏幕上重点看什么（可选）</span>
            <textarea
              rows={2}
              value={draft.hud_hints}
              maxLength={400}
              placeholder="如：左上血量、右上层数、底部手牌"
              onChange={(event) => setDraft({ ...draft, hud_hints: event.target.value })}
            />
          </label>
          <div className={styles.field}>
            <span>她的陪玩角色（必选）</span>
            <RolePicker
              roles={roles}
              value={draft.role}
              onChange={(role) => setDraft({ ...draft, role })}
            />
          </div>
          <div className={styles.actions}>
            <button type="button" className={ui.pill} onClick={() => setAdding(false)}>
              取消
            </button>
            <button
              type="button"
              className={`${ui.pill} ${ui.primary}`}
              disabled={busy || !draft.name.trim() || !draft.app_names.length || !draft.role}
              onClick={() => void add()}
            >
              添加
            </button>
          </div>
        </section>
      ) : (
        <div className={styles.actions}>
          <button
            type="button"
            className={`${ui.pill} ${ui.primary}`}
            onClick={() => {
              setAdding(true);
              api.getRunningApps().then(setRunning, () => undefined);
            }}
          >
            添加游戏
          </button>
        </div>
      )}

      {deleting && (
        <ConfirmDialog
          title={`删除「${deleting.name}」？`}
          body="会删掉账号档案、里程碑、场次、战报与本机原始资料。可分享的游戏知识文件留在仓库里；她关于这个游戏的长期记忆会保留，转为普通记忆。"
          confirmLabel="删除"
          busy={busy}
          onConfirm={() => void remove(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
