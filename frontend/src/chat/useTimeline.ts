import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Message } from "../types";

const PAGE_SIZE = 100;
const POLL_MS = 20000;

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

/** 唯一的时间线：分页读取、追加新消息，并定期拉取微信与主动消息（按 id 去重）。 */
export function useTimeline() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const loadingOlder = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getMessages(PAGE_SIZE)
      .then((rows) => {
        if (cancelled) return;
        setMessages(rows);
        setHasMore(rows.length === PAGE_SIZE);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (loading) return;
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const rows = await api.getMessages(30);
        setMessages((current) => {
          const lastId = current.length ? current[current.length - 1].id : 0;
          const fresh = rows.filter((row) => row.id > lastId);
          return fresh.length ? [...current, ...fresh] : current;
        });
      } catch {
        // 后台刷新失败不打断当前对话。
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [loading]);

  const loadOlder = useCallback(async () => {
    if (loadingOlder.current) return;
    const first = messages[0];
    if (!first) return;
    loadingOlder.current = true;
    try {
      const rows = await api.getMessages(PAGE_SIZE, first.id);
      setMessages((current) => [...rows, ...current]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      loadingOlder.current = false;
    }
  }, [messages]);

  const append = useCallback((next: Message[]) => {
    setMessages((current) => {
      const known = new Set(current.map((item) => item.id));
      const fresh = next.filter((item) => !known.has(item.id));
      return fresh.length ? [...current, ...fresh] : current;
    });
  }, []);

  const patch = useCallback((id: number, change: Partial<Message>) => {
    setMessages((current) =>
      current.map((item) => (item.id === id ? { ...item, ...change } : item)),
    );
  }, []);

  const remove = useCallback((id: number) => {
    setMessages((current) => current.filter((item) => item.id !== id));
  }, []);

  return { messages, loading, hasMore, error, loadOlder, append, patch, remove };
}
