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

// ===== 工具函数 =====
function formatAmount(val: number): string {
  if (!val || val === 0) return '0';
  const abs = Math.abs(val);
  if (abs >= 1e8) return (val / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return (val / 1e4).toFixed(2) + '万';
  return val.toFixed(2);
}

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

export const getIndustryFundFlowTool = {
  name: 'get_industry_fund_flow',
  label: '行业板块行情',
  description: '获取行业板块实时行情排行（按涨幅或主力资金流向排序）。数据源：东财push2实时(主力/超大单/大单/中单/小单净流入+板块广度+领涨股)。sort_by=pct_change看涨幅榜，sort_by=main_net看资金榜',
  parameters: Type.Object({
    limit: Type.Optional(Type.Number({ description: '返回数量，默认15' })),
    sort_by: Type.Optional(Type.String({ description: '排序字段: pct_change(涨幅排行) / main_net(主力净流入排行)' })),
  }),
  async execute(_toolCallId: string, params: { limit?: number; sort_by?: string }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    query.set('type', 'industry');
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    const qs = query.toString();
    const data = await apiFetch(`/market/sector-flow${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const sectors = data.sectors || [];
    if (sectors.length === 0) {
      return { content: [{ type: 'text', text: '暂无行业板块行情数据' }], details: data };
    }
    const sortLabel = params.sort_by === 'main_net' ? '主力资金流入排行' : '涨幅排行';
    const lines = [`📊 行业板块行情 (${sortLabel})`, ''];
    sectors.forEach((s: any, idx: number) => {
      const sign = s.pct_change >= 0 ? '+' : '';
      let line = `${idx + 1}. ${s.name} | ${sign}${s.pct_change.toFixed(2)}% | `;
      if (s.main_net_fmt) {
        line += `主力:${s.main_net_fmt}`;
      } else if (s.main_net) {
        line += `主力:${(s.main_net / 10000).toFixed(2)}亿`;
      }
      if (s.advancing !== undefined) {
        const ratio = s.total_stocks ? `(${s.advancing}/${s.total_stocks})` : '';
        line += ` | 📈${s.advancing}📉${s.declining}${ratio}`;
      }
      if (s.lead_stock_name) {
        line += ` | 领涨:${s.lead_stock_name}(${s.lead_stock_code})`;
      }
      lines.push(line);
    });
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
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
  description: '【日频·非实时】获取A股个股历史日K线数据（未复权），包含开高低收、成交量、成交额等。数据源：Tushare daily（盘后数据，今日K线收盘后才生成）。用于分析个股历史走势、判断趋势、寻找支撑阻力位',
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
    const lastDate = klines[0].trade_date || '--';
    const firstDate = klines[klines.length - 1].trade_date || '--';
    const lines: string[] = [];
    lines.push(`${data.symbol} 历史日K线 · 未复权 (${firstDate} → ${lastDate}，共${klines.length}条，日频·非实时·Tushare daily 盘后数据)`);
    lines.push(`⚠️ 数据截止日期: ${lastDate}（最近收盘日，当日K线收盘后才生成）`);
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

// 前复权 K线（复盘专用，调用 pro_bar 接口，避免除权缺口干扰技术分析）
export const getDailyKlineQfqTool = {
  name: 'get_daily_kline_qfq',
  label: '日K线(前复权)',
  description: '【日频·非实时/前复权】获取A股个股历史日K线数据（前复权 qfq），包含开高低收、成交量、成交额等。数据源：Tushare pro_bar（盘后数据）。前复权保证了除权除息日无价格跳空缺口，均线/MACD/RSI等技术指标连续可靠。复盘分析专用。',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认90天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认100，最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    query.set('adj', 'qfq');
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const data = await apiFetch(`/market/pro-bar/${params.symbol}?${qs}`);
    if (data.error) throw new Error(data.error);
    const bars = data.bars || [];
    if (bars.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的前复权K线数据` }], details: data };
    }
    const lastDate_2 = bars[0].trade_date || '--';
    const firstDate_2 = bars[bars.length - 1].trade_date || '--';
    const lines: string[] = [];
    lines.push(`${data.symbol} 历史日K线 · 前复权 (${firstDate_2} → ${lastDate_2}，共${bars.length}条，日频·非实时·Tushare pro_bar 盘后数据)`);
    lines.push(`⚠️ 数据截止日期: ${lastDate_2}（最近收盘日，当日K线收盘后才生成）`);
    lines.push('日期       | 开盘   | 收盘   | 最高   | 最低   | 成交量(手) | 成交额(万元)');
    lines.push('-'.repeat(85));
    for (const b of bars.slice(0, 20)) {
      const volWan = (b.vol / 100).toFixed(0);
      const amtWan = (b.amount / 10).toFixed(0);
      lines.push(`${b.trade_date} | ${b.open.toFixed(2).padStart(6)} | ${b.close.toFixed(2).padStart(6)} | ${b.high.toFixed(2).padStart(6)} | ${b.low.toFixed(2).padStart(6)} | ${volWan.padStart(9)} | ${amtWan.padStart(10)}`);
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
	description: '【实时】获取个股实时资金流向（东方财富/同花顺即时数据：主力/超大单/大单/中单/小单净流入+净占比+5日/10日累计）。用于判断主力资金动向',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/market/moneyflow/${params.symbol}`);
    if (data.error) throw new Error(data.error);
    const sign = (v: number) => v >= 0 ? '+' : '';
    const lines = [
      `${data.symbol}${data.name ? ' ' + data.name : ''} 实时资金流向`,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
      `最新价: ${data.price}  |  涨跌幅: ${data.change_pct}%  |  换手率: ${data.turnover_rate || '-'}`,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
    ];
    if (data.source === 'eastmoney' || data.source === 'eastmoney_stock_get') {
      const fmtRow = (label: string, net: number, pct: string) =>
        `  ${label}: ${sign(net)}${formatAmount(net)}  (${pct || '-'}%)`;
      lines.push('[今日]');
      lines.push(fmtRow('主力  ', data.main_net, data.main_pct));
      lines.push(fmtRow('超大单', data.lg_net, data.lg_pct));
      lines.push(fmtRow('大  单', data.md_net, data.md_pct));
      lines.push(fmtRow('中  单', data.sm_net, data.sm_pct));
      lines.push(fmtRow('小  单', data.xs_net, data.xs_pct));
      if (data.d5_main_net) {
        lines.push('');
        lines.push('[5日参考]');
        lines.push(fmtRow('主力  ', data.d5_main_net, data.d5_main_pct));
        lines.push(fmtRow('超大单', data.d5_lg_net, data.d5_lg_pct));
        lines.push(fmtRow('大  单', data.d5_md_net, data.d5_md_pct));
      }
      if (data.d10_main_net) {
        lines.push('');
        lines.push('[10日参考]');
        lines.push(fmtRow('主力  ', data.d10_main_net, data.d10_main_pct));
        lines.push(fmtRow('超大单', data.d10_lg_net, data.d10_lg_pct));
        lines.push(fmtRow('大  单', data.d10_md_net, data.d10_md_pct));
      }
    } else {
      lines.push(`🔴 流入: ${formatAmount(data.inflow)}  |  🟢 流出: ${formatAmount(data.outflow)}`);
      lines.push(`📊 净额: ${sign(data.net_amount)}${formatAmount(data.net_amount)}`);
    }
    lines.push(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
    const srcMap: Record<string, string> = { eastmoney_stock_get: '东方财富(实时)', eastmoney: '东方财富(即时)', ths: '同花顺(即时)', tushare: 'Tushare(日频降级)' };
    lines.push(`数据源: ${srcMap[data.source] || data.source}`);
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getTechnicalTool = {
  name: 'get_technical',
  label: '技术指标',
  description: '【日频·非实时】获取A股个股历史盘后技术面因子数据，包含MACD、KDJ、RSI、布林带等60+指标。数据源：Tushare stk_factor_pro（盘后数据，基于收盘价计算）。⚠️ 返回的是最近收盘日的已确认值，不是当日盘中值。用于判断超买超卖、背离、趋势强度、金叉死叉等交易信号。如需当日盘中指标请用 get_realtime_indicators',
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
    const latestDate = rows[0].trade_date || '--';
    const lines: string[] = [];
    lines.push(`${data.symbol} 技术指标 (最近${rows.length}条，日频·非实时·Tushare stk_factor_pro 盘后确认值)`);
    lines.push(`⚠️ 数据截止日期: ${latestDate}（最近收盘日，基于收盘价计算，非当日盘中值）`);
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

// ===== 技术指标工具（牛股计算器策略） =====

export const getFibonacciLevelsTool = {
  name: 'get_fibonacci_levels',
  label: '斐波那契回撤',
  description: '计算斐波那契回撤价位（0.382/0.618/0.786），用于判断支撑/阻力位和当前价格所处区间。传入 symbol 自动从90天K线提取阶段顶/底，也可手动指定 high/low。用于右侧交易寻找入场点和止损位参考',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字 600519' }),
    high: Type.Optional(Type.Number({ description: '阶段顶部价格（不传则自动从K线提取）' })),
    low: Type.Optional(Type.Number({ description: '阶段底部价格（不传则自动从K线提取）' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; high?: number; low?: number }, _signal?: AbortSignal) {
    const body: Record<string, unknown> = { symbol: params.symbol };
    if (params.high !== undefined) body.high = params.high;
    if (params.low !== undefined) body.low = params.low;
    const data = await apiFetch('/indicator/fibonacci', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (data.error) throw new Error(data.error);
    const lines = [
      `📊 ${data.symbol} 斐波那契回撤分析`,
      `阶段顶部: ${data.high}  阶段底部: ${data.low}  差价: ${data.diff}`,
      `当前价格: ${data.current_price}`,
      '',
      `回撤价位:`,
    ];
    for (const lv of data.levels || []) {
      const isCurrent = data.current_price && lv.price ? 
        (data.current_price <= lv.price * 1.03 && data.current_price >= lv.price * 0.97) : false;
      const marker = isCurrent ? ' ◀ 当前附近' : '';
      lines.push(`  ${lv.ratio} (${(lv.ratio*100).toFixed(1)}%): ${lv.price} — ${lv.label}${marker}`);
    }
    lines.push('');
    lines.push(`📍 当前区间: ${data.position_zone}`);
    lines.push(`💡 建议: ${data.zone_suggestion}`);
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getDailyChannelTool = {
  name: 'get_daily_channel',
  label: '日内通道',
  description: '计算日内压力/支撑通道（基于K=0.98848常数）。压力线=分时均价/K，支撑线=分时均价×K。用于判断日内超短线交易的精确入场/离场价位',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字 600519' }),
    avg_price: Type.Optional(Type.Number({ description: '分时均价（不传则从行情估算）' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; avg_price?: number }, _signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (params.avg_price !== undefined) query.set('avg_price', String(params.avg_price));
    const qs = query.toString();
    const data = await apiFetch(`/indicator/daily-channel/${params.symbol}${qs ? '?' + qs : ''}`);
    if (data.error) throw new Error(data.error);
    const lines = [
      `📊 ${data.symbol} 日内K值通道 (K=${data.constant_k})`,
      `分时均价: ${data.avg_price}`,
      `当前价格: ${data.current_price}`,
      '',
      `🔴 压力线: ${data.top_line} (均价/K)`,
      `🟢 支撑线: ${data.bottom_line} (均价×K)`,
      `通道宽度: ${data.channel_width_pct}%`,
      '',
      `📍 当前位置: ${data.position}`,
    ];
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

export const getTradeAdviceTool = {
  name: 'get_trade_advice',
  label: '操作建议',
  description: '获取完整的股票操作建议（牛股计算器决策树）。当你需要判断某只股票该买入/持有/卖出时调用此工具。结合斐波那契回撤、K值通道、时间证伪、破底止损等规则给出明确信号。cost（成本价）有值则为持仓模式，不传则为观察模式。触发场景：用户问"这只股票怎么看""该买还是该卖""现在什么建议""帮我分析持仓"',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字 600519' }),
    cost: Type.Optional(Type.Number({ description: '成本价（有持仓时传入，触发持仓模式决策逻辑）' })),
    high: Type.Optional(Type.Number({ description: '阶段顶部价格（不传则自动从K线提取）' })),
    low: Type.Optional(Type.Number({ description: '阶段底部价格（不传则自动从K线提取）' })),
    avg_price: Type.Optional(Type.Number({ description: '分时均价（用于K值通道计算，不传则用当前价估算）' })),
    buy_date: Type.Optional(Type.String({ description: '建仓日期 YYYY-MM-DD（有持仓时传入）' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; cost?: number; high?: number; low?: number; avg_price?: number; buy_date?: string }, _signal?: AbortSignal) {
    const body: Record<string, unknown> = { symbol: params.symbol };
    if (params.cost !== undefined) body.cost = params.cost;
    if (params.high !== undefined) body.high = params.high;
    if (params.low !== undefined) body.low = params.low;
    if (params.avg_price !== undefined) body.avg_price = params.avg_price;
    if (params.buy_date !== undefined) body.buy_date = params.buy_date;
    const data = await apiFetch('/indicator/advice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (data.error) throw new Error(data.error);
    const signalEmoji: Record<string, string> = {
      danger: '🔴', warning: '🟡', gold: '🏆', blue: '🔵', cyan: '🩵', normal: '⚪',
    };
    const lines = [
      `📊 ${data.symbol}${data.name ? ' ' + data.name : ''} 操作建议`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `当前价: ${data.current_price}  (${data.change_pct >= 0 ? '+' : ''}${data.change_pct}%)`,
      `模式: ${data.mode === 'holding' ? '🏠 持仓模式' : '👀 观察模式'}`,
      '',
    ];
    if (data.mode === 'holding' && data.cost) {
      lines.push(`💰 成本价: ${data.cost}`);
      if (data.hold_days !== null) lines.push(`📅 持仓: ${data.hold_days} 个交易日`);
      if (data.high_water_mark) lines.push(`📈 最高价: ${data.high_water_mark} (${data.days_since_high}天前)`);
      lines.push('');
    }
    lines.push(`── 斐波那契回撤 ──`);
    lines.push(`  0.382 (常规买点): ${data.fib_382}`);
    lines.push(`  0.618 (强防生死线): ${data.fib_618}`);
    lines.push(`  0.786 (深坑/放弃): ${data.fib_786}`);
    lines.push('');
    lines.push(`── K值通道 (K=0.98848) ──`);
    lines.push(`  🔴 压力线: ${data.k_channel_top}`);
    lines.push(`  🟢 支撑线: ${data.k_channel_bottom}`);
    lines.push(`  宽度: ${data.k_channel_width_pct}%`);
    lines.push('');
    lines.push(`${signalEmoji[data.signal_class] || '⚪'} 操作建议: ${data.signal}`);
    if (data.signal_details && data.signal_details.length > 0) {
      for (const d of data.signal_details) {
        lines.push(`  └ ${d}`);
      }
    }
    if (data.risk_flags && data.risk_flags.length > 0) {
      lines.push('');
      lines.push(`⚠️ 风险标记: ${data.risk_flags.join(' / ')}`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

// ===== 盘中实时技术指标工具（腾讯行情+Tushare历史结合计算） =====

export const getRealtimeIndicatorsTool = {
  name: 'get_realtime_indicators',
  label: '实时技术指标',
  description: '【实时·盘中估算】获取个股盘中实时估算技术指标（KDJ/MACD/RSI/MA5/MA10/MA20）。数据源：腾讯qt.gtimg.cn实时行情+Tushare历史日线计算。⚠️ data_source="intraday_estimate"（盘中估算），今日高低点未最终确认，仅作辅助参考，不能作为独立建仓的唯一理由。同时返回最近3日Tushare盘后确认值作基准对比',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字 600519' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal?: AbortSignal) {
    const data = await apiFetch(`/indicator/realtime/${params.symbol}`);
    if (data.error) throw new Error(data.error);

    const rt = data.realtime;
    const hist = data.historical || [];
    const warning = data.warning || '';

    if (!rt) {
      return { content: [{ type: 'text', text: `${data.symbol || params.symbol}: 盘中实时指标不可用。${warning}` }], details: data };
    }

    const lines: string[] = [];
    lines.push(`🔴 实时盘中指标 — ${data.name || data.symbol}`);
    lines.push(`计算时间: ${rt.calc_time || data.updated_at || '--'} | 当前价: ${rt.current_price} | 锚点日期: ${rt.prev_trade_date || '--'}`);
    lines.push(`⚠️ 数据来源: ${rt.data_source || 'intraday_estimate'}（腾讯实时行情+Tushare历史日线），未收盘确认，仅辅助参考`);
    lines.push(`⚠️ ${rt.warning || warning}`);
    lines.push('');
    lines.push('── 盘中估算值（data_source=intraday_estimate）──');
    // KDJ
    const kdj_signal = rt.kdj_k > rt.kdj_d ? 'K>D ↑' : rt.kdj_k < rt.kdj_d ? 'K<D ↓' : 'K=D ─';
    lines.push(`KDJ(9,3,3): K=${rt.kdj_k.toFixed(2)} D=${rt.kdj_d.toFixed(2)} J=${rt.kdj_j.toFixed(2)} [${kdj_signal}]`);
    // MACD
    const macd_signal = rt.macd_dif > rt.macd_dea ? 'DIF>DEA ↑' : rt.macd_dif < rt.macd_dea ? 'DIF<DEA ↓' : 'DIF=DEA ─';
    lines.push(`MACD(12,26,9): DIF=${rt.macd_dif.toFixed(4)} DEA=${rt.macd_dea.toFixed(4)} 柱=${rt.macd_bar >= 0 ? '+' : ''}${rt.macd_bar.toFixed(4)} [${macd_signal}]`);
    // RSI
    const rsi6_label = rt.rsi_6 >= 70 ? '超买' : rt.rsi_6 <= 30 ? '超卖' : '正常';
    const rsi12_label = rt.rsi_12 >= 70 ? '超买' : rt.rsi_12 <= 30 ? '超卖' : '正常';
    lines.push(`RSI: 6=${rt.rsi_6.toFixed(2)}[${rsi6_label}] 12=${rt.rsi_12.toFixed(2)}[${rsi12_label}] 24=${rt.rsi_24.toFixed(2)}`);
    // MA
    const ma_position = rt.current_price > rt.ma5 ? '价>MA5 ↑' : rt.current_price < rt.ma5 ? '价<MA5 ↓' : '价=MA5 ─';
    lines.push(`MA: 5=${rt.ma5.toFixed(2)} 10=${rt.ma10.toFixed(2)} 20=${rt.ma20.toFixed(2)} [${ma_position}]`);

    if (hist.length > 0) {
      lines.push('');
      lines.push('── 盘后确认值（Tushare stk_factor_pro，基准对比）──');
      for (const h of hist.slice(0, 3)) {
        const kdj_s = h.kdj_k > h.kdj_d ? '金叉' : h.kdj_k < h.kdj_d ? '死叉' : '持平';
        const macd_s = h.macd_dif > h.macd_dea ? '金叉' : h.macd_dif < h.macd_dea ? '死叉' : '持平';
        lines.push(`  ${h.trade_date}: KDJ(${h.kdj_k.toFixed(1)}/${h.kdj_d.toFixed(1)})${kdj_s} | MACD(${h.macd_dif.toFixed(3)}/${h.macd_dea.toFixed(3)})${macd_s} | RSI6=${h.rsi_6.toFixed(1)}`);
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
  getIndustryFundFlowTool,
  getMarketMoneyflowTool,
  getConceptMappingTool,
  getEtfQuoteTool,
  getEtfKlineTool,
  getDailyKlineTool,
  getMoneyflowTool,
  getTechnicalTool,
  getRealtimeIndicatorsTool,
  getFibonacciLevelsTool,
  getDailyChannelTool,
  getTradeAdviceTool,
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
// 操作: get_panel_history, get_daily_kline, get_technical, get_realtime_indicators, get_moneyflow, read_db_table
// 与聊天/交易模式完全隔离，无交易权限
export const REFLECT_TOOLS = [
  // Tushare 历史数据
  getDailyKlineQfqTool,
  getTechnicalTool,
  getMoneyflowTool,
  getRealtimeIndicatorsTool,
  getFibonacciLevelsTool,
  getDailyChannelTool,
  getTradeAdviceTool,
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
