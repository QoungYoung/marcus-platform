/**
 * dsh-t-compaction — 做T专用上下文压缩引擎（摘要指令覆盖）
 *
 * 原理（基于 @deepseek-ai/dsh-compaction-basic@0.1.0-rc.6 源码）：
 *   BasicCompactionEngine 的 summarize(input, agent, signal) 是"唯一可覆盖钩子"
 *   （源码注释: "Override this sole hook for a template or remote summarizer"），
 *   所有压缩路径（agent/pre-step 压力、context-overflow 恢复、/compact 手动压缩）
 *   最终都经 regionDependencies() 的 `summarize: (input, owner, abort) =>
 *   this.summarize(...)` 动态分发（lib/index.js 第 957 行），因此对本插件的
 *   实例方法补丁完全生效，无需重注册 compaction 服务。
 *
 * 行为：sessionId 含 "t-agent-"（bridge t_bridge.py 用 `t-agent-{symbol}`，
 *       bridge /chat 映射为 `chat:t-agent-{symbol}`）的会话使用"股票投资信息优先"
 *       的结构化摘要指令；其他会话原样走 DSH 默认摘要（通用编码助手行为）。
 *
 * 部署：写盘 <profile>/node_modules/dsh-t-compaction/（package.json + lib/index.js）
 *       并在 <profile>/{web,service}/cordis.patch.yml 增加
 *       `- insert: [{ id: dsh-t-compaction, name: dsh-t-compaction }]`，重启生效。
 */
import { BasicCompactionEngine } from '@deepseek-ai/dsh-compaction-basic';
import { BlockAssembler, LlmError, contentHasImage, createUserMessage } from '@deepseek-ai/dsh-llm';

const name = 'dsh-t-compaction';
/** 依赖 compaction 服务：保证 apply 时 ctx.compaction（BasicCompactionEngine 实例）已就绪 */
const inject = ['compaction'];

/** 做T 会话识别：t_bridge.py 用 `t-agent-{symbol}`，bridge /chat 键为 `chat:t-agent-{symbol}` */
const T_AGENT_MARKER = 't-agent-';

/** 已打过补丁的引擎实例（防止插件热重载重复包裹） */
const patchedEngines = new WeakSet();

/**
 * 做T 会话的摘要指令：股票投资信息优先。
 * 结构覆盖：标的/持仓、监控条件、触发事件、环境、技术指标、决策与执行、风控状态、下一步。
 * 作为重放对话后的最后一条 user message 追加（复用 provider KV 缓存），
 * 机制与 dsh-compaction-basic 的 COMPACTION_INSTRUCTION 完全一致，仅替换指令文本。
 */
const T_COMPACTION_INSTRUCTION = [
  'You are now acting as a compaction engine for an A-share day-trading (T+0) assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential trading context.',
  '',
  'Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.',
  '',
  '## Symbols and Positions',
  '- [every stock code discussed, with its holding size, average cost, sellable shares, and current P&L% if known]',
  '',
  '## Monitor Conditions (t_conditions)',
  '- [each active condition: symbol, trigger_kind, expression summary (fields/operators/values), sell target, stop loss, armed state]',
  '',
  '## Trigger Events (t_triggers)',
  '- [each trigger: condition id, symbol, event type, trigger price, quote price, suggest bid/ask, status (pending/executed/blocked/cancelled), mode (auto/human)]',
  '',
  '## Market Environment (regime)',
  '- [current regime state (ACTIVE/CAUTIOUS/HALT), index drops (HS300/SH/SZ), gate states, interpret sign, any regime transitions]',
  '',
  '## Technical Indicators',
  '- [key tech.* values when they drove a decision: MACD golden/death cross, KDJ overbought/oversold, RSI, MA relationships, volume-price patterns (放量/缩量/恐慌)]',
  '',
  '## Decisions and Executions',
  '- [each buy/sell decision: symbol, side, price, volume, gateway result, reason; quote the exact reason string]',
  '',
  '## Risk and Compliance State',
  '- [STOP_ALL flag, daily loss breaker, consecutive losses, sellable ledger changes, any escalation (human_confirm) or blocked orders with reasons]',
  '',
  '## User Intent and Preferences',
  '- [the user\'s original and evolving goals for the T account; quote verbatim where exact wording matters, e.g. "放量上涨", "缩量下跌到XX元并企稳"]',
  '',
  '## Pending Jobs and Next Step',
  '- [work explicitly requested but not completed]',
  '- [the single next action, directly in line with the most recent request, or "(none)"]',
  '',
  'Rules:',
  '- Write concise trading-relevant notes. Preserve exact stock codes, prices, volumes, trigger thresholds, stop-loss levels, and order IDs.',
  '- Prioritize numbers and state that affect the next trading decision (sellable shares, stop-loss distance, regime).',
  '- Capture user feedback and explicit instructions faithfully, especially corrections to strategy or conditions.',
  '- Do NOT mention this summarization request or that the context was compacted.',
  '- Output only the checkpoint text: do not call any tool or take any other action.',
  '- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure.',
].join('\n');

