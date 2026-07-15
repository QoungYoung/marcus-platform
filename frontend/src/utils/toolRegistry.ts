/**
 * Marcus 工具注册表 — 前端测试台用
 *
 * 所有工具定义与后端 servers/pi-server/src/tools.ts 保持一致，
 * 映射到 /api/v1 的 HTTP 端点，供 ToolsPage 调用。
 */

export interface ParamDef {
  type: 'string' | 'number' | 'date' | 'stock_code' | 'select' | 'boolean';
  description: string;
  required: boolean;
  default?: any;
  options?: { label: string; value: string }[]; // select 类型
  placeholder?: string;
}

export interface ToolDef {
  name: string;
  label: string;
  description: string;
  category: ToolCategory;
  endpoint: {
    method: 'GET' | 'POST' | 'DELETE';
    path: string; // 相对 /api/v1 的路径模板，如 '/market/quote/{symbol}'
  };
  parameters: Record<string, ParamDef>;
  /** 需要替换到 URL 路径中的参数名 */
  pathParamNames: string[];
}

export type ToolCategory =
  | 'market'        // 行情查询
  | 'technical'     // 技术分析
  | 'moneyflow'     // 资金流向
  | 'fundamental'   // 基本面
  | 'trading'       // 交易执行
  | 'database'      // 数据库
  | 'analysis';     // 分析历史

export const CATEGORY_LABELS: Record<ToolCategory, string> = {
  market: '行情查询',
  technical: '技术分析',
  moneyflow: '资金流向',
  fundamental: '基本面',
  trading: '交易执行',
  database: '数据库查询',
  analysis: '分析历史',
};

export const CATEGORY_ICONS: Record<ToolCategory, string> = {
  market: 'TrendingUp',
  technical: 'Activity',
  moneyflow: 'DollarSign',
  fundamental: 'Building2',
  trading: 'ArrowLeftRight',
  database: 'Database',
  analysis: 'FileSearch',
};

// ─── 全部工具定义 ───

