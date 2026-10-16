import { useEffect, useState } from "react";
import { api } from "../api";
import type { Route } from "../app/route";
import type { CompanionConfig, Game, GameDraft, GameRole, GameRoleId } from "../types";
import ConfirmDialog from "../ui/ConfirmDialog";
import GameDetail from "./GameDetail";
import { AppPicker, RolePicker } from "./GamePickers";
import { errorText, splitNames } from "./gameHelpers";
import ui from "../ui/ui.module.css";
import styles from "./GamesScreen.module.css";

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

/** 游戏（看屏时自动识别的场景）：游戏档案、陪玩角色、账号档案与里程碑、可分享的游戏知识、战报与这个游戏的记忆。 */
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

  const ready = companion?.consented;
  const roleLabel = (id: GameRoleId) => roles.find((role) => role.id === id)?.label ?? id;

  return (
    <div className={styles.page}>
      <p className={styles.lead}>
        游戏是看屏时自动认出的场景：你让她看一眼时，她认出游戏就会带上这里的档案和知识回答，没见过的游戏会自动加进来。
        让她一直陪着看时（设置里常开，或通话中说“这局陪我看着”），她按你选的角色陪着：战斗里不出声，只在间隙说话。
      </p>
      {companion && !ready && (
        <div className={`${ui.card} ${styles.notice}`}>
          要她一直陪着看，需要先在设置「看屏 · 陪着看」里同意截图外发；只是让她看一眼不需要。
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
            {game.auto_detected && <span className={ui.tag}>自动识别</span>}
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
