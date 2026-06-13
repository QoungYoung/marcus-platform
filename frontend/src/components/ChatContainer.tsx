import { useCallback, useEffect, useRef, useState } from 'react';

/** Generate UUID v4 - works in both secure and non-secure contexts */
function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Fallback for HTTP deployments
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
import { html } from 'lit';
import type { LitElement } from 'lit';
import { Agent, type AgentState, type AgentTool } from '@earendil-works/pi-agent-core';
import { getModel, Type } from '@earendil-works/pi-ai';
import {
  ChatPanel,
  IndexedDBStorageBackend,
  ProviderKeysStore,
  SessionsStore,
  SettingsStore,
  AppStorage,
  setAppStorage,
  defaultConvertToLlm,
  ApiKeyPromptDialog,
  CustomProvidersStore,
  registerMessageRenderer,
  registerToolRenderer,
  type MessageRenderer,
} from '@earendil-works/pi-web-ui';
import '@earendil-works/pi-web-ui/app.css';
import '../styles/agent-theme.css';

const MARCUS_API = '/api/v1';

interface IndexData {
  name: string;
  current_price: string;
  change_pct: number;
}

// Tool definitions
const getMarketIndicesTool = {
  name: 'get_market_indices',
  description: '获取 A股指数（上证、深证、创业板）、美股指数、港股指数的实时行情',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/market/indices`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    const indices = data.indices || [];
    const lines = indices.map((idx: any) => {
      const sign = idx.change_pct >= 0 ? '+' : '';
      return `${idx.name}: ${idx.current_price} (${sign}${idx.change_pct}%)`;
    }).join('\n');
    return { content: [{ type: 'text', text: lines || '暂无数据' }], details: data };
  },
};

const getQuoteTool = {
  name: 'get_quote',
  description: '查询个股实时行情，包括当前价格、涨跌、成交量等',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/market/quote/${params.symbol}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const q = data;
    const sign = q.percent >= 0 ? '+' : '';
    const lines = [`${q.name} (${q.symbol})`,
`当前价: ${q.current}  涨跌: ${sign}${q.change} (${sign}${q.percent}%)`,
`今开: ${q.open}  最高: ${q.high}  最低: ${q.low}`,
`昨收: ${q.last_close}  成交量: ${q.volume}  成交额: ${q.amount}`];
    if (q.turnover_rate) lines.push(`换手率: ${q.turnover_rate}%  振幅: ${q.amplitude || '--'}%`);
    if (q.pe_ttm) lines.push(`市盈率: ${q.pe_ttm}  市净率: ${q.pb || '--'}`);
    const text = lines.join('\n');
    return { content: [{ type: 'text', text }], details: data };
  },
};

const getPortfolioTool = {
  name: 'get_portfolio',
  description: '查看当前账户资金状况和所有持仓',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/portfolio`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    const acc = data.account || {};
    const lines = [
      `账户总览`,
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
        lines.push(`${p.name}(${p.symbol}): ${p.volume}股 成本${p.avg_price?.toFixed(2)} 现价${p.current_price?.toFixed(2)} 浮动${sign}${p.floating_pnl?.toFixed(2)}(${sign}${p.floating_pnl_pct?.toFixed(2)}%)`);
      });
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getConceptFundFlowTool = {
  name: 'get_concept_fund_flow',
  description: '获取概念板块实时行情排行（按涨幅或主力资金流向排序）。数据源：东财push2实时接口(主力/超大单/大单/中单/小单净流入+板块广度+领涨股)，Tushare降级兜底。sort_by=pct_change看涨幅榜，sort_by=main_net看资金榜',
  parameters: Type.Object({
    limit: Type.Optional(Type.Number({ description: '返回数量，默认15' })),
    sort_by: Type.Optional(Type.String({ description: '排序字段: pct_change(涨幅) / main_net(主力净流入)' })),
  }),
  async execute(_toolCallId: string, params: { limit?: number; sort_by?: string }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    const qs = query.toString();
    const res = await fetch(`${MARCUS_API}/market/concept-fund-flow${qs ? '?' + qs : ''}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const sectors = data.sectors || [];
    if (sectors.length === 0) {
      return { content: [{ type: 'text', text: '暂无概念板块行情数据' }], details: data };
    }
    const tradeDate = data.trade_date ? `日期: ${data.trade_date}` : '';
    const sortLabel = params.sort_by === 'main_net' ? '资金流入排行' : '涨幅排行';
    const lines = [`📊 概念板块行情 (${sortLabel})`, tradeDate, ''];
    sectors.forEach((s: any, idx: number) => {
      const sign = s.pct_change >= 0 ? '+' : '';
      const amountYi = (s.amount / 100000000).toFixed(2);
      let line = `${idx + 1}. ${s.name} | 涨跌:${sign}${s.pct_change}% | 成交:${amountYi}亿`;
      // 附加资金流数据（如果有）
      if (s.main_net_fmt) {
        const nature = s.flow_nature || '';
        line += ` | 主力:${s.main_net_fmt} ${nature}`;
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

const getConceptMappingTool = {
  name: 'get_concept_mapping',
  description: '查询东方财富概念板块及其成分股。不传参数则列出所有概念，传concept_name则返回该概念下的所有股票',
  parameters: Type.Object({
    concept_name: Type.Optional(Type.String({ description: '概念名称，如 人形机器人、固态电池、AI芯片。不传则返回所有概念列表' })),
    limit: Type.Optional(Type.Number({ description: '返回数量，默认30' })),
  }),
  async execute(_toolCallId: string, params: { concept_name?: string; limit?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.concept_name) query.set('concept', params.concept_name);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const res = await fetch(`${MARCUS_API}/market/concept${qs ? '?' + qs : ''}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const concepts = data.concepts || [];
    const total = data.total || concepts.length;

    if (params.concept_name) {
      // 查询某一概念下的股票
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
      // 列出所有概念
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

const getEtfQuoteTool = {
  name: 'get_etf_quote',
  description: '查询ETF基金的实时行情',
  parameters: Type.Object({
    symbol: Type.String({ description: 'ETF代码，如 510300、159915' }),
  }),
  async execute(_toolCallId: string, params: { symbol: string }, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/etf/quote/${params.symbol}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
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
    ].join('\n');
    return { content: [{ type: 'text', text }], details: data };
  },
};

const getEtfKlineTool = {
  name: 'get_etf_kline',
  description: '获取ETF历史K线数据，包含开高低收、成交量、成交额等。用于分析ETF走势和趋势判断',
  parameters: Type.Object({
    symbol: Type.String({ description: 'ETF代码，如 159513、510300' }),
    period: Type.Optional(Type.String({ description: 'K线周期: day/week/month，默认day' })),
    count: Type.Optional(Type.Number({ description: '数据条数，默认284（约一年日线）' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; period?: string; count?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.period) query.set('period', params.period);
    if (params.count) query.set('count', String(params.count));
    const qs = query.toString();
    const res = await fetch(`${MARCUS_API}/etf/kline/${params.symbol}${qs ? '?' + qs : ''}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const klines = data.klines || [];
    if (klines.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的ETF K线数据` }], details: data };
    }
    const lines = [`📊 ${params.symbol} ETF K线 (${params.period || 'day'}) - 共${klines.length}条`, ''];
    for (const k of klines.slice(-20)) {
      const up = k.close >= k.open ? '📈' : '📉';
      const dateStr = k.timestamp ? new Date(k.timestamp).toISOString().slice(0, 10) : '--';
      lines.push(`${dateStr} | 开:${k.open?.toFixed(3)} 高:${k.high?.toFixed(3)} 低:${k.low?.toFixed(3)} 收:${k.close?.toFixed(3)} ${up} 量:${(k.volume / 1e4).toFixed(0)}万`);
    }
    if (klines.length > 20) {
      lines.push(`... 仅显示最近20条，共${klines.length}条`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const readDbTableTool = {
  name: 'read_db_table',
  description: '读取数据库表的数据，支持查询、筛选和排序',
  parameters: Type.Object({
    db: Type.String({ description: '数据库名(files): stock_pool.db, trades.db, news.db, cache.db' }),
    table: Type.String({ description: '表名。stock_pool.db有: stock_pool(全A股,含symbol/name/industry/market_cap), sectors, stock_concept_map' }),
    columns: Type.Optional(Type.String({ description: '要查询的列，逗号分隔' })),
    where: Type.Optional(Type.String({ description: 'WHERE条件' })),
    orderBy: Type.Optional(Type.String({ description: '排序，如 change_pct DESC' })),
    limit: Type.Optional(Type.Number({ description: '返回条数，默认100' })),
  }),
  async execute(_toolCallId: string, params: { db: string; table: string; columns?: string; where?: string; orderBy?: string; limit?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams({ db: params.db, table: params.table });
    if (params.columns) query.set('columns', params.columns);
    if (params.where) query.set('where', params.where);
    if (params.orderBy) query.set('order_by', params.orderBy);
    if (params.limit) query.set('limit', String(params.limit));
    const res = await fetch(`${MARCUS_API}/db/query?${query}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return { content: [{ type: 'text', text: JSON.stringify(data.rows || [], null, 2) }], details: data };
  },
};

const getDbSchemaTool = {
  name: 'get_db_schema',
  description: '获取数据库的表结构和字段信息',
  parameters: Type.Object({
    db: Type.String({ description: '数据库名: stock_pool, trades, news, cache' }),
  }),
  async execute(_toolCallId: string, params: { db: string }, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/db/schema/${params.db}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return { content: [{ type: 'text', text: JSON.stringify(data.schema || [], null, 2) }], details: data };
  },
};

const getDailyKlineTool = {
  name: 'get_daily_kline',
  description: '获取A股个股历史日K线数据（前复权 qfq），包含开高低收、成交量、成交额等。数据源：Tushare。用于分析个股历史走势、判断趋势、寻找支撑阻力位。前复权保证了除权除息日无价格跳空，技术指标连续可靠',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认90天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认100，最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const url = `${MARCUS_API}/market/kline/${params.symbol}${qs ? '?' + qs : ''}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const klines = data.klines || [];
    if (klines.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的K线数据，请检查股票代码和日期范围是否正确。` }], details: data };
    }
    // 构建结构化输出：表头 + 最近N条数据 + 基本统计
    const lines: string[] = [];
    lines.push(`${data.symbol} 历史日K线 (最近${klines.length}条)`);
    lines.push('日期       | 开盘   | 收盘   | 最高   | 最低   | 涨跌幅  | 成交量(手) | 成交额(万元)');
    lines.push('-'.repeat(85));
    for (const k of klines.slice(0, 20)) {
      const sign = k.pct_chg >= 0 ? '+' : '';
      const volWan = (k.vol / 100).toFixed(0); // 手转万股
      const amtWan = (k.amount / 10).toFixed(0); // 千元转万元
      lines.push(`${k.trade_date} | ${k.open.toFixed(2).padStart(6)} | ${k.close.toFixed(2).padStart(6)} | ${k.high.toFixed(2).padStart(6)} | ${k.low.toFixed(2).padStart(6)} | ${sign}${k.pct_chg.toFixed(2)}% | ${volWan.padStart(9)} | ${amtWan.padStart(10)}`);
    }
    // 基本统计
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
      lines.push(`区间涨跌: ${totalChg}% (${firstClose.toFixed(2)} → ${lastClose.toFixed(2)})`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getMoneyflowTool = {
  name: 'get_moneyflow',
  description: '获取A股个股资金流向数据，分析大单/小单/特大单净流入/净流出情况。数据源：Tushare。用于判断主力资金是否在入场或出逃，配合K线趋势确认信号',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认30天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认30，最大100' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const url = `${MARCUS_API}/market/moneyflow/${params.symbol}${qs ? '?' + qs : ''}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const flows = data.flows || [];
    if (flows.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的资金流向数据，请检查股票代码和日期范围是否正确。` }], details: data };
    }
    // 构建结构化输出
    const lines: string[] = [];
    lines.push(`${data.symbol} 资金流向 (最近${flows.length}条)`);
    lines.push('日期       | 特大单净流入(万) | 大单净流入(万) | 中单净流入(万) | 小单净流入(万) | 当日净流入(万)');
    lines.push('-'.repeat(90));
    for (const f of flows.slice(0, 20)) {
      const net = f.net_mf_amount >= 0 ? '+' : '';
      lines.push(`${f.trade_date} | ${(f.buy_elg_amount - f.sell_elg_amount).toFixed(0).padStart(13)} | ${(f.buy_lg_amount - f.sell_lg_amount).toFixed(0).padStart(13)} | ${(f.buy_md_amount - f.sell_md_amount).toFixed(0).padStart(13)} | ${(f.buy_sm_amount - f.sell_sm_amount).toFixed(0).padStart(13)} | ${net}${f.net_mf_amount.toFixed(0).padStart(12)}`);
    }
    // 基本统计
    if (flows.length >= 3) {
      const recentNet = flows.slice(0, 3).reduce((s: number, f: any) => s + f.net_mf_amount, 0);
      const sign = recentNet >= 0 ? '+' : '';
      lines.push('');
      lines.push(`近3日主力净流入: ${sign}${(recentNet / 10000).toFixed(2)}万元`);
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getTechnicalTool = {
  name: 'get_technical',
  description: '获取A股个股技术面因子数据，包含MACD、KDJ、RSI、布林带等60+指标。数据源：Tushare stk_factor_pro接口。用于判断超买超卖、背离、趋势强度、金叉死叉等交易信号',
  parameters: Type.Object({
    symbol: Type.String({ description: '股票代码，如 SH600519、SZ000001 或纯数字如 600519' }),
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYYMMDD，如 20240101，默认90天前' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYYMMDD，如 20240524，默认今天' })),
    limit: Type.Optional(Type.Number({ description: '返回条数上限，默认100，最大500' })),
  }),
  async execute(_toolCallId: string, params: { symbol: string; start_date?: string; end_date?: string; limit?: number }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    const url = `${MARCUS_API}/market/technical/${params.symbol}${qs ? '?' + qs : ''}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const rows = data.data || [];
    if (rows.length === 0) {
      return { content: [{ type: 'text', text: `未获取到 ${params.symbol} 的技术指标数据，请检查股票代码和日期范围是否正确。` }], details: data };
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
    // 信号提示
    if (rows.length >= 2) {
      const latest = rows[0];
      const prev = rows[1];
      const signals: string[] = [];
      // MACD 金叉/死叉
      if (prev.macd_dif < prev.macd_dea && latest.macd_dif >= latest.macd_dea) signals.push('MACD 金叉↑');
      if (prev.macd_dif > prev.macd_dea && latest.macd_dif <= latest.macd_dea) signals.push('MACD 死叉↓');
      // KDJ 超买超卖
      if (latest.kdj >= 80) signals.push('KDJ 超买');
      if (latest.kdj <= 20) signals.push('KDJ 超卖');
      // RSI
      if (latest.rsi_6 >= 70) signals.push('RSI6 超买');
      if (latest.rsi_6 <= 30) signals.push('RSI6 超卖');
      // 收盘价与布林带关系
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

const getMarketMoneyflowTool = {
  name: 'get_market_moneyflow',
  label: '大盘资金流向',
  description: '获取沪深两市大盘实时资金流向（主力/超大单/大单/中单/小单净流入+买/卖分明细+总成交额）。数据源：东财push2实时(优先)+Tushare日频(降级)。用于判断大盘整体资金情绪和主力动向',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal: AbortSignal | undefined) {
    const res = await fetch(`${MARCUS_API}/market/moneyflow-mkt`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const m = data.data;
    if (!m) return { content: [{ type: 'text', text: '暂无大盘资金流向数据' }], details: data };
    const isRealtime = (m.data_source || '').includes('实时');
    const label = isRealtime ? '实时' : '日频';
    let totalAmountLine = '';
    if (m.total_amount_fmt) {
      totalAmountLine = `总成交: ${m.total_amount_fmt}`;
    }
    const lines = [`大盘资金流向 ${m.trade_date} (${label})`, totalAmountLine].filter(Boolean);
    if (m.close_sh || m.close_sz) {
      const signSh = m.pct_change_sh >= 0 ? '+' : '';
      const signSz = m.pct_change_sz >= 0 ? '+' : '';
      lines.push(`上证: ${m.close_sh} (${signSh}${m.pct_change_sh}%)`);
      lines.push(`深证: ${m.close_sz} (${signSz}${m.pct_change_sz}%)`);
    }
    // 沪深分开（实时数据有）
    if (data.sh && data.sz) {
      lines.push(`沪市主力: ${data.sh.main_net_fmt} | 深市主力: ${data.sz.main_net_fmt}`);
    }
    lines.push(
      `主力净流入: ${m.net_amount_fmt}${m.net_amount_rate ? ` (${m.net_amount_rate}%)` : ''}`,
      `超大单: ${(m.buy_elg_amount / 10000).toFixed(2)}亿 (${m.buy_elg_amount_rate}%)`,
      `大单: ${(m.buy_lg_amount / 10000).toFixed(2)}亿 (${m.buy_lg_amount_rate}%)`,
      `中单: ${(m.buy_md_amount / 10000).toFixed(2)}亿 (${m.buy_md_amount_rate}%)`,
      `小单: ${(m.buy_sm_amount / 10000).toFixed(2)}亿 (${m.buy_sm_amount_rate}%)`,
      `性质: ${m.flow_nature}`,
    );
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getLatestScanReportTool = {
  name: 'get_latest_scan_report',
  label: '最新扫描报告',
  description: '获取最新的盘中扫描报告，包含市场立场、热门概念、观察列表、完整分析。用于复盘时了解当前市场状态和交易背景',
  parameters: Type.Object({
    date: Type.Optional(Type.String({ description: '日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { date?: string }, _signal: AbortSignal | undefined) {
    const query = params.date ? `?date=${params.date}` : '';
    let data: any;
    try {
      const res = await fetch(`${MARCUS_API}/scan/latest${query}`);
      if (!res.ok) throw new Error(`API error ${res.status}`);
      data = await res.json();
    } catch (e: any) {
      const reason = e?.message?.includes('404') ? '今日暂无扫描报告' : `API 错误: ${e.message}`;
      return { content: [{ type: 'text', text: `📊 盘中扫描报告: ${reason}` }], details: { error: reason } };
    }
    if (data.error) throw new Error(data.error);
    const lines = [
      `📊 盘中扫描报告 (${data.timestamp || '--'})`,
      `市场立场: ${data.market_stance} (仓位上限: ${data.position_limit}%)`,
      '',
    ];
    if (data.hot_concepts?.length > 0) {
      lines.push('🔥 热门概念:');
      for (const c of data.hot_concepts.slice(0, 8)) {
        const name = typeof c === 'string' ? c : (c.name || c.concept || JSON.stringify(c));
        lines.push(`  - ${name}`);
      }
      lines.push('');
    }
    if (data.report) {
      lines.push('📝 系统扫描报告:');
      lines.push(data.report.slice(0, 3000));
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getPiAnalysisHistoryTool = {
  name: 'get_pi_analysis_history',
  label: 'Pi分析历史',
  description: '按日期范围查询整周 Pi 分析历史记录。返回每天每轮扫描的 Pi 策略分析，包含 stance（立场）、position_limit（仓位上限）、reason（判断理由）和完整 report。用于周度反思时回顾整周策略演变',
  parameters: Type.Object({
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYY-MM-DD，默认本周一' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { start_date?: string; end_date?: string }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    const qs = query.toString();
    const res = await fetch(`${MARCUS_API}/scan/pi-analysis${qs ? '?' + qs : ''}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const records = data.records || [];
    if (records.length === 0) {
      return { content: [{ type: 'text', text: `📋 日期范围 ${data.date_range?.start || '--'} 至 ${data.date_range?.end || '--'} 内暂无 Pi 分析记录` }], details: data };
    }
    const lines = [`📊 Pi 分析历史 (${data.date_range?.start || '--'} → ${data.date_range?.end || '--'})`, `共 ${data.days_count} 天，${data.total_records} 条记录`, ''];
    let currentDate = '';
    for (const r of records) {
      if (r.date !== currentDate) {
        currentDate = r.date;
        lines.push(`--- ${currentDate} ---`);
      }
      const time = r.timestamp ? r.timestamp.slice(11, 19) : '--';
      lines.push(`  [${time}] ${r.task_name || '--'} | 立场: ${r.stance || '--'} | 仓位上限: ${r.position_limit || '--'}%`);
      if (r.reason) lines.push(`     理由: ${r.reason}`);
      if (r.report) {
        const brief = r.report.length > 300 ? r.report.slice(0, 300) + '...' : r.report;
        lines.push(`     报告: ${brief.replace(/\n/g, ' ')}`);
      }
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

const getTradeHistoryTool = {
  name: 'get_trade_history',
  label: '交易报告历史',
  description: '按日期范围查询整周 Pi 交易执行报告。返回每天每次交易窗口的完整报告，包含买卖决策、仓位变化、产业链组合逻辑、风险监控等。用于复盘时评估策略执行质量',
  parameters: Type.Object({
    start_date: Type.Optional(Type.String({ description: '开始日期 YYYY-MM-DD，默认本周一' })),
    end_date: Type.Optional(Type.String({ description: '结束日期 YYYY-MM-DD，默认今天' })),
  }),
  async execute(_toolCallId: string, params: { start_date?: string; end_date?: string }, _signal: AbortSignal | undefined) {
    const query = new URLSearchParams();
    if (params.start_date) query.set('start_date', params.start_date);
    if (params.end_date) query.set('end_date', params.end_date);
    const qs = query.toString();
    const res = await fetch(`${MARCUS_API}/scan/trade-reports${qs ? '?' + qs : ''}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const records = data.records || [];
    if (records.length === 0) {
      return { content: [{ type: 'text', text: `📋 日期范围 ${data.date_range?.start || '--'} 至 ${data.date_range?.end || '--'} 内暂无交易执行报告` }], details: data };
    }
    const lines = [`📊 交易执行报告 (${data.date_range?.start || '--'} → ${data.date_range?.end || '--'})`, `共 ${data.days_count} 天，${data.total_records} 条记录`, ''];
    let currentDate = '';
    for (const r of records) {
      if (r.date !== currentDate) { currentDate = r.date; lines.push(`--- ${currentDate} ---`); }
      const time = r.timestamp ? r.timestamp.slice(11, 19) : '--';
      const taskLabel = (r.task_id || '').includes('morning') ? '早盘' : (r.task_id || '').includes('late') ? '午前' : (r.task_id || '').includes('afternoon') ? '午后' : (r.task_id || '').includes('closing') ? '尾盘' : (r.task_id || '');
      lines.push(`  [${time}] ${taskLabel} | 立场: ${r.stance || '--'} | 仓位上限: ${r.position_limit || '--'}%`);
      if (r.reason) lines.push(`     理由: ${r.reason}`);
      if (r.report) { const brief = r.report.length > 400 ? r.report.slice(0, 400) + '...' : r.report; lines.push(`     报告: ${brief.replace(/\n/g, ' ')}`); }
    }
    return { content: [{ type: 'text', text: lines.join('\n') }], details: data };
  },
};

// Convert tools to AgentTool format
function createTool(toolDef: any): AgentTool {
  return {
    name: toolDef.name,
    label: toolDef.label || toolDef.name,
    description: toolDef.description,
    parameters: toolDef.parameters,
    execute: toolDef.execute,
  } as AgentTool;
}

// Helper: 给原始文本保持 $SYMBOL(NAME) 格式，不做 HTML 注入
// 后渲染阶段通过 DOM 扫描来处理（因为 markdown-block 会转义 HTML）
function processMentions(text: string): string {
  return text; // 保持原样
}

// 后渲染：扫描 DOM 中的 $SYMBOL(NAME) 文本节点并替换为可点击 span
function applyMentionSpans(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      // 跳过 stock-mention 内部和 script/style
      const parent = node.parentElement;
      if (!parent || parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE') return NodeFilter.FILTER_REJECT;
      if (parent.closest('.stock-mention')) return NodeFilter.FILTER_REJECT;
      // 跳过 message-editor 里的 textarea
      if (parent.closest('message-editor')) return NodeFilter.FILTER_REJECT;
      return /\$(\w+)\([^)]+\)/.test(node.textContent || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);

  for (const textNode of nodes) {
    const text = textNode.textContent || '';
    const parent = textNode.parentNode;
    if (!parent) continue;

    const fragment = document.createDocumentFragment();
    let lastIdx = 0;
    const re = /\$(\w+)\(([^)]+)\)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      // 前面的普通文本
      if (m.index > lastIdx) {
        fragment.appendChild(document.createTextNode(text.slice(lastIdx, m.index)));
      }
      // 替换为 span
      const span = document.createElement('span');
      span.className = 'stock-mention';
      span.dataset.symbol = m[1];
      span.dataset.name = m[2];
      span.title = '点击查看详情';
      span.textContent = `${m[2]}(${m[1]})`;
      fragment.appendChild(span);
      lastIdx = m.index + m[0].length;
    }
    // 剩余文本
    if (lastIdx < text.length) {
      fragment.appendChild(document.createTextNode(text.slice(lastIdx)));
    }

    parent.replaceChild(fragment, textNode);
  }
}

// Register custom message renderers
registerMessageRenderer('user', {
  render: (msg: any) => {
    const rawText = typeof msg.content === 'string' ? msg.content : msg.content?.[0]?.text || '';
    const processed = processMentions(rawText);
    const copyMsg = (e: Event) => {
      e.stopPropagation();
      const btn = e.currentTarget as HTMLElement;
      const doCopy = () => {
        // 优先使用 Clipboard API（需要 HTTPS/localhost）
        if (navigator.clipboard?.writeText) {
          return navigator.clipboard.writeText(rawText);
        }
        // 降级：execCommand（兼容 HTTP 环境）
        return new Promise<void>((resolve, reject) => {
          const ta = document.createElement('textarea');
          ta.value = rawText;
          ta.style.position = 'fixed';
          ta.style.left = '-9999px';
          ta.style.top = '-9999px';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          try {
            document.execCommand('copy');
            document.body.removeChild(ta);
            resolve();
          } catch (err) {
            document.body.removeChild(ta);
            reject(err);
          }
        });
      };
      doCopy().then(() => {
        const orig = btn.textContent;
        btn.textContent = '✅';
        setTimeout(() => { btn.textContent = orig; }, 1200);
      }).catch(() => {});
    };
    const deleteMsg = (e: Event) => {
      e.stopPropagation();
      new CustomEvent('marcus-delete-message', {
        bubbles: true, composed: true,
        detail: { messageId: msg.id },
      });
      (e.target as HTMLElement).dispatchEvent(new CustomEvent('marcus-delete-message', {
        bubbles: true, composed: true,
        detail: { messageId: msg.id },
      }));
    };
    return html`
    <div class="flex justify-end mx-4 message-row group">
      <div class="user-message-container py-2 px-4 relative" style="background: var(--agent-user-msg-bg); border-radius: 18px 18px 4px 18px; border-right: 3px solid var(--agent-gold); padding: 12px 16px; max-width: 80%; box-shadow: var(--agent-shadow-msg);">
        <markdown-block .content=${processed}></markdown-block>
        <div class="message-actions" style="position:absolute; top:-8px; right:8px; display:flex; gap:4px; opacity:0; transition:opacity 0.15s;">
          <button @click=${copyMsg} title="复制" style="background:var(--agent-surface); border:1px solid var(--agent-border); border-radius:6px; padding:2px 6px; cursor:pointer; font-size:12px; color:var(--agent-text-secondary);">📋</button>
          <button @click=${deleteMsg} title="删除" style="background:var(--agent-surface); border:1px solid var(--agent-border); border-radius:6px; padding:2px 6px; cursor:pointer; font-size:12px; color:var(--agent-text-secondary);">🗑️</button>
        </div>
      </div>
    </div>
    <style>
      .message-row:hover .message-actions { opacity: 1 !important; }
    </style>
  `;
  },
} as MessageRenderer<any>);

// assistant 消息使用 Pi 内置的 <assistant-message> 组件渲染（原生支持 text/thinking/toolCall）
// 不再注册自定义 assistant renderer，工具调用的 Input/Output 由内置 <tool-message> 组件自动展示

// ===== 工具分组 =====
// 聊天模式工具（只读）
const chatTools: AgentTool[] = [
  createTool(getMarketIndicesTool),
  createTool(getQuoteTool),
  createTool(getPortfolioTool),
  createTool(getConceptFundFlowTool),
  createTool(getMarketMoneyflowTool),
  createTool(getConceptMappingTool),
  createTool(getEtfQuoteTool),
  createTool(getEtfKlineTool),
  createTool(getDailyKlineTool),
  createTool(getMoneyflowTool),
  createTool(getTechnicalTool),
  createTool(readDbTableTool),
  createTool(getDbSchemaTool),
];

// 复盘模式工具（聊天工具 + 扫描报告 + Pi 历史 + 交易历史）
const reflectTools: AgentTool[] = [
  ...chatTools,
  createTool(getLatestScanReportTool),
  createTool(getPiAnalysisHistoryTool),
  createTool(getTradeHistoryTool),
];

// 占位工具（reflect 模式下不调用本地模型，给 agent 一个空工具让它直接返回）
const spoofTools: AgentTool[] = [createTool({
  name: 'placeholder',
  description: 'Internal tool for panel discussion mode',
  parameters: Type.Object({}),
  async execute(_toolCallId: string, _params: unknown, _signal: AbortSignal | undefined) {
    return { content: [{ type: 'text' as const, text: 'ok' }] };
  },
})];

// 默认工具集（向后兼容）
const tradingTools = chatTools;

// ===== 可折叠工具调用渲染器 =====
const COLLAPSIBLE_TOOLS = [
  'get_market_indices', 'get_quote', 'get_portfolio',
  'get_concept_fund_flow', 'get_market_moneyflow', 'get_concept_mapping',
  'get_etf_quote', 'get_etf_kline', 'get_daily_kline', 'get_moneyflow',
  'get_technical', 'read_db_table', 'get_db_schema',
  'get_latest_scan_report', 'get_pi_analysis_history', 'get_trade_history',
];

// 中文工具名映射
const TOOL_LABELS: Record<string, string> = {
  get_market_indices: '获取市场指数',
  get_quote: '查询个股行情',
  get_portfolio: '查看账户持仓',
  get_concept_fund_flow: '概念资金流向',
  get_market_moneyflow: '大盘资金流向',
  get_concept_mapping: '查询概念成分股',
  get_etf_quote: '查询ETF行情',
  get_etf_kline: 'ETF K线',
  get_daily_kline: '查询历史K线',
  get_moneyflow: '查询个股资金流向',
  get_technical: '查询技术指标',
  read_db_table: '读取数据库表',
  get_db_schema: '获取数据库结构',
  get_latest_scan_report: '最新扫描报告',
  get_pi_analysis_history: 'Pi分析历史',
  get_trade_history: '交易报告历史',
};

const makeCollapsibleRenderer = (toolName: string) => ({
  render(params: any, result: any, isStreaming?: boolean) {
    const state = result ? (result.isError ? 'error' : 'complete') : isStreaming ? 'inprogress' : 'complete';
    const statusColor = state === 'complete' ? '#2ecc71' : state === 'error' ? '#e74c3c' : '#f0b90b';

    // 格式化输入参数
    let paramsJson = '';
    if (params) {
      try { paramsJson = JSON.stringify(params, null, 2); } catch { paramsJson = String(params); }
    }

    // 格式化输出结果
    let outputJson = '';
    let outputLang = 'text';
    if (result) {
      outputJson = result.content
        ?.filter((c: any) => c.type === 'text')
        .map((c: any) => c.text)
        .join('\n') || '(no output)';
      try { JSON.parse(outputJson); outputLang = 'json'; } catch { /* not JSON */ }
    }

    const label = TOOL_LABELS[toolName] || toolName;

    return {
      content: html`
        <div>
          <!-- 折叠头部 -->
          <div class="flex items-center justify-between gap-2 cursor-pointer select-none"
               style="color: var(--agent-text-secondary); font-size: 13px; padding: 2px 0;"
               @click=${(e: Event) => {
                 const btn = e.currentTarget as HTMLElement;
                 const panel = btn.nextElementSibling as HTMLElement;
                 const arrow = btn.querySelector('.tool-arrow') as HTMLElement;
                 if (panel) {
                   const collapsed = panel.style.display === 'none';
                   panel.style.display = collapsed ? 'block' : 'none';
                   if (arrow) arrow.style.transform = collapsed ? 'rotate(0deg)' : 'rotate(-90deg)';
                 }
               }}>
            <div class="flex items-center gap-2">
              <span style="color:${statusColor}; font-size:10px;">●</span>
              <span>🔧 ${label}</span>
            </div>
            <span class="tool-arrow" style="transform:rotate(-90deg); transition:transform 0.2s; font-size:10px; color:var(--agent-text-dim);">▼</span>
          </div>
          <!-- 可折叠内容（默认隐藏） -->
          <div style="display:none; padding-top: 8px;">
            <div class="space-y-3">
              ${paramsJson ? html`
                <div>
                  <div class="text-xs font-medium mb-1" style="color:var(--agent-text-dim);">📥 Input</div>
                  <code-block .code=${paramsJson} language="json"></code-block>
                </div>
              ` : ''}
              ${result ? html`
                <div>
                  <div class="text-xs font-medium mb-1" style="color:var(--agent-text-dim);">📤 Output</div>
                  <code-block .code=${outputJson} language=${outputLang}></code-block>
                </div>
              ` : isStreaming ? html`
                <div class="text-xs" style="color:var(--agent-text-dim);">执行中...</div>
              ` : ''}
            </div>
          </div>
        </div>
      `,
      isCustom: false,
    };
  },
});

COLLAPSIBLE_TOOLS.forEach(name => registerToolRenderer(name, makeCollapsibleRenderer(name)));

// ===== Session Metadata Helpers =====
interface SessionMeta {
  id: string;
  title: string;
  createdAt: string;
  messageCount: number;
}

const SESSIONS_META_KEY = 'marcus_sessions_meta';

const loadSessionsMeta = (): Record<string, Omit<SessionMeta, 'id'>> => {
  try { return JSON.parse(localStorage.getItem(SESSIONS_META_KEY) || '{}'); }
  catch { return {}; }
};

const saveSessionsMeta = (meta: Record<string, Omit<SessionMeta, 'id'>>) => {
  localStorage.setItem(SESSIONS_META_KEY, JSON.stringify(meta));
};

const buildSessionsList = (): SessionMeta[] => {
  const meta = loadSessionsMeta();
  return Object.entries(meta)
    .map(([id, m]) => ({ id, ...m }))
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
};

const updateSessionMeta = (sessionId: string, updates: Partial<Omit<SessionMeta, 'id'>>) => {
  const meta = loadSessionsMeta();
  meta[sessionId] = { ...(meta[sessionId] || { title: '新会话', createdAt: new Date().toISOString(), messageCount: 0 }), ...updates };
  saveSessionsMeta(meta);
};

const removeSessionMeta = (sessionId: string) => {
  const meta = loadSessionsMeta();
  delete meta[sessionId];
  saveSessionsMeta(meta);
};

const generateAISessionTitle = async (messages: any[], apiKey: string): Promise<string> => {
  // 收集前 2 条用户消息作为上下文
  const userMessages = messages
    .filter((m: any) => m.role === 'user')
    .slice(0, 2);
  if (userMessages.length === 0) return '新会话';

  const userText = userMessages
    .map((m: any) => typeof m.content === 'string' ? m.content : m.content?.[0]?.text || '')
    .join('\n');

  // 回退方案：无 API key 或消息太短时直接截取
  const fallback = () => {
    const cleaned = userText.replace(/\n/g, ' ').trim();
    return cleaned.length > 30 ? cleaned.slice(0, 30) + '...' : (cleaned || '新会话');
  };

  if (!apiKey || !userText.trim()) return fallback();

  try {
    const res = await fetch('https://api.deepseek.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: 'deepseek-chat',
        messages: [
          {
            role: 'system',
            content: '你是一个标题生成助手。根据用户的聊天内容生成一个极短的中文标题摘要（6-15字），只输出标题本身，不要引号、标点或额外解释。内容涉及股票/交易时突出关键股票或操作意图。',
          },
          {
            role: 'user',
            content: `请为以下对话生成短标题：\n${userText.slice(0, 500)}`,
          },
        ],
        temperature: 0.3,
        max_tokens: 30,
      }),
    });

    if (res.ok) {
      const data = await res.json();
      const title = data.choices?.[0]?.message?.content?.trim() || '';
      if (title) return title.length > 25 ? title.slice(0, 25) : title;
    }
  } catch (e) {
    console.log('AI标题生成失败，使用回退方案:', e);
  }

  return fallback();
};

const generateSessionTitle = (messages: any[]): string => {
  const firstUserMsg = messages.find((m: any) => m.role === 'user');
  if (!firstUserMsg) return '新会话';
  const content = typeof firstUserMsg.content === 'string'
    ? firstUserMsg.content
    : firstUserMsg.content?.[0]?.text || '';
  const cleaned = content.replace(/\n/g, ' ').trim();
  return cleaned.length > 30 ? cleaned.slice(0, 30) + '...' : (cleaned || '新会话');
};

const formatSessionTime = (isoStr: string): string => {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin}分钟前`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}小时前`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 7) return `${diffDay}天前`;
    return `${d.getMonth() + 1}/${d.getDate()}`;
  } catch { return ''; }
};

// ===== Build system prompt with dynamic time and market status =====
interface TradeStatus {
  is_trade_day: boolean;
  trading_status: string;
  status_label: string;
  time_display: string;
  trade_date: string | null;
  reason: string;
}

type ChatMode = 'chat' | 'reflect'; // reflect = 专家组群聊讨论

const getFormattedTime = (tradeStatus?: TradeStatus | null): { timeStr: string; marketStatus: string } => {
  let timeStr: string;
  let marketStatus: string;

  if (tradeStatus) {
    // 使用后端 API 返回的数据（基于 Tushare trade_cal）
    timeStr = tradeStatus.time_display;
    marketStatus = tradeStatus.status_label;
  } else {
    // Fallback: 客户端本地计算
    const now = new Date();
    timeStr = now.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'long',
    });
    const day = now.getDay();
    const timeInMinutes = now.getHours() * 60 + now.getMinutes();
    if (day === 0 || day === 6) marketStatus = '🔴 今日休市（周末）';
    else if (timeInMinutes < 9 * 60 + 15) marketStatus = '⏳ 尚未开盘，等待集合竞价（9:15 开始）';
    else if (timeInMinutes < 9 * 60 + 25) marketStatus = '🟡 集合竞价中（9:15-9:25）';
    else if (timeInMinutes < 9 * 60 + 30) marketStatus = '🟡 集合竞价结束，等待连续竞价（9:30 开盘）';
    else if (timeInMinutes < 11 * 60 + 30) marketStatus = '🟢 早盘交易中（9:30-11:30）';
    else if (timeInMinutes < 13 * 60) marketStatus = '🔴 午间休市（11:30-13:00）';
    else if (timeInMinutes < 15 * 60) marketStatus = '🟢 午盘交易中（13:00-15:00）';
    else marketStatus = '🔴 今日已收盘';
  }
  return { timeStr, marketStatus };
};

const CHAT_SYSTEM_PROMPT = `## 你是 Marcus — 短线右侧交易专家

### 交易理念
**右侧交易，顺势而为**：
- 不抄底，不摸顶，只做趋势确认后的行情
- 等待价格突破关键阻力/支撑位后确认趋势方向
- 在趋势形成初期入场，在趋势衰竭时离场

### 交易风格
- **短线为主**：持仓周期 1-5 天，追求快速复利
- **严格止损**：单笔亏损不超过总资金的 2%
- **趋势跟踪**：用技术面信号（均线、MACD、成交量）确认方向
- **仓位管理**：趋势明确时重仓，趋势不明时轻仓或空仓

### 分析框架
1. **趋势确认**：价格站稳 5 日线上方看多，跌破 5 日线看空
2. **关键位置**：关注前高/前低、平台突破、均线交叉
3. **量价配合**：放量突破是真突破，缩量上涨需警惕
4. **市场情绪**：结合板块轮动和资金流向判断热点
5. **右侧纪律**：不抄底不摸顶，等确认信号
6. **产业链思维** — 选中一条主线后，沿产业链上下游布局
   - 概念板块排行 → 发现主线方向
   - 行业分类（industry）→ 区分上中下游层级
   - 概念标签差异化 → 识别各环节核心标的
   - 资金流向验证 → 剔除伪概念股
   - 组合分配 → 上游重仓/中游适中/下游轻仓

### 风险控制（最高优先级）
- **永远不要逆势加仓** — 亏损时第一时间止损
- **单只股票仓位 ≤ 15%** — 分散风险
- **单日总仓位 ≤ 60%** — 保留现金应对极端行情
- **总回撤 ≥ 5% 时停止交易** — 强制冷静期
- **盈利出金** — 赚了钱要落袋为安

### 操作纪律
1. 入场前写好止损点位，不随意改动
2. 到达止损坚决执行，不幻想反弹
3. 盈利时分批止盈，锁住利润
4. 连续亏损 3 笔后强制休息 30 分钟

### 沟通风格
- **冷静理性**：不以物喜，不以己悲
- **数据说话**：用客观信号决策，不凭感觉
- **简洁直接**：给出明确的买入/卖出/观望建议
- **风险提示**：每次操作前说明风险和止损位置

### 可用工具
- get_market_indices: 获取A股、美股、港股指数行情
- get_quote: 查询个股实时行情
- get_portfolio: 查看账户持仓和资金
- get_market_moneyflow: 获取大盘实时资金流向（沪深分开+合计，含买/卖分明细+总成交额+资金性质）
- get_concept_fund_flow: 获取概念板块实时行情（支持涨幅/主力资金排序，含净流入拆解+板块广度+资金性质+领涨股，默认实时）
- get_concept_mapping: 查询概念板块及成分股（不传参列所有概念，传concept_name查该概念下的股票）
- get_etf_quote: 查询ETF基金行情
- get_etf_kline: 获取ETF历史K线数据（开高低收/量/额）
- get_daily_kline: 获取A股历史日K线数据（开高低收/量/额）
- get_moneyflow: 获取A股资金流向数据（大单/小单/特大单净流入）
- get_technical: 获取MACD、KDJ、RSI、布林带等60+技术指标
- read_db_table: 查询数据库表数据
- get_db_schema: 获取数据库表结构

### 产业链选股逻辑（与交易模式一致）

当用户要求选股或分析板块时，按以下流程操作：

1. **锁定主线** — 用 get_concept_fund_flow 找出强势板块（大盘跌时还涨、连续资金流入的）
2. **概念拆解** — 调用 get_concept_mapping(概念名) 获取全部成分股
3. **行业分层** — 用 read_db_table(stock_pool, where="industry=...") 按行业区分产业链层级：
   同行业归为同一层级（如"机械基件"→上游，"电气设备"→中游，"汽车配件"→下游）
4. **纯度验证** — 剔除伪概念股（涨幅远低于板块均值、资金持续流出、主营不匹配的）
5. **龙头确认** — 涨幅+资金+行业地位三因子排序，确定各环节龙头
6. **组合建议** — 各环节选1只最优，形成3-4只产业链组合，上游重仓(10-15%)/中游适中(5-10%)/下游轻仓(3-5%)

### 概念映射查询

查询股票所属概念板块时，使用 stock_pool.db 的 stock_concept_map 表：
- 查某股票的概念：read_db_table(db="stock_pool.db", table="stock_concept_map", where="ts_code LIKE '000001%'")
- 查某概念包含的股票：read_db_table(db="stock_pool.db", table="stock_concept_map", where="concept_name = '半导体概念'", limit=50)
- ts_code 格式为 "代码.交易所"（如 000001.SZ），symbol 为纯数字代码（如 000001）`;

// ===== 专家组群聊讨论模式 — 由 pi-server 执行，前端仅做中转 =====
const REFLECT_SYSTEM_PROMPT = `## 专家组群聊讨论模式

你是 Marcus 系统的专家组主持人。本模式由 5 位不同性格的右侧交易专家组成群聊讨论：
- 🛡️ 风控审计师 (DeepSeek-v4-pro) — 保守吹毛求疵，审计每一笔风控
- 📈 趋势交易员 (DeepSeek-v4-flash) — 激进右侧信仰，评估趋势和选股
- 📊 数据统计师 (MiniMax-M2.7) — 客观量化，纯数据统计
- 🔍 逆向质疑者 (MiniMax-M3) — 怀疑论者，挑战所有共识
- 🎤 主持人 (DeepSeek-v4-pro) — 公正提炼，综合各方观点

### 讨论流程（4 轮）
1. **数据采集** — 收集整周交易数据、Pi分析、持仓
2. **独立分析** — 4 位专家并行独立分析（约 2 分钟）
3. **交叉评论** — 每位专家对他人的报告发表评论（约 2 分钟）
4. **二次反思** — 根据评论修正/强化自己的分析（约 2 分钟）
5. **主持人综合** — 综合所有讨论产出最终报告（约 2 分钟）

⏱️ 总耗时约 5-9 分钟，请耐心等待。`;

const buildSystemPrompt = (tradeStatus?: TradeStatus | null, mode?: ChatMode): string => {
  const { timeStr, marketStatus } = getFormattedTime(tradeStatus);

  const header = `### ⏰ 当前时间\n${timeStr}\n\n### 📊 市场状态\n${marketStatus}\n\n`;
  const tail = `${mode === 'reflect' ? `\n\n最后一行请输出：SIGNAL: <green|yellow|red> POSITION:<0-100> REASON:<一句话总结>` : ''}`;

  if (mode === 'reflect') {
    return REFLECT_SYSTEM_PROMPT + '\n\n' + header + tail;
  }
  return CHAT_SYSTEM_PROMPT + '\n\n' + header;
};

export default function ChatContainer({ onStockSelect }: { onStockSelect?: (stock: { symbol: string; name: string }) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [indices, setIndices] = useState<IndexData[]>([]);
  const sessionIdRef = useRef<string>(localStorage.getItem('marcus_session_id') || '');
  const sessionsRef = useRef<SessionsStore | null>(null);
  const agentRef = useRef<Agent | null>(null);
  const apiKeyRef = useRef<string>('');
  const tradeStatusRef = useRef<TradeStatus | null>(null);
  const [mode, setMode] = useState<ChatMode>((localStorage.getItem('marcus_chat_mode') || 'chat') as ChatMode);
  const modeRef = useRef<ChatMode>(mode);

  // Keep modeRef in sync
  useEffect(() => { modeRef.current = mode; }, [mode]);

  // 触发 ChatPanel 的消息列表刷新（同步 agent.state.messages → UI）
  const triggerUIRefresh = () => {
    queueMicrotask(() => {
      const ml = document.querySelector('message-list') as any;
      if (ml) {
        ml.messages = [...(agentRef.current?.state?.messages || [])];
        (ml as LitElement).requestUpdate?.();
      }
      const me = document.querySelector('message-editor') as any;
      if (me) {
        me.isStreaming = false;
        (me as LitElement).requestUpdate?.();
      }
    });
  };

  // ===== 模式切换：更新 Agent 的 systemPrompt + tools =====
  useEffect(() => {
    if (!agentRef.current) return;
    const agent = agentRef.current;
    agent.state.systemPrompt = buildSystemPrompt(tradeStatusRef.current, mode);
    // reflect（专家组群聊）模式下不调用本地模型，spoofTools 确保 agent 立即返回不浪费 API
    agent.state.tools = mode === 'reflect' ? spoofTools : chatTools;
    localStorage.setItem('marcus_chat_mode', mode);
    console.log(`[模式] 切换为: ${mode === 'reflect' ? '👥 专家组群聊' : '💬 聊天模式'}`);
  }, [mode]);

  // ===== Session Management State =====
  const [sessionsList, setSessionsList] = useState<SessionMeta[]>([]);

  const refreshSessionList = useCallback(() => {
    setSessionsList(buildSessionsList());
  }, []);

  const switchToSession = useCallback(async (targetSessionId: string) => {
    if (targetSessionId === sessionIdRef.current) {
      return;
    }
    // 保存当前会话（🔒 快照 messages 防止异步期间被篡改）
    const currentSid = sessionIdRef.current;
    if (currentSid && sessionsRef.current && agentRef.current) {
      const currentMeta = loadSessionsMeta();
      const currentTitle = currentMeta[currentSid]?.title || '';
      const isFirstExchange = !currentTitle || currentTitle === '新会话';

      const messagesSnapshot = [...agentRef.current.state.messages];
      const fallbackTitle = isFirstExchange ? generateSessionTitle(messagesSnapshot) : currentTitle;
      const stateForSave = { ...agentRef.current.state, messages: messagesSnapshot };

      sessionsRef.current.saveSession(currentSid, stateForSave, undefined, fallbackTitle)
        .catch(e => console.log('切换时保存失败:', e));
      updateSessionMeta(currentSid, {
        title: fallbackTitle,
        messageCount: messagesSnapshot.length,
        createdAt: new Date().toISOString(),
      });
      // 仅首次对话时后台异步触发 AI 标题更新
      if (isFirstExchange && messagesSnapshot.length > 0) {
        generateAISessionTitle(messagesSnapshot, apiKeyRef.current)
          .then(aiTitle => {
            if (aiTitle && aiTitle !== fallbackTitle) {
              sessionsRef.current?.saveSession(currentSid, stateForSave, undefined, aiTitle).catch(() => {});
              updateSessionMeta(currentSid, { title: aiTitle });
              refreshSessionList();
            }
          })
          .catch(() => {});
      }
    }
    // 加载目标会话
    if (sessionsRef.current) {
      const saved = await sessionsRef.current.loadSession(targetSessionId).catch(() => null);
      const messages = saved?.messages || [];
      // 重建 Agent
      if (agentRef.current) {
        agentRef.current.state.messages = messages;
      }
      // 更新 DOM
      const ml = document.querySelector('message-list') as any;
      if (ml) { ml.messages = [...messages]; (ml as LitElement).requestUpdate?.(); }
      const sc = document.querySelector('streaming-message-container') as any;
      if (sc && sc.setMessage) sc.setMessage(null, true);
      // 更新 sessionId
      sessionIdRef.current = targetSessionId;
      localStorage.setItem('marcus_session_id', targetSessionId);
    }
    refreshSessionList();
  }, [refreshSessionList]);

  const deleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('确定要删除这个会话吗？')) return;
    // 从IndexedDB删除
    if (sessionsRef.current) {
      sessionsRef.current.delete(sessionId).catch(() => {});
    }
    // 从元数据删除
    removeSessionMeta(sessionId);
    // 如果删除的是当前会话，新建一个
    if (sessionId === sessionIdRef.current) {
      const newId = generateUUID();
      localStorage.setItem('marcus_session_id', newId);
      sessionIdRef.current = newId;
      updateSessionMeta(newId, { title: '新会话', messageCount: 0, createdAt: new Date().toISOString() });
      agentRef.current?.reset();
      const ml = document.querySelector('message-list') as any;
      if (ml) { ml.messages = []; (ml as LitElement).requestUpdate?.(); }
      const sc = document.querySelector('streaming-message-container') as any;
      if (sc && sc.setMessage) sc.setMessage(null, true);
    }
    refreshSessionList();
  }, [refreshSessionList]);

  // ===== 导入导出会话 =====
  const handleExportAll = useCallback(async () => {
    if (!sessionsRef.current) return;
    const meta = loadSessionsMeta();
    const sessionIds = Object.keys(meta);
    if (sessionIds.length === 0) { alert('没有可导出的会话'); return; }

    const exportData: any = { version: 1, exportedAt: new Date().toISOString(), sessions: [] };
    for (const sid of sessionIds) {
      try {
        const saved = await sessionsRef.current.loadSession(sid).catch(() => null);
        const messages = saved?.messages || [];
        exportData.sessions.push({
          id: sid,
          title: meta[sid]?.title || '新会话',
          createdAt: meta[sid]?.createdAt || '',
          messageCount: messages.length,
          messages,
        });
      } catch { /* skip broken sessions */ }
    }

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `marcus-sessions-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const handleExportSingle = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!sessionsRef.current) return;
    const meta = loadSessionsMeta();
    const info = meta[sessionId];
    try {
      const saved = await sessionsRef.current.loadSession(sessionId).catch(() => null);
      const messages = saved?.messages || [];
      const exportData = {
        version: 1,
        exportedAt: new Date().toISOString(),
        sessions: [{
          id: sessionId,
          title: info?.title || '新会话',
          createdAt: info?.createdAt || '',
          messageCount: messages.length,
          messages,
        }],
      };
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `marcus-session-${(info?.title || 'session').slice(0, 20)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { alert('导出失败'); }
  }, []);

  const handleImport = useCallback(async () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file || !sessionsRef.current) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        if (!data.sessions || !Array.isArray(data.sessions)) {
          alert('无效的会话文件格式'); return;
        }
        let imported = 0;
        for (const s of data.sessions) {
          if (!s.id || !s.messages) continue;
          const stateForSave = {
            systemPrompt: buildSystemPrompt(tradeStatusRef.current, modeRef.current),
            model: agentRef.current?.state.model,
            thinkingLevel: 'off',
            messages: s.messages,
            isStreaming: false,
            pendingToolCalls: new Set(),
          };
          await sessionsRef.current.saveSession(s.id, stateForSave as any, undefined, s.title || '导入会话');
          updateSessionMeta(s.id, {
            title: s.title || '导入会话',
            messageCount: s.messages.length,
            createdAt: s.createdAt || new Date().toISOString(),
          });
          imported++;
        }
        refreshSessionList();
        alert(`成功导入 ${imported} 个会话`);
      } catch (e) {
        alert(`导入失败: ${(e as Error).message}`);
      }
    };
    input.click();
  }, [refreshSessionList]);

  // ===== @提及 状态 =====
  const [mentionVisible, setMentionVisible] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionResults, setMentionResults] = useState<any[]>([]);
  const [mentionPos, setMentionPos] = useState({ top: 0, left: 0 });

  const searchMentions = useCallback(async (q: string) => {
    if (!q.trim()) { setMentionResults([]); return; }
    try {
      const res = await fetch(`/api/v1/market/search?q=${encodeURIComponent(q)}`);
      if (res.ok) {
        const data = await res.json();
        setMentionResults(data.results || []);
      }
    } catch {
      setMentionResults([]);
    }
  }, []);

  const insertMention = useCallback((item: any) => {
    const me = document.querySelector('message-editor') as any;
    if (!me) return;
    const textarea = me.querySelector('textarea') as HTMLTextAreaElement;
    if (!textarea) return;

    const text = me.value || textarea.value || '';
    const cursorPos = textarea.selectionStart || text.length;

    const before = text.slice(0, cursorPos);
    const atIdx = before.lastIndexOf('@');
    if (atIdx === -1) return;

    const mention = `$${item.symbol}(${item.name}) `;
    const newText = text.slice(0, atIdx) + mention + text.slice(cursorPos);

    me.value = newText;
    textarea.value = newText;

    const newCursorPos = atIdx + mention.length;
    textarea.focus();
    textarea.setSelectionRange(newCursorPos, newCursorPos);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));

    setMentionVisible(false);
  }, []);

  // Fetch market indices for ticker
  useEffect(() => {
    const fetchIndices = async () => {
      try {
        const res = await fetch(`${MARCUS_API}/market/indices`);
        if (res.ok) {
          const data = await res.json();
          setIndices(data.indices || []);
        }
      } catch (e) {
        console.log('Failed to fetch indices:', e);
      }
    };

    fetchIndices();
    const interval = setInterval(fetchIndices, 30000);
    return () => clearInterval(interval);
  }, []);

  // Fetch trade calendar status from backend (uses Tushare trade_cal)
  useEffect(() => {
    const fetchTradeStatus = async () => {
      try {
        const res = await fetch(`${MARCUS_API}/market/trade-status`);
        if (res.ok) {
          const data = await res.json();
          tradeStatusRef.current = data;
        }
      } catch (e) {
        console.log('Failed to fetch trade status:', e);
      }
    };

    fetchTradeStatus();
    const interval = setInterval(fetchTradeStatus, 30000);
    return () => clearInterval(interval);
  }, []);


  // Initialize ChatPanel
  useEffect(() => {
    if (containerRef.current?.dataset.initialized) return;
    if (containerRef.current) containerRef.current.dataset.initialized = 'true';

    let mounted = true;
    const cleanupFns: (() => void)[] = [];

    const init = async () => {
      const settings = new SettingsStore();
      const providerKeys = new ProviderKeysStore();
      const sessions = new SessionsStore();
      const customProviders = new CustomProvidersStore();

      const backend = new IndexedDBStorageBackend({
        dbName: 'marcus-trading-clean',
        version: 200,
        stores: [
          settings.getConfig(),
          providerKeys.getConfig(),
          sessions.getConfig(),
          SessionsStore.getMetadataConfig(),
          customProviders.getConfig(),
        ],
      });

      settings.setBackend(backend);
      providerKeys.setBackend(backend);
      sessions.setBackend(backend);
      customProviders.setBackend(backend);
      sessionsRef.current = sessions;

      // 🔥 立即创建并插入 ChatPanel，输入框秒出，不等异步初始化
      const chatPanel = new ChatPanel();
      if (mounted && containerRef.current) {
        containerRef.current.appendChild(chatPanel);
      }

      // ===== 会话持久化 + 配置加载（并行）=====
      let loadedMessages: any[] = [];
      let sessionId = sessionIdRef.current;

      if (!sessionId) {
        sessionId = generateUUID();
        sessionIdRef.current = sessionId;
        localStorage.setItem('marcus_session_id', sessionId);
      }

      // 🔥 并行加载：会话从 IndexedDB 读 + 配置从后端取
      const [savedSession, configRes] = await Promise.all([
        sessionId ? sessions.loadSession(sessionId).catch(() => null) : Promise.resolve(null),
        fetch('/api/v1/config').catch(() => null),
      ]);

      if (savedSession && savedSession.messages && savedSession.messages.length > 0) {
        loadedMessages = savedSession.messages;
        console.log(`📂 加载会话: ${savedSession.id}, ${loadedMessages.length} 条消息`);
      }

      // 初始化当前会话元数据
      const currentMeta = loadSessionsMeta();
      if (!currentMeta[sessionId]) {
        const title = loadedMessages.length > 0 ? generateSessionTitle(loadedMessages) : '新会话';
        updateSessionMeta(sessionId, {
          title,
          messageCount: loadedMessages.length,
          createdAt: new Date().toISOString(),
        });
      }
      // 刷新会话列表
      refreshSessionList();

      let apiKey = '';
      if (configRes?.ok) {
        try {
          const config = await configRes.json();
          if (config.deepseek_api_key) {
            apiKey = config.deepseek_api_key;
            apiKeyRef.current = apiKey;
            // 不 await，异步写 IndexedDB
            providerKeys.set('deepseek', apiKey).catch(() => {});
          }
        } catch (e) {
          console.log('Parse config error:', e);
        }
      }

      const storage = new AppStorage(settings, providerKeys, sessions, customProviders, backend);
      setAppStorage(storage);

      const model = getModel('deepseek', 'deepseek-v4-flash');

      const agent = new Agent({
        initialState: {
          systemPrompt: buildSystemPrompt(tradeStatusRef.current, mode),
          model: model,
          thinkingLevel: 'off',
          messages: loadedMessages,
          tools: mode === 'reflect' ? reflectTools : chatTools,
          isStreaming: false,
          pendingToolCalls: new Set(),
        } as unknown as AgentState,
        convertToLlm: defaultConvertToLlm,
        getApiKey: async () => apiKey,
        sessionId: sessionId,
      });
      agentRef.current = agent;

      // ===== Reflect 模式拦截：路由到后端 pi-server 专家组群聊 =====
      const originalPrompt = (agent as any).prompt.bind(agent);
      (agent as any).prompt = async (message: string) => {
        if (modeRef.current !== 'reflect') {
          return originalPrompt(message);
        }

        // --- Panel Discussion 模式 ---
        const msgs = agent.state.messages;
        // 添加用户消息到 UI（使用 textContent 格式）
        msgs.push({ role: 'user', content: [{ type: 'text', text: message }] } as any);
        agent.state.messages = [...msgs];
        triggerUIRefresh();

        // 构建加载消息内容
        const makeLoadingContent = (text: string) => [{ type: 'text' as const, text }];
        const loadingId = generateUUID();
        const loadingMsg = `## 👥 专家组群聊讨论启动\n\n> 5 位专家已就位，正在进行多轮讨论...\n\n| 阶段 | 状态 |\n|------|:--:|\n| 🗂️ 数据采集 | ⏳ 进行中... |\n| 📝 独立分析（4 专家并行） | ⬜ 等待中 |\n| 💬 交叉评论（相互点评） | ⬜ 等待中 |\n| 🔄 二次反思改进 | ⬜ 等待中 |\n| 🎤 主持人综合 | ⬜ 等待中 |\n\n⏱️ 预计耗时 5-9 分钟，请耐心等待...`;
        // 加载消息带上标记 ID，SSE 事件到达时原地替换
        msgs.push({ role: 'assistant', content: makeLoadingContent(loadingMsg), _panelLoadingId: loadingId } as any);
        agent.state.messages = [...msgs];
        triggerUIRefresh();

        // 群聊模式：每个专家完成后立即追加独立聊天气泡
        const appendExpertBubble = (roleLabel: string, content: string) => {
          console.log(`[Panel] 🫧 appendExpertBubble: ${roleLabel} (${content.length}字)`);
          const bubbleContent = `**${roleLabel}**\n\n${content}`;
          agent.state.messages = [...agent.state.messages, { role: 'assistant', content: makeLoadingContent(bubbleContent) } as any];
          console.log(`[Panel] 📋 消息总数: ${agent.state.messages.length}, 最新:`, agent.state.messages[agent.state.messages.length - 1]);
          triggerUIRefresh();
          console.log(`[Panel] ✅ triggerUIRefresh 完成`);
        };

        // 首次收到专家消息时，移除加载提示
        let loadingRemoved = false;
        const removeLoadingOnce = () => {
          if (loadingRemoved) return;
          loadingRemoved = true;
          const idx = agent.state.messages.findIndex((m: any) => m._panelLoadingId === loadingId);
          console.log(`[Panel] 🗑️ removeLoadingOnce: loadingId=${loadingId.slice(-8)}, foundIdx=${idx}, 当前消息数=${agent.state.messages.length}`);
          if (idx !== -1) {
            agent.state.messages.splice(idx, 1);
            console.log(`[Panel] 🗑️ 已移除加载消息, 剩余消息数=${agent.state.messages.length}`);
          }
        };

        try {
          const resp = await fetch('/api/v1/panel/reflect/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
          });

          if (!resp.ok) {
            const errText = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${errText}`);
          }

          console.log('[Panel] 🔌 SSE 连接建立, 开始读取流...');
          const reader = resp.body?.getReader();
          const decoder = new TextDecoder();
          if (!reader) throw new Error('Response body not readable');

          let buffer = '';
          let currentEvent = '';
          let expertCount = 0;

          while (true) {
            const { done, value } = await reader.read();
            if (done) { console.log('[Panel] 🔌 SSE 流结束 (done=true)'); break; }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (!line) continue;
              if (line.startsWith('event: ')) {
                currentEvent = line.slice(7).trim();
                console.log(`[Panel] 📡 event: ${currentEvent}`);
              } else if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));
                  console.log(`[Panel] 📦 data keys: ${Object.keys(data).join(',')}, event=${currentEvent}`);
                  if (currentEvent === 'expert_message') {
                    removeLoadingOnce();
                    for (const r of (data.results || [])) {
                      appendExpertBubble(data.label || r.roleLabel, r.content);
                      expertCount++;
                    }
                  } else if (currentEvent === 'error') {
                    throw new Error(data.message || 'Panel error');
                  } else if (currentEvent === 'done') {
                    const totalSec = ((data.elapsed_ms || 0) / 1000).toFixed(1);
                    agent.state.messages = [...agent.state.messages, {
                      role: 'assistant',
                      content: makeLoadingContent(`> ⏱️ 群聊讨论完成，总耗时 ${totalSec} 秒 (${(Number(totalSec) / 60).toFixed(1)} 分钟)，共 ${expertCount} 条专家发言`),
                    } as any];
                    triggerUIRefresh();
                  } else if (currentEvent === 'start') {
                    console.log('[Panel] 🟢 讨论已启动');
                  } else {
                    console.warn(`[Panel] ⚠️ 未知事件: ${currentEvent}`);
                  }
                } catch (parseErr: any) {
                  console.error('[Panel] ❌ JSON 解析失败:', parseErr.message, line.slice(0, 200));
                }
              }
            }
          }

          // 保存群聊消息到 IndexedDB（chat 模式走 subscribe 的 agent_end，这里单独处理）
          const panelSnapshot = [...agent.state.messages];
          if (sessionId && sessionsRef.current) {
            const stateForSave = { ...agent.state, messages: panelSnapshot };
            sessionsRef.current.saveSession(sessionId, stateForSave, undefined, '专家群聊').catch(() => {});
            updateSessionMeta(sessionId, { messageCount: panelSnapshot.length });
          }

        } catch (e: any) {
          const idx = agent.state.messages.findIndex((m: any) => m._panelLoadingId === loadingId);
          if (idx !== -1) {
            const errMsg = `## ❌ 专家组群聊讨论失败\n\n> 错误: ${e.message}\n\n请检查 pi-server 是否正常运行，或切换到聊天模式重试。`;
            agent.state.messages[idx] = { role: 'assistant', content: makeLoadingContent(errMsg) } as any;
            agent.state.messages = [...agent.state.messages];
            triggerUIRefresh();
          }
          // 失败也保存（保留已有讨论记录）
          if (sessionId && sessionsRef.current) {
            const stateForSave = { ...agent.state, messages: [...agent.state.messages] };
            sessionsRef.current.saveSession(sessionId, stateForSave, undefined, '专家群聊').catch(() => {});
          }
        }
      };

      agent.subscribe(async (ev: any) => {
        // 🔄 每次对话轮次前更新系统提示词中的时间和开市状态
        const msgs = agent.state.messages;
        if (msgs.length > 0 && msgs[msgs.length - 1]?.role === 'user') {
          agent.state.systemPrompt = buildSystemPrompt(tradeStatusRef.current, modeRef.current);
        }

        if (ev.type === 'message_end') {
          queueMicrotask(() => {
            const ml = document.querySelector('message-list') as any;
            if (ml) {
              ml.messages = [...ml.messages];
              (ml as LitElement).requestUpdate?.();
            }
          });
        }
        if (ev.type === 'agent_end') {
          // 保存会话到 IndexedDB + 更新元数据
          // 🔒 立即快照 sid 和 messages，防止异步期间被 switchToSession 篡改
          const sid = sessionIdRef.current;
          const messagesSnapshot = [...agent.state.messages];

          if (sid && sessionsRef.current) {
            const currentMeta = loadSessionsMeta();
            const currentTitle = currentMeta[sid]?.title || '';
            const isFirstExchange = !currentTitle || currentTitle === '新会话';

            const fallbackTitle = isFirstExchange ? generateSessionTitle(messagesSnapshot) : currentTitle;
            // 用快照构造 state 对象，避免 agent.state 活引用被篡改
            const stateForSave = { ...agent.state, messages: messagesSnapshot };
            sessionsRef.current.saveSession(sid, stateForSave, undefined, fallbackTitle)
              .then(async () => {
                updateSessionMeta(sid, {
                  title: fallbackTitle,
                  messageCount: messagesSnapshot.length,
                  createdAt: new Date().toISOString(),
                });
                refreshSessionList();
                // 仅首次对话时异步调用 AI 生成更精准的标题
                if (isFirstExchange && messagesSnapshot.length > 0) {
                  try {
                    const aiTitle = await generateAISessionTitle(messagesSnapshot, apiKeyRef.current);
                    if (aiTitle && aiTitle !== fallbackTitle) {
                      sessionsRef.current!.saveSession(sid, stateForSave, undefined, aiTitle).catch(() => {});
                      updateSessionMeta(sid, { title: aiTitle });
                      refreshSessionList();
                    }
                  } catch (e) {
                    console.log('AI标题更新失败:', e);
                  }
                }
              })
              .catch(e => console.log('保存会话失败:', e));
          }
          setTimeout(() => {
            const me = document.querySelector('message-editor') as any;
            if (me) {
              me.isStreaming = false;
              (me as LitElement).requestUpdate?.();
            }
          }, 150);
        }
      });

      await chatPanel.setAgent(agent, {
        onApiKeyRequired: (provider) => ApiKeyPromptDialog.prompt(provider),
      });

      agent.state.tools = mode === 'reflect' ? spoofTools : chatTools;

      // 删除消息事件监听
      const handleDeleteMessage = (e: Event) => {
        const ce = e as CustomEvent;
        const messageId = ce.detail?.messageId;
        if (!messageId) return;
        const msgs = agent.state.messages;
        const idx = msgs.findIndex((m: any) => m.id === messageId);
        if (idx !== -1) {
          agent.state.messages = [...msgs.slice(0, idx), ...msgs.slice(idx + 1)];
          // 强制刷新 message-list
          queueMicrotask(() => {
            const ml = document.querySelector('message-list') as any;
            if (ml) {
              ml.messages = [...agent.state.messages];
              (ml as LitElement).requestUpdate?.();
            }
          });
        }
      };
      document.addEventListener('marcus-delete-message', handleDeleteMessage);
      cleanupFns.push(() => document.removeEventListener('marcus-delete-message', handleDeleteMessage));

      if (mounted && containerRef.current) {
        const container = containerRef.current;

        // ========== 修复聊天区域滚动问题 ==========
        //
        // Pi 组件结构 (Light DOM, 无 Shadow DOM):
        //   pi-chat-panel
        //     └ div(class="relative w-full h-full overflow-hidden flex")
        //         └ div(class="h-full")  ← 需要 min-height:0
        //             └ agent-interface
        //                 └ div(class="flex flex-col h-full ...")
        //                     ├ div(class="flex-1 overflow-y-auto") ← 真正的滚动容器!
        //                     │   └ div(class="max-w-3xl mx-auto p-4 pb-0")
        //                     │       ├ message-list
        //                     │       └ streaming-message-container
        //                     └ div(class="shrink-0")  ← 输入区域
        //                         └ message-editor
        //
        // 问题：Pi 内部 h-full div 缺少 min-height:0，导致 flex 链断裂
        //
        const fixScrolling = () => {
          const cp = container.querySelector('pi-chat-panel');
          if (!cp) return;

          // 1. 确保 pi-chat-panel ❮ 的 flex 约束 (connectedCallback 已设置，以防万一)
          const cpHtml = cp as HTMLElement;
          cpHtml.style.display = 'flex';
          cpHtml.style.flexDirection = 'column';
          cpHtml.style.height = '100%';
          cpHtml.style.minHeight = '0';
          cpHtml.style.overflow = 'hidden';

          // 2. pi-chat-panel 内部的 h-full wrapper div 需要 min-height:0
          //    这是 pi-chat-panel.render() 中 agent-interface 的父容器
          const hFullWrappers = cp.querySelectorAll('.h-full');
          hFullWrappers.forEach((el: Element) => {
            const htmlEl = el as HTMLElement;
            htmlEl.style.minHeight = '0';
          });

          // 3. 找到真正的滚动容器: agent-interface 内的 flex-1 overflow-y-auto
          const scrollDiv = cp.querySelector('.overflow-y-auto') as HTMLElement;
          if (scrollDiv) {
            // 这个 div 已经有 Tailwind 的 overflow-y-auto，但可能因为父容器
            // flex 链没有 min-height:0 而无法生效
            scrollDiv.style.minHeight = '0';

            // 确保内容容器也 OK
            const contentDiv = scrollDiv.querySelector('.max-w-3xl') as HTMLElement;
            if (contentDiv) {
              contentDiv.style.minHeight = '0';
            }
          }

          // 4. 确保 agent-interface 本身的 flex 约束
          const ai = cp.querySelector('agent-interface') as HTMLElement;
          if (ai) {
            ai.style.display = 'flex';
            ai.style.flexDirection = 'column';
            ai.style.height = '100%';
            ai.style.minHeight = '0';
          }
        };

        // 多次执行 (Pi 组件异步渲染)
        [100, 300, 600, 1200, 2000].forEach(t => {
          const id = setTimeout(() => {
            fixScrolling();
            applyMentionSpans(container);
          }, t);
          cleanupFns.push(() => clearTimeout(id));
        });

        // MutationObserver 监听 DOM 变化（滚动修复 + 提及渲染）
        const mo = new MutationObserver(() => {
          fixScrolling();
          // 后渲染: 将 $SYMBOL(NAME) 文本节点转换为可点击 span
          requestAnimationFrame(() => applyMentionSpans(container));
        });
        const waitForPanel = setInterval(() => {
          const cp = container.querySelector('pi-chat-panel');
          if (cp) {
            clearInterval(waitForPanel);
            mo.observe(cp, { childList: true, subtree: true });
            fixScrolling();
            applyMentionSpans(container);
          }
        }, 50);
        cleanupFns.push(() => { clearInterval(waitForPanel); mo.disconnect(); });
        const sbTimeout = setTimeout(() => clearInterval(waitForPanel), 30000);
        cleanupFns.push(() => clearTimeout(sbTimeout));

        // ResizeObserver
        const ro = new ResizeObserver(() => fixScrolling());
        ro.observe(container);
        cleanupFns.push(() => ro.disconnect());

        // ========== @提及 功能 ==========
        // 监听 textarea 的 input 事件来检测 @
        let mentionTimer: ReturnType<typeof setTimeout>;
        const handleMentionInput = (e: Event) => {
          const textarea = e.target as HTMLTextAreaElement;
          const text = textarea.value;
          const cursorPos = textarea.selectionStart || 0;
          const beforeCursor = text.slice(0, cursorPos);

          // 找最近的 @ 符号
          const lastAtIdx = beforeCursor.lastIndexOf('@');
          if (lastAtIdx === -1) {
            setMentionVisible(false);
            setMentionQuery('');
            return;
          }

          // @ 后面的文本就是查询词
          const query = beforeCursor.slice(lastAtIdx + 1);
          setMentionQuery(query);

          // 如果 @ 后面有空格，不触发
          if (query.includes(' ') || query.includes('\n')) {
            setMentionVisible(false);
            return;
          }

          // 计算位置
          const rect = textarea.getBoundingClientRect();
          setMentionPos({ top: rect.top - 6, left: rect.left + 12 });
          setMentionVisible(true);

          // 防抖搜索
          clearTimeout(mentionTimer);
          mentionTimer = setTimeout(() => searchMentions(query), 200);
        };

        // 等待 message-editor 渲染后绑定事件
        const bindMentionListener = setInterval(() => {
          const me = document.querySelector('message-editor');
          if (!me) return;
          const textarea = me.querySelector('textarea');
          if (!textarea) return;
          clearInterval(bindMentionListener);
          textarea.addEventListener('input', handleMentionInput);
          cleanupFns.push(() => textarea.removeEventListener('input', handleMentionInput));
        }, 100);
        cleanupFns.push(() => clearInterval(bindMentionListener));
        cleanupFns.push(() => clearTimeout(mentionTimer));

        // 点击消息中的 stock-mention 触发查看详情
        const handleMentionClick = (e: MouseEvent) => {
          const target = (e.target as HTMLElement).closest('.stock-mention') as HTMLElement;
          if (!target || !onStockSelect) return;
          const symbol = target.dataset.symbol || '';
          const name = target.dataset.name || symbol;
          onStockSelect({ symbol, name });
        };
        document.addEventListener('click', handleMentionClick, true);
        cleanupFns.push(() => document.removeEventListener('click', handleMentionClick, true));
      } // end if (mounted && containerRef.current)
    };

    init().catch(err => console.error('Init error:', err));

    return () => {
      mounted = false;
      // 执行所有清理
      cleanupFns.forEach(fn => fn());
    };
  }, []);

  return (
    <div style={chatContainerStyle}>
      <style>{sessionPanelCss}</style>
      {/* 左侧：常驻会话面板 */}
      <div style={sessionPanelStyle}>
        {/* 面板头部 */}
        <div style={sessionPanelHeaderStyle}>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--agent-text-primary)' }}>
            <i className="fas fa-history" style={{ marginRight: '6px', color: 'var(--agent-gold)', fontSize: '11px' }}></i>
            会话
          </span>
          <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
            <span style={{
              fontSize: '10px', color: 'var(--agent-text-dim)',
              background: 'rgba(240,185,11,0.08)', padding: '1px 7px', borderRadius: '8px',
            }}>{sessionsList.length}</span>
            <button onClick={handleImport} title="导入会话"
              style={sessionIconBtnStyle}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--agent-green)'; e.currentTarget.style.background = 'rgba(46,204,113,0.1)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--agent-text-dim)'; e.currentTarget.style.background = 'none'; }}>
              <i className="fas fa-file-import"></i>
            </button>
            <button onClick={handleExportAll} title="导出全部会话"
              style={sessionIconBtnStyle}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--agent-gold)'; e.currentTarget.style.background = 'var(--agent-gold-muted)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--agent-text-dim)'; e.currentTarget.style.background = 'none'; }}>
              <i className="fas fa-file-export"></i>
            </button>
          </div>
        </div>
        {/* 会话列表 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 8px 8px' }}>
          {sessionsList.length === 0 ? (
            <div style={{
              padding: '24px 8px', textAlign: 'center',
              color: 'var(--agent-text-dim)', fontSize: '11px',
            }}>
              <i className="fas fa-inbox" style={{ fontSize: '20px', display: 'block', marginBottom: '8px', opacity: 0.25 }}></i>
              暂无会话
            </div>
          ) : (
            sessionsList.map(session => {
              const isActive = session.id === sessionIdRef.current;
              return (
                <div
                  key={session.id}
                  className="session-item-row"
                  onClick={() => switchToSession(session.id)}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '10px 12px', borderRadius: '9px', cursor: 'pointer',
                    marginBottom: '3px',
                    background: isActive ? 'rgba(240,185,11,0.1)' : 'transparent',
                    border: isActive ? '1px solid rgba(240,185,11,0.2)' : '1px solid transparent',
                    borderLeft: isActive ? '3px solid var(--agent-gold)' : '3px solid transparent',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => {
                    if (!isActive) e.currentTarget.style.background = 'var(--agent-bg-hover)';
                  }}
                  onMouseLeave={e => {
                    if (!isActive) e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0, marginRight: '6px' }}>
                    <div style={{
                      fontSize: '12.5px', fontWeight: 500,
                      color: isActive ? 'var(--agent-gold)' : 'var(--agent-text-secondary)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      marginBottom: '3px', lineHeight: 1.3,
                    }}>
                      {session.title || '新会话'}
                    </div>
                    <div style={{
                      fontSize: '10px', color: 'var(--agent-text-dim)',
                      display: 'flex', gap: '8px',
                    }}>
                      <span>{session.messageCount || 0}条</span>
                      <span>{formatSessionTime(session.createdAt)}</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '2px', flexShrink: 0 }}>
                    <button
                      onClick={(e) => handleExportSingle(session.id, e)}
                      title="导出会话"
                      style={{
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: 'var(--agent-text-dim)', fontSize: '10px',
                        padding: '4px 5px', borderRadius: '4px', flexShrink: 0,
                        transition: 'opacity 0.15s',
                      }}
                      className="session-export-btn"
                      onMouseEnter={e => {
                        e.currentTarget.style.color = 'var(--agent-gold)';
                        e.currentTarget.style.background = 'var(--agent-gold-muted)';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.color = 'var(--agent-text-dim)';
                        e.currentTarget.style.background = 'none';
                      }}
                    >
                      <i className="fas fa-download"></i>
                    </button>
                    <button
                      onClick={(e) => deleteSession(session.id, e)}
                      title="删除会话"
                      style={{
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: 'var(--agent-text-dim)', fontSize: '11px',
                        padding: '4px 6px', borderRadius: '4px', flexShrink: 0,
                        transition: 'opacity 0.15s',
                      }}
                      className="session-delete-btn"
                      onMouseEnter={e => {
                        e.currentTarget.style.color = 'var(--agent-red)';
                        e.currentTarget.style.background = 'var(--agent-red-bg)';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.color = 'var(--agent-text-dim)';
                        e.currentTarget.style.background = 'none';
                      }}
                    >
                      <i className="fas fa-trash-alt"></i>
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 右侧：聊天主区域 */}
      <div style={chatMainStyle}>
        {/* Market Ticker + Session Tools */}
        <div className="market-ticker" style={{ ...tickerStyle, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center', flex: 1 }}>
          {indices.length > 0 ? (
            indices.slice(0, 4).map((idx, i) => (
              <div key={idx.name} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {i > 0 && <span style={dividerStyle}></span>}
                <div className="ticker-item" style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '12px', fontWeight: 500 }}>
                  <span className="symbol" style={{ color: 'var(--agent-text-muted)', fontWeight: 400 }}>
                    {idx.name.replace('指数', '').replace('上证', '上证').replace('深证', '深证')}
                  </span>
                  <span className="price" style={{ color: 'var(--agent-text-primary)', fontWeight: 600 }}>
                    {idx.current_price}
                  </span>
                  <span className={`change ${idx.change_pct < 0 ? 'down' : ''}`} style={changeBadgeStyle(idx.change_pct >= 0)}>
                    {idx.change_pct >= 0 ? '+' : ''}{idx.change_pct.toFixed(2)}%
                  </span>
                </div>
              </div>
            ))
          ) : (
            <div className="ticker-item">
              <span className="symbol" style={{ color: 'var(--agent-text-muted)' }}>上证</span>
              <span className="price" style={{ color: 'var(--agent-text-primary)' }}>--</span>
              <span className="change" style={changeBadgeStyle(true)}>加载中</span>
            </div>
          )}
          </div>
          {/* Mode Toggle + Session tools */}
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexShrink: 0, marginLeft: '12px' }}>
            {/* 模式切换按钮 */}
            <button
              onClick={() => setMode(prev => prev === 'chat' ? 'reflect' : 'chat')}
              title={mode === 'reflect' ? '切换为聊天模式' : '切换为专家组群聊讨论'}
              style={{
                ...toolBtnStyle,
                display: 'flex', alignItems: 'center', gap: '5px',
                fontSize: '11px', fontWeight: 600,
                padding: '3px 10px', borderRadius: '16px',
                color: mode === 'reflect' ? '#7c3aed' : 'var(--agent-gold)',
                background: mode === 'reflect' ? 'rgba(124,58,237,0.12)' : 'var(--agent-gold-muted)',
                border: mode === 'reflect' ? '1px solid rgba(124,58,237,0.3)' : '1px solid rgba(240,185,11,0.3)',
                transition: 'all 0.25s',
              }}
            >
              {mode === 'reflect' ? (
                <><i className="fas fa-users" style={{ fontSize: '10px' }}></i> 群聊</>
              ) : (
                <><i className="fas fa-comments" style={{ fontSize: '10px' }}></i> 聊天</>
              )}
            </button>
            {/* 新建会话 */}
            <button
              onClick={() => {
                // 保存当前会话元数据后再新建
                const sid = sessionIdRef.current;
                if (sid && sessionsRef.current && agentRef.current) {
                  const title = generateSessionTitle(agentRef.current.state.messages);
                  updateSessionMeta(sid, {
                    title,
                    messageCount: agentRef.current.state.messages?.length || 0,
                    createdAt: new Date().toISOString(),
                  });
                }
                const newId = generateUUID();
                localStorage.setItem('marcus_session_id', newId);
                sessionIdRef.current = newId;
                // 新建会话元数据
                updateSessionMeta(newId, { title: '新会话', messageCount: 0, createdAt: new Date().toISOString() });
                agentRef.current?.reset();
                const ml = document.querySelector('message-list') as any;
                if (ml) { ml.messages = []; (ml as LitElement).requestUpdate?.(); }
                const sc = document.querySelector('streaming-message-container') as any;
                if (sc && sc.setMessage) sc.setMessage(null, true);
                refreshSessionList();
              }}
              title="开启新会话"
              style={toolBtnStyle}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--agent-gold)'; e.currentTarget.style.background = 'var(--agent-gold-muted)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--agent-text-dim)'; e.currentTarget.style.background = 'none'; }}
            >
              <i className="fas fa-plus"></i>
            </button>
          </div>
        </div>

        {/* Chat Panel Container */}
        <div
          ref={containerRef}
          id="trading-agent-container"
          style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}
        />
      </div>

      {/* @提及 下拉弹窗 */}
      {mentionVisible && (
        <div
          className="mention-popup"
          style={{
            position: 'fixed',
            top: mentionPos.top,
            left: mentionPos.left,
            transform: 'translateY(-100%)',
            zIndex: 9999,
            background: 'var(--agent-bg-card)',
            border: '1px solid rgba(240,185,11,0.2)',
            borderRadius: '12px',
            boxShadow: 'var(--agent-shadow-popup)',
            width: '320px',
            maxHeight: '300px',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* 搜索提示 */}
          <div style={{
            padding: '8px 14px',
            borderBottom: '1px solid var(--agent-border-light)',
            fontSize: '11px',
            color: 'var(--agent-text-dim)',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}>
            <i className="fas fa-search" style={{ fontSize: '10px' }} />
            <span>提及股票/ETF · @{mentionQuery}</span>
          </div>

          {/* 结果列表 */}
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {mentionResults.length > 0 ? (
              mentionResults.map((item, i) => (
                <div
                  key={item.symbol}
                  onClick={() => insertMention(item)}
                  style={{
                    padding: '8px 14px',
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    borderBottom: i < mentionResults.length - 1 ? '1px solid var(--agent-border-subtle)' : 'none',
                    background: 'transparent',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--agent-bg-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <div>
                    <span style={{ color: 'var(--agent-text-primary)', fontSize: '13px', fontWeight: 500 }}>
                      {item.name}
                    </span>
                    <span style={{ color: 'var(--agent-text-dim)', fontSize: '11px', marginLeft: '8px' }}>
                      {item.symbol}
                    </span>
                  </div>
                  <span style={{
                    fontSize: '10px',
                    padding: '1px 6px',
                    borderRadius: '8px',
                    background: item.type === 'etf' ? 'rgba(46,204,113,0.15)' : 'rgba(240,185,11,0.12)',
                    color: item.type === 'etf' ? 'var(--agent-green)' : 'var(--agent-gold)',
                  }}>
                    {item.type === 'etf' ? 'ETF' : item.type === 'index' ? '指数' : '股票'}
                  </span>
                </div>
              ))
            ) : (
              <div style={{ padding: '16px 14px', textAlign: 'center', color: 'var(--agent-text-dim)', fontSize: '12px' }}>
                {mentionQuery ? '无匹配结果' : '输入关键词搜索...'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// 会话删除按钮 hover 效果（通过 CSS 实现）
const sessionPanelCss = `
  .session-delete-btn { opacity: 0; }
  .session-export-btn { opacity: 0; }
  .session-item-row:hover .session-delete-btn { opacity: 1; }
  .session-item-row:hover .session-export-btn { opacity: 1; }
`;

// Styles
const chatContainerStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 920,
  minHeight: 0,
  alignSelf: 'stretch',
  display: 'flex',
  flexDirection: 'row',
  background: 'var(--agent-bg-main)',
  borderRadius: '24px',
  border: '1px solid var(--agent-bg-card)',
  boxShadow: 'var(--agent-shadow-chat)',
  overflow: 'hidden',
  position: 'relative',
};

const sessionPanelStyle: React.CSSProperties = {
  width: '210px',
  minWidth: '210px',
  display: 'flex',
  flexDirection: 'column',
  background: 'var(--agent-panel-bg)',
  borderRight: '1px solid var(--agent-gold-border)',
  flexShrink: 0,
  overflow: 'hidden',
};

const sessionPanelHeaderStyle: React.CSSProperties = {
  padding: '14px 16px 12px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  flexShrink: 0,
  borderBottom: '1px solid var(--agent-border-subtle)',
};

const chatMainStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  minWidth: 0,
  overflow: 'hidden',
};

const sessionIconBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--agent-text-dim)',
  fontSize: '11px',
  cursor: 'pointer',
  padding: '3px 5px',
  borderRadius: '4px',
  transition: 'all 0.2s',
  flexShrink: 0,
};

const toolBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--agent-text-dim)',
  fontSize: '13px',
  cursor: 'pointer',
  padding: '3px 6px',
  borderRadius: '4px',
  transition: 'all 0.2s',
};

const tickerStyle: React.CSSProperties = {
  background: 'var(--agent-bg-card)',
  padding: '8px 20px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  borderBottom: '1px solid var(--agent-border-subtle)',
  flexShrink: 0,
  gap: '6px',
  flexWrap: 'wrap' as const,
};

const dividerStyle: React.CSSProperties = {
  width: '1px',
  height: '20px',
  background: 'var(--agent-border-subtle)',
  flexShrink: 0,
};

const changeBadgeStyle = (isUp: boolean): React.CSSProperties => ({
  fontSize: '11px',
  fontWeight: 600,
  padding: '2px 8px',
  borderRadius: '14px',
  background: isUp ? 'var(--agent-green-bg)' : 'var(--agent-red-bg)',
  color: isUp ? 'var(--agent-green)' : 'var(--agent-red)',
});
