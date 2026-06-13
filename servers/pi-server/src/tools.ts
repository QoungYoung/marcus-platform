/**
 * Marcus Trading Tools — 从 ChatContainer.tsx 提取的服务端版本
 * 
 * 所有工具调用 localhost:8000 的 Backend API 获取数据
 */

import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SESSIONS_DIR = resolve(__dirname, '..', 'sessions');

// ===== 简化的 Type 工厂（服务端不需要 TypeBox 的完整反射，用 JSON Schema 即可） =====
const Type = {
  Object: (props: Record<string, any>) => ({
    type: 'object' as const,
    properties: props,
    required: Object.keys(props).filter(k => !props[k]?.optional),
  }),
  String: (opts?: { description?: string }) => ({
    type: 'string' as const,
    ...opts,
  }),
  Number: (opts?: { description?: string }) => ({
    type: 'number' as const,
    ...opts,
  }),
  Optional: (inner: any) => ({ ...inner, optional: true }),
};

const MARCUS_API = process.env.MARCUS_API_URL || 'http://localhost:8000/api/v1';

async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${MARCUS_API}${path}`, init);
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text().catch(() => '')}`);
  return res.json();
}

// ===== 工具定义 =====

export const getMarketIndicesTool = {
  name: 'get_market_indices',
  label: '市场行情',
  description: '获取 A股指数（上证、深证、创业板）、美股指数、港股指数的实时行情',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal?: AbortSignal) {
    const data = await apiFetch('/market/indices');
    const indices = data.indices || [];
    const lines = indices.map((idx: any) => {
      const sign = idx.change_pct >= 0 ? '+' : '';
      return `${idx.name}: ${idx.current_price} (${sign}${idx.change_pct}%)`;
    }).join('\n');
    return { content: [{ type: 'text', text: lines || '暂无数据' }], details: data };
  },
};