/** 做T 会话判断：sessionId 含 t-agent- 即命中（chat:t-agent-XXX 同样命中） */
function isTAgentSession(sessionId) {
  return typeof sessionId === 'string' && sessionId.includes(T_AGENT_MARKER);
}

/** Map a terminal summarization finish to its fail-closed error（对齐 basic 引擎 finishError）。 */
function finishError(finish) {
  switch (finish.kind) {
    case 'error':
    case 'aborted': {
      const error = new Error(finish.failure.message);
      error.code = finish.failure.code;
      return error;
    }
    case 'max-tokens': {
      const error = new Error('summarization truncated at the token cap (incomplete checkpoint)');
      error.code = 'MAX_TOKENS';
      return error;
    }
    default:
      return undefined;
  }
}

/** Reject visual output and keep only text blocks（对齐 basic 引擎 summaryText）。 */
function summaryText(blocks) {
  if (contentHasImage(blocks)) throw new LlmError('compaction summary cannot contain image output', 'UNSUPPORTED_CONTENT');
  return blocks.filter((block) => block.type === 'text');
}

/**
 * 做T 摘要：与 dsh-compaction-basic 的 summarizeWithLlm 同构（缓存复用式
 * ctx.llm.stream 一次调用：重放对话前缀 + 末尾追加做T指令），仅替换指令文本与来源。
 * @param engine - 挂载的 BasicCompactionEngine 实例（提供 ctx 与 config）。
 * @param input - 重放对话前缀（system/tools/messages）。
 * @param agent - 提供路由模型历史、回退模型与会话 id。
 * @param signal - 可选取消信号。
 */
async function summarizeWithTInstruction(engine, input, agent, signal) {
  const { ctx, config } = engine;
  const latest = agent.session.requestHeader()?.config;
  const configured = config.summarizationProvider.length === 0 ? undefined : {
    provider: config.summarizationProvider,
    model: config.summarizationModel,
  };
  const agentTarget = agent.options.provider !== undefined && agent.options.provider.length > 0
    && agent.options.model !== undefined && agent.options.model.length > 0
    ? { provider: agent.options.provider, model: agent.options.model }
    : undefined;
  const target = configured ?? latest ?? agentTarget;
  if (target === undefined) {
    throw new Error('no provider/model available for t-compaction summarization: set both BasicCompactionConfig summarization fields, route one request, or set both AgentOptions fields');
  }

  const assembler = new BlockAssembler();
  const messages = [...input.messages, createUserMessage({
    content: [{ type: 'text', text: T_COMPACTION_INSTRUCTION }],
    source: { kind: 'plugin', plugin: name },
  })];
  const options = {
    provider: target.provider,
    model: target.model,
    messages,
    ...(input.system === undefined ? {} : { system: input.system }),
    ...(input.tools === undefined ? {} : { tools: [...input.tools] }),
    maxTokens: config.maxTokens,
    sessionId: agent.session.id,
    purpose: 'compaction',
    ...(signal === undefined ? {} : { signal }),
  };

  for await (const chunk of ctx.llm.stream(options)) assembler.push(chunk);
  const error = finishError(assembler.finish);
  if (error !== undefined) throw error;
  const rawOutput = assembler.blocks();
  const summary = summaryText(rawOutput);
  if (!summary.some((block) => block.text.trim().length > 0)) {
    throw new Error('summarization produced no text summary content');
  }
  return {
    summary,
    rawOutput,
    llmStreamCall: true,
    provider: options.provider,
    model: options.model,
    maxTokens: config.maxTokens,
    ...(assembler.usage === undefined ? {} : { usage: assembler.usage }),
  };
}

function apply(ctx) {
  const engine = ctx.compaction;
  if (!(engine instanceof BasicCompactionEngine)) {
    console.warn('[t-compaction] ⚠️ ctx.compaction 不是 BasicCompactionEngine 实例，跳过做T摘要覆盖');
    return;
  }
  if (patchedEngines.has(engine)) {
    console.log('[t-compaction] 引擎实例已打过补丁，跳过');
    return;
  }
  const originalSummarize = engine.summarize.bind(engine);
  engine.summarize = async (input, agent, signal) => {
    const sessionId = agent?.session?.id;
    if (isTAgentSession(sessionId)) return summarizeWithTInstruction(engine, input, agent, signal);
    return originalSummarize(input, agent, signal);
  };
  patchedEngines.add(engine);
  console.log('[t-compaction] ✅ 已挂载做T摘要钩子（t-agent-* 会话用投资专用指令，其余保持 DSH 默认）');
}

export { apply, inject, name };
