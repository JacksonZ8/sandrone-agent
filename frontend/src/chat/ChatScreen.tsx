import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePrefs } from "../prefs/prefs";
import CallView from "../stage/CallView";
import { useVoiceCall } from "../voice/useVoiceCall";
import type { AppState, Message } from "../types";
import { api } from "../api";
import ConfirmDialog from "../ui/ConfirmDialog";
import ui from "../ui/ui.module.css";
import styles from "./ChatScreen.module.css";
import Composer from "./Composer";
import { dayLabel } from "./format";
import LiveStatus from "./LiveStatus";
import MessageItem from "./MessageItem";
import TaskTray from "./TaskTray";
import { useChatStream } from "./useChatStream";
import { useTimeline } from "./useTimeline";
import { useVoice } from "./useVoice";

interface Props {
  appState: AppState | null;
  onStateChange: () => void;
}

interface DayGroup {
  day: string;
  items: { message: Message; initiated: boolean }[];
}

function groupByDay(messages: Message[]): DayGroup[] {
  const groups: DayGroup[] = [];
  messages.forEach((message) => {
    const initiated = message.role === "assistant" && Boolean(message.initiated);
    const day = dayLabel(message.created_at);
    const last = groups[groups.length - 1];
    if (!last || last.day !== day) groups.push({ day, items: [{ message, initiated }] });
    else last.items.push({ message, initiated });
  });
  return groups;
}

