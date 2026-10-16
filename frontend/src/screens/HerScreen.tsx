import { useEffect, useState } from "react";
import { api } from "../api";
import type { Route } from "../app/route";
import { usePrefs } from "../prefs/prefs";
import { timeLabel } from "../chat/format";
import { useLive2DStatus } from "../live2d/status";
import { currentSubscription, disablePush, enablePush, pushSupport } from "../pushNotifications";
import CharacterStage from "../stage/CharacterStage";
import type { AppState, DiaryEntry, ProactiveConfig, RelationshipState } from "../types";
import { ChevronIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./HerScreen.module.css";

interface Props {
  appState: AppState | null;
  navigate: (route: Route) => void;
}

const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" });

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export default function HerScreen({ appState, navigate }: Props) {
  const { prefs, setPrefs } = usePrefs();
  const [relationship, setRelationship] = useState<RelationshipState | null>(null);
  const [diary, setDiary] = useState<DiaryEntry[]>([]);
  const [proactive, setProactive] = useState<ProactiveConfig | null>(null);
  const [showDiary, setShowDiary] = useState(false);
  const [reflecting, setReflecting] = useState(false);
  const [notice, setNotice] = useState("");
  const [pushOn, setPushOn] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushNote, setPushNote] = useState("");
  const support = pushSupport();

  useEffect(() => {
    let cancelled = false;
    api.getRelationship().then(
      (value) => !cancelled && setRelationship(value),
      () => undefined,
    );
    api.getDiary(30).then(
      (value) => !cancelled && setDiary(value),
      () => undefined,
    );
    api.getProactiveConfig().then(
      (value) => !cancelled && setProactive(value),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void currentSubscription().then(
      (subscription) => !cancelled && setPushOn(Boolean(subscription)),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function togglePush(enabled: boolean) {
    setPushBusy(true);
    setPushNote("");
    try {
      if (enabled) await enablePush();
      else await disablePush();
      setPushOn(enabled);
    } catch (caught) {
      setPushNote(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPushBusy(false);
    }
  }

  async function testPush() {
    setPushNote("");
    try {
      const result = await api.testPush();
      setPushNote(
        result.ok
          ? "已发出，稍等几秒看看通知。"
          : "推送没有发出去：订阅可能已失效，关掉再打开试试。",
      );
    } catch (caught) {
      setPushNote(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function toggleProactive(enabled: boolean) {
    try {
      setProactive(await api.updateProactiveConfig({ enabled }));
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function reflectNow() {
    setReflecting(true);
    setNotice("");
    try {
      const result = await api.reflectDiary();
      setNotice(result.message);
      setDiary(await api.getDiary(30));
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setReflecting(false);
    }
  }

  const live2d = useLive2DStatus();
  const live2dHint = !live2d
    ? "读取中…"
    : live2d.ready
      ? `已加载 ${live2d.model_url?.split("/").pop() ?? ""}`
      : !live2d.core_present
        ? "缺少 Cubism Core：把 live2dcubismcore.min.js 放进 data/live2d"
        : "缺少模型：把含 .model3.json 的模型文件夹放进 data/live2d";
  const mood = appState?.agent_state.mood;
  const focus = appState?.agent_state.focus;
  const latest = diary[0];

  return (
    <div className={styles.page}>
      <section className={styles.profile}>
        <div className={styles.portrait}>
          <span className={styles.halo} aria-hidden="true" />
          <CharacterStage size="avatar" emotion={mood} label="她的头像，表情随心情变化" />
        </div>
        <h2 className={styles.name}>SANDRONE</h2>
        {(mood || focus) && (
          <div className={styles.mood}>
            {mood && <span className={`${ui.glass} ${styles.moodChip}`}>{mood}</span>}
            {focus && <span className={ui.hint}>{focus}</span>}
          </div>
        )}
      </section>

      {relationship && (
        <section className={ui.card} aria-label="你们之间">
          <div className={styles.cardHead}>
            <span>你们之间</span>
            <span>好感 {percent(relationship.affection)}</span>
          </div>
          <div className={styles.phase}>{relationship.phase}</div>
          <p className={styles.body}>{relationship.guidance}</p>
          <div className={styles.meter} aria-hidden="true">
            <span style={{ width: percent(relationship.affection) }} />
          </div>
          <div className={styles.cardFoot}>
            <span>熟悉 {percent(relationship.familiarity)}</span>
            <span>信任 {percent(relationship.trust)}</span>
          </div>
          {relationship.user_emotion && (
            <p className={ui.hint}>
              她觉得你最近有点{relationship.user_emotion.label}（
              {relationship.user_emotion.source === "llm" ? "从你的话推测" : "从用词粗略判断"}，
              {timeLabel(relationship.user_emotion.observed_at)}）
            </p>
          )}
        </section>
      )}

      <section className={ui.card} aria-label="她的日记">
        <div className={styles.cardHead}>
          <span>
            她愿意给你看的日记{latest ? ` · ${dateFormat.format(new Date(latest.created_at))}` : ""}
          </span>
          {diary.length > 1 && (
            <button
              type="button"
              className={styles.link}
              onClick={() => setShowDiary((value) => !value)}
            >
              {showDiary ? "收起" : `全部 ${diary.length} 篇`}
            </button>
          )}
        </div>
        {latest ? (
          <p className={styles.diary}>“{latest.summary}”</p>
        ) : (
          <p className={styles.body}>她还没有写日记。多聊几轮后她会自己整理。</p>
        )}
        {showDiary && (
          <ol className={styles.diaryList}>
            {diary.slice(1).map((entry) => (
              <li key={entry.id}>
                <span className={ui.hint}>{dateFormat.format(new Date(entry.created_at))}</span>
                <span>{entry.summary}</span>
              </li>
            ))}
          </ol>
        )}
        <div className={styles.cardActions}>
          <button
            type="button"
            className={ui.pill}
            onClick={() => void reflectNow()}
            disabled={reflecting}
          >
            {reflecting ? "她在写…" : "让她现在写一篇"}
          </button>
        </div>
        {notice && <p className={ui.hint}>{notice}</p>}
      </section>

      <section className={ui.list} aria-label="设置">
        <label className={ui.row}>
          <span className={ui.rowText}>
            让她主动找你
            <span className={ui.rowHint}>
              {proactive
                ? `每天最多 ${proactive.daily_max} 条 · ${proactive.quiet_start}–${proactive.quiet_end} 安静`
                : "读取中…"}
            </span>
          </span>
          <input
            type="checkbox"
            role="switch"
            className={ui.switch}
            checked={Boolean(proactive?.enabled)}
            disabled={!proactive}
            onChange={(event) => void toggleProactive(event.target.checked)}
          />
        </label>
        <label className={ui.row}>
          <span className={ui.rowText}>
            文字里保留动作描写
            <span className={ui.rowHint}>关掉后只看她说的话；语音模式下动作交给她的形象</span>
          </span>
          <input
            type="checkbox"
            role="switch"
            className={ui.switch}
            checked={prefs.actionText}
            onChange={(event) => setPrefs({ actionText: event.target.checked })}
          />
        </label>
        <label className={ui.row}>
          <span className={ui.rowText}>
            自动播放她的声音
            <span className={ui.rowHint}>
              {appState?.voice_ready
                ? "每条回复都合成语音，本地合成需要几秒"
                : appState?.voice_message || "本地语音暂不可用"}
            </span>
          </span>
          <input
            type="checkbox"
            role="switch"
            className={ui.switch}
            checked={prefs.autoplay}
            disabled={!appState?.voice_ready}
            onChange={(event) => setPrefs({ autoplay: event.target.checked })}
          />
        </label>
        <label className={ui.row}>
          <span className={ui.rowText}>
            手机通知
            <span className={ui.rowHint}>
              {pushNote ||
                (support === "supported"
                  ? "她主动找你时推送到这台设备"
                  : support === "needs-install"
                    ? "在 Safari 里“添加到主屏幕”后，从主屏幕打开再开启"
                    : "这个浏览器不支持推送通知")}
            </span>
          </span>
          {pushOn && (
            <button type="button" className={ui.pill} onClick={() => void testPush()}>
              试一条
            </button>
          )}
          <input
            type="checkbox"
            role="switch"
            className={ui.switch}
            checked={pushOn}
            disabled={support !== "supported" || pushBusy}
            onChange={(event) => void togglePush(event.target.checked)}
          />
        </label>
        <div className={ui.row} role="note">
          <span className={ui.rowText}>
            Live2D 形象
            <span className={ui.rowHint}>{live2dHint}</span>
          </span>
        </div>
        {(
          [
            ["wallpaper", "壁纸与玻璃"],
            ["memory", "她记得的事"],
            ["games", "陪你玩游戏"],
            ["settings", "模型、微信与工具"],
            ["usage", "API 用量"],
          ] as const
        ).map(([route, label]) => (
          <button key={route} type="button" className={ui.row} onClick={() => navigate(route)}>
            <span className={ui.rowText}>{label}</span>
            <ChevronIcon size={14} className={styles.chevron} />
          </button>
        ))}
      </section>
    </div>
  );
}
