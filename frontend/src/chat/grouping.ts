import type { Message } from "../types";

/** 同一个人连续发言、间隔不超过这么久时并成一组：只在第一条显示头像，间距收紧 */
export const CONTINUE_WITHIN_MS = 3 * 60 * 1000;

export interface TimelineItem {
  message: Message;
  /** 她主动发来的消息（没有对应的用户发言） */
  initiated: boolean;
  /** 紧接着同一个人的上一条 */
  continued: boolean;
}

export interface DayGroup {
  day: string;
  items: TimelineItem[];
}

/** 按日期分组，并标出连续发言；dayOf 把时间戳映射成日期标题（今天 / 昨天 / 具体日期） */
export function groupTimeline(messages: Message[], dayOf: (iso: string) => string): DayGroup[] {
  const groups: DayGroup[] = [];
  let previous: Message | null = null;
  messages.forEach((message) => {
    const initiated = message.role === "assistant" && Boolean(message.initiated);
    const day = dayOf(message.created_at);
    let last = groups[groups.length - 1];
    if (!last || last.day !== day) {
      last = { day, items: [] };
      groups.push(last);
      previous = null;
    }
    const gap = previous
      ? Date.parse(message.created_at) - Date.parse(previous.created_at)
      : Infinity;
    const continued =
      previous !== null &&
      previous.role === message.role &&
      !initiated &&
      gap >= 0 &&
      gap <= CONTINUE_WITHIN_MS;
    last.items.push({ message, initiated, continued });
    previous = message;
  });
  return groups;
}
