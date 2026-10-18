import assert from "node:assert/strict";
import test from "node:test";
import { sectionStatus } from "../src/components/settings/sectionStatus.ts";
import type { LLMConfig, WeChatStatus } from "../src/types.ts";

const llm = (change: Partial<LLMConfig>) =>
  ({ provider: "deepseek", provider_label: "DeepSeek", has_api_key: true, ...change }) as LLMConfig;
const wechat = (change: Partial<WeChatStatus>) =>
  ({ enabled: true, status: "connected", owner_paired: true, ...change }) as WeChatStatus;

test("sections whose config has not loaded show no badge", () => {
  for (const id of ["model", "abilities", "wechat", "voice", "advanced"] as const) {
    assert.equal(sectionStatus(id, {}), null);
  }
});

test("model badge distinguishes mock, a missing key and a ready provider", () => {
  assert.deepEqual(sectionStatus("model", { llm: llm({ provider: "mock" }) }), {
    text: "Mock 联调",
    tone: "warn",
  });
  assert.deepEqual(sectionStatus("model", { llm: llm({ has_api_key: false }) }), {
    text: "缺少 API Key",
    tone: "warn",
  });
  assert.deepEqual(sectionStatus("model", { llm: llm({}) }), { text: "DeepSeek", tone: "ok" });
});

test("wechat badge walks through off, error, unpaired, connected and proactive", () => {
  assert.equal(sectionStatus("wechat", { wechat: wechat({ enabled: false }) })?.tone, "off");
  assert.equal(sectionStatus("wechat", { wechat: wechat({ status: "error" }) })?.text, "连接异常");
  assert.equal(
    sectionStatus("wechat", { wechat: wechat({ owner_paired: false }) })?.text,
    "已连接 · 未绑定",
  );
  assert.equal(sectionStatus("wechat", { wechat: wechat({}) })?.text, "已连接");
  assert.equal(
    sectionStatus("wechat", { wechat: wechat({}), proactive: { enabled: true } as never })?.text,
    "已连接 · 会主动找你",
  );
});

test("abilities badge counts the switches that are on", () => {
  assert.deepEqual(
    sectionStatus("abilities", {
      files: { files_enabled: true } as never,
      screen: { screen_enabled: false } as never,
      companion: { enabled: true } as never,
      proactive: { enabled: false } as never,
    }),
    { text: "已打开 2 项", tone: "ok" },
  );
  assert.deepEqual(sectionStatus("abilities", { files: { files_enabled: false } as never }), {
    text: "都没打开",
    tone: "off",
  });
});
