/** Persisted by scripts/persist-cordis-plugin.mjs — host half of "marcus-dsh-bridge". */
import { createUserMessage } from '@deepseek-ai/dsh-llm';

const name = "dsh-marcus-bridge";
const inject = ["webServer","agents"];

function apply(ctx) {

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
      const handle = await ctx.agents.create({
        sessionId: key,
        agentOptions: { provider: 'deepseek-official', model: modelId },
        meta: { cwd: process.env.MARCUS_WORKSPACE || '/app' },
        setup(agentCtx) {
          agentCtx.systemPrompt.section({
            name: 'marcus-bridge-prompt',
            order: -50,
            text: systemPrompt,
          });
        },
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
      return () => { for (const d of disposers) d(); };
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
