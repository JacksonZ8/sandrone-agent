import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AppState } from "../types";

export function useAppState() {
  const [state, setState] = useState<AppState | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setState(await api.getState());
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getState()
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { state, error, refresh };
}

export function statusText(state: AppState | null, error: string): string {
  if (error) return "后端连接失败";
  if (!state) return "连接中…";
  if (state.provider !== "mock" && !state.has_api_key) return "尚未配置 API Key";
  return state.agent_state.mood || "在";
}
