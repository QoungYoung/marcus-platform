import { readFileSync } from "node:fs";
import { getModel } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai";

// ---- same resolveModel logic as patched index.ts ----
function resolveModel(provider, modelId) {
  const host = (process.env.DEEPSEEK_API_HOST || "").trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const isDeepSeek = provider === "deepseek" || provider === "opencode-go";
  if (!isDeepSeek || !host || host === "api.deepseek.com") {
    return getModel(provider, modelId);
  }
  const openCodeModel = getModel("opencode-go", modelId);
  if (openCodeModel) return openCodeModel;
  const base = getModel("deepseek", modelId);
  const baseUrl = `https://${host}${host.endsWith("/v1") ? "" : "/v1"}`;
  return { ...base, baseUrl };
}

const model = resolveModel("deepseek", "deepseek-v4-flash");
console.log("resolved provider:", model.provider, "| baseUrl:", model.baseUrl, "| id:", model.id);

// realistic 2-turn history with thinking + tool calls + tool results (tests reasoning_content replay)
const history = [
  { role: "user", content: [{ type: "text", text: "科创50 ETF 现在点位如何？" }], timestamp: Date.now() },
  { role: "assistant",
    content: [
      { type: "thinking", thinking: "需要查询科创50行情", thinkingSignature: "reasoning_content" },
      { type: "text", text: "我来查询一下。" },
      { type: "toolCall", id: "call_00_test1", name: "get_daily_kline", arguments: { code: "588000" } },
    ],
    stopReason: "toolUse", timestamp: Date.now() },
  { role: "toolResult", toolCallId: "call_00_test1", toolName: "get_daily_kline",
    content: [{ type: "text", text: '{"close": 1.02}' }], timestamp: Date.now() },
  { role: "user", content: [{ type: "text", text: "我现在从08-05开始定投持有科创50，如果我想在科创50反弹中提高收益，买入哪些etf？" }], timestamp: Date.now() },
];

const context = {
  systemPrompt: "当前时间: 2026-08-10 09:20:00 (周一)\n你是股票投资助手。",
  messages: history,
  tools: [],
};
const started = Date.now();
const s = streamSimple(model, context, { apiKey: process.env.DEEPSEEK_API_KEY, reasoning: "medium", sessionId: "gateway-smoke" });
let textLen = 0, think = 0, err = 0;
for await (const ev of s) {
  if (ev.type === "text_delta") textLen += ev.delta.length;
  else if (ev.type === "thinking_delta") think++;
  else if (ev.type === "error") err++;
}
const result = await s.result();
console.log("elapsed_ms:", Date.now() - started, "| stopReason:", result.stopReason, "| err:", result.errorMessage || "-");
console.log("thinking deltas:", think, "| final text len:", textLen);
