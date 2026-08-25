/** Persisted by scripts/persist-cordis-plugin.mjs — host half of "marcus-dsh-bridge". */
import { createUserMessage } from '@deepseek-ai/dsh-llm';

const name = "dsh-marcus-bridge";
const inject = ["webServer","agents","tools"];

// ═══ 可选全局出站代理（web_search / LLM 出站 fetch 共用 undici 全局 dispatcher）═══
// 容器内设 DSH_PROXY_URL（HTTP CONNECT 代理，如 http://user:pass@host:port）后生效；
// HTTP_PROXY/HTTPS_PROXY 可单独覆盖；NO_PROXY 排除内网（backend/postgres 等 docker 服务名）。
// Node 22 内置 undici（EnvHttpProxyAgent 自 undici 6.19）；解析失败自动降级直连。
async function setupGlobalProxy() {
  const proxyUrl = process.env.DSH_PROXY_URL;
  if (!proxyUrl) return;
  try {
    const { setGlobalDispatcher, EnvHttpProxyAgent } = await import('undici');
    setGlobalDispatcher(new EnvHttpProxyAgent({
      httpProxy: process.env.HTTP_PROXY || proxyUrl,
      httpsProxy: process.env.HTTPS_PROXY || proxyUrl,
      noProxy: process.env.NO_PROXY || '127.0.0.1,localhost,backend,postgres,frontend',
    }));
    console.log('[Bridge] 出站代理已启用: ' + proxyUrl);
  } catch (e) {
    console.warn('[Bridge] 出站代理初始化失败，继续直连: ' + e.message);
  }
}
setupGlobalProxy();

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
          const rejectReason = (data.reason || data.message || args.reason || '').toString();
          return { ok: !data.error, text: [status + ' | ' + (args.side === 'buy' ? '买入' : '卖出') + ' ' + args.symbol, '价格: ' + args.price + ' | 数量: ' + args.volume + '股', '金额: ' + (args.price * args.volume).toFixed(2), '订单号: ' + (data.order_id || 'N/A'), '理由: ' + rejectReason].join('\n') };
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

      // ═══ 做T（T+0）自由表达式监控条件工具 ═══
      register(defineTool({
        name: 'list_t_fields',
        description: '查询做T自由表达式监控可用的全部数据字段（行情/量比/分钟线/技术指标/环境/持仓/指数）。Agent 编写监控条件前先查此表，用字段名写表达式。',
        parameters: {
          category: { type: 'string', description: '可选过滤: quote(行情)/vol_ratio(量比)/minute(分钟线)/tech(技术指标)/regime(环境)/position(持仓)/index(指数)。不填返回全部' },
        },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/t/fields');
          let fields = data.fields || [];
          if (args.category) {
            const prefix = args.category + '.';
            fields = fields.filter((f) => f.field.startsWith(prefix));
          }
          const lines = ['📊 做T可监控字段（' + fields.length + ' 个）：', ''];
          fields.forEach((f) => {
            lines.push('• ' + f.field + ' — ' + f.description + ' (' + f.type + ')');
          });
          if (fields.length === 0) {
            lines.push('（无匹配字段，category 可选: quote/vol_ratio/minute/tech/regime/position/index）');
          }
          lines.push('');
          lines.push('表达式示例: {"and":[{"field":"quote.change_pct","op":"<=","value":-1.5},{"field":"vol_ratio","op":">=","value":1.5},{"field":"tech.macd_golden_cross","op":"==","value":true}]}');
          lines.push('支持操作符: and/or/not, > >= < <= == != in not_in between');
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'create_t_condition',
        description: '创建/更新一条做T监控条件。迭代#58g 规范（防呆，不要再出错来纠）：① 字段按腿归属——low_buy 用 target_price(低吸价)、high_sell/high_sell_then_buy_back 用 sell_target_price(高抛目标)；② direction 必填：custom 必须显式 buy/sell（buy=买腿可无底仓建仓、sell=卖腿需有可卖底仓），low_buy=买、high_sell=卖；③ 低吸不要放高抛价；④ 止损 < 成本；⑤ 表达式字段用 list_t_fields 查。表达式只控触发时机，触发后仍走网关风控',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、SZ000001' },
          expression: { type: 'object', additionalProperties: true, required: true, description: '触发条件表达式 JSON，形如 {"and":[{"field":"quote.current","op":"<=","value":98},{"field":"vol_ratio","op":">=","value":1.5}]}。字段名用 list_t_fields 查询；op 支持 > >= < <= == != in not_in between；组合用 and/or/not' },
          trigger_kind: { type: 'string', description: '条件类型（默认 custom）：low_buy(低吸/买)、high_sell(高抛/卖)、high_sell_then_buy_back(高抛接回/卖)、custom(自定义)' },
          direction: { type: 'string', enum: ['buy', 'sell'], description: '执行方向（迭代#58g，必填）：buy=买腿（未建仓时命中按建仓规模买入建仓），sell=卖腿（需有可卖底仓）。custom 未填会按表达式推断，推断不出则拒绝' },
          sell_target_price: { type: 'number', description: '高抛止盈目标价（元）' },
          stop_loss_price: { type: 'number', description: '止损价（元）' },
          vol_ratio_thresh: { type: 'number', description: '量比阈值（默认 1.5，仅无 expression 时用默认逻辑）' },
          regime_gate: { type: 'string', description: '环境闸门 ALLOWED/MANUAL_ONLY/BLOCKED（默认 ALLOWED）' },
          reason: { type: 'string', description: '创建理由（必填，至少10字）' },
        },
        output: textOut(),
        async execute(args) {
          const body = {
            symbol: args.symbol,
            trigger_kind: args.trigger_kind || 'custom',
            expression: args.expression,
            regime_gate: args.regime_gate || 'ALLOWED',
          };
          if (args.direction) body.direction = args.direction;
          if (args.sell_target_price !== undefined) body.sell_target_price = args.sell_target_price;
          if (args.stop_loss_price !== undefined) body.stop_loss_price = args.stop_loss_price;
          if (args.vol_ratio_thresh !== undefined) body.vol_ratio_thresh = args.vol_ratio_thresh;
          const data = await apiFetch('/t/conditions', { method: 'POST', body: JSON.stringify(body) });
          return {
            ok: true,
            text: '✅ 做T监控条件已创建\n条件ID: ' + data.condition_id + '\n标的: ' + args.symbol + '\n方向: ' + (args.direction === 'buy' ? '买（未建仓时命中按建仓规模买入）' : (args.direction === 'sell' ? '卖' : '按类型默认')) + '\n表达式: ' + (data.expression_summary || JSON.stringify(args.expression)) + '\n说明: 触发后仍走网关风控（可卖底仓/跌停/STOP_ALL/限额）；开盘后 TMonitor 每 30s 评估',
          };
        },
      }));

      register(defineTool({
        name: 'list_t_conditions',
        description: '查看当前做T监控条件列表（含表达式摘要/armed 状态/今日触发次数）。',
        parameters: {
          symbol: { type: 'string', description: '可选按代码过滤' },
        },
        output: textOut(),
        async execute(args) {
          const q = args.symbol ? ('?symbol=' + encodeURIComponent(args.symbol)) : '';
          const data = await apiFetch('/t/conditions' + q);
          const conds = data.conditions || [];
          if (conds.length === 0) return { ok: true, text: '📭 暂无做T监控条件。可用 create_t_condition 创建（字段先查 list_t_fields）。' };
          const lines = ['📋 当前做T监控条件（' + conds.length + ' 条）：', ''];
          conds.forEach((c) => {
            const exprSummary = (c.expression && c.expression_summary) ? c.expression_summary : JSON.stringify(c.expression || '');
            const armed = c.armed === 1 ? '🟢 armed' : '⛔ 冷却/已触发';
            const dir = c.direction === 'buy' ? '买' : c.direction === 'sell' ? '卖' : '按类型';
            lines.push('#' + c.id + ' ' + c.symbol + ' [' + c.trigger_kind + '] 方向:' + dir + ' ' + armed + ' 触发' + (c.trigger_count_today || 0) + '次');
            lines.push('   expr: ' + (exprSummary || '默认逻辑'));
            if (c.sell_target_price) lines.push('   高抛目标: ' + c.sell_target_price + ' | 止损: ' + c.stop_loss_price);
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));
      register(defineTool({
        name: 't_no_rebuild_symbols',
        description: '查看/维护「禁重建标的」名单（只减不补等语义，迭代#58h）：名单内的标的不生成/不重建低吸买腿（AI 重建、盘后生成、消费式重建全部跳过），只保留手动配置的卖腿（高抛/破位）。合理决策：某持仓标的不再适合低吸补仓（下跌趋势/破位风险/用户要求只减不补）→ 加入名单并说明理由；低吸闭环恢复（企稳回踩/趋势向上）→ 可移除。',
        parameters: {
          action: { type: 'string', enum: ['list', 'add', 'remove', 'set'], description: 'list=查看；add/remove=增减单只（配 symbol）；set=全量替换（配 symbols）' },
          symbol: { type: 'string', description: 'add/remove 时传入的代码，如 SH515880' },
          symbols: { type: 'array', items: { type: 'string' }, description: 'set 全量名单' },
        },
        output: textOut(),
        async execute(args) {
          const action = args.action || 'list';
          const body = { action };
          if (action === 'add' || action === 'remove') {
            if (!args.symbol) return { ok: true, text: '❌ add/remove 需传 symbol' };
            body.symbol = String(args.symbol).toUpperCase();
          } else if (action === 'set') {
            if (!Array.isArray(args.symbols)) return { ok: true, text: '❌ set 需传 symbols 数组' };
            body.symbols = args.symbols.map((s) => String(s).toUpperCase());
          }
          const data = await apiFetch('/t/build/no-rebuild', { method: 'POST', body: JSON.stringify(body) });
          const syms = (data.symbols || []).join(', ') || '（空）';
          let head = '📛 禁重建标的（只减不补，AI 重建/盘后生成/消费式重建均跳过）';
          if (action !== 'list' && data.ignored && data.ignored.length) head += `\n⚠️ 已忽略非法项: ${data.ignored.join(', ')}`;
          return { ok: true, text: head + '\n『 ' + syms + ' 』\n' + (action === 'list' ? '（list）' : `已 ${action}，改动生效，后续 AI 重建将跳过名单内标的最多补入卖腿`) };
        },
      }));

      register(defineTool({
        name: 'list_t_ai_actions',
        description: '查询做T AI 决策审计（t_ai_actions）：最近 N 条决策（exec/wait/abandon/update_condition/build/review），含理由与网关结果。AI 决策前/复盘时查最近决策，判断是否连续未实质改善。',
        parameters: {
          symbol: { type: 'string', description: '可选按代码过滤，如 SH600519' },
          trade_date: { type: 'string', description: '可选按日期过滤 YYYY-MM-DD，默认今天' },
          limit: { type: 'number', description: '返回条数（默认10）' },
        },
        output: textOut(),
        async execute(args) {
          const qs = new URLSearchParams({ limit: String(args.limit || 10) });
          if (args.symbol) qs.set('symbol', args.symbol);
          if (args.trade_date) qs.set('trade_date', args.trade_date);
          const data = await apiFetch('/t/ai/actions?' + qs);
          const acts = data.actions || [];
          if (acts.length === 0) return { ok: true, text: '📭 暂无 AI 决策记录' };
          const lines = ['🤖 最近 AI 决策（' + acts.length + ' 条）：', ''];
          acts.forEach((a) => {
            const out = a.output || {};
            const gw = a.gateway_result || {};
            const reason = (out.reason || gw.reason || '') || '';
            const gwStatus = gw.status ? (' | 网关: ' + gw.status) : '';
            lines.push('• ' + (a.created_at || '').slice(0, 19) + ' ' + (a.symbol || '') + ' [' + (a.action_type || '') + ']' + gwStatus);
            if (reason) lines.push('    ' + reason.slice(0, 120));
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'run_t_backtest',
        description: '发起做T监控条件历史回测（m5 粒度，单标的多日，防前视）。验证监控条件（表达式/阈值）在历史上触发得准不准、赚不赚钱。参数：symbol、start_date、end_date、conditions（监控条件数组，含 trigger_kind/target_price/vol_ratio_thresh/expression 等）、init_shares（初始底仓股数，默认1000）、review_mode（llm=真实LLM复核/rule=纯规则对照，默认llm）。返回任务 id；任务异步执行，完成后可再调用传入 task_id 查询报告。',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、600519' },
          start_date: { type: 'string', required: true, description: '回测起始日 YYYY-MM-DD' },
          end_date: { type: 'string', required: true, description: '回测截止日 YYYY-MM-DD' },
          conditions: { type: 'array', required: true, description: '监控条件数组。每条含 trigger_kind(low_buy/high_sell/panic_vibrate/high_sell_then_buy_back/custom)、target_price(低吸触发价)、sell_target_price(高抛目标)、stop_loss_price、vol_ratio_thresh(量比阈值,0关闭)、expression(自由表达式JSON,可省)、stabilize_level、armed(默认1)。expression 示例 {"and":[{"field":"quote.current","op":"<=","value":98},{"field":"vol_ratio","op":">=","value":1.5}]}' },
          init_shares: { type: 'number', description: '初始假设底仓股数（默认1000，成本=回测首日价）' },
          review_mode: { type: 'string', description: 'llm(默认,真实LLM复核) 或 rule(纯规则对照)' },
          task_id: { type: 'number', description: '传入已创建任务的 id 时查询该任务报告' },
        },
        output: textOut(),
        async execute(args) {
          if (args.task_id) {
            const detail = await apiFetch('/t/backtest/' + args.task_id);
            const task = detail.task || {};
            const m = (detail.metrics || {}).metrics || {};
            const lines = ['📊 做T回测任务 #' + args.task_id + ' | ' + (task.symbol || '') + ' | ' + (task.status || '')];
            if (task.status === 'completed' && m.total_return_pct !== undefined) {
              lines.push('总收益: ' + m.total_return_pct + '% | 胜率: ' + m.win_rate_pct + '% | 触发: ' + m.trigger_count + ' | 成交: ' + m.executed_count + ' | 拦截: ' + m.blocked_count + ' | 升级人工: ' + m.escalated_human_count);
              lines.push('最大回撤: ' + m.max_drawdown_pct + '% | 买入持有对比: ' + m.buy_hold_return_pct + '% | 成交率: ' + m.execution_rate_pct + '%');
              lines.push('口径差异与完整报告见 /api/v1/t/backtest/' + args.task_id + '/report');
            } else if (task.status === 'running' || task.status === 'pending') {
              lines.push('⏳ 任务执行中，稍后传入 task_id 查询结果');
            } else if (task.status === 'failed') {
              lines.push('❌ 失败: ' + (task.error_message || '未知错误'));
            }
            return { ok: true, text: lines.join('\n') };
          }
          const conds = Array.isArray(args.conditions) ? args.conditions : [];
          const data = await apiFetch('/t/backtest', { method: 'POST', body: JSON.stringify({
            symbol: args.symbol, start_date: args.start_date, end_date: args.end_date,
            conditions: conds, init_shares: args.init_shares || 1000,
            review_mode: args.review_mode || 'llm',
          }) });
          return { ok: true, text: '✅ 做T回测任务已创建 #' + data.task_id + '\n标的: ' + args.symbol + ' | ' + args.start_date + '~' + args.end_date + ' | 条件 ' + conds.length + ' 条\n执行中可稍后传入 task_id 查询报告' };
        },
      }));
      console.log('[Bridge] 写工具注册完成（place_order/cancel_order/calc_position/update_golden_pit_etf_config/list_t_fields/create_t_condition/list_t_conditions/list_t_ai_actions/run_t_backtest）');

      // ═══ 做T底仓建仓工具（t-position-building，走 t 专用后端端点，不直触下单）═══
      register(defineTool({
        name: 'scan_t_candidates',
        description: '扫描做T底仓建仓候选短名单（基于可T质量打分+趋势闸门+风险惩罚）。底仓=T+0弹药，建仓前必查。返回按打分降序的候选与通过状态。',
        parameters: {
          source: { type: 'string', description: '候选来源: pool(既有候选池)/scan(全市场粗筛)。用户指定标的请用 POST /t/build/scan' },
          limit: { type: 'number', description: '返回条数上限（默认20）' },
        },
        output: textOut(),
        async execute(args) {
          const qs = new URLSearchParams({ source: args.source || 'pool', limit: String(args.limit || 20) });
          const data = await apiFetch('/t/build/candidates?' + qs);
          const cands = data.candidates || [];
          if (cands.length === 0) return { ok: true, text: '📭 暂无建仓候选（来源: ' + data.source + '）。可调 POST /t/build/scan 传入用户指定标的。' };
          const lines = ['🎯 做T底仓建仓候选（' + cands.length + ' 只，来源: ' + data.source + '）：', ''];
          cands.forEach((c) => {
            const pass = c.pass_gate ? '✅' : '⛔';
            lines.push(pass + ' ' + c.symbol + ' build_score=' + c.score.toFixed(2) + ' (门槛0.55)');
            lines.push('   可T质量: ' + (c.quality && c.quality.score !== undefined ? c.quality.score.toFixed(2) : 'N/A') + ' | 趋势: ' + ((c.trend && c.trend.note) || 'N/A'));
            if (c.reasons && c.reasons.length) lines.push('   说明: ' + c.reasons.join('；'));
          });
          lines.push('');
          lines.push('建仓规则: 首开新标的需人工确认；单笔≤净值5%；总底仓≤净值55%；冷静期9:45后/午后禁建；单票当日单批。');
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'build_t_position',
        description: '做T底仓建仓（走独立建仓网关校验：熔断/规模/regime/时段/封板/人工升级）。底仓=做T弹药，建仓成交后次日自动生成做T条件。首开新标的或超阈值将升级人工确认。',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、SZ000001' },
          price: { type: 'number', required: true, description: '委托价格（元）' },
          volume: { type: 'number', description: '股数（默认按单笔≤净值5%自动计算，100的整数倍）' },
          reason: { type: 'string', required: true, description: '建仓理由（必填，至少10字，说明选股依据）' },
          skip_timing: { type: 'boolean', description: '跳过回踩/量比/企稳时机确认（仅人工决策时用，默认false）' },
        },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/t/build/position', { method: 'POST', body: JSON.stringify({
            symbol: args.symbol, price: args.price, volume: args.volume, reason: args.reason,
            decision_source: 'agent', skip_timing: !!args.skip_timing,
          }) });
          if (data.status === 'human_confirm') {
            return { ok: true, text: '👤 建仓已升级人工确认（事件 #' + data.event_id + '）\n原因: ' + (data.reason || '') + '\n请在 TAccount 页面或 POST /t/build/events/' + data.event_id + '/confirm 处理。' };
          }
          if (data.status === 'success') {
            return { ok: true, text: '✅ 底仓建仓成交\n标的: ' + args.symbol + ' | 价格: ' + (data.price || args.price) + ' | 数量: ' + (data.volume || args.volume) + '股\n事件: #' + data.event_id + '（次日自动生成做T条件）' };
          }
          return { ok: false, text: '❌ 建仓被拒: ' + (data.reason || '未知原因') + (data.level ? ' (level=' + data.level + ')' : '') };
        },
      }));

      register(defineTool({
        name: 'auto_gen_conditions',
        description: '为做T实盘池/当日建仓标的补生成次日(trade_date=D+1)做T监控条件（低吸=成本×0.98/复归+0.4%/高抛+1.5%/止损-3%）。盘后任务自动执行，也可手动触发。',
        parameters: {},
        output: textOut(),
        async execute() {
          const data = await apiFetch('/t/build/auto-gen', { method: 'POST' });
          return { ok: true, text: '✅ 次日做T条件补生成完成: ' + (data.created || 0) + ' 条' };
        },
      }));

      register(defineTool({
        name: 'rebalance_floors',
        description: '底仓再平衡评估：跌破保留下限(市值<成本50%)的标的转只监控禁高抛；可T质量退化标的降级；评估达标可补建的标的。',
        parameters: {},
        output: textOut(),
        async execute() {
          const data = await apiFetch('/t/build/rebalance', { method: 'POST' });
          const acts = data.actions || [];
          if (acts.length === 0) return { ok: true, text: '✅ 底仓健康，无需再平衡动作' };
          const lines = ['🔄 底仓再平衡评估（' + acts.length + ' 项）：', ''];
          acts.forEach((a) => {
            lines.push('• ' + a.symbol + ' [' + a.action + '] ' + a.reason);
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_floor_overview',
        description: '做T底仓总览：t账户净值、当前底仓市值、三档上限(单笔/单标/总底仓)、regime档位、建仓服务状态。建仓与再平衡前必查。',
        parameters: {},
        output: textOut(),
        async execute() {
          const data = await apiFetch('/t/build/overview');
          const svc = data.service || {};
          return { ok: true, text: [
            '🏦 做T底仓总览（' + data.account_id + '）',
            'regime: ' + data.regime + '（档位: ' + data.tier + '）',
            '净值: ' + Number(data.net_asset || 0).toLocaleString(),
            '当前底仓市值: ' + Number(data.total_floor_value || 0).toLocaleString(),
            '总底仓上限: ' + Number(data.total_floor_cap || 0).toLocaleString() + '（' + (data.net_asset ? Math.round(data.total_floor_value / data.net_asset * 100) : 0) + '%）',
            '单标上限: ' + Number(data.per_symbol_cap || 0).toLocaleString(),
            '单笔上限: ' + Number(data.single_order_cap || 0).toLocaleString(),
            '组合标的上限: ' + (data.max_floor_symbols || '-'),
            '建仓服务: ' + (svc.running ? '🟢 运行中' : '⚪ 未启动') + (svc.last_result ? ' | ' + svc.last_result : ''),
          ].join('\n') };
        },
      }));

      // ═══ 只读行情/持仓/指标查询工具（AI 主导决策的"眼睛"：自主看盘用）═══
      register(defineTool({
        name: 'get_stock_quote',
        description: '查个股实时行情（当前价/涨跌幅/开高低/成交量额/换手/振幅/日内价格分位）。AI 决策前必查现价与量能。',
        parameters: { symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、SZ000001、600519' } },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/market/quote/' + encodeURIComponent(args.symbol));
          return { ok: true, text: [
            '📈 ' + (data.name || args.symbol) + ' 实时行情',
            '现价: ' + data.current + ' | 涨跌: ' + data.change + '（' + data.percent + '%）',
            '昨收: ' + data.last_close + ' | 今开: ' + (data.open ?? '-'),
            '最高: ' + (data.high ?? '-') + ' | 最低: ' + (data.low ?? '-'),
            '成交量: ' + (data.volume ?? '-') + ' | 成交额: ' + (data.amount ?? '-'),
            '换手率: ' + (data.turnover_rate ?? '-') + ' | 振幅: ' + (data.amplitude ?? '-'),
            '日内分位: ' + (data.intraday_percentile ?? '-'),
          ].join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_portfolio_positions',
        description: '查看账户持仓（含持仓量/可卖/成本/现价/浮动盈亏/市值）。决策前必查当前持仓与可卖数量。',
        parameters: {},
        output: textOut(),
        async execute() {
          const data = await apiFetch('/portfolio/positions');
          const list = Array.isArray(data) ? data : (data.positions || data.list || []);
          if (!Array.isArray(list) || list.length === 0) return { ok: true, text: '📭 当前无持仓' };
          const lines = ['💼 当前持仓（' + list.length + ' 只）：', ''];
          list.forEach((p) => {
            const sellable = p.sellable ?? p.available ?? '-';
            lines.push('• ' + (p.symbol || '') + ' ' + (p.name || '') + ' 持仓' + (p.volume ?? '-') + '股 可卖' + sellable + ' 成本' + (p.avg_price ?? '-') + ' 现价' + (p.current_price ?? p.last_price ?? '-') + ' 浮盈' + (p.floating_pnl_pct ?? p.pnl_pct ?? '-') + '%');
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_t_realtime_indicators',
        description: '查个股实时技术指标（MA/MACD/KDJ/RSI）。判断趋势/超买超卖/金叉死叉用。',
        parameters: { symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、600519' } },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/indicator/realtime/' + encodeURIComponent(args.symbol));
          const rt = data.realtime || data;
          return { ok: true, text: [
            '📊 ' + (data.symbol || args.symbol) + ' 实时技术指标',
            '现价: ' + (data.current_price ?? rt.current_price ?? '-'),
            'MACD: DIF ' + (rt.macd_dif ?? '-') + ' DEA ' + (rt.macd_dea ?? '-') + ' BAR ' + (rt.macd_bar ?? '-'),
            'KDJ: K ' + (rt.kdj_k ?? '-') + ' D ' + (rt.kdj_d ?? '-') + ' J ' + (rt.kdj_j ?? '-'),
            'RSI6: ' + (rt.rsi_6 ?? '-') + ' | RSI12: ' + (rt.rsi_12 ?? '-') + ' | RSI24: ' + (rt.rsi_24 ?? '-'),
          ].join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_stock_moneyflow',
        description: '查个股资金流向（主力/大单/中单/小单净流入及占比）。验证主力动向用。',
        parameters: { symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、600519' } },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/market/moneyflow/' + encodeURIComponent(args.symbol));
          return { ok: true, text: [
            '💰 ' + (data.name || args.symbol) + ' 资金流向',
            '现价: ' + (data.price ?? '-') + ' | 涨跌: ' + (data.change_pct ?? '-'),
            '主力净流入: ' + (data.main_net ?? '-') + '（' + (data.main_pct ?? '-') + '%）',
            '大单: ' + (data.lg_net ?? '-') + '（' + (data.lg_pct ?? '-') + '%）',
            '中单: ' + (data.md_net ?? '-') + ' | 小单: ' + (data.sm_net ?? '-'),
          ].join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_market_state',
        description: '查大盘环境状态（市场诊断：指数涨跌/涨跌家数/量能/市场情绪）。判断 regime 与整体环境用。',
        parameters: {},
        output: textOut(),
        async execute() {
          const data = await apiFetch('/market/market-state');
          const ind = data.indicators || {};
          return { ok: true, text: [
            '🌐 市场状态（' + (data.trade_date || '') + '）',
            '状态: ' + (data.label || data.state || '未知'),
            '建议: ' + (data.suggestion || '-'),
            ind && Object.keys(ind).length ? ('指标: ' + JSON.stringify(ind).slice(0, 400)) : '（今日尚未执行盘前诊断）',
          ].join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_stock_technical',
        description: '查个股技术面（MACD/KDJ/RSI/均线等完整技术指标历史序列）。深度技术分析用。',
        parameters: { symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、600519' } },
        output: textOut(),
        async execute(args) {
          const data = await apiFetch('/market/technical/' + encodeURIComponent(args.symbol));
          return { ok: true, text: '🔬 ' + (data.symbol || args.symbol) + ' 技术面（' + (data.count ?? 0) + ' 期）\n' + JSON.stringify(data.data || data, null, 1).slice(0, 600) };
        },
      }));

      register(defineTool({
        name: 'get_intraday_minute',
        description: '查个股分钟K线（1/5/15/30/60分钟，可指定日期）。观察日内走势/量能/分时企稳用。',
        parameters: {
          symbol: { type: 'string', required: true, description: '股票代码，如 SH600519、600519' },
          freq: { type: 'string', description: '周期 1/5/15/30/60 分钟（默认5）' },
        },
        output: textOut(),
        async execute(args) {
          const freq = args.freq || '5';
          const data = await apiFetch('/market/kline/' + encodeURIComponent(args.symbol) + '?freq=' + freq);
          const bars = data.klines || [];
          if (!Array.isArray(bars) || bars.length === 0) return { ok: true, text: '📭 无分钟K线数据' };
          const lines = ['⏱️ ' + (data.symbol || args.symbol) + ' ' + freq + '分钟K线（最近 ' + bars.length + ' 根，倒序）：', ''];
          bars.slice(0, 12).forEach((b) => {
            lines.push('• ' + (b.trade_date || b.time || b.day || '') + ' O' + (b.open ?? '-') + ' H' + (b.high ?? '-') + ' L' + (b.low ?? '-') + ' C' + (b.close ?? '-') + ' V' + (b.vol ?? b.volume ?? '-'));
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_etf_kline',
        description: '查ETF日线K线（日K，Tushare fund_daily 盘后数据）：日期/开高低收/成交量/涨跌幅。用户询问ETF走势、日K、涨跌趋势时用。仅支持ETF代码，格式可为 510050 / SH510050 / SZ159995（与个股 get_daily_kline 区分；个股日K请用其他工具）。',
        parameters: {
          symbol: { type: 'string', required: true, description: 'ETF代码，如 SH510050、510050、SZ159995' },
          count: { type: 'number', description: '返回最近多少根日线（默认20）' },
          period: { type: 'string', description: 'K线周期（默认day，走Tushare fund_daily）；其他周期走雪球源' },
        },
        output: textOut(),
        async execute(args) {
          const period = args.period || 'day';
          const count = args.count && args.count > 0 ? args.count : 20;
          let data;
          try {
            data = await apiFetch('/etf/kline/' + encodeURIComponent(args.symbol) + '?period=' + encodeURIComponent(period) + '&count=' + count);
          } catch (e) {
            return { ok: false, text: '❌ 获取ETF日线失败: ' + (e.message || e) };
          }
          const bars = (data && data.klines) || [];
          if (!Array.isArray(bars) || bars.length === 0) {
            return { ok: true, text: '📭 无日线数据: ' + args.symbol + '（请确认是ETF代码，如 SH510050）' };
          }
          const lines = ['📈 ' + (data.symbol || args.symbol) + ' ETF日线（' + bars.length + ' 根，Tushare fund_daily）：', ''];
          bars.forEach((b) => {
            const chg = (b.change_pct !== undefined && b.change_pct !== null) ? (' ' + (b.change_pct > 0 ? '+' : '') + b.change_pct + '%') : '';
            lines.push('• ' + b.timestamp + ' O' + (b.open ?? '-') + ' H' + (b.high ?? '-') + ' L' + (b.low ?? '-') + ' C' + (b.close ?? '-') + chg);
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));

      register(defineTool({
        name: 'get_t_candidates_summary',
        description: '做T候选扫描摘要：候选池/全市场扫描的可T质量候选短名单（build_score/趋势/理由）。AI 选股时用（pool 优先，scan 补充）。',
        parameters: {
          source: { type: 'string', description: '候选来源 pool（默认，做T候选池）或 scan（全市场扫描）' },
          limit: { type: 'number', description: '返回条数（默认10）' },
        },
        output: textOut(),
        async execute(args) {
          const qs = new URLSearchParams({ source: args.source || 'pool', limit: String(args.limit || 10) });
          const data = await apiFetch('/t/build/candidates?' + qs);
          const cands = data.candidates || [];
          if (cands.length === 0) return { ok: true, text: '📭 无建仓候选（来源: ' + data.source + '）' };
          const lines = ['🎯 做T建仓候选（' + cands.length + ' 只，来源: ' + data.source + '）：', ''];
          cands.forEach((c) => {
            const pass = c.pass_gate ? '✅' : '⛔';
            lines.push(pass + ' ' + c.symbol + ' build_score=' + (c.score ?? 'N/A') + '（门槛0.55）');
            if (c.reasons && c.reasons.length) lines.push('   说明: ' + c.reasons.join('；'));
          });
          return { ok: true, text: lines.join('\n') };
        },
      }));
      console.log('[Bridge] 做T底仓建仓工具注册完成（scan_t_candidates/build_t_position/auto_gen_conditions/rebalance_floors/get_floor_overview）');
      console.log('[Bridge] 只读查询工具注册完成（get_stock_quote/get_portfolio_positions/get_t_realtime_indicators/get_stock_moneyflow/get_market_state/get_stock_technical/get_intraday_minute/get_t_candidates_summary/get_etf_kline）');
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

const SESSION_TTL_MS = 45 * 60 * 1000;  // 会话空闲 TTL：45 分钟回收（防僵尸 agent）
const SESSION_CHAT_TTL_MS = 30 * 24 * 60 * 60 * 1000;  // QQ 对话等长期上下文：30 天（用户要求保留）
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

    // ── 会话 → Agent 映射（chat / trade / backtest 模式），存 handle（含 dispose）──
    // 会话键 ASCII 化（修复中文 session_id 导致 DSH agent 空回复 (无回复)：
    // 中文键在 agent resume/持久化路径异常，followup 后无 assistant 消息）
    function asciiSessionId(sid) {
      return String(sid || '').replace(/[^\x20-\x7e]/g, (ch) => '_u' + ch.codePointAt(0).toString(16));
    }

    // 会话 setup 构造（参数化：mode/sessionId 派生角色与工具白名单）
    function makeAgentSetup(mode, sessionId) {
      const isTrade = mode === 'trade';
      const isTAgentSession = String(sessionId || '').includes('t-agent-');
      const isConditionsMode2 = mode === 'conditions';
      const isBacktestReview = mode === 'backtest';
      const systemPrompt = getPrompt(mode === 'trade' ? 'TRADE_SYSTEM_PROMPT' : 'CHAT_SYSTEM_PROMPT');
      const tBuildFull = getPrompt('T_BUILD_SYSTEM_PROMPT');
      const tBuildPrompt = (isTAgentSession && !isConditionsMode2) ? getPrompt('T_BUILD_SYSTEM_PROMPT') : '';
      const CONDITIONS_SYSTEM_PROMPT = [
        '你是做T条件设定器（纯输出模式）。',
        '你的任务只有一个：根据给定的标的、成本、振幅等信息，输出做T触发条件数组（JSON）。',
        '条件组合由你自主决定（通常 low_buy + high_sell_then_buy_back 各一，可加减，1~4 条），不要冗余重复。',
        '工具使用：你被放行 8 个只读查询工具（get_stock_quote/get_t_realtime_indicators/',
        'get_intraday_minute/get_stock_moneyflow/get_market_state/get_stock_technical/',
        'get_portfolio_positions/get_t_candidates_summary）。当给定信息不足以设定合理条件时，',
        '请按需调用查询工具补数（如现价/振幅/趋势/资金流存疑）；信息足够则直接输出，不必强行调用。',
        '权限边界由系统控制，你只能使用上述查询工具——不要尝试其他工具。',
        '输出格式（不要 markdown 代码块、不要任何其他文字）：',
        '[{"trigger_kind":"low_buy","target_price":..,"sell_target_price":..,"stop_loss_price":..,"vol_ratio_thresh":..,"stabilize_level":"..","reason":"一句话"},{"trigger_kind":"high_sell_then_buy_back","target_price":..,"sell_target_price":..,"stop_loss_price":..,"vol_ratio_thresh":..,"reason":"一句话"}]',
      ].join('\n');
      return (agentCtx) => {
        const makeSetup = () => (agentCtx) => {
          agentCtx.systemPrompt.section({
            name: 'marcus-bridge-prompt',
            order: -50,
            text: isConditionsMode2 ? CONDITIONS_SYSTEM_PROMPT
              : ((isTrade && isTAgentSession) ? tBuildFull : systemPrompt),
          });
          if (tBuildPrompt && !(isTrade && isTAgentSession) && !isConditionsMode2) {
            agentCtx.systemPrompt.section({
              name: 'marcus-t-build-prompt',
              order: -49,
              text: tBuildPrompt,
            });
          }
          if (isConditionsMode2) {
            // 条件生成：白名单模式（迭代#55c，用户要求"只能用放行的工具"）——
            // 只放行 8 个只读查询工具（AI 可查行情做参考），禁一切文件/探索/写工具
            try {
              if (agentCtx.tools && typeof agentCtx.tools.restrict === 'function') {
                agentCtx.tools.restrict({
                  allow: [
                    'get_stock_quote', 'get_t_realtime_indicators', 'get_intraday_minute',
                    'get_stock_moneyflow', 'get_market_state', 'get_stock_technical',
                    'get_portfolio_positions', 'get_t_candidates_summary',
                  ],
                });
                console.log('[Bridge] 条件生成会话已启用白名单（仅 8 个查询工具）');
              }
            } catch (e) {
              console.warn('[Bridge] 条件生成工具隔离失败: ' + e.message);
            }
          }
          if (isTrade && isTAgentSession) {
            // 做T决策会话（trade/t-agent-*）：白名单模式（迭代#55c）——
            // 放行：8 查询工具 + 条件管理（list_t_conditions/list_t_ai_actions/list_t_fields/
            // create_t_condition）+ 建仓工具（scan_t_candidates/get_floor_overview/
            // build_t_position/auto_gen_conditions/rebalance_floors）。
            // 禁：bash/grep/glob/read/write/edit/web_search 等通用探索工具、
            //     place_order/cancel_order（做T走网关不直下）、run_t_backtest（用户主动工具）、
            //     update_golden_pit_etf_config（无关）、calc_position（做T用 build 网关）。
            // 注意：allow 名单必须覆盖"做T真正需要"的全部工具，否则 AI 会被静默剥夺能力。
            try {
              if (agentCtx.tools && typeof agentCtx.tools.restrict === 'function') {
                agentCtx.tools.restrict({
                  allow: [
                    'get_stock_quote', 'get_t_realtime_indicators', 'get_intraday_minute',
                    'get_stock_moneyflow', 'get_market_state', 'get_stock_technical',
                    'get_portfolio_positions', 'get_t_candidates_summary',
                    'list_t_conditions', 'list_t_ai_actions', 'list_t_fields', 'create_t_condition',
                    't_no_rebuild_symbols',
                    'scan_t_candidates', 'get_floor_overview', 'build_t_position',
                    'auto_gen_conditions', 'rebalance_floors',
                  ],
                });
                console.log('[Bridge] 做T决策会话已启用白名单（查询+条件+建仓）');
              }
            } catch (e) {
              console.warn('[Bridge] 做T决策工具隔离失败: ' + e.message);
            }
          }
          if (isBacktestReview) {
            agentCtx.systemPrompt.section({
              name: 'marcus-backtest-review',
              order: -48,
              text: BACKTEST_REVIEW_PROMPT,
            });
            try {
              if (agentCtx.tools && typeof agentCtx.tools.restrict === 'function') {
                agentCtx.tools.restrict({ deny: BACKTEST_DENY_TOOLS });
                console.log('[Bridge] 回测复核会话已隔离生产写工具（restrict deny）');
              }
            } catch (e) {
              console.warn('[Bridge] 回测工具隔离失败: ' + e.message);
            }
          }
        };
      };
    }

    async function getOrCreateAgent(sessionId, mode, modelOverride, thinkingLevelOverride) {
      const key = mode + ':' + asciiSessionId(sessionId);
      // 会话 TTL（修复僵尸 agent：长期空闲的 agent 底层运行循环可能退出，
      // followup 不唤醒 → whenIdle 立即返回空 → 盘前扫描收到 (无回复)）
      const nowTs = Date.now();
      if (sessions.has(key)) {
        const h = sessions.get(key);
        const lastTs = h && h._ts ? h._ts : (h && h.agent && h.agent._lastTs ? h.agent._lastTs : 0);
        const isReportSession = String(sessionId || '').startsWith('report_');
        // QQ 对话（chat 模式非 report_）保留长期上下文（用户要求）；report_* 报告会话 45 分钟回收
        const ttlMs = (mode === 'chat' && !isReportSession) ? SESSION_CHAT_TTL_MS : SESSION_TTL_MS;
        if (lastTs && nowTs - lastTs > ttlMs) {
          console.warn('[Bridge] 会话超时回收: ' + key + '（空闲 ' + Math.round((nowTs - lastTs) / 60000) + ' 分钟）');
          try { await h.dispose(); } catch (e) { console.warn('[Bridge] dispose 失败: ' + e.message); }
          sessions.delete(key);
        }
      }
      if (sessions.has(key)) { sessions.get(key)._ts = Date.now(); return sessions.get(key).agent; }
      const modelId = modelOverride || (mode === 'trade' ? DEEPSEEK_TRADE_MODEL : DEEPSEEK_MODEL);
      // conditions 模式（AI 条件生成）用 low thinking：设定双条件是明确的结构化任务，
      // 不需要深度推理——medium 推理 1.5-3min 导致回测 120s 超时回退规则（迭代#55b）
      const thinkingLevel = thinkingLevelOverride || (
        mode === 'trade' ? 'high' : (mode === 'conditions' ? 'low' : 'medium'));
      const systemPrompt = getPrompt(mode === 'trade' ? 'TRADE_SYSTEM_PROMPT' : 'CHAT_SYSTEM_PROMPT');
      // 做T会话（t-agent-*）附加底仓建仓工作流指引
      const isTAgentSession = String(sessionId || '').includes('t-agent-');
      // 条件生成会话（conditions 模式）：不注入 T_BUILD/BACKTEST_REVIEW，避免三重角色冲突
      // （迭代#54 P1：此前误用 backtest 模式 → 注入"只做 exec/wait 决策"的复核提示 +
      //  deny 写工具，与"输出双条件数组"语义冲突，LLM 条件生成大量落规则兜底）
      const isConditionsMode = mode === 'conditions';
      const tBuildPrompt = (isTAgentSession && !isConditionsMode) ? getPrompt('T_BUILD_SYSTEM_PROMPT') : '';
      // 做T会话工具名错位修复（迭代#54 P2，t2 工具分析）：TRADE/CHAT_SYSTEM_PROMPT 是
      // legacy agent.py 旧注册表（get_quote/get_portfolio 等），bridge 实际注册
      // get_stock_quote/get_portfolio_positions 等新名——做T会话若注入 legacy 工具表，
      // AI 按旧名调用会被 unknown tool 拒绝（backtest-999 实录）。做T会话基础提示
      // 直接用 T_BUILD_SYSTEM_PROMPT（工具名与 bridge 注册一致），不叠加 legacy 表。
      const isTrade = mode === 'trade';
      const tBuildFull = getPrompt('T_BUILD_SYSTEM_PROMPT');
      // 回测复核会话（t-backtest-*）：沙盒隔离（design 5.2）——只做 auto/human 决策，
      // 通过 tools.restrict 拒绝生产写工具，绝不触达真实交易通道
      const isBacktestReview = mode === 'backtest';
      // 条件生成会话（conditions）：纯结构化输出任务——AI 只输出双条件 JSON 数组，
      // 不需要任何工具。迭代#55b 实测：AI 会调 bash/grep/glob/list_t_conditions 探索
      // （会话 164s，工具调用耗掉一半，120s 超时回退规则）→ deny 全部工具 + 专用提示
      const isConditionsMode2 = mode === 'conditions';
      // 1) 尝试恢复持久化会话（容器重启后保留对话）
      try {
        const resumed = await ctx.agents.resume({
          resumeSessionId: key,
          agentOptions: { provider: 'deepseek-official', model: modelId },
          setup: makeAgentSetup(mode, sessionId),
        });
      resumed._ts = Date.now();
      sessions.set(key, resumed);
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
        setup: makeAgentSetup(mode, sessionId),
      });
      handle._ts = Date.now();
      sessions.set(key, handle);
      return handle.agent;
    }

    // ── 读最终回复：等待停稳后从会话投影取最后 assistant 消息 ──
    // ── 坏会话 fork 迁移（DSH rc.6 resume 边界 bug：多次中断的会话 driver 不启动，
    //    消息 spliced 后 turn 不 claim → 空回复。fork 保留已完成对话历史，新 driver 可正常跑）──
    function extractCompletePrefix(events) {
      // 取最后一个完整 turn/end 之前的平衡前缀（无 open turn/step/dangling tool call）
      let lastEnd = -1;
      let depth = 0;
      for (const ev of events || []) {
        if (ev.type === 'turn/start') depth += 1;
        else if (ev.type === 'turn/end') { depth = Math.max(0, depth - 1); if (depth === 0) lastEnd = ev.seq; }
      }
      if (lastEnd < 0) return [];
      return (events || []).filter((ev) => ev.seq <= lastEnd);
    }

    async function migrateForkedSession(sessionId, mode, model, thinkingLevel, oldAgent) {
      const oldKey = mode + ':' + asciiSessionId(sessionId);
      const h = sessions.get(oldKey);
      if (h) { try { await h.dispose(); } catch (e) { console.warn('[Bridge] 迁移 dispose 失败: ' + e.message); } sessions.delete(oldKey); }
      locks.delete(oldKey);
      const newSessionId = asciiSessionId(sessionId) + '_m' + Date.now();
      const seed = extractCompletePrefix(oldAgent ? oldAgent.session.events : []);
      const modelId = model || (mode === 'trade' ? DEEPSEEK_TRADE_MODEL : DEEPSEEK_MODEL);
      try {
        const handle = await ctx.agents.create({
          sessionId: mode + ':' + newSessionId,
          agentOptions: { provider: 'deepseek-official', model: modelId },
          meta: { cwd: process.env.MARCUS_WORKSPACE || '/app' },
          setup: makeAgentSetup(mode, newSessionId),
          ...(seed.length ? { seed } : {}),
        });
        handle._ts = Date.now();
        sessions.set(mode + ':' + newSessionId, handle);
        console.warn('[Bridge] 会话已 fork 迁移: ' + oldKey.slice(-20) + ' -> ' + newSessionId.slice(-24) + '（seed ' + seed.length + ' 事件）');
        return { agent: handle.agent, sessionId: newSessionId };
      } catch (e) {
        console.error('[Bridge] fork 迁移失败: ' + (e && e.message ? e.message : e));
        return null;
      }
    }

    async function runAgentTurn(agent, message) {
      // 修复 resume 后 driver 卡住：cancel 收敛（无活动时 no-op；卡住时 abort 并让
      // driver 回到 idle，随后 followup 才能正常开启 turn）
      try {
        if (agent.cancel) agent.cancel('pre-turn-converge');
      } catch (e) {
        console.warn('[Bridge] cancel 收敛失败: ' + (e && e.message ? e.message : e));
      }
      try {
        await agent.whenIdle();
      } catch (e) {
        console.warn('[Bridge] 收敛等待失败: ' + (e && e.message ? e.message : e));
      }

      // 清理中断遗留的 pending inbox（dsh 重启/中断后 resume 的会话 turn 不启动：
      // 事件流只有 agent/inbox/spliced + turn/end 而无 turn/start → 空回复 (无回复)）
      // 保留会话历史（长期上下文），仅丢弃未消费的残留输入
      try {
        if (agent.inbox && agent.inbox.hasPending) {
          let pendingN = 0;
          try { pendingN = (agent.inbox.nextTurn?.length || 0) + (agent.inbox.nextStep?.length || 0); } catch (e) {}
          agent.inbox.clear();
          console.warn('[Bridge] 清理残留 pending inbox ' + pendingN + ' 条（会话 ' + String(agent.id || '').slice(-16) + '）');
        }
      } catch (e) {
        console.warn('[Bridge] inbox 清理失败: ' + (e && e.message ? e.message : e));
      }

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
      if (!text) {
        const types = {};
        let started2 = false;
        for (const ev of agent.session.events) {
          if (ev.type === 'turn/start') { started2 = true; continue; }
          if (!started2) continue;
          types[ev.type] = (types[ev.type] || 0) + 1;
        }
        console.warn('[Bridge] runAgentTurn 空回复，本回合事件分布: ' + JSON.stringify(types));
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
              if (chatMode === 'backtest') { json(res, 501, { error: '回测复核请使用 POST /backtest/review（回测会话沙盒）' }); return; }
              if (chatMode === 'reflect') { json(res, 501, { error: 'reflect 模式请使用 POST /chat/stream' }); return; }
              const lockKey = chatMode + ':' + asciiSessionId(sessionId);
              const prev = locks.get(lockKey);
              if (prev) await prev;
              let release;
              const lock = new Promise((r) => { release = r; });
              locks.set(lockKey, lock);
              try {
                let agent = await getOrCreateAgent(sessionId, chatMode, model, thinking_level);
                let reply = await runAgentTurn(agent, message);
                if (!reply || reply === '(无回复)') {
                  // 僵尸/异常会话：销毁重建重试一次（修复盘前扫描收到 (无回复)）
                  const retryKey = chatMode + ':' + sessionId;
                  const h = sessions.get(retryKey);
                  if (h) { try { await h.dispose(); } catch (e) { console.warn('[Bridge] retry dispose 失败: ' + e.message); } sessions.delete(retryKey); }
                  locks.delete(retryKey);
                  console.warn('[Bridge] /chat 空回复，销毁重建会话重试: ' + retryKey);
                  agent = await getOrCreateAgent(sessionId, chatMode, model, thinking_level);
                  reply = await runAgentTurn(agent, message);
                }
                let finalSessionId = sessionId;
                if (!reply || reply === '(无回复)') {
                  // 二次空回复：DSH resume 边界 bug 无法自愈 → fork 迁移（保留历史，新 driver）
                  const migrated = await migrateForkedSession(sessionId, chatMode, model, thinking_level, agent);
                  if (migrated) {
                    agent = migrated.agent;
                    finalSessionId = migrated.sessionId;
                    reply = await runAgentTurn(agent, message);
                  }
                }
                json(res, 200, { reply, session_id: finalSessionId, mode: chatMode, elapsed_ms: Date.now() - startTime, migrated: String(finalSessionId).indexOf('_m') > -1 });
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
        ctx.webServer.register({
          kind: 'exact',
          path: '/backtest/review',
          async handler(req, res) {
            if (req.method === 'OPTIONS') { res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' }); res.end(); return; }
            if (req.method !== 'POST') { json(res, 405, { error: 'Method Not Allowed' }); return; }
            try {
              const body = JSON.parse(await readBody(req));
              const { task_id, symbol, trigger, regime, rule_hint, position } = body;
              if (!task_id || !trigger) { json(res, 400, { error: '缺少 task_id / trigger' }); return; }
              // 同 task 复核串行化（迭代#56c：消费式重建后触发增多，同会话并发
              // followup 排队导致 45s 超时——"决策异常(保守等待): timed out"）
              const lockKey = 'backtest:' + task_id;
              const prev = locks.get(lockKey);
              if (prev) await prev;
              let release;
              const lock = new Promise((r) => { release = r; });
              locks.set(lockKey, lock);
              try {
                // 回测复核会话（沙盒）：key = backtest:t-backtest-{taskId}-{symbol}
                // （迭代#56c：按 task+symbol 隔离——此前同 task 所有标的共享
                // backtest:t-backtest-{taskId} 一个会话，消费式重建后触发变多，
                // 同会话 followup 排队导致 45s 超时"决策异常(保守等待)"）
                const agent = await getOrCreateAgent('t-backtest-' + task_id + '-' + (symbol || ''),
                                                     'backtest', null, null);
                const pos = position || {};
                const posLine = (pos.sellable !== undefined && pos.volume !== undefined)
                  ? ('持仓: 可卖' + pos.sellable + '股 总持仓' + pos.volume + '股 成本' + (pos.avg_price ?? '-') + ' 已实现盈亏' + (pos.realized_pnl ?? 0) + ' 当日回转' + (pos.day_turnover ?? 0))
                  : '持仓: （无回测持仓快照）';
                const prompt = [
                  '请对以下做T触发事件做出决策：exec（执行，默认）、wait（等待）、abandon（放弃）或 update_condition（调整条件）。',
                  '',
                  '标的: ' + (symbol || trigger.symbol || ''),
                  '触发: ' + JSON.stringify(trigger, null, 1),
                  'regime: ' + JSON.stringify(regime || {}, null, 1),
                  '规则预判(供参考): ' + JSON.stringify(rule_hint || {}, null, 1),
                  posLine,
                  '',
                  '【默认动作 = exec】本次触发已命中监控条件并通过系统规则预筛，默认执行。',
                  '仅当存在客观证据时才 wait/abandon，且 reason 必须写明具体证据：',
                  '① 现价与目标价/建议价脱节（差 >1%）；② 已跌破止损价；③ regime 禁自动；④ 恐慌放量追跌（量比骤升+创新低）。',
                  '信息不足 ≠ wait：可调用查询工具补数（get_stock_quote 实时行情 / get_t_realtime_indicators 技术指标 /',
                  'get_intraday_minute 分钟K线 / get_stock_moneyflow 资金流 / get_market_state 大盘）。',
                  '高抛卖腿（high_sell_then_buy_back）是兑现利润的正向动作——有可卖底仓时触达高抛价应倾向 exec；',
                  '低吸买腿需确认非恐慌追跌（区分温和回踩 vs 放量下跌创新低）。',
                  '【消费式条件】本次触发后该条件已销毁——如需继续做T请用 update_condition 附新 condition 重建',
                  '（当日剩余 bar 生效），不重建则本标的当日不再触发；严禁编造已销毁条件继续触发。',
                  '只输出一行 JSON（不要 markdown 代码块、不要其他文字）：{"action":"exec|wait|abandon|update_condition","reason":"一句话理由","condition":{}}（condition 仅 update_condition 时必填）',
                ].join('\n');
                const reply = await runAgentTurn(agent, prompt);
                const parsed = parseDecision(reply);
                console.log('[Bridge] /backtest/review task#' + task_id + ' → ' + (parsed.action || parsed.decision) + ' (' + parsed.reason.slice(0, 60) + ')');
                json(res, 200, parsed);
              } finally {
                release();
                if (locks.get(lockKey) === lock) locks.delete(lockKey);
              }
            } catch (e) {
              console.error('[Bridge] /backtest/review error:', e);
              json(res, 500, { error: e.message || '内部错误' });
            }
          },
        }),
        ctx.webServer.register({
          kind: 'exact',
          path: '/conditions/generate',
          async handler(req, res) {
            if (req.method === 'OPTIONS') { res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' }); res.end(); return; }
            if (req.method !== 'POST') { json(res, 405, { error: 'Method Not Allowed' }); return; }
            try {
              const body = JSON.parse(await readBody(req));
              const { symbol, cost, amp_med, trend, regime, context, session_id, quote_price, rebuild_ctx } = body;
              if (!symbol || !cost) { json(res, 400, { error: '缺少 symbol / cost' }); return; }
              // 条件生成会话：独立 conditions 模式（迭代#54 P1：不再误用 backtest 沙盒，
              // 避免 BACKTEST_REVIEW_PROMPT 注入 + deny 写工具污染条件生成语义）
              const agent = await getOrCreateAgent(session_id || ('t-agent-' + symbol), 'conditions', null, null);
              const prompt = [
                '你是做T条件设定者：为持仓标的自主设定**一组做T触发条件**（条件组合由你决定——数量、类型、触发价、量比、企稳、止损、**触发后买卖股数**全由你自主设计），让系统在条件命中时**自动执行**（止损/止盈自动卖出、低吸自动买入），执行完成后报告你复盘并重建新条件。',
                '【工具使用】以下信息若不足以设定合理条件（如振幅/趋势/现价缺失或存疑），可调用查询工具补数：',
                'get_stock_quote（实时行情）/ get_t_realtime_indicators（技术指标）/ get_intraday_minute（分钟K线）/',
                'get_stock_moneyflow（资金流）/ get_market_state（大盘）/ get_stock_technical（深度技术）/',
                'get_portfolio_positions（持仓）/ get_t_candidates_summary（候选）——按需调用，信息足够就直接输出，不必强行调用。',
                '',
                '标的: ' + symbol,
                '持仓成本: ' + cost + '（**条件基准 = 成本，不是现价！**）',
                '现价(若提供): ' + (quote_price ?? '未知'),
                '近6日振幅中位(%): ' + (amp_med ?? '未知'),
                '趋势: ' + (trend ? JSON.stringify(trend) : '未知'),
                'regime: ' + (regime ? JSON.stringify(regime) : '未知'),
                '参考上下文: ' + (context ? JSON.stringify(context, null, 1) : '（无）'),
                (rebuild_ctx ? ('【上次触发（本次重建原因）】' + JSON.stringify(rebuild_ctx) +
                  '——新条件必须**移动**：高抛目标价必须 > 上次触发价和现价（只有价格继续涨才触发），' +
                  '低吸目标价必须 < 上次触发价和现价（只有回踩才触发），止损 < 现价；严禁重复设定与上次相同的触发价。')
                  : ''),
                '',
                '规则参考（可偏离，但需合理）：低吸=成本×(1−max(2%,振幅×0.75))、高抛=成本×(1+max(1.5%,振幅×0.75))、止损=成本×(1−max(3%,振幅×0.55))。',
                '设定要点：',
                '① 高抛卖腿（high_sell_then_buy_back）：触发价应高于成本且可及；**volume = 本次触发卖出的股数**（≤可卖底仓，100 的整数倍，建议 30%~50% 底仓，保留底仓继续做T）',
                '② 低吸买腿（low_buy）：触发价应低于成本（回踩买点）；**volume = 本次触发买入的股数**（100 的整数倍，考虑可用资金）',
                '③ 止损价（stop_loss_price）必须低于成本（防深跌），且不被正常波动击穿（结合振幅）；止损触发自动卖出 volume 股',
                '④ vol_ratio_thresh（量比阈值，1.0~3.0）与 stabilize_level（not_new_low/other）可调',
                '⑤ 条件组合由你决定：通常 low_buy + high_sell_then_buy_back 各一条；',
                '   若行情需要可加 panic_vibrate（恐慌低吸）等，数量 1~4 条均可，但不要冗余重复',
                '⑥ **输出规范（防呆）**：',
                '   - low_buy（买腿）：只填 target_price（必须 < 成本，回踩买点）+ vol_ratio_thresh/stabilize_level；**不要填 sell_target_price**（那是卖腿字段）',
                '   - high_sell_then_buy_back（卖腿）：填 sell_target_price（必须 > 成本）+ target_price 可省略；vol_ratio_thresh/stabilize_level 可省',
                '   - 止损 stop_loss_price：所有条件统一且 < 成本×0.99',
                '   - volume：100 整数倍；低吸=计划加仓量、高抛=计划卖出量（≤可卖底仓，保留底仓）',
                '   - 严禁输出 target=现价/成本 或无意义条件；重建时（rebuild_ctx）新价必须与上次错开（移动基准）',
                '⑦ 输出【条件数组】（不要 markdown 代码块、不要其他文字），示例：',
                '[{"trigger_kind":"low_buy","target_price":..,"stop_loss_price":..,"vol_ratio_thresh":..,"stabilize_level":"..","volume":300,"reason":"一句话"},{"trigger_kind":"high_sell_then_buy_back","sell_target_price":..,"stop_loss_price":..,"vol_ratio_thresh":..,"volume":300,"reason":"一句话"}]',
                '价格保留两位小数；volume 为 100 的整数倍；同一标的各条件的 stop_loss_price 应一致。',
              ].join('\n');
              const reply = await runAgentTurn(agent, prompt);
              const parsed = parseConditions(reply, cost, symbol, amp_med);
              console.log('[Bridge] /conditions/generate ' + symbol + ' → ' + JSON.stringify(parsed));
              json(res, 200, parsed);
            } catch (e) {
              console.error('[Bridge] /conditions/generate error:', e);
              json(res, 500, { error: e.message || '内部错误' });
            }
          },
        }),
      ];
      const all = [...disposers, ...panelDisposers];
      return () => { for (const d of all) d(); };
    });

    // ── AI 条件生成：解析 AI 输出的双条件 JSON 数组，容错兜底 ──
    // 迭代#54 P6/P8：条件统一 schema——补 symbol 注入、止损钳制（可更紧不可更宽）、
    // 价格校验；产出可直接喂 upsert_condition（后端在落库前还会做规则止损兜底）
    // 迭代#54b：提取改用平衡括号扫描（旧贪婪正则 /\[\s*\{[\s\S]*\}\s*\]/ 在 AI 回复
    // 带 markdown 围栏/解释文字时截断或跨对象误配 → 全部 fallback）
    function parseConditions(reply, cost, symbol, ampMed) {
      const fallback = [];  // 兜底由调用方（后端）按规则公式生成
      if (!reply) return { conditions: fallback, source: 'fallback', reason: '空回复' };
      const text = String(reply).trim();
      const arr = extractJsonArray(text);
      if (!arr) return { conditions: fallback, source: 'fallback', reason: '无 JSON 数组: ' + text.slice(0, 120) };
      try {
        const arr2 = Array.isArray(arr) ? arr : JSON.parse(arr);
        if (!Array.isArray(arr2) || arr2.length === 0) return { conditions: fallback, source: 'fallback', reason: '空数组' };
        // 规则止损下限（迭代#52：AI 放宽止损→坏标的扛单多亏；可更紧不可更宽）
        const ruleStop = round2(Number(cost) * (1 - Math.max(0.03, (Number(ampMed) || 3.0) / 100 * 0.55)));
        const conditions = [];
        for (const c of arr2) {
          const kind = String(c.trigger_kind || '');
          if (kind !== 'low_buy' && kind !== 'high_sell_then_buy_back') continue;
          const costNum = Number(cost) || 0;
          const tp = Number(c.target_price);
          const stp = Number(c.sell_target_price) > 0 ? Number(c.sell_target_price) : tp;
          const st = Number(c.stop_loss_price);
          if (!tp || tp <= 0) continue;
          // 迭代#58g：价格×成本方向校验（AI 把现价当成本/目标设反 → 丢弃，规则兜底）
          //   低吸 = 买腿：target 必须低于成本（回踩买点）
          //   高抛 = 卖腿：sell_target 必须高于成本（兑现卖点）
          if (kind === 'low_buy' && !(costNum > 0 && tp < costNum)) continue;
          if (kind === 'high_sell_then_buy_back' && !(costNum > 0 && stp > costNum)) continue;
          // 止损钳制（迭代#52 下限 + 迭代#56c 上限）：
          //   - 不得低于规则值（更低=更宽，取 max）
          //   - **必须低于成本**（止损高于成本=逻辑错误，AI 可能把现价误当成本基准——
          //     #61 中 000636 止损 60.07 > 成本 29.6 → 每根 bar 触发止损连卖 4 次）
          //     → 高于成本 99% 直接回退规则值
          const costCap = round2(Number(cost) * 0.99);
          let stop = st > 0 ? Math.max(st, ruleStop) : ruleStop;
          if (stop > costCap) stop = ruleStop;
          const cond = {
            trigger_kind: kind,
            symbol: String(c.symbol || symbol || ''),
            target_price: round2(tp),
            sell_target_price: round2(stp),
            stop_loss_price: round2(stop),
            vol_ratio_thresh: Number(c.vol_ratio_thresh) > 0 ? Number(c.vol_ratio_thresh) : 1.5,
            stabilize_level: c.stabilize_level || 'not_new_low',
            // 触发后自动执行股数（迭代#57，用户需求）：100 的整数倍；非法/缺省由引擎回退规则
            volume: (Number(c.volume) > 0) ? Math.floor(Number(c.volume) / 100) * 100 : undefined,
            armed: 1,
            status: 'active',
            reason: String(c.reason || '') ,
          };
          conditions.push(cond);
        }
        if (conditions.length === 0) return { conditions: fallback, source: 'fallback', reason: '无有效条件' };
        return { conditions, source: 'ai', reason: 'AI 生成' };
      } catch (e) {
        return { conditions: fallback, source: 'fallback', reason: '解析失败: ' + e.message };
      }
    }

    // ── 提取首个平衡 JSON 数组（跳过 markdown 代码块围栏与前后文字）──
    function extractJsonArray(text) {
      const t = String(text).replace(/```json|```/g, '');
      let start = -1;
      for (let i = 0; i < t.length; i++) {
        if (t[i] === '[') { start = i; break; }
      }
      if (start < 0) return null;
      let depth = 0, inStr = false, esc = false;
      for (let i = start; i < t.length; i++) {
        const ch = t[i];
        if (inStr) {
          if (esc) esc = false;
          else if (ch === '\\') esc = true;
          else if (ch === '"') inStr = false;
          continue;
        }
        if (ch === '"') { inStr = true; continue; }
        if (ch === '[') depth++;
        else if (ch === ']') {
          depth--;
          if (depth === 0) {
            try { return JSON.parse(t.slice(start, i + 1)); } catch (e) { return null; }
          }
        }
      }
      return null;
    }

    function round2(v) { return Math.round(v * 100) / 100; }

    // ── 回测复核：解析 LLM 决策 JSON（action: exec|wait|abandon；兼容旧 decision:auto|human）──
    function parseDecision(reply) {
      if (!reply) return { action: 'wait', reason: '空回复' };
      const text = String(reply).trim();
      // 提取首个平衡 JSON 对象（支持嵌套 condition 对象）
      const obj = extractJsonObject(text);
      if (obj) {
        // 新格式：{"action": "exec|wait|abandon|update_condition", ...}
        const action = obj.action || '';
        if (action === 'exec' || action === 'wait' || action === 'abandon' || action === 'update_condition') {
          return { action, reason: String(obj.reason || ''), condition: obj.condition || null };
        }
        // 兼容旧格式：{"decision": "auto|human"}
        if (obj.decision === 'auto') return { action: 'exec', reason: String(obj.reason || '') };
        if (obj.decision === 'human') return { action: 'wait', reason: String(obj.reason || '') };
      }
      // 无 JSON：按文本关键词兜底
      if (/update_condition|调整条件|update.*condition/.test(text)) return { action: 'update_condition', reason: text.slice(0, 120) };
      if (/exec|执行|放行|auto/.test(text)) return { action: 'exec', reason: text.slice(0, 120) };
      if (/abandon|放弃/.test(text)) return { action: 'abandon', reason: text.slice(0, 120) };
      return { action: 'wait', reason: text.slice(0, 120) };
    }

    // ── 提取首个平衡 JSON 对象（跳过 markdown 代码块围栏）──
    function extractJsonObject(text) {
      const t = String(text).replace(/```json|```/g, '');
      let start = -1;
      for (let i = 0; i < t.length; i++) {
        if (t[i] === '{') { start = i; break; }
      }
      if (start < 0) return null;
      let depth = 0, inStr = false, esc = false;
      for (let i = start; i < t.length; i++) {
        const ch = t[i];
        if (inStr) {
          if (esc) esc = false;
          else if (ch === '\\') esc = true;
          else if (ch === '"') inStr = false;
          continue;
        }
        if (ch === '"') { inStr = true; continue; }
        if (ch === '{') depth++;
        else if (ch === '}') {
          depth--;
          if (depth === 0) {
            try { return JSON.parse(t.slice(start, i + 1)); } catch (e) { return null; }
          }
        }
      }
      return null;
    }

    // ── 内置回退 Prompt（启动时从 Backend 拉取后覆盖）──
    const promptCache = new Map();
    const FALLBACK_PROMPTS = {
      CHAT_SYSTEM_PROMPT: '你是 Marcus — 短线右侧交易专家。你可以查询行情、板块、资金流、技术指标等数据帮助用户了解市场状况。',
      TRADE_SYSTEM_PROMPT: '你是 Marcus — 短线右侧交易专家（trade 模式）。你可以查询数据并执行交易操作。',
      T_BUILD_SYSTEM_PROMPT: [
        '## 做T底仓建仓工作流指引（t-agent 会话附加）',
        '做T = 用"已有底仓"做 T+0 高抛低吸回转。**底仓是弹药**。',
        '你是**做T决策主体**：选股、操作、监控条件（定时器）发布与复盘均由你决定；',
        '系统规则只负责条件命中检测、唤醒你、网关风控兜底与审计。',
        '',
        '被条件命中唤醒时输出一行 JSON（不要 markdown 代码块、不要其他文字）：',
        '{"action": "exec|wait|abandon|update_condition", "reason": "一句话理由", "condition": {...}}',
        '- exec：**默认动作**。触发已命中你的监控条件并通过网关规则预筛，默认执行（按建议价经网关，可能被拒）',
        '- wait：仅在存在客观证据时（现价与目标脱节>1% / 跌破止损 / regime 禁自动 / 恐慌放量追跌），reason 必须写明证据',
        '- abandon：放弃本次触发（追高/信号矛盾，同样需写明证据）',
        '- update_condition：触发价与行情明显脱节或连续命中未改善——更新监控条件（必须附完整 condition 对象）',
        '',
        '连续命中告警（consecutive_hit_alert=true）时二选一：① 输出 update_condition 附新条件；',
        '② 输出 wait 并注明"连续命中，等待冷却"（让系统自动冷却）。',
        '严禁在没有新事实时只把 target_price 往现价方向微调制造下一轮触发。',
        '',
        '自主看盘（可调用查询工具，决策前按需调用，不必全调）：',
        '- 行情：get_stock_quote(实时行情) / get_intraday_minute(分钟K线) / get_stock_moneyflow(资金流)',
        '- 技术面：get_t_realtime_indicators(MA/MACD/KDJ/RSI) / get_stock_technical(深度技术)',
        '- 大盘：get_market_state(大盘/regime)',
        '- 持仓/条件自检：get_portfolio_positions(持仓) / list_t_conditions(当前监控条件) / list_t_ai_actions(最近决策审计)',
        '【硬约束】快照缺现价/量能时必须调用 get_stock_quote / get_t_realtime_indicators 补数，禁止仅凭自述理由放行 exec。',
        '',
        '【迭代#58 条件单建仓】低吸（low_buy）与自定义买方向（custom + direction=buy）监控条件',
        '在标的**未建仓**时命中也会触发：系统按建仓规模（单笔上限÷现价）自动买入开仓，无需先建底仓；',
        '14:45 后禁开仓、near-limit/恐慌放量等风控照常。发布自定义条件时用 create_t_condition 的 direction 参数',
        '显式声明 buy/sell（buy=买腿可建仓，sell=卖腿需有可卖底仓）；缺省按 trigger_kind（low_buy/panic_vibrate=买，其余=卖）。',
        '',
        '建仓：scan_t_candidates 选股（候选池优先，空则全市场扫描）→ get_floor_overview →',
        'build_t_position（ai_led 首开自动放行；单笔≤净值5%、总底仓≤净值55%；冷静期/午后不自动建）。',
        '硬约束：仅 t 账户；STOP_ALL/日亏熔断/HALT 禁自动；T+1 当日禁卖；单票当日 1 批；所有下单经网关（ai_led 不豁免）。',
        '',
        '【禁重建标的 no_rebuild_symbols】名单内 = 只减不补：系统跳过其低吸重建（AI 重建/盘后/',
        '消费式重建），只保留卖腿。用 t_no_rebuild_symbols 查看；持仓走弱/破位/浮亏扩大/用户要求',
        '只减不补时加入（写明证据）；企稳回踩/趋势回升时可移出恢复低吸闭环。',
      ].join('\n'),
    };
    // 回测复核会话系统提示（沙盒：AI 决策 exec/wait/abandon/update_condition，禁止交易/写操作）
    const BACKTEST_REVIEW_PROMPT = [
      '你是做T回测决策 Agent（沙盒模式）。只对触发事件做 exec/wait/abandon/update_condition 决策，不执行任何交易。',
      '你的工具已被沙盒隔离：生产写工具（下单/撤单/建仓等）对你不可见；只读查询工具可用。',
      '【默认动作 = exec】触发已命中监控条件并通过规则预筛，默认执行。仅当存在客观证据时 wait/abandon：',
      '① 现价与目标价脱节（>1%）；② 跌破止损；③ regime 禁自动；④ 恐慌放量追跌。信息不足 ≠ wait，可调查询工具补数。',
      '【消费式条件】本次触发后该条件已销毁——如需继续做T，请在决策时评估重建：',
      '用 update_condition 附新 condition（重建新条件），不重建则本标的当日不再触发。',
      '高抛卖腿是兑现正向动作倾向 exec；低吸需区分温和回踩 vs 恐慌追跌。',
      '每次输出一行 JSON：{"action":"exec|wait|abandon|update_condition","reason":"一句话理由","condition":{}}。',
    ].join('\n');
    // 沙盒 deny 名单：回测复核会话禁用的生产写工具
    const BACKTEST_DENY_TOOLS = [
      'place_order', 'cancel_order', 'calc_position',
      'update_golden_pit_etf_config', 'create_t_condition',
      'list_t_fields', 'list_t_conditions', 'run_t_backtest',
      't_no_rebuild_symbols',
    ];

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
