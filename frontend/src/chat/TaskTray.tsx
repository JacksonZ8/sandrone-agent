import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AgentLive, AgentTask, AgentWorkspace, CodexThread } from "../types";
import ui from "../ui/ui.module.css";
import styles from "./TaskTray.module.css";
import TerminalPanel from "./TerminalPanel";

interface Props {
  /** 变化时立刻重新读取（她刚给出或处理了一张任务卡、一轮对话结束） */
  signal: number;
}

const ACTIVE_POLL_MS = 4000;
const IDLE_POLL_MS = 30000;
const SHOW_FINISHED_MS = 30 * 60 * 1000;

const LIVE_POLL_MS = 1500;
const TOOL_STATUS: Record<string, string> = {
  pending: "…",
  in_progress: "…",
  completed: "✓",
  failed: "✗",
};

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

function isActive(task: AgentTask): boolean {
  return task.status === "proposed" || task.status === "running";
}

function visible(task: AgentTask, now: number): boolean {
  if (isActive(task) || task.workspace) return true; // 改动还没合并或丢弃的卡一直留着
  if (task.status === "expired" || task.status === "cancelled") return false;
  const finished = task.finished_at ? Date.parse(task.finished_at) : 0;
  return now - finished < SHOW_FINISHED_MS;
}

