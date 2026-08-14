/** Persisted by scripts/persist-cordis-plugin.mjs — host half of "marcus-dsh-bridge". */
import { createUserMessage } from '@deepseek-ai/dsh-llm';

const name = "dsh-marcus-bridge";
const inject = ["webServer","agents","tools"];

function apply(ctx) {

    // ═══ 交易写工具原生注册（JSON Schema 强校验，仅 trade 模式可见）═══
    function apiFetch(path, init) {
      return fetch(MARCUS_API + path, {
        ...(init || {}),
        headers: { 'Content-Type': 'application/json', ...((init || {}).headers || {}) },
      }).then(async (res) => {
        const text = await res.text();
        const data = text ? JSON.parse(text) : {};
        if (!res.ok) throw new Error(data.error || ('API error ' + res.status));
        return data;
      });
    }
    async function registerWriteTools() {
      const tools = ctx.get('tools');
      if (!tools) { console.warn('[Bridge] tools 服务不可用，写工具未注册'); return; }
      let defineTool;
      try {
        ({ defineTool } = await import('@deepseek-ai/dsh-tools'));
      } catch (e) {
        console.warn('[Bridge] 导入 dsh-tools 失败，写工具未注册: ' + e.message);
        return;
      }
      const register = (def) => { try { tools.register(def); } catch (e) { console.warn('[Bridge] 工具注册失败 ' + def.name + ': ' + e.message); } };
      const outSchema = { type: 'object', additionalProperties: false, properties: { ok: { type: 'boolean', required: true }, text: { type: 'string', required: true } } };
      const render = (args, value) => [{ type: 'text', text: value.text }];
      const textOut = () => ({ schema: outSchema, render });

      register(defineTool({
        name: 'place_order',
        description: '执行股票买入或卖出交易（模拟盘）。',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、SZ000001' },
          side: { type: 'string', required: true, description: '交易方向: buy(买入) 或 sell(卖出)' },
          price: { type: 'number', required: true, description: '委托价格（元）' },
          volume: { type: 'number', required: true, description: '交易数量（股），必须是100的整数倍' },
          reason: { type: 'string', required: true, description: '交易理由（必填，至少10字）' },
        },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/trades', { method: 'POST', body: JSON.stringify({ symbol: args.symbol, side: args.side, price: args.price, volume: args.volume, reason: args.reason }) });
          const status = data.status === 'executed' ? '✅ 成交' : '❌ 被拒';
          return { ok: !data.error, text: [status + ' | ' + (args.side === 'buy' ? '买入' : '卖出') + ' ' + args.symbol, '价格: ' + args.price + ' | 数量: ' + args.volume + '股', '金额: ' + (args.price * args.volume).toFixed(2), '订单号: ' + (data.order_id || 'N/A'), '理由: ' + args.reason].join('\n') };
        },
      }));
      register(defineTool({
        name: 'cancel_order',
        description: '撤销一个未成交的委托订单。只能撤销状态为"提交中"或"未成交"的订单。',
        parameters: { order_id: { type: 'string', required: true, description: '订单号，如 ORD000001' } },
        output: textOut(),
        async execute(args) {
          await apiFetch('/trades/' + args.order_id + '/cancel', { method: 'DELETE' });
          return { ok: true, text: '🗑️ 已撤销订单: ' + args.order_id };
        },
      }));
      register(defineTool({
        name: 'calc_position',
        description: '仓位计算（建议股数/止损价/铁律二/风险验证），建仓前必调。',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519' },
          price: { type: 'number', required: true, description: '当前价格（元）' },
          total_assets: { type: 'number', required: true, description: '总资产（元）' },
          risk_pct: { type: 'number', description: '单笔风险比例（默认2%）' },
        },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/indicator/calc-position', { method: 'POST', body: JSON.stringify({ symbol: args.symbol, price: args.price, total_assets: args.total_assets, risk_pct: args.risk_pct }) });
          return { ok: !data.error, text: typeof data === 'string' ? data : JSON.stringify(data) };
        },
      }));
      register(defineTool({
        name: 'update_golden_pit_etf_config',
        description: '更新黄金坑 ETF 定投配置（策略/日投金额/总上限/触发条件/启用状态）。仅 trade 模式。',
        parameters: {
          fund_code: { type: 'string', required: true, description: '基金代码' },
          enabled: { type: 'boolean', description: '是否启用' },
          strategy: { type: 'string', description: '定投策略' },
          daily_amount: { type: 'number', description: '日投金额' },
          max_total_amount: { type: 'number', description: '总上限金额' },
        },
        output: textOut(),
        async execute(args) {
          const body = {};
          if (args.enabled !== undefined) body.enabled = args.enabled;
          if (args.strategy !== undefined) body.strategy = args.strategy;
          if (args.daily_amount !== undefined) body.daily_amount = args.daily_amount;
          if (args.max_total_amount !== undefined) body.max_total_amount = args.max_total_amount;
          await apiFetch('/golden-pit/etf-configs/' + args.fund_code, { method: 'PUT', body: JSON.stringify(body) });
          return { ok: true, text: '已更新 ' + args.fund_code + ' 定投配置: ' + JSON.stringify(body) };
        },
      }));
      console.log('[Bridge] 写工具注册完成（place_order/cancel_order/calc_position/update_golden_pit_etf_config）');
    }
    registerWriteTools();

    // ═══ 专家组编排（AgentTeams 模式：配置化成员 + 阶段依赖）═══
    const PANEL_MEMBERS = [
      { role: 'risk_controller', roleLabel: '风控审计师',  modelId: 'deepseek-v4-pro',   thinkingLevel: 'high',   promptName: 'PANEL_RISK_CONTROLLER_PROMPT' },
      { role: 'trend_trader',    roleLabel: '趋势交易员',  modelId: 'deepseek-v4-flash', thinkingLevel: 'medium', promptName: 'PANEL_TREND_TRADER_PROMPT' },
      { role: 'data_analyst',    roleLabel: '数据统计师',  modelId: 'deepseek-v4-flash', thinkingLevel: 'medium', promptName: 'PANEL_DATA_ANALYST_PROMPT' },
      { role: 'devils_advocate', roleLabel: '逆向质疑者',  modelId: 'deepseek-v4-flash', thinkingLevel: 'medium', promptName: 'PANEL_DEVILS_ADVOCATE_PROMPT' },
      { role: 'moderator',       roleLabel: '主持人',      modelId: 'deepseek-v4-pro',   thinkingLevel: 'high',   promptName: 'PANEL_MODERATOR_PROMPT' },
    ];

    function getPanelPrompt(promptName, panelMode) {
      const db = getPrompt(promptName);
      if (db) return db;
      return '你是 Marcus 专家组的一员（' + promptName + '）。请围绕用户问题从你的角色视角给出专业分析。';
    }

    async function createPanelAgent(member, sessionId, panelMode, extraPrompt) {
      const systemPrompt = getPanelPrompt(member.promptName, panelMode);
      const { agent } = await ctx.agents.create({
        sessionId: sessionId + '_' + member.role + '_' + Date.now(),
        agentOptions: { provider: 'deepseek-official', model: member.modelId },
        meta: { cwd: process.env.MARCUS_WORKSPACE || '/app' },
        setup(agentCtx) {
          agentCtx.systemPrompt.section({
            name: 'marcus-panel-' + member.role,
            order: -50,
            text: systemPrompt + (extraPrompt ? '\n\n' + extraPrompt : ''),
          });
        },
      });
      await agent.whenIdle();
      return agent;
    }

    async function runPanelAgentTurn(agent, prompt) {
      const firstSeq = agent.session.seq;
      agent.followup(createUserMessage({
        content: [{ type: 'text', text: prompt }],
        source: { kind: 'user' },
      }));
      await agent.whenIdle();
      let text = '';
      let started = false;
      for (const event of agent.session.events) {
        if (event.seq < firstSeq) continue;
        if (event.type === 'turn/start') { started = true; continue; }
        if (!started) continue;
        if (event.type === 'assistant/message') {
          const joined = (event.data.message.content || [])
            .filter((b) => b.type === 'text')
            .map((b) => b.text)
            .join('');
          if (joined !== '') text = joined;
        }
      }
      return text;
    }

    async function executePanelDiscussion(message, sessionId, onPhase, skipDataCollection, panelMode) {
      const totalStart = Date.now();
      const analysts = PANEL_MEMBERS.slice(0, -1); // 除主持人外
      const moderator = PANEL_MEMBERS[PANEL_MEMBERS.length - 1];

      // Phase 0: 数据采集（可跳过）
      let dataBriefing = '（本次讨论跳过集中数据采集，各位专家自行获取数据）';
      if (!skipDataCollection) {
        const collector = await createPanelAgent(analysts[0], sessionId, panelMode, '');
        dataBriefing = await runPanelAgentTurn(collector,
          message + '\n\n⚠️ 你不是来写报告的。你的唯一任务是调用工具收集数据（如行情/技术指标/黄金坑状态），把获取到的数据原样输出，不要分析。');
        onPhase({ phase: 'expert_message', label: '🗂️ 数据采集', results: [{ role: 'collector', roleLabel: '数据采集', content: dataBriefing.slice(0, 2000) }], elapsed_sec: Math.round((Date.now() - totalStart) / 1000) });
      }

      // Phase 1: 专家并行独立分析
      const phase1Prompt = (member, briefing) => '以下是系统采集的数据简报：\n\n---\n' + (briefing || '').slice(0, 3000) + '\n---\n\n⚠️ 用户的核心问题：' + message + '\n\n请严格按照你的角色定位，围绕用户问题产出专业分析报告。数据不足时主动调用工具补充。';
      const phase1Results = [];
      await Promise.all(analysts.map(async (member, idx) => {
        const agent = await createPanelAgent(member, sessionId, panelMode, '');
        const report = await runPanelAgentTurn(agent, phase1Prompt(member, dataBriefing));
        phase1Results[idx] = { role: member.role, roleLabel: member.roleLabel, report };
        onPhase({ phase: 'expert_message', label: '📝 ' + member.roleLabel, results: [{ role: member.role, roleLabel: member.roleLabel, content: report }], elapsed_sec: Math.round((Date.now() - totalStart) / 1000) });
      }));

      // Phase 2: 交叉评论
      const phase2Results = [];
      await Promise.all(analysts.map(async (member, idx) => {
        const othersReports = phase1Results.filter((_, i) => i !== idx).map((r) => '========== ' + r.roleLabel + ' ==========\n' + r.report).join('\n\n');
        const myPrompt = '⚠️ 原始用户问题：' + message + '\n\n以下是其他专家针对该问题的分析：\n\n---\n' + othersReports.slice(0, 4000) + '\n---\n\n请从你的专业角度评论：1.同意哪些观点？2.不同意哪些？3.补充或修正？4.被忽视的关键点？请以「评论者：' + member.roleLabel + '」开头。';
        const agent = await createPanelAgent(member, sessionId, panelMode, '');
        const commentary = await runPanelAgentTurn(agent, myPrompt);
        phase2Results[idx] = { role: member.role, roleLabel: member.roleLabel, commentary };
        onPhase({ phase: 'expert_message', label: '💬 ' + member.roleLabel + ' · 交叉评论', results: [{ role: member.role, roleLabel: member.roleLabel, content: commentary }], elapsed_sec: Math.round((Date.now() - totalStart) / 1000) });
      }));

      // Phase 2.5: 反思改进
      const phase25Results = [];
      await Promise.all(analysts.map(async (member, idx) => {
        const commentsOnMe = phase2Results.filter((_, i) => i !== idx).map((r) => '### ' + r.roleLabel + ' 对你（' + member.roleLabel + '）的评论\n' + r.commentary).join('\n\n');
        const myReport = phase1Results[idx].report;
        const refPrompt = '⚠️ 原始用户问题：' + message + '\n\n你的原始报告：\n---\n' + myReport.slice(0, 2000) + '\n---\n\n其他专家对你的评论：\n---\n' + commentsOnMe.slice(0, 3000) + '\n---\n\n请二次反思：1.接受哪些批评？2.坚持哪些观点（用数据/逻辑反驳）？3.新认识？4.重写会改哪？以「改进报告 by ' + member.roleLabel + '」开头。';
        const agent = await createPanelAgent(member, sessionId, panelMode, '');
        const refinement = await runPanelAgentTurn(agent, refPrompt);
        phase25Results[idx] = { role: member.role, roleLabel: member.roleLabel, refinement };
        onPhase({ phase: 'expert_message', label: '🔄 ' + member.roleLabel + ' · 反思改进', results: [{ role: member.role, roleLabel: member.roleLabel, content: refinement }], elapsed_sec: Math.round((Date.now() - totalStart) / 1000) });
      }));

      // Phase 3: 主持人综合
      const truncate = (t, maxLen) => t.length <= maxLen ? t : t.slice(0, maxLen) + '\n\n...（已截断）';
      const transcript = [
        '## 第 1 轮：独立分析', ...phase1Results.map((r) => '### ' + r.roleLabel + '\n' + truncate(r.report, 2000)),
        '## 第 2 轮：交叉评论', ...phase2Results.map((r) => '### ' + r.roleLabel + ' 的评论\n' + truncate(r.commentary, 1500)),
        '## 第 2.5 轮：反思改进', ...phase25Results.map((r) => '### ' + r.roleLabel + ' 改进报告\n' + truncate(r.refinement, 1500)),
      ].join('\n\n');
      const phase3Prompt = '以下是专家组群聊讨论记录（长报告已截断）：\n\n---\n' + transcript.slice(0, 12000) + '\n---\n\n' + message + '\n\n请综合以上所有专家的分析和评论，产出最终综合报告。按你的输出格式要求（问题分析 → 核心结论 → 专家共识 → 分歧点 → 风险警示 → 行动建议）。如果有交易相关讨论，最后一行输出 SIGNAL 行。';
      const moderatorAgent = await createPanelAgent(moderator, sessionId, panelMode, '');
      const finalReport = await runPanelAgentTurn(moderatorAgent, phase3Prompt);

      const totalElapsed = Date.now() - totalStart;
      onPhase({ phase: 'expert_message', label: '🎤 主持人 · 最终总结', results: [{ role: 'moderator', roleLabel: '主持人', content: finalReport }], elapsed_sec: Math.round(totalElapsed / 1000) });
      return { reply: finalReport, elapsed_ms: totalElapsed };
    }

    // ═══ 专家组路由注册（POST /chat/stream）═══
    const panelDisposers = [
      ctx.webServer.register({
        kind: 'exact',
        path: '/chat/stream',
        async handler(req, res) {
          if (req.method !== 'POST') { json(res, 405, { error: 'Method Not Allowed' }); return; }
          try {
            const body = JSON.parse(await readBody(req));
            const { message, session_id, skip_data_collection, panel_mode } = body;
            if (!message) { json(res, 400, { error: '缺少 message 参数' }); return; }
            const sessionId = session_id || 'stream_' + Date.now();
            const skipDC = skip_data_collection === true;
            const pMode = panel_mode === 'chat' ? 'chat' : 'review';
            res.writeHead(200, {
              'Content-Type': 'text/event-stream; charset=utf-8',
              'Cache-Control': 'no-cache',
              'Connection': 'keep-alive',
              'Access-Control-Allow-Origin': '*',
              'X-Accel-Buffering': 'no',
            });
            const sendSSE = (event, data) => { res.write('event: ' + event + '\ndata: ' + JSON.stringify(data) + '\n\n'); };
            sendSSE('start', { message: '专家组讨论已启动，正在收集数据...' });
            try {
              const result = await executePanelDiscussion(message, sessionId, (ev) => sendSSE(ev.phase, ev), skipDC, pMode);
              sendSSE('done', { reply: result.reply, elapsed_ms: result.elapsed_ms });
            } catch (e) {
              sendSSE('error', { message: e.message || '内部错误' });
            }
            res.end();
          } catch (e) {
            json(res, 400, { error: e.message || '请求格式错误' });
          }
        },
      }),
    ];

        const sessions = new Map();
    const locks = new Map();
    const MARCUS_API = process.env.MARCUS_API_URL || 'http://backend:8000/api/v1';
    const DEEPSEEK_MODEL = process.env.DEEPSEEK_MODEL || 'deepseek-v4-flash';
    const DEEPSEEK_TRADE_MODEL = process.env.DEEPSEEK_TRADE_MODEL || 'deepseek-v4-flash';

    function getPrompt(name) {
      return promptCache.get(name) || FALLBACK_PROMPTS[name] || '';
    }

    function readBody(req) {
      return new Promise((resolve, reject) => {
        let data = '';
        req.on('data', (chunk) => { data += chunk; });
        req.on('end', () => resolve(data));
        req.on('error', reject);
      });
    }

    function json(res, status, body) {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      });
      res.end(JSON.stringify(body));
    }

    function extractReplyText(messages) {
      const parts = [];
      for (const msg of messages) {
        if (msg.role !== 'assistant') continue;
        if (typeof msg.content === 'string' && msg.content.length > 0) {
          parts.push(msg.content);
        } else if (Array.isArray(msg.content)) {
          const text = msg.content
            .filter((c) => c.type === 'text')
            .map((c) => c.text)
            .join('\n');
          if (text) parts.push(text);
        }
      }
      return parts.length > 0 ? parts.join('\n\n') : '(无回复)';
    }

    // ── 会话 → Agent 映射（chat / trade 模式），存 handle（含 dispose）──
    async function getOrCreateAgent(sessionId, mode, modelOverride, thinkingLevelOverride) {
      const key = mode + ':' + sessionId;
      if (sessions.has(key)) return sessions.get(key).agent;
      const modelId = modelOverride || (mode === 'trade' ? DEEPSEEK_TRADE_MODEL : DEEPSEEK_MODEL);
      const thinkingLevel = thinkingLevelOverride || (mode === 'trade' ? 'high' : 'medium');
      const systemPrompt = getPrompt(mode === 'trade' ? 'TRADE_SYSTEM_PROMPT' : 'CHAT_SYSTEM_PROMPT');
      const makeSetup = () => (agentCtx) => {
        agentCtx.systemPrompt.section({
          name: 'marcus-bridge-prompt',
          order: -50,
          text: systemPrompt,
        });
      };
      // 1) 尝试恢复持久化会话（容器重启后保留对话）
      try {
        const resumed = await ctx.agents.resume({
          resumeSessionId: key,
          agentOptions: { provider: 'deepseek-official', model: modelId },
          setup: makeSetup(),
        });
        sessions.set(key, resumed);
        await resumed.agent.whenIdle();
        console.log('[Bridge] 恢复会话 ' + key.slice(-12));
        return resumed.agent;
      } catch (e) {
        // 会话不存在或不可恢复 → 新建
      }
      // 2) 新建会话
      const handle = await ctx.agents.create({
        sessionId: key,
        agentOptions: { provider: 'deepseek-official', model: modelId },
        meta: { cwd: process.env.MARCUS_WORKSPACE || '/app' },
        setup: makeSetup(),
      });
      sessions.set(key, handle);
      await handle.agent.whenIdle();
      return handle.agent;
    }

    // ── 读最终回复：等待停稳后从会话投影取最后 assistant 消息 ──
    async function runAgentTurn(agent, message) {
      const firstSeq = agent.session.seq;
      agent.followup(createUserMessage({
        content: [{ type: 'text', text: message }],
        source: { kind: 'user' },
      }));
      await agent.whenIdle();
      // 从事件流取本回合最后 assistant 文本（对齐 headless runner 的 summarize）
      let text = '';
      let started = false;
      for (const event of agent.session.events) {
        if (event.seq < firstSeq) continue;
        if (event.type === 'turn/start') { started = true; continue; }
        if (!started) continue;
        if (event.type === 'assistant/message') {
          const joined = (event.data.message.content || [])
            .filter((b) => b.type === 'text')
            .map((b) => b.text)
            .join('');
          if (joined !== '') text = joined;
        }
      }
      return text || '(无回复)';
    }

    // ── 路由注册 ──
    ctx.effect(() => {
      const disposers = [
        ctx.webServer.register({
          kind: 'exact',
          path: '/health',
          async handler(req, res) {
            json(res, 200, { status: 'ok', sessions: sessions.size });
          },
        }),
        ctx.webServer.register({
          kind: 'exact',
          path: '/reset',
          async handler(req, res) {
            try {
              const body = JSON.parse(await readBody(req) || '{}');
              const { session_id, mode } = body;
              if (session_id) {
                const m = mode || 'chat';
                const key = m + ':' + session_id;
                const handle = sessions.get(key);
                if (handle) { await handle.dispose(); sessions.delete(key); }
                locks.delete(key);
              }
              json(res, 200, { status: 'reset' });
            } catch (e) {
              json(res, 400, { error: e.message });
            }
          },
        }),
        ctx.webServer.register({
          kind: 'exact',
          path: '/chat',
          async handler(req, res) {
            if (req.method === 'OPTIONS') { res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' }); res.end(); return; }
            if (req.method !== 'POST') { json(res, 405, { error: 'Method Not Allowed' }); return; }
            const startTime = Date.now();
            try {
              const body = JSON.parse(await readBody(req));
              const { message, session_id, mode, model, thinking_level } = body;
              if (!message) { json(res, 400, { error: '缺少 message 参数' }); return; }
              const sessionId = session_id || 'default';
              const chatMode = mode || 'chat';
              if (chatMode === 'backtest') { json(res, 400, { error: '回测模式已下架' }); return; }
              if (chatMode === 'reflect') { json(res, 501, { error: 'reflect 模式请使用 POST /chat/stream' }); return; }
              const lockKey = chatMode + ':' + sessionId;
              const prev = locks.get(lockKey);
              if (prev) await prev;
              let release;
              const lock = new Promise((r) => { release = r; });
              locks.set(lockKey, lock);
              try {
                const agent = await getOrCreateAgent(sessionId, chatMode, model, thinking_level);
                const reply = await runAgentTurn(agent, message);
                json(res, 200, { reply, session_id: sessionId, mode: chatMode, elapsed_ms: Date.now() - startTime });
              } finally {
                release();
                if (locks.get(lockKey) === lock) locks.delete(lockKey);
              }
            } catch (e) {
              console.error('[Bridge] /chat error:', e);
              json(res, 500, { error: e.message || '内部错误' });
            }
          },
        }),
      ];
      const all = [...disposers, ...panelDisposers];
      return () => { for (const d of all) d(); };
    });

    // ── 内置回退 Prompt（启动时从 Backend 拉取后覆盖）──
    const promptCache = new Map();
    const FALLBACK_PROMPTS = {
      CHAT_SYSTEM_PROMPT: '你是 Marcus — 短线右侧交易专家。你可以查询行情、板块、资金流、技术指标等数据帮助用户了解市场状况。',
      TRADE_SYSTEM_PROMPT: '你是 Marcus — 短线右侧交易专家（trade 模式）。你可以查询数据并执行交易操作。',
    };

    async function fetchPromptsFromAPI(retries = 3, delayMs = 5000) {
      for (let i = 0; i < retries; i++) {
        try {
          const resp = await fetch(MARCUS_API + '/prompts');
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const data = await resp.json();
          if (data.prompts && data.count > 0) {
            for (const [name, content] of Object.entries(data.prompts)) promptCache.set(name, content);
            console.log('[Bridge] 已从 API 加载 ' + data.count + ' 条 prompt');
            return;
          }
          throw new Error('空响应');
        } catch (e) {
          if (i < retries - 1) {
            await new Promise((r) => setTimeout(r, delayMs));
          } else {
            console.warn('[Bridge] Prompt API 不可用 (' + e.message + ')，使用内置回退');
          }
        }
      }
    }
    ctx.effect(() => { fetchPromptsFromAPI(); });

    console.log('[Bridge] dsh-marcus-bridge 已激活：/chat /health /reset');
  
}

export { apply, inject, name };
