import { useEffect, useState } from "react";
import { api } from "../api";
import type { GameKnowledge, GameKnowledgeResult } from "../types";
import ui from "../ui/ui.module.css";
import styles from "./GamesScreen.module.css";

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

const KIND_LABELS: Record<string, string> = {
  note: "你的笔记",
  web: "网页",
  search: "搜索摘要",
  play: "陪玩时的观察",
  chat: "聊天中记下",
};

function summary(result: GameKnowledgeResult): string {
  const added = result.added.length;
  const failed = result.failed.length;
  const parts = [added ? `新增 ${added} 份资料` : "没有可写进知识的通用内容"];
  if (failed) parts.push(`${failed} 个网页没用上`);
  if (result.account_updated) parts.push("你个人的部分已记进账号档案");
  return `整理好了：${parts.join("，")}。`;
}

/** 游戏知识：规则与高手打法，每个游戏一份仓库里的 Markdown 文件，可分享，不含个人信息；主要由她维护。 */
export default function GameKnowledgePanel({
  gameId,
  gameName,
}: {
  gameId: number;
  gameName: string;
}) {
  const [book, setBook] = useState<GameKnowledge | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [noteTitle, setNoteTitle] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.getGameKnowledge(gameId).then(
      (next) => !cancelled && setBook(next),
      (caught: unknown) => !cancelled && setError(errorText(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  async function run(label: string, action: () => Promise<GameKnowledge | GameKnowledgeResult>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const next = await action();
      setBook(next);
      if ("added" in next) setNotice(summary(next));
      return true;
    } catch (caught) {
      setError(errorText(caught));
      return false;
    } finally {
      setBusy("");
    }
  }

  return (
    <section>
      <h3>游戏知识（可分享）</h3>
      <p className={ui.hint}>
        规则、机制和高手打法，存在仓库的 <code>{book?.file ?? "knowledge/games/"}</code>
        ，不含任何个人信息，可以提交分享给别人。主要由她维护：陪玩结束时把看到的通用规律记进来，聊天里也会随手记；你可以让她查资料或导入笔记（笔记里你个人的部分会记进账号档案）。陪玩和聊到
        {gameName}
        时她只拿相关的几节来用。
        {book?.pending ? ` 还有 ${book.pending} 条正在后台整理。` : ""}
      </p>

      <div className={styles.apps}>
        <input
          value={topic}
          maxLength={80}
          placeholder="让她查什么，如：心脏打法、第四幕路线"
          onChange={(event) => setTopic(event.target.value)}
        />
        <button
          type="button"
          className={`${ui.pill} ${ui.primary}`}
          disabled={Boolean(busy) || !topic.trim()}
          onClick={() =>
            void run("research", () => api.researchGame(gameId, topic.trim())).then(
              (ok) => ok && setTopic(""),
            )
          }
        >
          {busy === "research" ? "她在查资料…约一分钟" : "让她去查"}
        </button>
      </div>

      <details className={styles.more}>
        <summary>导入我的笔记</summary>
        <div className={styles.card}>
          <label className={styles.field}>
            <span>标题（可选）</span>
            <input
              value={noteTitle}
              maxLength={120}
              onChange={(event) => setNoteTitle(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span>内容</span>
            <textarea
              rows={5}
              value={note}
              maxLength={12000}
              placeholder="粘贴攻略、心得或你自己的总结"
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        </div>
        <div className={styles.actions}>
          <button
            type="button"
            className={ui.pill}
            disabled={Boolean(busy) || !note.trim()}
            onClick={() =>
              void run("note", () =>
                api.importGameNote(gameId, noteTitle.trim(), note.trim()),
              ).then((ok) => {
                if (!ok) return;
                setNote("");
                setNoteTitle("");
              })
            }
          >
            {busy === "note" ? "她在整理…" : "交给她整理"}
          </button>
        </div>
      </details>

      {error && (
        <div className={ui.error} role="alert">
          {error}
        </div>
      )}
      {notice && <p className={ui.hint}>{notice}</p>}

      {editing !== null ? (
        <>
          <textarea
            className={styles.book}
            rows={18}
            value={editing}
            maxLength={20000}
            onChange={(event) => setEditing(event.target.value)}
          />
          <div className={styles.actions}>
            <button type="button" className={ui.pill} onClick={() => setEditing(null)}>
              取消
            </button>
            <button
              type="button"
              className={`${ui.pill} ${ui.primary}`}
              disabled={Boolean(busy)}
              onClick={() =>
                void run("save", () => api.updateGameKnowledge(gameId, editing)).then(
                  (ok) => ok && setEditing(null),
                )
              }
            >
              保存知识文件
            </button>
          </div>
        </>
      ) : book?.content ? (
        <>
          <pre className={styles.book}>{book.content}</pre>
          <div className={styles.actions}>
            <button type="button" className={ui.pill} onClick={() => setEditing(book.content)}>
              编辑知识文件
            </button>
          </div>
        </>
      ) : (
        <p className={ui.hint}>
          {book ? "还没有内容。让她查一个主题、导入一份笔记，或者陪你玩一局。" : "正在读取…"}
        </p>
      )}

      {book && book.sources.length > 0 && (
        <details className={styles.more}>
          <summary>原始资料 {book.sources.length} 份</summary>
          <ul className={styles.sessions}>
            {book.sources.map((source) => (
              <li key={source.number}>
                [{source.number}]{" "}
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer noopener">
                    {source.title}
                  </a>
                ) : (
                  source.title
                )}
                <span className={styles.meta}>
                  {" "}
                  · {KIND_LABELS[source.kind] ?? source.kind}
                  {source.pending ? " · 整理中" : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