export const getQuoteTool = {
  name: 'get_quote',
  label: '个股行情',
  description: '查询个股实时行情，包括当前价格、涨跌、成交量等',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/market/quote/${params.symbol}`);
    if (data.error) throw new Error(data.error);
    const q = data;
    const sign = q.percent >= 0 ? '+' : '';
    const lines = [
      `${q.name} (${q.symbol})`,
      `当前价: ${q.current}  涨跌: ${sign}${q.change} (${sign}${q.percent}%)`,
      `今开: ${q.open}  最高: ${q.high}  最低: ${q.low}`,
      `昨收: ${q.last_close}  成交量: ${q.volume}  成交额: ${q.amount}`,
    ];
    if (q.turnover_rate) lines.push(`换手率: ${q.turnover_rate}%  振幅: ${q.amplitude || '--'}%`);
    if (q.pe_ttm) lines.push(`市盈率: ${q.pe_ttm}  市净率: ${q.pb || '--'}`);
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getPortfolioTool = {
  name: 'get_portfolio',
  label: '账户持仓',
  description: '查看当前账户资金状况和所有持仓。成本价为实际成交价（不复权），当前价为实时行情。短期交易除权概率低。',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal?: AbortSignal) {
    const data = await apiFetch('/portfolio');
    const acc = data.account || {};
    const lines = [
      '账户总览',
      `总资产: ${acc.total_asset?.toFixed(2)}`,
      `可用资金: ${acc.available_cash?.toFixed(2)}`,
      `持仓市值: ${acc.position_value?.toFixed(2)}`,
      `总盈亏: ${acc.total_pnl?.toFixed(2)} (${(data.total_return_pct ?? 0) >= 0 ? '+' : ''}${(data.total_return_pct ?? 0)?.toFixed(2)}%)`,
      `持仓比例: ${acc.position_ratio?.toFixed(2)}%`,
      '',
      '持仓明细:',
    ];
    const positions = acc.positions || [];
    if (positions.length === 0) {
      lines.push('暂无持仓');
    } else {
      positions.forEach((p: any) => {
        const sign = p.floating_pnl >= 0 ? '+' : '';
        lines.push(`${p.name}(${p.symbol}): ${p.volume}股 成本${p.avg_price} 现价${p.current_price} 浮动${sign}${p.floating_pnl}(${sign}${p.floating_pnl_pct}%)`);
      });
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getConceptFundFlowTool = {
  name: 'get_concept_fund_flow',
  label: '概念板块行情',
  description: '获取概念板块实时行情排行（按涨幅或主力资金流向排序）。数据源：东财push2实时(主力/超大单/大单/中单/小单净流入+板块广度+领涨股)，Tushare降级兜底。sort_by=pct_change看涨幅榜，sort_by=main_net看资金榜',
  parameters: Type.Object({
    limit: Type.Optional(Type.Number({ description: '返回数量，默认15' })),
    sort_by: Type.Optional(Type.String({ description: '排序字段: pct_change(涨幅排行) / main_net(主力净流入排行)' })),
  }),
  async execute(_toolCallId: string, params: { limit?: number; sort_by?: string }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    const qs = query.toString();
    const data = await apiFetch(`/market/concept-fund-flow${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const sectors = data.sectors || [];
    if (sectors.length === 0) {
      return { content: [{ type: 'text', text: '暂无概念板块行情数据' }], details: data };
    }
    const tradeDate = data.trade_date ? `日期: ${data.trade_date}` : '';
    const sortLabel = params.sort_by === 'main_net' ? '主力资金流入排行' : '涨幅排行';
    const lines = [`📊 概念板块行情 (${sortLabel})`, tradeDate, ''];
    sectors.forEach((s: any, idx: number) => {
      const sign = s.pct_change >= 0 ? '+' : '';
      const amountYi = (s.amount / 100000000).toFixed(2);
      let line = `${idx + 1}. ${s.name} | 涨跌:${sign}${s.pct_change}% | 成交:${amountYi}亿`;
      // 附加资金流数据
      if (s.main_net_fmt) {
        const nature = s.flow_nature ? `[${s.flow_nature}]` : '';
        line += ` | 主力:${s.main_net_fmt}${nature}`;
      }
      if (s.advancing !== undefined && s.declining !== undefined) {
        line += ` | ↑${s.advancing}/↓${s.declining}`;
      }
      if (s.lead_stock_name) {
        line += ` | 领涨:${s.lead_stock_name}(${s.lead_stock_code})`;
      }
      lines.push(line);
    });
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getConceptMappingTool = {
  name: 'get_concept_mapping',
  label: '概念板块查询',
  description: '查询东方财富概念板块及其成分股。不传参数则列出所有概念，传concept_name则返回该概念下的所有股票',
  parameters: Type.Object({
    concept_name: Type.Optional(Type.String({ description: '概念名称，如 人形机器人、固态电池、AI芯片。不传则返回所有概念列表' })),
    limit: Type.Optional(Type.Number({ description: '返回数量，默认30' })),
  }),
  async execute(_toolCallId: string, params: { concept_name?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.concept_name) query.set('concept', params.concept_name);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/market/concept${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const concepts = data.concepts || [];
    const total = data.total || concepts.length;

    if (params.concept_name) {
      const stocks = data.stocks || [];
      if (stocks.length === 0) {
        return { content: [{ type: 'text', text: `概念 [${params.concept_name}] 下暂无成分股数据` }], details: data };
      }
      const lines = [`📊 ${params.concept_name} (共${stocks.length}只成分股)`, ''];
      for (const s of stocks.slice(0, params.limit || 30)) {
        lines.push(`${s.ts_code} | ${s.symbol} | ${s.name}${s.market_cap ? ' | 市值:' + s.market_cap.toFixed(0) + '亿' : ''}`);
      }
      if (stocks.length > (params.limit || 30)) {
        lines.push(`... 还有 ${stocks.length - (params.limit || 30)} 只未显示`);
      }
      return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
    } else {
      if (concepts.length === 0) {
        return { content: [{ type: 'text', text: '暂无概念板块数据' }], details: data };
      }
      const lines = [`📊 概念板块列表 (共${total}个)`, ''];
      for (const c of concepts.slice(0, params.limit || 30)) {
        lines.push(`${c.sector_name} | ${c.stock_count}只成分股`);
      }
      if (concepts.length > (params.limit || 30)) {
        lines.push(`... 还有 ${concepts.length - (params.limit || 30)} 个概念未显示`);
      }
      return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
    }
  },
};

export const getEtfQuoteTool = {
  name: 'get_etf_quote',
  label: 'ETF行情',
  description: '查询ETF基金的实时行情',
  parameters: Type.Object({
    symbol: Type.String({ description: 'ETF代码，如 510300、159915' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/etf/quote/${params.symbol}`);
    if (data.error) throw new Error(data.error);
    const q = data;
    const change = q.last_close ? (q.current - q.last_close).toFixed(3) : '--';
    const sign = (q.percent ?? 0) >= 0 ? '+' : '';
    const text = [
      `${q.name} (${q.symbol})`,
      `当前价: ${q.current ?? '--'}`,
      `涨跌: ${sign}${change} (${sign}${q.percent ?? '--'}%)`,
      `昨收: ${q.last_close ?? '--'}  最高: ${q.high ?? '--'}  最低: ${q.low ?? '--'}`,
      `成交额: ${(q.amount ?? 0) >= 1e8 ? (q.amount / 1e8).toFixed(2) + '亿' : (q.amount / 1e4).toFixed(0) + '万'}  换手率: ${q.turnover_rate_est ?? '--'}%`,
      `更新时间: ${q.updated_at ?? '--'}`,
    ].join('\n');
    return { content: [{ type: 'text', text }], details: data };
  },
};

export const getEtfKlineTool = {
  name: 'get_etf_kline',
  label: 'ETF K线',
  description: '获取ETF历史K线数据，包含开高低收、成交量、成交额等。支持日/周/月K线，用于分析ETF走势和趋势判断',
  parameters: Type.Object({
    symbol: Type.String({ description: 'ETF代码，如 159513、510300' }),
    period: Type.Optional(Type.String({ description: 'K线周期: day(日线)/week(周线)/month(月线)，默认day' })),
    count: Type.Optional(Type.Number({ description: '数据条数，默认284（约一年日线），最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; period?: string; count?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.period) query.set('period', params.period);
    if (params.count) query.set('count', String(params.count));
    const qs = query.toString();
    const data = await apiFetch(`/etf/kline/${params.symbol}${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const klines = data.klines || [];
    if (klines.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的ETF K线数据` }], details: data };
    }
    const latest = klines[klines.length - 1];
    const lines = [
      `📊 ${params.symbol} ETF K线 (${params.period || 'day'}) - 共${klines.length}条，截至 ${latest?.timestamp || '--'}`,
      '',
    ];
    for (const k of klines.slice(-20)) {
      const sign = (k.close >= k.open) ? '📈' : '📉';
      lines.push(`${k.timestamp?.slice(0, 10) || '--'} | 开:${k.open?.toFixed(3)} 高:${k.high?.toFixed(3)} 低:${k.low?.toFixed(3)} 收:${k.close?.toFixed(3)} ${sign} 量:${(k.volume / 1e4).toFixed(0)}万`);
    }
    if (klines.length > 20) {
      lines.push(`... 仅显示最近20条，共${klines.length}条`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getDailyKlineTool = {
  name: 'get_daily_kline',
  label: '日K线',
  description: '获取A股个股历史日K线数据（前复权 qfq），包含开高低收、成交量、成交额等。用于分析个股历史走势、判断趋势、寻找支撑阻力位。数据已做前复权处理，除权除息日无价格跳空缺口，技术指标计算不受除权干扰',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认90天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认100，最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/market/kline/${params.symbol}${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const klines = data.klines || [];
    if (klines.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的K线数据` }], details: data };
    }
    const lines: string[] = [];
    lines.push(`${data.symbol} 历史日K线 (最近${klines.length}条)`);
    lines.push('日期       | 开盘   | 收盘   | 最高   | 最低   | 涨跌幅  | 成交量(手) | 成交额(万元)');
    lines.push('-'.repeat(85));
    for (const k of klines.slice(0, 20)) {
      const sign = k.pct_chg >= 0 ? '+' : '';
      const volWan = (k.vol / 100).toFixed(0);
      const amtWan = (k.amount / 10).toFixed(0);
      lines.push(`${k.trade_date} | ${k.open.toFixed(2).padStart(6)} | ${k.close.toFixed(2).padStart(6)} | ${k.high.toFixed(2).padStart(6)} | ${k.low.toFixed(2).padStart(6)} | ${sign}${k.pct_chg.toFixed(2)}% | ${volWan.padStart(9)} | ${amtWan.padStart(10)}`);
    }
    if (klines.length >= 5) {
      const closes = klines.map((k: any) => k.close);
      const maxClose = Math.max(...closes);
      const minClose = Math.min(...closes);
      const avgClose = (closes.reduce((a: number, b: number) => a + b, 0) / closes.length).toFixed(2);
      const firstClose = closes[closes.length - 1];
      const lastClose = closes[0];
      const totalChg = ((lastClose - firstClose) / firstClose * 100).toFixed(2);
      lines.push('');
      lines.push(`统计: 最高收盘 ${maxClose.toFixed(2)} | 最低收盘 ${minClose.toFixed(2)} | 均价 ${avgClose}`);
      if (firstClose !== 0) {
        lines.push(`区间涨跌: ${totalChg}% (${firstClose.toFixed(2)} → ${lastClose.toFixed(2)})`);
      }
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getMarketMoneyflowTool = {
	name: 'get_market_moneyflow',
	label: '大盘资金流向',
	description: '获取沪深两市大盘实时资金流向（主力/超大单/大单/中单/小单净流入+买/卖分明细+总成交额）。数据源：东财push2实时(优先)+Tushare日频(降级)。用于判断大盘整体资金情绪和主力动向',
	parameters: Type.Object({}),
	async execute(_toolCallId: string, _params: unknown, _signal?: AbortSignal) {
		const data = await apiFetch('/market/moneyflow-mkt');
		if (data.error) throw new Error(data.error);
		const m = data.data;
		if (!m) return { content: [{ type: 'text', text: '暂无大盘资金流向数据' }], details: data };
		const isRealtime = (m.data_source || '').includes('实时');
		const label = isRealtime ? '实时' : '日频';
		let totalAmountLine = m.total_amount_fmt ? `总成交: ${m.total_amount_fmt}` : '';
		const lines = [`大盘资金流向 ${m.trade_date} (${label})`, totalAmountLine].filter(Boolean);
		const signSh = m.pct_change_sh >= 0 ? '+' : '';
		const signSz = m.pct_change_sz >= 0 ? '+' : '';
		if (m.close_sh || m.close_sz) {
			lines.push(`上证: ${m.close_sh} (${signSh}${m.pct_change_sh}%)`);
			lines.push(`深证: ${m.close_sz} (${signSz}${m.pct_change_sz}%)`);
		}
		// 沪深分开
		if (data.sh && data.sz) {
			lines.push(`沪市主力: ${data.sh.main_net_fmt} | 深市主力: ${data.sz.main_net_fmt}`);
		}
		lines.push(
			`主力净流入: ${m.net_amount_fmt}${m.net_amount_rate ? ` (${m.net_amount_rate}%)` : ''}`,
			`超大单: ${(m.buy_elg_amount/10000).toFixed(2)}亿 (${m.buy_elg_amount_rate}%)`,
			`大单: ${(m.buy_lg_amount/10000).toFixed(2)}亿 (${m.buy_lg_amount_rate}%)`,
			`中单: ${(m.buy_md_amount/10000).toFixed(2)}亿 (${m.buy_md_amount_rate}%)`,
			`小单: ${(m.buy_sm_amount/10000).toFixed(2)}亿 (${m.buy_sm_amount_rate}%)`,
			`性质: ${m.flow_nature}`,
		);
		return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
	},
};

export const getMoneyflowTool = {
	name: 'get_moneyflow',
	label: '资金流向',
	description: '获取A股个股资金流向数据，分析大单/小单/特大单净流入/净流出情况。用于判断主力资金是否在入场或出逃',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认30天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认30，最大100' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/market/moneyflow/${params.symbol}${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const flows = data.flows || [];
    if (flows.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的资金流向数据` }], details: data };
    }
    const lines: string[] = [];
    lines.push(`${data.symbol} 资金流向 (最近${flows.length}条)`);
    lines.push('日期       | 特大单净流入(万) | 大单净流入(万) | 中单净流入(万) | 小单净流入(万) | 当日净流入(万)');
    lines.push('-'.repeat(90));
    for (const f of flows.slice(0, 20)) {
      const net = f.net_mf_amount >= 0 ? '+' : '';
      lines.push(`${f.trade_date} | ${(f.buy_elg_amount - f.sell_elg_amount).toFixed(0).padStart(13)} | ${(f.buy_lg_amount - f.sell_lg_amount).toFixed(0).padStart(13)} | ${(f.buy_md_amount - f.sell_md_amount).toFixed(0).padStart(13)} | ${(f.buy_sm_amount - f.sell_sm_amount).toFixed(0).padStart(13)} | ${net}${f.net_mf_amount.toFixed(0).padStart(12)}`);
    }
    if (flows.length >= 3) {
      const recentNet = flows.slice(0, 3).reduce((s: number, f: any) => s + f.net_mf_amount, 0);
      const sign = recentNet >= 0 ? '+' : '';
      lines.push('');
      lines.push(`近3日主力净流入: ${sign}${(recentNet / 10000).toFixed(2)}万元`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getTechnicalTool = {
  name: 'get_technical',
  label: '技术指标',
  description: '获取A股个股技术面因子数据，包含MACD、KDJ、RSI、布林带等60+指标。用于判断超买超卖、背离、趋势强度、金叉死叉等交易信号',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认90天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认100，最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/market/technical/${params.symbol}${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const rows = data.data || [];
    if (rows.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的技术指标数据` }], details: data };
    }
    const lines: string[] = [];
    lines.push(`${data.symbol} 技术指标 (最近${rows.length}条)`);
    lines.push('日期       | 收盘价 | MACD(DIF/DEA/柱) | KDJ(K/D/J) | RSI(6/12/24) | BOLL(上/中/下) | CCI | WR');
    lines.push('-'.repeat(110));
    for (const r of rows.slice(0, 20)) {
      const macdSign = r.macd >= 0 ? '+' : '';
      const kdjSign = r.kdj >= 0 ? '+' : '';
      lines.push(
        `${r.trade_date} | ${r.close.toFixed(2).padStart(6)} | ` +
        `${r.macd_dif.toFixed(2).padStart(6)}/${r.macd_dea.toFixed(2).padStart(6)}/${macdSign}${r.macd.toFixed(2).padStart(5)} | ` +
        `${r.kdj_k.toFixed(1).padStart(4)}/${r.kdj_d.toFixed(1).padStart(4)}/${kdjSign}${r.kdj.toFixed(1).padStart(5)} | ` +
        `${r.rsi_6.toFixed(1).padStart(4)}/${r.rsi_12.toFixed(1).padStart(5)}/${r.rsi_24.toFixed(1).padStart(5)} | ` +
        `${r.boll_upper.toFixed(2).padStart(6)}/${r.boll_mid.toFixed(2).padStart(6)}/${r.boll_lower.toFixed(2).padStart(6)} | ` +
        `${r.cci.toFixed(1).padStart(5)} | ${r.wr.toFixed(1)}`
      );
    }
    if (rows.length >= 2) {
      const latest = rows[0];
      const prev = rows[1];
      const signals: string[] = [];
      if (prev.macd_dif < prev.macd_dea && latest.macd_dif >= latest.macd_dea) signals.push('MACD 金叉↑');
      if (prev.macd_dif > prev.macd_dea && latest.macd_dif <= latest.macd_dea) signals.push('MACD 死叉↓');
      if (latest.kdj >= 80) signals.push('KDJ 超买');
      if (latest.kdj <= 20) signals.push('KDJ 超卖');
      if (latest.rsi_6 >= 70) signals.push('RSI6 超买');
      if (latest.rsi_6 <= 30) signals.push('RSI6 超卖');
      if (latest.close > latest.boll_upper) signals.push('价格突破BOLL上轨');
      if (latest.close < latest.boll_lower) signals.push('价格跌破BOLL下轨');
      if (signals.length > 0) {
        lines.push('');
        lines.push('📡 信号提示: ' + signals.join(' | '));
      }
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const readDbTableTool = {
  name: 'read_db_table',
  label: '数据库查询',
  description: '读取数据库表的数据，支持查询、筛选和排序',
  parameters: Type.Object({
    db: Type.String({ description: '数据库名: stock_pool.db, trades.db, news.db, cache.db' }),
    table: Type.String({ description: '表名' }),
    columns: Type.Optional(Type.String({ description: '要查询的列，逗号分隔' })),
    where: Type.Optional(Type.String({ description: 'WHERE条件' })),
    orderBy: Type.Optional(Type.String({ description: '排序，如 change_pct DESC' })),
    limit: Type.Optional(Type.Number({ description: '返回条数，默认100' })),
  }),
  async execute(_toolCallId: string, params: { db: string; table: string; columns?: string; where?: string; orderBy?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams({ db: params.db, table: params.table });
    if (params.columns) query.set('columns', params.columns);
    if (params.where) query.set('where', params.where);
    if (params.orderBy) query.set('order_by', params.orderBy);
    if (params.limit) query.set('limit', String(params.limit));
    const data = await apiFetch(`/db/query?${query}`);
    if (data.error) throw new Error(data.error);
    return { content: [{ type: 'text', text: JSON.stringify(data.rows || [], null, 2) }], details: data };
  },
};

export const getDbSchemaTool = {
  name: 'get_db_schema',
  label: '数据库结构',
  description: '获取数据库的表结构和字段信息',
  parameters: Type.Object({
    db: Type.String({ description: '数据库名: stock_pool, trades, news, cache' }),
  }),
  async execute(_toolCallId: string, params: { db: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/db/schema/${params.db}`);
    if (data.error) throw new Error(data.error);
    return { content: [{ type: 'text', text: JSON.stringify(data.schema || [], null, 2) }], details: data };
  },
};

// ===== 交易工具 =====

export const placeOrderTool = {
  name: 'place_order',
  label: '下单交易',
  description: '执行股票买入或卖出交易（模拟交易）。⚠️ A股T+1规则：当天买入的股票当天不能卖出！卖出前必须确认该持仓的入场日期不是今天。下单前必须先用 get_quote 获取最新价，用 get_portfolio 确认仓位和资金。返回订单结果含成交价、数量、成本/盈亏。',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001' }),
    side: Type.String({ description: '交易方向: buy(买入) 或 sell(卖出)' }),
    price: Type.Number({ description: '委托价格（元），必须用 get_quote 获取的实时价格' }),
    volume: Type.Number({ description: '交易数量（股），必须是100的整数倍' }),
    reason: Type.Optional(Type.String({ description: '交易理由，如"MACD金叉 放量突破前高"' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; side: string; price: number; volume: number; reason?: string }, _signal?: AbortSignal) {
    const data = await apiFetch('/trades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: params.symbol,
        side: params.side,
        price: params.price,
        volume: params.volume,
        reason: params.reason || '',
      }),
    });
    if (data.error) throw new Error(data.error);
    const status = data.status === 'executed' ? '✅ 成交' : '❌ 被拒';
    const lines = [
      `${status} | ${params.side === 'buy' ? '买入' : '卖出'} ${params.symbol}`,
      `价格: ${params.price} | 数量: ${params.volume}股`,
      `金额: ${(params.price * params.volume).toFixed(2)}`,
      `订单号: ${data.order_id || 'N/A'}`,
      `理由: ${params.reason || '未填写'}`,
    ];
    if (data.detail) lines.push(`详情: ${data.detail}`);
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getOrdersTool = {
  name: 'get_orders',
  label: '查询订单',
  description: '查询当前活跃订单（未成交/部分成交）。用于确认是否有未完成委托，避免重复下单。',
  parameters: Type.Object({
    symbol: Type.Optional(Type.String({ description: '按股票代码筛选，不传则查全部' })),
    status: Type.Optional(Type.String({ description: '按状态筛选: 提交中/未成交/部分成交' })),
    limit: Type.Optional(Type.Number({ description: '返回条数，默认50' })),
  }),
  async execute(_toolCallId: string, params: { symbol?: string; status?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.symbol) query.set('symbol', params.symbol);
    if (params.status) query.set('status', params.status);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/trades/orders${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const orders = data.orders || [];
    if (orders.length === 0) {
      return { content: [{ type: 'text', text: '📋 当前无活跃订单' }], details: data };
    }
    const lines = [`📋 活跃订单 (${orders.length}条)`, ''];
    for (const o of orders.slice(0, 20)) {
      lines.push(`${o.orderid} | ${o.direction} ${o.symbol} @ ${o.price} x ${o.volume}股 | 状态: ${o.status} | ${o.created_at?.slice(0, 19) || ''}`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const cancelOrderTool = {
  name: 'cancel_order',
  label: '撤销订单',
  description: '撤销一个未成交的委托订单。只能撤销状态为"提交中"或"未成交"的订单。',
  parameters: Type.Object({
    order_id: Type.String({ description: '订单号，如 ORD000001' }),
  }),
  async execute(_toolCallId: string, params: { order_id: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/trades/${params.order_id}/cancel`, { method: 'DELETE' });
    if (data.error) throw new Error(data.error);
    return {
      content: [{ type: 'text', text: `🗑️ 已撤销订单: ${params.order_id}` }],
      details: data,
    };
  },
};

export const getLatestScanReportTool = {
  name: 'get_latest_scan_report',
  label: '最新扫描报告',
  description: '获取最新的盘中扫描报告，包含市场立场、热门概念、观察列表、完整分析。这是Pi进行交易决策的核心数据源。',
  parameters: Type.Object({
    date: Type.Optional(Type.String({ description: '日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { date?: string }, _signal?: AbortSignal) {
    const query = params.date ? `?date=${params.date}` : '';
    let data: any;
    try {
      data = await apiFetch(`/scan/latest${query}`);
    } catch (e: any) {
      // 404 等无数据情况 → 返回空结果而不是抛异常，让 Pi 优雅处理
      const reason = e?.message?.includes('404') ? '今日暂无扫描报告' : `API 错误: ${e.message}`;
      return {
        content: [{ type: 'text', text: `📊 盘中扫描报告: ${reason}` }],
        details: { error: reason },
      };
    }
    if (data.error) throw new Error(data.error);
    const lines = [
      `📊 盘中扫描报告 (${data.timestamp || '--'})`,
      `市场立场: ${data.market_stance} (仓位上限: ${data.position_limit}%)`,
      '',
    ];
    if (data.hot_concepts && data.hot_concepts.length > 0) {
      lines.push('🔥 热门概念:');
      for (const c of data.hot_concepts.slice(0, 8)) {
        const name = typeof c === 'string' ? c : (c.name || c.concept || JSON.stringify(c));
        lines.push(`  - ${name}`);
      }
      lines.push('');
    }
    if (data.watchlist && data.watchlist.length > 0) {
      lines.push('👀 观察列表:');
      for (const w of data.watchlist.slice(0, 10)) {
        const name = typeof w === 'string' ? w : (w.name || w.symbol || JSON.stringify(w));
        lines.push(`  - ${name}`);
      }
      lines.push('');
    }
    if (data.report) {
      lines.push('📝 系统扫描报告:');
      lines.push(data.report.slice(0, 3000));
    }
    // Pi 分析报告（由盘前/盘中扫描后 Pi 分析生成，含策略建议）
    if (data.pi_analysis && data.pi_analysis.report) {
      lines.push('');
      lines.push('🧠 Pi 策略分析 (已预消化):');
      lines.push(`立场: ${data.pi_analysis.stance} | 仓位上限: ${data.pi_analysis.position_limit}%`);
      lines.push(`判断: ${data.pi_analysis.reason || ''}`);
      lines.push('');
      lines.push(data.pi_analysis.report.slice(0, 2000));
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

// ===== 周度反思工具 =====
export const getPiAnalysisHistoryTool = {
  name: 'get_pi_analysis_history',
  label: 'Pi分析历史',
  description: '按日期范围查询整周 Pi 分析历史记录。返回每天每轮扫描的 Pi 策略分析，包含 stance（立场）、position_limit（仓位上限）、reason（判断理由）和完整 report。用于周度反思时回顾整周策略演变。',
  parameters: Type.Object({
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYY-MM-DD，默认本周一' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { start_date?: string; end_date?: string }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    const qs = query.toString();
    const data = await apiFetch(`/scan/pi-analysis${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);

    const records = data.records || [];
    if (records.length === 0) {
      return {
        content: [{ type: 'text', text: `📋 日期范围 ${data.date_range?.start || '--'} 至 ${data.date_range?.end || '--'} 内暂无 Pi 分析记录` }],
        details: data,
      };
    }

    // 按日期分组展示
    const lines: string[] = [
      `📊 Pi 分析历史 (${data.date_range?.start || '--'} → ${data.date_range?.end || '--'})`,
      `共 ${data.days_count} 天，${data.total_records} 条记录`,
      '',
    ];

    let currentDate = '';
    for (const r of records) {
      if (r.date !== currentDate) {
        currentDate = r.date;
        lines.push(`--- ${currentDate} ---`);
      }
      const time = r.timestamp ? r.timestamp.slice(11, 19) : '--';
      lines.push(`  [${time}] ${r.task_name || '--'} | 立场: ${r.stance || '--'} | 仓位上限: ${r.position_limit || '--'}%`);
      if (r.reason) {
        lines.push(`     理由: ${r.reason}`);
      }
      if (r.report) {
        lines.push(`     报告:\n${r.report}`);
      }
    }

    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

// ===== 交易报告历史工具（周度反思用） =====
export const getTradeHistoryTool = {
  name: 'get_trade_history',
  label: '交易报告历史',
  description: '按日期范围查询整周 Pi 交易执行报告。返回每天每次交易窗口的完整报告，包含买卖决策、仓位变化、产业链组合逻辑、风险监控等。用于周度反思时评估策略执行质量，对比交易动作与 Pi 分析的一致性。',
  parameters: Type.Object({
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYY-MM-DD，默认本周一' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { start_date?: string; end_date?: string }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    const qs = query.toString();
    const data = await apiFetch(`/scan/trade-reports${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);

    const records = data.records || [];
    if (records.length === 0) {
      return {
        content: [{ type: 'text', text: `📋 日期范围 ${data.date_range?.start || '--'} 至 ${data.date_range?.end || '--'} 内暂无交易执行报告` }],
        details: data,
      };
    }

    // 按日期分组展示
    const lines: string[] = [
      `📊 交易执行报告 (${data.date_range?.start || '--'} → ${data.date_range?.end || '--'})`,
      `共 ${data.days_count} 天，${data.total_records} 条记录`,
      '',
    ];

    let currentDate = '';
    for (const r of records) {
      if (r.date !== currentDate) {
        currentDate = r.date;
        lines.push(`--- ${currentDate} ---`);
      }
      const time = r.timestamp ? r.timestamp.slice(11, 19) : '--';
      const taskLabel = (r.task_id || '').includes('morning') ? '早盘' :
                        (r.task_id || '').includes('late') ? '午前' :
                        (r.task_id || '').includes('afternoon') ? '午后' :
                        (r.task_id || '').includes('closing') ? '尾盘' : (r.task_id || '');
      lines.push(`  [${time}] ${taskLabel} | 立场: ${r.stance || '--'} | 仓位上限: ${r.position_limit || '--'}%`);
      if (r.reason) {
        lines.push(`     理由: ${r.reason}`);
      }
      if (r.report) {
        lines.push(`     报告:\n${r.report}`);
      }
    }

    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

// ===== 工具分组 =====
// 聊天模式（只读，QQ 聊天使用）
export const CHAT_TOOLS = [
  getMarketIndicesTool,
  getQuoteTool,
  getPortfolioTool,
  getConceptFundFlowTool,
  getMarketMoneyflowTool,
  getConceptMappingTool,
  getEtfQuoteTool,
  getEtfKlineTool,
  getDailyKlineTool,
  getMoneyflowTool,
  getTechnicalTool,
  readDbTableTool,
  getDbSchemaTool,
];

// 交易模式（全工具，自动交易使用）
export const TRADE_TOOLS = [
  ...CHAT_TOOLS,
  // 交易执行工具
  placeOrderTool,
  getOrdersTool,
  cancelOrderTool,
  getLatestScanReportTool,
];

// ===== 历史复盘查询工具（专家组群聊） =====
export const getPanelHistoryTool = {
  name: 'get_panel_history',
  label: '历史复盘记录',
  description: '查询历史专家组群聊复盘记录。不传参数列出所有历史复盘，传 date（YYYY-MM-DD）返回指定日期的完整讨论报告。用于专家交叉评论时引用上周/上月的复盘结论。',
  parameters: Type.Object({
    date: Type.Optional(Type.String({ description: '复盘日期 YYYY-MM-DD，不传则列出所有可选日期' })),
  }),
  async execute(_toolCallId: string, params: { date?: string }, _signal?: AbortSignal) {
    try {
      if (!existsSync(SESSIONS_DIR)) {
        return { content: [{ type: 'text', text: '暂无历史复盘记录（sessions 目录不存在）' }] };
      }
      const files = readdirSync(SESSIONS_DIR).filter(f => f.startsWith('panel_') && f.endsWith('.json'));
      if (files.length === 0) {
        return { content: [{ type: 'text', text: '暂无历史复盘记录' }] };
      }

      if (!params.date) {
        // 列出所有可用复盘的日期和概览
        const summaries = files.map(f => {
          try {
            const data = JSON.parse(readFileSync(resolve(SESSIONS_DIR, f), 'utf-8'));
            const replyLen = data.reply ? data.reply.length : 0;
            const elapsed = data.elapsed_ms ? `${(data.elapsed_ms / 1000 / 60).toFixed(1)}分钟` : '未知';
            return `📅 ${data.timestamp?.slice(0, 10) || '未知日期'} | 耗时: ${elapsed} | 报告字数: ${replyLen}`;
          } catch { return `📅 ${f}`; }
        });
        return { content: [{ type: 'text', text: `📋 历史复盘记录 (${files.length}条):\n\n${summaries.join('\n')}` }] };
      }

      // 查找指定日期的复盘
      const targetFile = files.find(f => {
        try {
          const data = JSON.parse(readFileSync(resolve(SESSIONS_DIR, f), 'utf-8'));
          return data.timestamp?.startsWith(params.date!);
        } catch { return false; }
      });

      if (!targetFile) {
        return { content: [{ type: 'text', text: `未找到 ${params.date} 的复盘记录` }] };
      }

      const data = JSON.parse(readFileSync(resolve(SESSIONS_DIR, targetFile), 'utf-8'));
      const header = `📊 历史复盘 — ${data.timestamp?.slice(0, 10) || '未知日期'}\n耗时: ${(data.elapsed_ms / 1000 / 60).toFixed(1)} 分钟\n\n`;
      return { content: [{ type: 'text', text: header + (data.reply || '无内容') }] };
    } catch (e: any) {
      return { content: [{ type: 'text', text: `读取复盘记录失败: ${e.message}` }] };
    }
  },
};

// 反思模式（仅 Tushare 历史数据 + 持久化记录，不含雪球实时查询）
// 操作: get_panel_history, get_daily_kline, get_technical, get_moneyflow, read_db_table
// 与聊天/交易模式完全隔离，无交易权限
export const REFLECT_TOOLS = [
  // Tushare 历史数据
  getDailyKlineTool,
  getTechnicalTool,
  getMoneyflowTool,
  // 持久化记录
  getPiAnalysisHistoryTool,
  getTradeHistoryTool,
  getLatestScanReportTool,
  getPanelHistoryTool,
  // 数据库查询
  readDbTableTool,
  getDbSchemaTool,
];

// 向后兼容
export const ALL_TOOLS = TRADE_TOOLS;
