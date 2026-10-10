import { useCallback, useState } from "react";
import { api, streamChat, streamRegenerate, type ChatOptions, type ChatStreamEvent } from "../api";
import type { Message, ToolCallTrace } from "../types";

export interface LiveEvent {
  id: number;
  kind: "thinking" | "tool";
  name?: string;
  arguments?: Record<string, unknown>;
  message?: string;
  preview?: string;
  ok?: boolean;
  done?: boolean;
}

export interface PendingApproval {
  requestId: string;
  name: string;
  arguments: Record<string, unknown>;
  timeoutSeconds: number;
}

interface Handlers {
  onMessages: (messages: Message[]) => void;
  onReply: (assistant: Message) => void;
  /** 重新生成成功后，旧回复的 id */
  onReplaced: (messageId: number) => void;
  onDone: () => void;
}

type Starter = (onEvent: (event: ChatStreamEvent) => void) => Promise<void>;

/** 一轮流式对话的进行中状态：思考/工具事件、写入确认与错误。 */
export function useChatStream({ onMessages, onReply, onReplaced, onDone }: Handlers) {
  const [sending, setSending] = useState(false);
  const [regeneratingId, setRegeneratingId] = useState<number | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  // 正在生成的回复正文：后端逐段推送，先显示出来，最终消息到达后由时间线里的正式消息替换
  const [partial, setPartial] = useState("");
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [error, setError] = useState("");

  const run = useCallback(
    /** 返回 true 表示消息没能进入时间线，调用方应把草稿放回输入框。 */
    async (start: Starter): Promise<boolean> => {
      setSending(true);
      setStartedAt(Date.now());
      setError("");
      setEvents([]);
      setPartial("");
      setApproval(null);
      let nextId = 0;
      const push = (event: Omit<LiveEvent, "id">) => {
        nextId += 1;
        const id = nextId;
        setEvents((current) => [...current, { ...event, id }]);
      };
      // 用户消息一旦写进时间线就不再放回草稿，避免重发出两条。
      let delivered = false;
      let restoreDraft = false;

      try {
        await start((raw) => {
          if (raw.type === "delta") {
            setPartial((current) => current + String(raw.text ?? ""));
          } else if (raw.type === "delta_reset") {
            setPartial("");
          } else if (raw.type === "thinking") {
            push({ kind: "thinking", message: String(raw.message || "正在思考…") });
          } else if (raw.type === "tool_call") {
            const args = (raw.arguments as Record<string, unknown>) || {};
            push({ kind: "tool", name: String(raw.name || ""), arguments: args, done: false });
            if (raw.requires_confirmation && raw.request_id) {
              setApproval({
                requestId: String(raw.request_id),
                name: String(raw.name || ""),
                arguments: args,
                timeoutSeconds: Number(raw.confirmation_timeout_seconds || 180),
              });
              setApprovalBusy(false);
            }
          } else if (raw.type === "approval_result") {
            setApproval(null);
          } else if (raw.type === "tool_result") {
            setEvents((current) => {
              let index = current.length - 1;
              while (index >= 0 && !(current[index].kind === "tool" && !current[index].done))
                index -= 1;
              if (index < 0) return current;
              const next = [...current];
              next[index] = {
                ...next[index],
                done: true,
                ok: Boolean(raw.ok),
                preview: String(raw.preview || ""),
              };
              return next;
            });
          } else if (raw.type === "final") {
            const user = raw.user_message as Message | null;
            const assistant = raw.assistant_message as Message | null;
            const traces = (raw.tool_calls as ToolCallTrace[]) || [];
            const merged = assistant
              ? { ...assistant, tool_calls: traces.length ? traces : assistant.tool_calls }
              : null;
            delivered = true;
            if (typeof raw.replaced_message_id === "number") onReplaced(raw.replaced_message_id);
            onMessages([user, merged].filter((item): item is Message => Boolean(item)));
            if (merged) onReply(merged);
          } else if (raw.type === "error") {
            setError(String(raw.message || "对话失败。"));
            const user = raw.user_message as Message | null;
            if (user) {
              delivered = true;
              onMessages([user]);
            } else restoreDraft = true;
          }
        });
      } catch (caught) {
        restoreDraft = !delivered;
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setSending(false);
        setRegeneratingId(null);
        setEvents([]);
        setPartial("");
        setApproval(null);
        setApprovalBusy(false);
        onDone();
      }
      return restoreDraft;
    },
    [onDone, onMessages, onReplaced, onReply],
  );

  const send = useCallback(
    (content: string, options: ChatOptions) =>
      run((onEvent) => streamChat(content, options, onEvent)),
    [run],
  );

  const regenerate = useCallback(
    async (messageId: number, options: ChatOptions) => {
      setRegeneratingId(messageId);
      await run((onEvent) => streamRegenerate(messageId, options, onEvent));
    },
    [run],
  );

  const answerApproval = useCallback(
    async (approved: boolean) => {
      if (!approval || approvalBusy) return;
      setApprovalBusy(true);
      try {
        const result = await api.approveAgentTool(approval.requestId, approved);
        if (!result.ok) {
          setError(result.message || "写入确认已失效。");
          setApproval(null);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        setApproval(null);
      } finally {
        setApprovalBusy(false);
      }
    },
    [approval, approvalBusy],
  );

  return {
    sending,
    regeneratingId,
    startedAt,
    events,
    partial,
    approval,
    approvalBusy,
    error,
    setError,
    send,
    regenerate,
    answerApproval,
  };
}