export const ALL_TOOLS: ToolDef[] = [
  // ═══ 行情查询 ═══
  {
    name: 'get_market_indices',
    label: '市场行情',
    description: '获取 A股指数（上证、深证、创业板）、美股指数、港股指数的实时行情',
    category: 'market',
    endpoint: { method: 'GET', path: '/market/indices' },
    parameters: {},
    pathParamNames: [],
  },
  {
    name: 'get_quote',
    label: '个股行情',
    description: '查询个股实时行情，包括当前价格、涨跌、成交量、换手率等',
    category: 'market',
    endpoint: { method: 'GET', path: '/market/quote/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码，如 SH600519、SZ000001', required: true, placeholder: 'SH600519' },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_portfolio',
    label: '账户持仓',
    description: '查看当前账户资金状况和所有持仓明细',
    category: 'market',
    endpoint: { method: 'GET', path: '/portfolio' },
    parameters: {},
    pathParamNames: [],
  },
  {
    name: 'get_concept_fund_flow',
    label: '概念板块行情',
    description: '获取概念板块行情排行（按涨幅或主力资金流向排序）',
    category: 'market',
    endpoint: { method: 'GET', path: '/market/concept-fund-flow' },
    parameters: {
      limit: { type: 'number', description: '返回数量，默认15', required: false, default: 15 },
      sort_by: {
        type: 'select', description: '排序字段', required: false, default: 'main_net',
        options: [
          { label: '主力净流入排行', value: 'main_net' },
          { label: '涨幅排行', value: 'pct_change' },
        ],
      },
    },
    pathParamNames: [],
  },
  {
    name: 'get_industry_fund_flow',
    label: '行业板块行情',
    description: '获取行业板块行情排行（按涨幅或主力资金流向排序）',
    category: 'market',
    endpoint: { method: 'GET', path: '/market/sector-flow' },
    parameters: {
      limit: { type: 'number', description: '返回数量，默认15', required: false, default: 15 },
      sort_by: {
        type: 'select', description: '排序字段', required: false, default: 'main_net',
        options: [
          { label: '主力净流入排行', value: 'main_net' },
          { label: '涨幅排行', value: 'pct_change' },
        ],
      },
    },
    pathParamNames: [],
  },
  {
    name: 'get_concept_mapping',
    label: '概念成分股',
    description: '查询概念板块成分股 / 反查股票所属概念 / 列出所有概念',
    category: 'market',
    endpoint: { method: 'GET', path: '/market/concept' },
    parameters: {
      concept_name: { type: 'string', description: '概念名称，如 人形机器人。不传则列出所有概念', required: false, placeholder: '人形机器人' },
      symbol: { type: 'stock_code', description: '股票代码，反查该股票所属概念', required: false, placeholder: 'SZ000001' },
      limit: { type: 'number', description: '返回数量，默认30', required: false, default: 30 },
    },
    pathParamNames: [],
  },
  {
    name: 'get_etf_quote',
    label: 'ETF行情',
    description: '查询ETF基金实时行情',
    category: 'market',
    endpoint: { method: 'GET', path: '/etf/quote/{symbol}' },
    parameters: {
      symbol: { type: 'string', description: 'ETF代码，如 510300、159915', required: true, placeholder: '510300' },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_etf_kline',
    label: 'ETF K线',
    description: '获取ETF历史K线数据（日/周/月），包含开高低收、成交量、成交额',
    category: 'market',
    endpoint: { method: 'GET', path: '/etf/kline/{symbol}' },
    parameters: {
      symbol: { type: 'string', description: 'ETF代码，如 510300', required: true, placeholder: '510300' },
      period: {
        type: 'select', description: 'K线周期', required: false, default: 'day',
        options: [{ label: '日线', value: 'day' }, { label: '周线', value: 'week' }, { label: '月线', value: 'month' }],
      },
      count: { type: 'number', description: '数据条数，默认284', required: false, default: 284 },
    },
    pathParamNames: ['symbol'],
  },

  // ═══ 技术分析 ═══
  {
    name: 'get_daily_kline',
    label: '日K线',
    description: '获取A股个股历史日K线数据（未复权），含开高低收、成交量、成交额。日频非实时，Tushare daily盘后数据',
    category: 'technical',
    endpoint: { method: 'GET', path: '/market/kline/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      start_date: { type: 'date', description: '开始日期 YYYYMMDD', required: false },
      end_date: { type: 'date', description: '结束日期 YYYYMMDD', required: false },
      limit: { type: 'number', description: '返回条数，默认100，最大500', required: false, default: 100 },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_daily_kline_qfq',
    label: '日K线(前复权)',
    description: '获取A股前复权日K线，消除除权除息跳空缺口，技术指标连续可靠。复盘分析专用',
    category: 'technical',
    endpoint: { method: 'GET', path: '/market/pro-bar/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      start_date: { type: 'date', description: '开始日期 YYYYMMDD', required: false },
      end_date: { type: 'date', description: '结束日期 YYYYMMDD', required: false },
      limit: { type: 'number', description: '返回条数，默认100', required: false, default: 100 },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_technical',
    label: '技术指标',
    description: '获取60+技术指标（MACD/KDJ/RSI/布林带/CCI/WR等）。日频非实时，Tushare盘后确认值',
    category: 'technical',
    endpoint: { method: 'GET', path: '/market/technical/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      start_date: { type: 'date', description: '开始日期 YYYYMMDD', required: false },
      end_date: { type: 'date', description: '结束日期 YYYYMMDD', required: false },
      limit: { type: 'number', description: '返回条数，默认100，最大500', required: false, default: 100 },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_realtime_indicators',
    label: '实时技术指标',
    description: '盘中实时估算KDJ/MACD/RSI/MA5/MA10/MA20。腾讯行情+Tushare历史计算，仅辅助参考',
    category: 'technical',
    endpoint: { method: 'GET', path: '/indicator/realtime/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_intraday_min',
    label: '实时分钟K线',
    description: '获取多只股票今日实时分钟K线（1/5/15/30/60分钟），支持批量查询最多10只',
    category: 'technical',
    endpoint: { method: 'GET', path: '/market/intraday-min' },
    parameters: {
      symbols: { type: 'string', description: '股票代码，逗号分隔，如 000001.SZ,600519.SH', required: true, placeholder: '000001.SZ,600519.SH' },
      freq: {
        type: 'select', description: 'K线周期', required: false, default: '1min',
        options: [
          { label: '1分钟', value: '1min' }, { label: '5分钟', value: '5min' },
          { label: '15分钟', value: '15min' }, { label: '30分钟', value: '30min' },
          { label: '60分钟', value: '60min' },
        ],
      },
    },
    pathParamNames: [],
  },
  {
    name: 'get_fibonacci_levels',
    label: '斐波那契回撤',
    description: '计算0.382/0.618/0.786回撤价位，判断支撑/阻力位和当前价格区间',
    category: 'technical',
    endpoint: { method: 'POST', path: '/indicator/fibonacci' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      high: { type: 'number', description: '阶段顶部价格（不传则自动提取）', required: false },
      low: { type: 'number', description: '阶段底部价格（不传则自动提取）', required: false },
    },
    pathParamNames: [],
  },
  {
    name: 'get_daily_channel',
    label: '日内K值通道',
    description: '计算日内压力/支撑通道（K=0.98848），用于超短线精确入场/离场价位',
    category: 'technical',
    endpoint: { method: 'GET', path: '/indicator/daily-channel/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      avg_price: { type: 'number', description: '分时均价（不传则从行情估算）', required: false },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_trade_advice',
    label: '综合操作建议',
    description: '牛股计算器决策树：结合斐波那契回撤、K值通道、持仓天数给出买入/持有/卖出明确信号',
    category: 'technical',
    endpoint: { method: 'POST', path: '/indicator/advice' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      cost: { type: 'number', description: '成本价（有持仓时传入）', required: false },
      high: { type: 'number', description: '阶段顶部价格', required: false },
      low: { type: 'number', description: '阶段底部价格', required: false },
      avg_price: { type: 'number', description: '分时均价', required: false },
      buy_date: { type: 'date', description: '建仓日期 YYYY-MM-DD', required: false },
    },
    pathParamNames: [],
  },
  {
    name: 'check_entry_filters',
    label: '入场三层过滤',
    description: '建仓前必调：技术面(MA5/MA20/MACD/分位) + 主力行为(5日/10日资金) + 超买(RSI6/KDJ-J)逐条判定',
    category: 'technical',
    endpoint: { method: 'POST', path: '/indicator/check-entry-filters' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      sector_net_inflow: { type: 'number', description: '所属板块主力资金净流入（元）', required: false },
      volume_ratio: { type: 'number', description: '量比', required: false },
    },
    pathParamNames: [],
  },
  {
    name: 'calc_position',
    label: '仓位计算',
    description: '建仓前必调：根据信号强度、产业链角色、加仓层级、市场立场计算建议仓位和止损价',
    category: 'technical',
    endpoint: { method: 'POST', path: '/indicator/calc-position' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      signal_strength: {
        type: 'select', description: '信号强度', required: true, default: 'medium',
        options: [
          { label: '高确定性 (3+指标共振+龙头+主力流入)', value: 'high' },
          { label: '中确定性 (2指标共振)', value: 'medium' },
          { label: '低确定性 (单一信号)', value: 'low' },
        ],
      },
      chain_role: {
        type: 'select', description: '产业链角色', required: true, default: 'mid',
        options: [
          { label: '上游核心', value: 'upstream' },
          { label: '中游配套', value: 'mid' },
          { label: '下游应用', value: 'downstream' },
        ],
      },
      tier: {
        type: 'select', description: '加仓层级', required: true, default: 'probe',
        options: [
          { label: '试探仓 (首仓)', value: 'probe' },
          { label: '确认仓 (需浮盈≥1%)', value: 'confirm' },
          { label: '冲刺仓 (需浮盈≥3%)', value: 'sprint' },
        ],
      },
      stance: {
        type: 'select', description: '市场立场', required: true, default: 'yellow',
        options: [
          { label: '激进 (总仓≤60%)', value: 'green' },
          { label: '谨慎 (总仓≤50%)', value: 'yellow' },
          { label: '观望 (总仓≤20%)', value: 'red' },
        ],
      },
    },
    pathParamNames: [],
  },
  {
    name: 'check_stop_profit',
    label: '止盈趋势检查',
    description: '止盈前必调：趋势完好+主力净流入+仍在主线 → 禁止止盈。趋势走弱 → 允许止盈',
    category: 'technical',
    endpoint: { method: 'GET', path: '/indicator/check-stop-profit/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
    },
    pathParamNames: ['symbol'],
  },

  // ═══ 资金流向 ═══
  {
    name: 'get_moneyflow',
    label: '个股资金流向',
    description: '获取个股实时资金流向（主力/超大单/大单/中单/小单净流入+5日/10日累计）',
    category: 'moneyflow',
    endpoint: { method: 'GET', path: '/market/moneyflow/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_market_moneyflow',
    label: '大盘资金流向',
    description: '获取沪深两市大盘资金流向（主力/超大单/大单/中单/小单净流入+买卖分明细+总成交额）',
    category: 'moneyflow',
    endpoint: { method: 'GET', path: '/market/moneyflow-mkt' },
    parameters: {},
    pathParamNames: [],
  },

  // ═══ 基本面 ═══
  {
    name: 'get_fina_mainbz',
    label: '主营业务构成',
    description: '获取个股主营业务构成（产品/行业/地区维度的收入与利润占比）。数据源：Tushare fina_mainbz',
    category: 'fundamental',
    endpoint: { method: 'GET', path: '/indicator/fina-mainbz/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      period: { type: 'date', description: '报告期 YYYYMMDD，默认最新', required: false },
      limit: { type: 'number', description: '返回条数，默认10', required: false, default: 10 },
    },
    pathParamNames: ['symbol'],
  },
  {
    name: 'get_express',
    label: '业绩快报',
    description: '获取个股业绩快报（营收/利润/EPS/ROE及同比增长率）。数据源：Tushare express',
    category: 'fundamental',
    endpoint: { method: 'GET', path: '/indicator/express/{symbol}' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      period: { type: 'date', description: '报告期 YYYYMMDD，默认最近', required: false },
      limit: { type: 'number', description: '返回期数，默认5', required: false, default: 5 },
    },
    pathParamNames: ['symbol'],
  },

  // ═══ 交易执行 ═══
  {
    name: 'place_order',
    label: '下单交易',
    description: '执行股票买入或卖出交易。⚠️ 真实交易，谨慎使用',
    category: 'trading',
    endpoint: { method: 'POST', path: '/trades' },
    parameters: {
      symbol: { type: 'stock_code', description: '股票代码', required: true, placeholder: 'SH600519' },
      side: {
        type: 'select', description: '交易方向', required: true,
        options: [{ label: '买入', value: 'buy' }, { label: '卖出', value: 'sell' }],
      },
      price: { type: 'number', description: '委托价格（元）', required: true },
      volume: { type: 'number', description: '交易数量（股），100的整数倍', required: true, default: 100 },
      reason: { type: 'string', description: '交易理由', required: false, placeholder: '突破前高确认右侧，试探仓入场' },
    },
    pathParamNames: [],
  },
  {
    name: 'get_orders',
    label: '查询订单',
    description: '查询当前活跃订单（未成交/部分成交），避免重复下单',
    category: 'trading',
    endpoint: { method: 'GET', path: '/trades/orders' },
    parameters: {
      symbol: { type: 'stock_code', description: '按股票代码筛选', required: false, placeholder: 'SH600519' },
      status: { type: 'string', description: '按状态筛选', required: false, placeholder: '未成交' },
      limit: { type: 'number', description: '返回条数，默认50', required: false, default: 50 },
    },
    pathParamNames: [],
  },
  {
    name: 'cancel_order',
    label: '撤销订单',
    description: '撤销一个未成交的委托订单',
    category: 'trading',
    endpoint: { method: 'DELETE', path: '/trades/{order_id}/cancel' },
    parameters: {
      order_id: { type: 'string', description: '订单号，如 ORD000001', required: true, placeholder: 'ORD000001' },
    },
    pathParamNames: ['order_id'],
  },
  {
    name: 'get_latest_scan_report',
    label: '最新扫描报告',
    description: '获取最新盘中扫描报告，包含市场立场、热门概念、观察列表、Pi策略分析',
    category: 'trading',
    endpoint: { method: 'GET', path: '/scan/latest' },
    parameters: {
      date: { type: 'date', description: '日期 YYYY-MM-DD，默认今天', required: false },
    },
    pathParamNames: [],
  },

  // ═══ 分析历史 ═══
  {
    name: 'get_pi_analysis_history',
    label: 'Pi分析历史',
    description: '按日期范围查询Pi策略分析历史记录，含立场/仓位上限/判断理由/完整报告',
    category: 'analysis',
    endpoint: { method: 'GET', path: '/scan/pi-analysis' },
    parameters: {
      start_date: { type: 'date', description: '开始日期 YYYY-MM-DD', required: false },
      end_date: { type: 'date', description: '结束日期 YYYY-MM-DD', required: false },
    },
    pathParamNames: [],
  },
  {
    name: 'get_trade_history',
    label: '交易报告历史',
    description: '按日期范围查询Pi交易执行报告，含买卖决策/仓位变化/产业链组合/风险监控',
    category: 'analysis',
    endpoint: { method: 'GET', path: '/scan/trade-reports' },
    parameters: {
      start_date: { type: 'date', description: '开始日期 YYYY-MM-DD', required: false },
      end_date: { type: 'date', description: '结束日期 YYYY-MM-DD', required: false },
    },
    pathParamNames: [],
  },

  // ═══ 数据库查询 ═══
  {
    name: 'read_db_table',
    label: '数据库查询',
    description: '读取数据库表数据，支持查询、筛选和排序。数据库: stock_pool.db / trades.db / news.db / cache.db',
    category: 'database',
    endpoint: { method: 'GET', path: '/db/query' },
    parameters: {
      db: {
        type: 'select', description: '数据库名', required: true,
        options: [
          { label: 'stock_pool.db (股票池)', value: 'stock_pool.db' },
          { label: 'trades.db (交易记录)', value: 'trades.db' },
          { label: 'news.db (资讯)', value: 'news.db' },
          { label: 'cache.db (缓存)', value: 'cache.db' },
        ],
      },
      table: { type: 'string', description: '表名', required: true, placeholder: 'stock_daily' },
      columns: { type: 'string', description: '要查询的列，逗号分隔', required: false, placeholder: 'ts_code,close,volume' },
      where: { type: 'string', description: 'WHERE条件', required: false, placeholder: "ts_code LIKE '000001%'" },
      orderBy: { type: 'string', description: '排序', required: false, placeholder: 'close DESC' },
      limit: { type: 'number', description: '返回条数，默认100', required: false, default: 100 },
    },
    pathParamNames: [],
  },
  {
    name: 'get_db_schema',
    label: '数据库结构',
    description: '获取数据库的表结构和字段信息',
    category: 'database',
    endpoint: { method: 'GET', path: '/db/schema/{db}' },
    parameters: {
      db: {
        type: 'select', description: '数据库名', required: true,
        options: [
          { label: 'stock_pool', value: 'stock_pool' },
          { label: 'trades', value: 'trades' },
          { label: 'news', value: 'news' },
          { label: 'cache', value: 'cache' },
        ],
      },
    },
    pathParamNames: ['db'],
  },
];

/** 按分类分组 */
export function getToolsByCategory(): Record<ToolCategory, ToolDef[]> {
  const groups: Record<string, ToolDef[]> = {};
  for (const cat of Object.keys(CATEGORY_LABELS) as ToolCategory[]) {
    groups[cat] = [];
  }
  for (const tool of ALL_TOOLS) {
    if (groups[tool.category]) {
      groups[tool.category].push(tool);
    }
  }
  return groups as Record<ToolCategory, ToolDef[]>;
}

/** 搜索工具 */
export function searchTools(query: string): ToolDef[] {
  const q = query.toLowerCase();
  return ALL_TOOLS.filter(t =>
    t.name.toLowerCase().includes(q) ||
    t.label.toLowerCase().includes(q) ||
    t.description.toLowerCase().includes(q)
  );
}
