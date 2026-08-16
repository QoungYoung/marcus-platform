/**
 * dsh-t-compaction 验证脚本（15 项断言）
 *
 * 验证：插件导出、补丁分发（t-agent-* 会话走做T指令 / 普通会话回退 DSH 默认摘要 /
 * 非 BasicCompactionEngine 实例防御跳过）、LLM 调用包络（purpose/maxTokens）。
 *
 * 运行前提：插件已装入某 profile（如本地 web）：
 *   ~/.dsh/profiles/node_modules/dsh-t-compaction/{package.json,lib/index.js}
 *   且该 profile 的 cordis.patch.yml 含 dsh-t-compaction 行。
 * 运行（cwd 必须是该 profile 根，保证裸模块解析）：
 *   cd ~/.dsh/profiles && node verify-t-compaction.mjs
 *   （本文件实际验证于 C:\Users\Administrator\.dsh\profiles\verify-t-compaction.mjs，
 *     与本副本内容一致）
 */
import { BasicCompactionEngine } from '@deepseek-ai/dsh-compaction-basic';
import { apply, name, inject } from 'dsh-t-compaction';

let failures = 0;
function assert(cond, msg) {
  if (cond) { console.log('  ✅ ' + msg); }
  else { failures += 1; console.error('  ❌ ' + msg); }
}

console.log('插件导出: name=' + name + ' inject=' + JSON.stringify(inject));
assert(name === 'dsh-t-compaction', 'name 正确');
assert(Array.isArray(inject) && !inject.includes('compaction'), 'inject 不再依赖 compaction（非阻塞设计：组合无 host 级引擎时跳过而非阻塞启动）');

// ── 构造真实 BasicCompactionEngine 实例（mock ctx，仅构造所需）──
const mockCtx = {
  on() {},
  get() { return undefined; },
  logger: { info() {}, warn() {} },
  reflect: { provide() {} },
};
const engine = new BasicCompactionEngine(mockCtx, { auto: false, maxTokens: 1024 });

// 替换 llm.stream 为可捕获 options 的 mock
let lastStreamOptions = null;
engine.ctx.llm = {
  async *stream(options) {
    lastStreamOptions = options;
    yield { type: 'block-start', index: 0, blockType: 'text' };
    yield { type: 'text-delta', index: 0, text: '## Symbols and Positions' };
    yield { type: 'block-end', index: 0, block: { type: 'text', text: '## Symbols and Positions' } };
    yield { type: 'finish', reason: { kind: 'stop' } };
  },
};

const input = {
  system: 'mock system',
  tools: [],
  messages: [{ role: 'user', content: [{ type: 'text', text: 'prior conversation' }] }],
};
const tAgent = {
  session: { id: 'chat:t-agent-SH600519', requestHeader: () => ({ config: { provider: 'deepseek-official', model: 'deepseek-v4-flash' } }) },
  options: { provider: 'deepseek-official', model: 'deepseek-v4-flash' },
};
const normalAgent = {
  session: { id: 'chat:default', requestHeader: () => ({ config: { provider: 'deepseek-official', model: 'deepseek-v4-flash' } }) },
  options: { provider: 'deepseek-official', model: 'deepseek-v4-flash' },
};

// ── apply 打补丁（真实实例应通过 instanceof 检查）──
apply({ compaction: engine });

console.log('场景1: t-agent 会话走做T指令');
{
  const r = await engine.summarize(input, tAgent, undefined);
  assert(r.llmStreamCall === true, '返回 llmStreamCall=true');
  assert(Array.isArray(r.summary) && r.summary[0].type === 'text', 'summary 为文本块数组');
  const lastMsg = lastStreamOptions.messages[lastStreamOptions.messages.length - 1];
  const instructionText = lastMsg.content.find((b) => b.type === 'text').text;
  assert(instructionText.includes('## Symbols and Positions'), '使用做T投资指令（含 Symbols and Positions 节）');
  assert(instructionText.includes('## Risk and Compliance State'), '做T指令含风控节');
  assert(lastStreamOptions.purpose === 'compaction', 'LLM 调用 purpose=compaction');
  assert(lastStreamOptions.maxTokens === 1024, 'maxTokens 取自 config');
}

console.log('场景2: 普通会话回退默认摘要');
{
  lastStreamOptions = null;
  const r = await engine.summarize(input, normalAgent, undefined);
  assert(r.llmStreamCall === true, '返回 llmStreamCall=true');
  const lastMsg = lastStreamOptions.messages[lastStreamOptions.messages.length - 1];
  const instructionText = lastMsg.content.find((b) => b.type === 'text').text;
  assert(instructionText.includes('## Primary Request and Intent'), '使用 DSH 默认摘要指令（含 Primary Request and Intent 节）');
  assert(!instructionText.includes('## Symbols and Positions'), '默认指令不含做T专属节');
  assert(r.summary[0].text === '## Symbols and Positions', '返回 mock 摘要文本（默认路径的 LLM 输出）');
}

console.log('场景3: 非 BasicCompactionEngine 实例跳过补丁（防御分支）');
{
  const fake = { summarize() {} };
  let warned = false;
  const oldWarn = console.warn;
  console.warn = (m) => { warned = true; };
  apply({ compaction: fake });
  console.warn = oldWarn;
  assert(warned === true, '输出跳过警告');
  assert(typeof fake.summarize === 'function', 'fake 引擎未被补丁破坏');
}

console.log('场景4: 无 compaction 服务时非阻塞（不抛错，仅警告）——保证 dsh 启动不受影响');
{
  let warned = false;
  const oldWarn = console.warn;
  console.warn = (m) => { warned = true; };
  let threw = false;
  try {
    apply({ get: () => undefined });
  } catch (error) {
    threw = true;
  }
  console.warn = oldWarn;
  assert(threw === false, '无引擎时 apply 不抛错');
  assert(warned === true, '输出"未找到 compaction 服务"警告');
}

console.log(failures === 0 ? '\n全部通过 ✅' : `\n${failures} 项失败 ❌`);
process.exit(failures === 0 ? 0 : 1);