export default function ChatScreen({ appState, onStateChange }: Props) {
  const { prefs, setPrefs } = usePrefs();
  const timeline = useTimeline();
  const { patch } = timeline;
  const voice = useVoice(
    useCallback((id: number, audioPath: string) => patch(id, { audio_path: audioPath }), [patch]),
  );
  const [draft, setDraft] = useState("");
  const [calling, setCalling] = useState(false);
  const [callAvailable, setCallAvailable] = useState(false);
  const [deleting, setDeleting] = useState<Message | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const voiceReady = Boolean(appState?.voice_ready);
  const { speak, toggle, stop: stopVoice, playingId, playing, busyIds } = voice;

  const onReply = useCallback(
    (assistant: Message) => {
      if (prefs.autoplay && voiceReady) void speak(assistant, true);
    },
    [prefs.autoplay, speak, voiceReady],
  );

  // 通话需要本地识别模型与语音合成都就绪
  useEffect(() => {
    let cancelled = false;
    api.getVoiceStatus().then(
      (status) => {
        if (!cancelled) setCallAvailable(status.models_present);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const { remove } = timeline;
  // 任务卡托盘在她给出或处理任务卡、以及每轮对话结束时重新读取
  const [taskSignal, setTaskSignal] = useState(0);
  const bumpTasks = useCallback(() => setTaskSignal((value) => value + 1), []);
  const onStreamDone = useCallback(() => {
    onStateChange();
    bumpTasks();
  }, [bumpTasks, onStateChange]);

  const stream = useChatStream({
    onMessages: timeline.append,
    onReply,
    onReplaced: remove,
    onDone: onStreamDone,
    onTask: bumpTasks,
  });

  const lastId = timeline.messages[timeline.messages.length - 1]?.id ?? 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lastId, stream.sending, stream.events.length, stream.partial.length]);

  const groups = useMemo(() => groupByDay(timeline.messages), [timeline.messages]);

  const replyOptions = useMemo(
    () => ({
      reply_length: prefs.replyLength,
      action_text: prefs.actionText,
    }),
    [prefs.actionText, prefs.replyLength],
  );

  const send = useCallback(async () => {
    const content = draft.trim();
    if (!content) return;
    setDraft("");
    setActionError("");
    const restore = await stream.send(content, replyOptions);
    if (restore) setDraft((current) => current || content);
  }, [draft, replyOptions, stream]);

  const { regenerate } = stream;
  const onRegenerate = useCallback(
    (message: Message) => {
      setActionError("");
      void regenerate(message.id, replyOptions);
    },
    [regenerate, replyOptions],
  );

  const confirmDelete = useCallback(async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await api.deleteMessage(deleting.id);
      remove(deleting.id);
      setDeleting(null);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
      setDeleting(null);
    } finally {
      setDeleteBusy(false);
    }
  }, [deleting, remove]);

  const listen = useCallback(
    (message: Message) => {
      if (playingId === message.id && playing) {
        toggle();
        return;
      }
      void speak(message, true);
    },
    [playing, playingId, speak, toggle],
  );

  const call = useVoiceCall({ replyLength: prefs.replyLength, onTurn: timeline.append });
  const { start: startCall, stop: stopCall } = call;
  const openCall = useCallback(() => {
    stopVoice();
    setCalling(true);
    void startCall();
  }, [startCall, stopVoice]);
  const hangUp = useCallback(() => {
    stopCall();
    setCalling(false);
    onStateChange();
  }, [onStateChange, stopCall]);
  const needsConfig =
    appState && appState.provider !== "mock" && !appState.has_api_key
      ? "尚未配置 API Key：到「她的资料 → 模型、微信与工具」里填写。"
      : "";
  const error = actionError || stream.error || timeline.error || voice.error;
  // 只有最新一条、且回应了上一条用户发言的回复可以重新生成（与后端校验一致）
  const last = timeline.messages[timeline.messages.length - 1];
  const beforeLast = timeline.messages[timeline.messages.length - 2];
  const regenerableId =
    last?.role === "assistant" &&
    beforeLast?.role === "user" &&
    beforeLast.turn_index === last.turn_index
      ? last.id
      : null;

  return (
    <div className={styles.screen}>
      <div className={styles.scroll}>
        <div className={styles.column}>
          {timeline.hasMore && (
            <button
              type="button"
              className={`${ui.pill} ${styles.older}`}
              onClick={() => void timeline.loadOlder()}
            >
              更早的对话
            </button>
          )}
          {timeline.loading && <div className={styles.hint}>正在读取时间线…</div>}
          {!timeline.loading && timeline.messages.length === 0 && (
            <div className={`${ui.card} ${styles.welcome}`}>
              <h2>这里不是新建会话，而是一段持续的对话。</h2>
              <p>她会记得值得记住的事。可以从任何一句开始，比如告诉她你希望她怎么称呼你。</p>
            </div>
          )}

          {groups.map((group) => (
            <section key={group.day} className={styles.day} aria-label={group.day}>
              <div className={`${ui.glass} ${styles.dayChip}`}>{group.day}</div>
              {group.items.map(({ message, initiated }) => (
                <MessageItem
                  key={message.id}
                  message={message}
                  initiated={initiated}
                  showActions={prefs.actionText}
                  voiceReady={voiceReady}
                  synthesizing={busyIds.includes(message.id)}
                  playing={playing && playingId === message.id}
                  onListen={listen}
                  canRegenerate={!stream.sending && message.id === regenerableId}
                  regenerating={stream.regeneratingId === message.id}
                  onRegenerate={onRegenerate}
                  onDelete={setDeleting}
                />
              ))}
            </section>
          ))}

          {stream.sending && (
            <LiveStatus
              events={stream.events}
              startedAt={stream.startedAt}
              partial={stream.partial}
            />
          )}
          <TaskTray signal={taskSignal} />
          {error && (
            <div className={ui.error} role="alert">
              {error}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className={styles.dock}>
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={() => void send()}
          sending={stream.sending}
          replyLength={prefs.replyLength}
          onReplyLength={(replyLength) => setPrefs({ replyLength })}
          callAvailable={callAvailable && voiceReady}
          onCall={openCall}
          warning={needsConfig}
        />
      </div>

      {deleting && (
        <ConfirmDialog
          title="删除这条消息？"
          body={`“${deleting.content.slice(0, 60)}${deleting.content.length > 60 ? "…" : ""}”会从时间线里移除，她之后也不会再从这条对话里回忆起什么；已经记下的事不受影响。`}
          confirmLabel="删除"
          busy={deleteBusy}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}

      {calling && (
        <CallView
          state={call.state}
          partial={call.partial}
          userText={call.userText}
          herText={call.herText}
          userSpeaking={call.userSpeaking}
          muted={call.muted}
          tool={call.tool}
          emotion={call.emotion}
          herLevel={call.herLevel}
          timings={call.timings}
          error={call.error}
          onToggleMute={call.toggleMute}
          onInterrupt={call.interrupt}
          onHangUp={hangUp}
        />
      )}
    </div>
  );
}
