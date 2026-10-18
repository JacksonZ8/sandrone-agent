import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Memory, MemoryPerspective } from "../types";
import ConfirmDialog from "../ui/ConfirmDialog";
import { PencilIcon, SearchIcon, TrashIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./MemoryScreen.module.css";

const TYPE_LABELS: Record<Memory["type"], string> = {
  fact: "事实",
  preference: "偏好",
  relationship: "关系",
  project: "项目",
  event: "经历",
};

const PERSPECTIVES: { key: MemoryPerspective; label: string }[] = [
  { key: "user", label: "关于你" },
  { key: "self", label: "关于她" },
  { key: "shared", label: "我们之间" },
];

function strengthLabel(strength: number): { text: string; faded: boolean; level: number } {
  if (strength >= 0.66) return { text: "常想起", faded: false, level: 3 };
  if (strength >= 0.33) return { text: "偶尔想起", faded: false, level: 2 };
  return { text: "渐渐淡忘", faded: true, level: 1 };
}

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

interface Loaded {
  perspective: MemoryPerspective;
  memories: Memory[];
}

export default function MemoryScreen({ onMemoryChange }: { onMemoryChange: () => void }) {
  const [perspective, setPerspective] = useState<MemoryPerspective>("user");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [query, setQuery] = useState("");
  const [type, setType] = useState<Memory["type"] | "all">("all");
  const [editing, setEditing] = useState<{ id: number; draft: string } | null>(null);
  const [forgetting, setForgetting] = useState<Memory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reload = useCallback(async (target: MemoryPerspective) => {
    const memories = await api.getMemories(target);
    setLoaded({ perspective: target, memories });
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.getMemories(perspective).then(
      (memories) => {
        if (!cancelled) setLoaded({ perspective, memories });
      },
      (caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [perspective]);

  const loading = loaded?.perspective !== perspective;
  const memories = useMemo(() => (loading ? [] : (loaded?.memories ?? [])), [loaded, loading]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return memories.filter(
      (memory) =>
        (type === "all" || memory.type === type) &&
        (!needle || memory.content.toLowerCase().includes(needle)),
    );
  }, [memories, query, type]);
  const editable = perspective === "user";

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setError("");
    try {
      await action();
      await reload(perspective);
      after?.();
      onMemoryChange();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function consolidate() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.consolidateMemories();
      setNotice(
        result.message === "当前没有需要整理的内容。"
          ? "她已经把记忆收好了，暂时没有需要合并或休眠的。"
          : result.message,
      );
      await reload(perspective);
      onMemoryChange();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.controls}>
        <div
          role="group"
          aria-label="记忆视角"
          className={`${ui.segmented} ${styles.perspectives}`}
        >
          {PERSPECTIVES.map((item) => (
            <button
              key={item.key}
              type="button"
              className={ui.segment}
              aria-pressed={perspective === item.key}
              onClick={() => {
                setPerspective(item.key);
                setType("all");
                setEditing(null);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label className={`${ui.glass} ${styles.search}`}>
          <SearchIcon size={16} />
          <span className="visually-hidden">搜索记忆</span>
          <input
            type="search"
            value={query}
            placeholder="搜索她记得的事"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className={styles.types}>
          {(["all", ...Object.keys(TYPE_LABELS)] as (Memory["type"] | "all")[]).map((key) => (
            <button
              key={key}
              type="button"
              className={ui.segment}
              aria-pressed={type === key}
              onClick={() => setType(key)}
            >
              {key === "all" ? "全部" : TYPE_LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className={ui.error} role="alert">
          {error}
        </div>
      )}

      <ul className={styles.list}>
        {loading && <li className={styles.empty}>正在读取…</li>}
        {!loading && visible.length === 0 && (
          <li className={styles.empty}>
            {memories.length === 0
              ? "这里还没有记忆。和她多聊几句，记忆会在后台慢慢沉淀。"
              : "没有符合条件的记忆。"}
          </li>
        )}
        {visible.map((memory) => {
          const strength = strengthLabel(memory.strength);
          const sources = memory.source_message_ids.length;
          return (
            <li key={memory.id} className={`${ui.card} ${styles.item}`} data-faded={strength.faded}>
              <div className={styles.meta}>
                <span className={memory.type === "project" ? ui.tagAccent : ui.tag}>
                  {TYPE_LABELS[memory.type]}
                </span>
                <span className={styles.strength} data-level={strength.level}>
                  {strength.text}
                  {sources > 0 ? ` · 来自 ${sources} 段对话` : ""}
                </span>
                {editable && editing?.id !== memory.id && (
                  <span className={styles.tools}>
                    <button
                      type="button"
                      className={`${ui.iconButton} ${ui.small}`}
                      aria-label="编辑这条记忆"
                      title="编辑"
                      onClick={() => setEditing({ id: memory.id, draft: memory.content })}
                    >
                      <PencilIcon size={16} />
                    </button>
                    <button
                      type="button"
                      className={`${ui.iconButton} ${ui.small} ${styles.forget}`}
                      aria-label="让她忘掉这件事"
                      title="忘掉"
                      onClick={() => setForgetting(memory)}
                    >
                      <TrashIcon size={16} />
                    </button>
                  </span>
                )}
              </div>
              {editing?.id === memory.id ? (
                <div className={styles.edit}>
                  <label className="visually-hidden" htmlFor={`memory-${memory.id}`}>
                    编辑记忆
                  </label>
                  <textarea
                    id={`memory-${memory.id}`}
                    rows={3}
                    value={editing.draft}
                    onChange={(event) => setEditing({ id: memory.id, draft: event.target.value })}
                  />
                  <div className={styles.actions}>
                    <button type="button" className={ui.pill} onClick={() => setEditing(null)}>
                      取消
                    </button>
                    <button
                      type="button"
                      className={`${ui.pill} ${ui.primary}`}
                      disabled={busy || !editing.draft.trim()}
                      onClick={() =>
                        void run(
                          () =>
                            api.updateMemory(memory.id, {
                              content: editing.draft.trim(),
                              importance: memory.importance,
                            }),
                          () => setEditing(null),
                        )
                      }
                    >
                      保存
                    </button>
                  </div>
                </div>
              ) : (
                <div className={styles.content}>{memory.content}</div>
              )}
            </li>
          );
        })}
        {!editable && (
          <li className={styles.footnote}>
            「关于她」和「我们之间」由她自己整理，只能看，不能改。
          </li>
        )}
      </ul>

      <div className={styles.consolidate}>
        <button
          type="button"
          className={ui.pill}
          onClick={() => void consolidate()}
          disabled={busy}
        >
          让她整理一下记忆
        </button>
        <span className={ui.hint} role="status">
          {notice || "合并重复的、让很久没想起的慢慢休眠"}
        </span>
      </div>

      {forgetting && (
        <ConfirmDialog
          title="让她忘掉这件事？"
          body={`“${forgetting.content}”她以后不会再想起它。`}
          confirmLabel="忘掉"
          busy={busy}
          onCancel={() => setForgetting(null)}
          onConfirm={() =>
            void run(
              () => api.deleteMemory(forgetting.id),
              () => setForgetting(null),
            )
          }
        />
      )}
    </div>
  );
}