function elapsedLabel(since: string | null, now: number): string {
  if (!since) return "";
  const seconds = Math.max(0, Math.round((now - Date.parse(since)) / 1000));
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function targetLabel(task: AgentTask): string {
  if (task.target === "codex_app") return `接着 Codex App 线程「${task.target_title}」`;
  if (task.parent_id) return `续接任务 #${task.parent_id}（同一个会话）`;
  return "新开一个会话";
}

/** 她提出的子代理任务卡：确认前可以改目标线程，确认后在后台跑，做完显示结果。 */
export default function TaskTray({ signal }: Props) {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [dismissed, setDismissed] = useState<number[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  // 渲染时用的“现在”：随轮询更新，算显示哪些卡片和执行了多久
  const [now, setNow] = useState(() => Date.now());

  const reload = useCallback(async () => {
    try {
      const rows = await api.listAgentTasks(10);
      setTasks(rows);
      setNow(Date.now());
    } catch {
      // 读取失败时保留上一次的卡片，下次轮询再试
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.listAgentTasks(10).then(
      (rows) => {
        if (cancelled) return;
        setTasks(rows);
        setNow(Date.now());
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [signal]);

  const hasActive = tasks.some(isActive);
  useEffect(() => {
    const timer = window.setInterval(
      () => {
        if (document.visibilityState === "visible") void reload();
      },
      hasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    );
    return () => window.clearInterval(timer);
  }, [hasActive, reload]);

  const act = useCallback(
    async (
      id: number,
      action: () => Promise<{ ok: boolean; message: string; task: AgentTask | null }>,
    ) => {
      setBusyId(id);
      setError("");
      try {
        const result = await action();
        if (!result.ok) setError(result.message);
        if (result.task) {
          const updated = result.task;
          setTasks((current) => current.map((item) => (item.id === updated.id ? updated : item)));
        }
      } catch (caught) {
        setError(errorText(caught));
      } finally {
        setBusyId(null);
      }
    },
    [],
  );

  const shown = tasks.filter((task) => visible(task, now) && !dismissed.includes(task.id));
  if (!shown.length && !error) return null;

  return (
    <div className={styles.tray}>
      {shown.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          now={now}
          busy={busyId === task.id}
          onConfirm={() => void act(task.id, () => api.confirmAgentTask(task.id))}
          onCancel={() => void act(task.id, () => api.cancelAgentTask(task.id))}
          onThread={(threadId) =>
            void act(task.id, () => api.setAgentTaskThread(task.id, threadId))
          }
          onPermission={(optionId) =>
            void act(task.id, () => api.answerAgentPermission(task.id, optionId))
          }
          onWorkspace={(action) => {
            if (action === "discard" && !window.confirm("丢弃这次的全部改动？丢弃后找不回来。"))
              return;
            void act(task.id, () => api.resolveAgentWorkspace(task.id, action));
          }}
          onDismiss={() => setDismissed((current) => [...current, task.id])}
        />
      ))}
      {error && (
        <div className={ui.error} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

interface CardProps {
  task: AgentTask;
  now: number;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onThread: (threadId: string) => void;
  onPermission: (optionId: string) => void;
  onWorkspace: (action: "merge" | "discard") => void;
  onDismiss: () => void;
}

function TaskCard({
  task,
  now,
  busy,
  onConfirm,
  onCancel,
  onThread,
  onPermission,
  onWorkspace,
  onDismiss,
}: CardProps) {
  const proposed = task.status === "proposed";
  const running = task.status === "running";
  return (
    <section
      className={`${ui.card} ${proposed ? ui.lifted : ""} ${styles.card}`}
      data-status={task.status}
      aria-label={`子代理任务：${task.title}`}
    >
      <div className={styles.head}>
        <strong>{task.title}</strong>
        <span className={styles.meta}>
          <span className={task.permission === "write" ? ui.tagAccent : ui.tag}>
            {task.permission === "write" ? "可写入" : "只读"}
          </span>
          <span className={ui.tag}>{task.agent_label}</span>
          <span className={ui.hint}>
            {running ? `执行中 · ${elapsedLabel(task.confirmed_at, now)}` : task.status_label}
          </span>
        </span>
      </div>

      {task.spoken_summary && <p className={styles.summary}>{task.spoken_summary}</p>}

      <dl className={styles.fields}>
        <dt>目录</dt>
        <dd>
          <code>{task.cwd}</code>
        </dd>
        <dt>会话</dt>
        <dd>{targetLabel(task)}</dd>
      </dl>

      {task.risks.length > 0 && (
        <ul className={styles.risks}>
          {task.risks.map((risk) => (
            <li key={risk}>{risk}</li>
          ))}
        </ul>
      )}

      <details className={styles.more}>
        <summary>完整任务</summary>
        <p className={styles.pre}>{task.prompt}</p>
      </details>

      {proposed && (task.command === "claude" || task.command === "codex") && (
        <ThreadPicker task={task} busy={busy} onPick={onThread} />
      )}

      {running && task.live && <LivePanel task={task} busy={busy} onPermission={onPermission} />}
      {running && task.terminal && <TerminalPanel taskId={task.id} label={task.agent_label} />}

      {(task.status === "completed" ||
        task.status === "failed" ||
        task.status === "interrupted") && (
        <details className={styles.more} open={task.status !== "completed"}>
          <summary>{task.status === "completed" ? "结果" : "出了什么问题"}</summary>
          <p className={styles.pre}>
            {task.status === "completed" ? task.output : task.error || task.output}
          </p>
        </details>
      )}

      {task.workspace && (
        <WorkspacePanel workspace={task.workspace} busy={busy} onResolve={onWorkspace} />
      )}

      <div className={styles.actions}>
        {task.codex_url && !proposed && (
          <a className={ui.pill} href={task.codex_url}>
            在 ChatGPT 中打开
          </a>
        )}
        {proposed && (
          <>
            <button type="button" className={ui.pill} onClick={onCancel} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className={`${ui.pill} ${ui.primary}`}
              onClick={onConfirm}
              disabled={busy}
            >
              {busy ? "处理中…" : "确认执行"}
            </button>
          </>
        )}
        {running && (
          <button type="button" className={ui.pill} onClick={onCancel} disabled={busy}>
            {busy ? "停止中…" : "停止"}
          </button>
        )}
        {!proposed && !running && !task.workspace && (
          <button type="button" className={ui.pill} onClick={onDismiss}>
            收起
          </button>
        )}
      </div>
    </section>
  );
}

interface PickerProps {
  task: AgentTask;
  busy: boolean;
  onPick: (threadId: string) => void;
}

/** 确认前改成接着 Codex App 里的某个已有线程（或改回新开会话）。 */
function ThreadPicker({ task, busy, onPick }: PickerProps) {
  const [threads, setThreads] = useState<CodexThread[] | null>(null);
  const [choice, setChoice] = useState(task.target === "codex_app" ? task.agent_session_id : "");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (threads) return;
    try {
      setThreads(await api.listCodexThreads());
    } catch (caught) {
      setError(errorText(caught));
    }
  }, [threads]);

  const current = task.target === "codex_app" ? task.agent_session_id : "";
  return (
    <details className={styles.more} onToggle={(event) => event.currentTarget.open && void load()}>
      <summary>接着 Codex App 里的已有线程…</summary>
      {error && <p className={ui.hint}>{error}</p>}
      {threads && (
        <div className={styles.picker}>
          <select
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
            disabled={busy}
          >
            <option value="">不接 App 线程（新开会话）</option>
            {threads.map((thread) => (
              <option key={thread.id} value={thread.id}>
                {thread.recently_active ? "● " : ""}
                {thread.title} — {thread.cwd.split("/").slice(-2).join("/")}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={ui.pill}
            onClick={() => onPick(choice)}
            disabled={busy || choice === current}
          >
            改用这个
          </button>
        </div>
      )}
      <p className={ui.hint}>● 表示两分钟内刚在 App 里更新过，可能还在跑。</p>
    </details>
  );
}

interface LiveProps {
  task: AgentTask;
  busy: boolean;
  onPermission: (optionId: string) => void;
}

/** 执行中的实时过程：计划、工具调用、正在写的回复，以及等你决定的权限请求。 */
function LivePanel({ task, busy, onPermission }: LiveProps) {
  const [live, setLive] = useState<AgentLive | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.getAgentTaskLive(task.id).then(
        (result) => !cancelled && setLive(result.live),
        () => undefined,
      );
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, LIVE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [task.id]);

  if (!live) return <p className={ui.hint}>正在连上 {task.agent_label}…</p>;
  const pending = live.pending_permission;
  const tools = live.tools.slice(-6);
  const denied = live.events.filter((event) => event.kind === "denied").slice(-2);

  return (
    <div className={styles.live} aria-live="polite">
      {pending && (
        <div className={styles.permission} role="alert">
          <strong>
            {task.agent_label} 想：{pending.title}
          </strong>
          <div className={styles.actions}>
            {pending.options.map((option) => (
              <button
                key={option.optionId}
                type="button"
                className={`${ui.pill} ${option.kind.startsWith("allow") ? ui.primary : ""}`}
                onClick={() => onPermission(option.optionId)}
                disabled={busy}
              >
                {option.name}
              </button>
            ))}
          </div>
          <p className={ui.hint}>10 分钟没回复就按拒绝处理。</p>
        </div>
      )}
      {live.plan.length > 0 && (
        <ol className={styles.plan}>
          {live.plan.map((entry, index) => (
            <li key={index} data-status={entry.status}>
              {entry.content}
            </li>
          ))}
        </ol>
      )}
      {tools.length > 0 && (
        <ul className={styles.tools}>
          {tools.map((tool) => (
            <li key={tool.id}>
              <span aria-hidden="true">{TOOL_STATUS[tool.status] ?? "·"}</span> {tool.title}
            </li>
          ))}
        </ul>
      )}
      {denied.map((event) => (
        <p key={event.at} className={ui.hint}>
          {event.text}
        </p>
      ))}
      {live.text && <p className={styles.pre}>{live.text.slice(-600)}</p>}
    </div>
  );
}

interface WorkspaceProps {
  workspace: AgentWorkspace;
  busy: boolean;
  onResolve: (action: "merge" | "discard") => void;
}

/** 写入任务在独立工作区里的改动：看过再合并进你的分支，或丢弃。 */
function WorkspacePanel({ workspace, busy, onResolve }: WorkspaceProps) {
  const target = workspace.target_branch || "当前分支";
  return (
    <div className={styles.workspace}>
      <div className={styles.workspaceHead}>
        <strong>
          {workspace.files.length ? `改了 ${workspace.files.length} 个文件` : "还没有改动"}
        </strong>
        <span className={ui.hint}>在独立工作区里，合并前不会碰你的目录</span>
      </div>
      {workspace.files.length > 0 && (
        <>
          <pre className={styles.diff}>{workspace.stat}</pre>
          <details className={styles.more}>
            <summary>看完整改动</summary>
            <pre className={styles.diff}>{workspace.diff}</pre>
          </details>
        </>
      )}
      <div className={styles.actions}>
        <button
          type="button"
          className={ui.pill}
          onClick={() => onResolve("discard")}
          disabled={busy}
        >
          丢弃改动
        </button>
        {workspace.files.length > 0 && (
          <button
            type="button"
            className={`${ui.pill} ${ui.primary}`}
            onClick={() => onResolve("merge")}
            disabled={busy}
          >
            {busy ? "处理中…" : `合并到 ${target}`}
          </button>
        )}
      </div>
    </div>
  );
}
