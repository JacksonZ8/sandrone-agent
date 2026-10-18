import assert from "node:assert/strict";
import test from "node:test";
import { groupTimeline } from "../src/chat/grouping.ts";
import type { Message } from "../src/types.ts";

let nextId = 1;
function message(role: "user" | "assistant", at: string, extra: Partial<Message> = {}): Message {
  return {
    id: nextId++,
    thread_id: "main",
    channel: "web",
    sender_id: "owner",
    chat_id: "web",
    turn_index: 1,
    role,
    content: "…",
    created_at: at,
    provider: "mock",
    model: "mock",
    latency_ms: 0,
    audio_path: "",
    tool_calls: [],
    memory_traces: [],
    initiated: false,
    ...extra,
  };
}
const day = (iso: string) => iso.slice(0, 10);

test("messages split by day and consecutive turns of one speaker are marked", () => {
  const groups = groupTimeline(
    [
      message("user", "2026-10-16T22:00:00Z"),
      message("assistant", "2026-10-16T22:00:05Z"),
      message("assistant", "2026-10-16T22:01:00Z"),
      message("assistant", "2026-10-16T22:30:00Z"),
      message("assistant", "2026-10-17T08:00:00Z"),
      message("user", "2026-10-17T08:00:30Z"),
      message("user", "2026-10-17T08:01:00Z"),
    ],
    day,
  );
  assert.deepEqual(
    groups.map((group) => group.day),
    ["2026-10-16", "2026-10-17"],
  );
  assert.deepEqual(
    groups[0].items.map((item) => item.continued),
    [false, false, true, false],
  );
  // 新的一天第一条不和前一天连在一起
  assert.deepEqual(
    groups[1].items.map((item) => item.continued),
    [false, false, true],
  );
});

test("a message she starts on her own never continues the previous one", () => {
  const [group] = groupTimeline(
    [
      message("assistant", "2026-10-17T08:00:00Z"),
      message("assistant", "2026-10-17T08:00:30Z", { initiated: true }),
    ],
    day,
  );
  assert.equal(group.items[1].initiated, true);
  assert.equal(group.items[1].continued, false);
});
