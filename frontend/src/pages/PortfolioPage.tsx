import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
  PieChart, Pie, Cell as PieCell,
} from 'recharts';
import { portfolioApi, marketApi, tradesApi, schedulerApi } from '../api/client';
import {
  computeSharpeRatio, computeMonthlyReturns, computeQuarterlyReturns,
  computeBenchmarkDelta, aggregatePnlContributions,
} from './portfolioMetrics';
import type { PnlContribution, PeriodReturn } from './portfolioMetrics';
import '../styles/agent-theme.css';
import '../styles/portfolio-page.css';

// ── 资金流类型 ──
interface MoneyflowRow {
  symbol: string; name?: string; main_net: number; inflow: number; outflow: number;
  net_amount: number; price?: number; change_pct?: string;
}

// ── 行业集中度类型 ──
interface SectorItem { name: string; weight_pct: number; stock_count: number; }
interface SectorConcentration {
  sectors: SectorItem[]; max_sector: SectorItem | null; concentration_level: string;
}

// ── 快捷交易表单 ──
interface TradeForm { direction: '买' | '卖'; price: number; volume: number; reason: string; }

// ── 类型 ──
interface Position {
  symbol: string; name: string; volume: number;
  avg_price: number; current_price: number; change_pct?: number; today_pnl?: number;
  market_value: number; floating_pnl: number; floating_pnl_pct: number;
}
interface Account {
  initial_capital: number; available_cash: number; frozen_cash?: number;
  position_value: number; total_asset: number; realized_pnl: number;
  float_pnl: number; total_pnl: number; position_ratio: number;
  week_realized_pnl?: number; week_float_pnl?: number;
  positions: Position[];
}
interface PortfolioSummary {
  account: Account; total_return: number; total_return_pct: number; win_rate: number;
  sector_concentration?: SectorConcentration | null;
}
interface EquityPoint { date: string; value: number; }
interface DailyPnl { date: string; pnl: number; }
interface IndexTicker { name: string; price: number; change_pct: number; }
interface TradeRecord { order_id?: string; symbol: string; name?: string; direction: string; price: number; volume: number; created_at?: string; }
interface StockPnlItem { symbol: string; name?: string; volume: number; close_price: number; prev_close: number; float_pnl: number; realized_pnl: number; }
interface PnlBreakdownItem { date: string; daily_pnl: number; realized_total: number; float_total: number; stocks: StockPnlItem[]; }

// ── 止损监控类型 ──
interface StopDistance {
  symbol: string; name?: string; avg_price: number; current_price: number; volume: number;
  float_pnl_pct: number; t1_locked: boolean; daily_stops_used: number;
  nearest_trigger: { rule: string; distance_pct: number; danger_level: string; };
  rule_distances: Record<string, number | null>;
}
interface StopLossStatus {
  running: boolean; thread_alive: boolean; interval_seconds: number;
  today_stops_count: number; is_trading_time: boolean;
  is_morning_volatility: boolean; position_count: number;
  triggered_count: number; positions: StopDistance[];
}

type SortKey = 'market_value' | 'floating_pnl' | 'floating_pnl_pct' | 'weight';

const GREEN = '#2ecc71'; const RED = '#e74c3c';

// ── 工具 ──
function fmtMoney(val: number): string {
  const abs = Math.abs(val);
  if (abs >= 1e8) return `${(val / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(val / 1e4).toFixed(2)}万`;
  return val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtMoneyShort(val: number): string {
  const abs = Math.abs(val); const sign = val < 0 ? '-' : '';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)}万`;
  return `${sign}${abs.toFixed(0)}`;
}
function cleanStockName(name: string | undefined, symbol: string): string {
  if (!name) return symbol;
  return name.replace(/^(SH|SZ|BJ)\d+/, '').trim() || symbol;
}

function calcMaxDrawdown(curve: EquityPoint[]): number {
  let peak = 0; let maxDD = 0;
  for (const pt of curve) {
    if (pt.value > peak) peak = pt.value;
    const dd = peak > 0 ? (peak - pt.value) / peak * 100 : 0;
    if (dd > maxDD) maxDD = dd;
  }
  return maxDD;
}

// ── 主组件 ──
export default function PortfolioPage() {
  const { t } = useTranslation();

  // ── 分片异步状态 ──
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [tickers, setTickers] = useState<IndexTicker[]>([]);
  const [recentTrades, setRecentTrades] = useState<TradeRecord[]>([]);
  const [realEquity, setRealEquity] = useState<{ date: string; equity: number; daily_pnl?: number }[]>([]);
  const [modalDate, setModalDate] = useState<string | null>(null);
  const [modalData, setModalData] = useState<PnlBreakdownItem | null>(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [stopLoss, setStopLoss] = useState<StopLossStatus | null>(null);
  const [breakdowns, setBreakdowns] = useState<PnlBreakdownItem[]>([]);

  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingTickers, setLoadingTickers] = useState(true);
  const [loadingEquity, setLoadingEquity] = useState(true);
  const [loadingTrades, setLoadingTrades] = useState(true);
  const [loadingStopLoss, setLoadingStopLoss] = useState(true);

  const [error, setError] = useState<string | null>(null);
  const [lastUpdate] = useState<Date>(new Date());
  const [sortKey, setSortKey] = useState<SortKey>('market_value');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [fabOpen, setFabOpen] = useState(false);
  const [unfreezing, setUnfreezing] = useState(false);
  const [slExpanded, setSlExpanded] = useState(false);
  const [slToggling, setSlToggling] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  usePortfolioBackground(canvasRef);

  // ── 资金流 ──
  const [moneyflowMap, setMoneyflowMap] = useState<Record<string, MoneyflowRow>>({});
  const [loadingFlow, setLoadingFlow] = useState(false);

  // ── 快捷交易面板 ──
  const [expandedTradeSymbol, setExpandedTradeSymbol] = useState<string | null>(null);
  const [tradeForm, setTradeForm] = useState<TradeForm>({ direction: '买', price: 0, volume: 0, reason: '' });
  const [tradeError, setTradeError] = useState<string | null>(null);
  const [tradeSubmitting, setTradeSubmitting] = useState(false);

  // ── 各模块独立 fetch（flushSync 确保 React 18 不批量合并，每个模块加载后即时渲染） ──
  const refreshSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const res = await portfolioApi.getSummary();
      flushSync(() => { setSummary(res.data); setError(null); setLoadingSummary(false); });
    } catch (err: unknown) {
      flushSync(() => { setError((err as Error).message); setLoadingSummary(false); });
    }
  }, []);

  const refreshTickers = useCallback(async () => {
    setLoadingTickers(true);
    try {
      const res = await marketApi.getIndices();
      if (res.data?.indices) {
        const list = res.data.indices.slice(0, 6).map((i: Record<string, unknown>) => ({
          name: String(i.name || '').slice(0, 4),
          price: Number(i.current_price ?? 0),
          change_pct: Number(i.change_pct ?? 0),
        }));
        flushSync(() => { setTickers(list); setLoadingTickers(false); });
      } else { flushSync(() => setLoadingTickers(false)); }
    } catch { flushSync(() => setLoadingTickers(false)); }
  }, []);

  const refreshEquity = useCallback(async () => {
    setLoadingEquity(true);
    try {
      const equityRes = await portfolioApi.getEquityHistory(60);
      if (equityRes.data && Array.isArray(equityRes.data) && equityRes.data.length > 0) {
        flushSync(() => { setRealEquity(equityRes.data); setLoadingEquity(false); });
      } else { flushSync(() => setLoadingEquity(false)); }
    } catch { flushSync(() => setLoadingEquity(false)); }
  }, []);

  const refreshTrades = useCallback(async () => {
    setLoadingTrades(true);
    try {
      const res = await tradesApi.getHistory({ limit: 8 });
      const trades = res.data?.trades || res.data?.data || [];
      flushSync(() => { setRecentTrades(Array.isArray(trades) ? trades.slice(0, 8) : []); setLoadingTrades(false); });
    } catch { flushSync(() => setLoadingTrades(false)); }
  }, []);

  const refreshStopLoss = useCallback(async () => {
    setLoadingStopLoss(true);
    try {
      const res = await schedulerApi.getStopLossMonitor();
      flushSync(() => {
        if (res.data?.success) { setStopLoss(res.data as StopLossStatus); }
        else { setStopLoss({ running: false } as StopLossStatus); }
        setLoadingStopLoss(false);
      });
    } catch { flushSync(() => { setStopLoss({ running: false } as StopLossStatus); setLoadingStopLoss(false); }); }
  }, []);

  // ── 首次并行加载 ──
  useEffect(() => {
    refreshSummary();
    refreshTickers();
    refreshEquity();
    refreshTrades();
    refreshStopLoss();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [loadingBreakdowns, setLoadingBreakdowns] = useState(true);

  // ── 30日盈亏明细（贡献排名用）──
  const refreshBreakdowns = useCallback(async () => {
    setLoadingBreakdowns(true);
    try {
      const res = await portfolioApi.getDailyPnlBreakdown(30);
      if (Array.isArray(res.data)) setBreakdowns(res.data);
    } catch { /* 静默失败 */ }
    finally { setLoadingBreakdowns(false); }
  }, []);

  useEffect(() => { refreshBreakdowns(); }, [refreshBreakdowns]);

  // ── 资金流数据（持仓股）──
  const refreshMoneyflow = useCallback(async () => {
    const syms = summary?.account?.positions?.map(p => p.symbol) || [];
    if (syms.length === 0) return;
    setLoadingFlow(true);
    const results = await Promise.allSettled(
      syms.map(sym => marketApi.getMoneyflow(sym).then(r => ({ symbol: sym, data: r.data })))
    );
    const map: Record<string, MoneyflowRow> = {};
    for (const r of results) {
      if (r.status === 'fulfilled') {
        const { symbol, data } = r.value;
        if (data) map[symbol] = data as MoneyflowRow;
      }
    }
    setMoneyflowMap(map);
    setLoadingFlow(false);
  }, [summary?.account?.positions]);

  useEffect(() => {
    if (summary?.account?.positions?.length) refreshMoneyflow();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary?.account?.positions?.length]);

  // ── 衍生：资金流汇总 ──
  const flowSummary = useMemo(() => {
    const symbols = Object.keys(moneyflowMap);
    if (symbols.length === 0) return null;
    const inflow = symbols.filter(s => (moneyflowMap[s].main_net || 0) > 0).length;
    const outflow = symbols.filter(s => (moneyflowMap[s].main_net || 0) < 0).length;
    return { inflow, outflow, total: symbols.length };
  }, [moneyflowMap]);

  // ── 衍生：行业集中度 ──
  const sectorData = summary?.sector_concentration ?? null;

  // 启动/停止止损监控
  const handleToggleSL = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (slToggling || !stopLoss) return;
    setSlToggling(true);
    try {
      const isRunning = stopLoss.running && stopLoss.thread_alive;
      if (isRunning) {
        await schedulerApi.stopStopLossMonitor();
      } else {
        await schedulerApi.startStopLossMonitor();
      }
      await refreshStopLoss();
    } catch (err) {
      console.error('止损监控操作失败:', err);
    } finally { setSlToggling(false); }
  }, [slToggling, stopLoss, refreshStopLoss]);

  const handleUnfreeze = useCallback(async () => {
    if (unfreezing) return;
    if (!window.confirm(t('portfolio.unfreezeConfirm'))) return;
    setUnfreezing(true);
    try {
      const res = await portfolioApi.unfreeze();
      if (res.data?.success) {
        alert(t('portfolio.unfreezeSuccess') + `: ¥${(res.data.unfrozen_amount || 0).toLocaleString()}`);
        await refreshSummary();
      } else {
        alert(t('portfolio.unfreezeFailed') + ': ' + (res.data?.message || ''));
      }
    } catch (err: unknown) {
      alert(t('portfolio.unfreezeFailed') + ': ' + (err instanceof Error ? err.message : String(err)));
    } finally { setUnfreezing(false); }
  }, [unfreezing, t, refreshSummary]);

  // ── 排序 ──
  const handleSort = useCallback((key: SortKey) => {
    setSortKey(prev => { setSortDir(prev === key ? (sortDir === 'desc' ? 'asc' : 'desc') : 'desc'); return key; });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortDir]);

  // ── 派生数据 ──
  const initialCap = summary?.account?.initial_capital || 0;
  const totalReturnPct = summary?.total_return_pct || 0;
  const positions = summary?.account?.positions || [];
  const posVal = summary?.account?.position_value || 0;
  const cash = summary?.account?.available_cash || 0;
  const totalAsset = summary?.account?.total_asset || 0;

  // ── 快捷交易 ──
  const openTradePanel = useCallback((symbol: string, direction: '买' | '卖', currentPrice: number, volume: number) => {
    if (expandedTradeSymbol === symbol) {
      setExpandedTradeSymbol(null);
      setTradeError(null);
    } else {
      setExpandedTradeSymbol(symbol);
      setTradeForm({ direction, price: currentPrice, volume: direction === '卖' ? volume : 0, reason: '' });
      setTradeError(null);
    }
  }, [expandedTradeSymbol]);

  const handleTradeSubmit = useCallback(async () => {
    if (!expandedTradeSymbol) return;
    setTradeError(null);
    if (tradeForm.price <= 0) { setTradeError('价格必须大于0'); return; }
    if (tradeForm.volume <= 0) { setTradeError('数量必须大于0'); return; }
    if (tradeForm.direction === '卖') {
      const pos = positions.find(p => p.symbol === expandedTradeSymbol);
      if (pos && tradeForm.volume > pos.volume) { setTradeError('卖出数量超过持仓'); return; }
    }
    setTradeSubmitting(true);
    try {
      await tradesApi.execute({
        symbol: expandedTradeSymbol,
        side: tradeForm.direction === '买' ? 'buy' : 'sell',
        price: tradeForm.price,
        volume: tradeForm.volume,
        reason: tradeForm.reason || undefined,
      });
      setExpandedTradeSymbol(null);
      setTradeError(null);
      await refreshSummary();
    } catch (err: unknown) {
      setTradeError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err instanceof Error ? err.message : '交易失败'));
    } finally {
      setTradeSubmitting(false);
    }
  }, [expandedTradeSymbol, tradeForm, positions, refreshSummary]);

  const equityCurve: EquityPoint[] = useMemo(() => {
    return realEquity.map(p => ({ date: p.date.slice(5), value: p.equity }));
  }, [realEquity]);

  const maxDrawdown = useMemo(() => calcMaxDrawdown(equityCurve), [equityCurve]);
  const dailyPnlData = useMemo(() => {
    if (realEquity.length < 2) return [];
    return realEquity.slice(1).map((p, i) => ({
      date: p.date.slice(5),
      fullDate: p.date,
      pnl: p.daily_pnl != null ? p.daily_pnl : (p.equity - realEquity[i].equity),
    }));
  }, [realEquity]);

  const handleBarClick = useCallback(async (data: any) => {
    if (!data?.fullDate) return;
    const date = data.fullDate;
    setModalDate(date);
    setModalLoading(true);
    setModalData(null);
    try {
      const res = await portfolioApi.getDailyPnlBreakdownByDate(date);
      setModalData(res.data);
    } catch { /* ignore */ }
    finally { setModalLoading(false); }
  }, []);
  const volatility = useMemo(() => {
    const returns = equityCurve.slice(1).map((p, i) => (p.value - equityCurve[i].value) / equityCurve[i].value);
    const mean = returns.reduce((a, b) => a + b, 0) / (returns.length || 1);
    return Math.sqrt(returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length || 1)) * Math.sqrt(252) * 100;
  }, [equityCurve]);

  const sortedPositions = useMemo(() => {
    const arr = [...positions];
    arr.sort((a, b) => {
      let va: number, vb: number;
      if (sortKey === 'weight') { va = totalAsset > 0 ? a.market_value / totalAsset : 0; vb = totalAsset > 0 ? b.market_value / totalAsset : 0; }
      else { va = (a as unknown as Record<string, number>)[sortKey] || 0; vb = (b as unknown as Record<string, number>)[sortKey] || 0; }
      return sortDir === 'desc' ? vb - va : va - vb;
    });
    return arr;
  }, [positions, sortKey, sortDir, posVal]);

  const ringData = useMemo(() => {
    const items = sortedPositions.slice(0, 5).map(p => ({
      name: cleanStockName(p.name, p.symbol),
      value: p.market_value,
      pnl: p.floating_pnl >= 0 ? 'up' as const : 'down' as const,
    }));
    const otherVal = sortedPositions.slice(5).reduce((s, p) => s + p.market_value, 0);
    if (otherVal > 0) items.push({ name: '其他', value: otherVal, pnl: 'up' as const });
    if (cash > 0) items.push({ name: '现金', value: cash, pnl: 'up' as const });
    return items;
  }, [sortedPositions, cash]);

  const PIE_COLORS = ['#f0b90b', '#3498db', '#2ecc71', '#9b59b6', '#e67e22', '#1abc9c', '#6a7d9b'];

  // ── 从 summary 解构 ──
  const account = summary?.account;
  const win_rate = summary?.win_rate || 0;
  const frozen = account?.frozen_cash || 0;
  const totalPnl = account?.total_pnl || 0;
  const realizedPnl = account?.realized_pnl || 0;
  const floatPnl = account?.float_pnl || 0;
  const posRatio = account?.position_ratio || 0;
  const total_return_pct = summary?.total_return_pct || 0;
  const weekRealized = account?.week_realized_pnl || 0;
  const weekFloat = account?.week_float_pnl || 0;
  const weekTotal = weekRealized + weekFloat;

  // ── 绩效指标（纯客户端计算）──
  const sharpeRatio = useMemo(() => computeSharpeRatio(realEquity), [realEquity]);
  const monthlyReturns = useMemo(() => computeMonthlyReturns(realEquity, 6), [realEquity]);
  const quarterlyReturns = useMemo(() => computeQuarterlyReturns(realEquity, 4), [realEquity]);
  const pnlContributions = useMemo(() => aggregatePnlContributions(breakdowns), [breakdowns]);
  // ── 贡献排序 ──
  type ContribSortKey = 'symbol' | 'name' | 'totalPnl';
  const [contribSortKey, setContribSortKey] = useState<ContribSortKey>('totalPnl');
  const [contribSortDir, setContribSortDir] = useState<'desc' | 'asc'>('desc');
  const sortedContributions = useMemo(() => {
    const arr = [...pnlContributions];
    arr.sort((a, b) => {
      const va = a[contribSortKey];
      const vb = b[contribSortKey];
      if (typeof va === 'string' && typeof vb === 'string') return contribSortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      return contribSortDir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number);
    });
    return arr;
  }, [pnlContributions, contribSortKey, contribSortDir]);
  const [contributionPage, setContributionPage] = useState(1);
  const CONTRIB_PAGE_SIZE = 10;
  const contributionPageCount = Math.max(1, Math.ceil(sortedContributions.length / CONTRIB_PAGE_SIZE));
  const contributionPageItems = useMemo(() => {
    const start = (contributionPage - 1) * CONTRIB_PAGE_SIZE;
    return sortedContributions.slice(start, start + CONTRIB_PAGE_SIZE);
  }, [sortedContributions, contributionPage]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setContributionPage(1); }, [pnlContributions.length]);

  // 沪深300 基准对比
  const hs300 = useMemo(() => {
    return tickers.find(t => t.name.includes('沪深') || t.name.includes('300'));
  }, [tickers]);
  const todayAccountReturn = useMemo(() => {
    if (!totalAsset || totalAsset <= 0) return 0;
    return (totalPnl / (totalAsset - totalPnl)) * 100;
  }, [totalAsset, totalPnl]);
  const benchmarkDelta = useMemo(() => {
    if (hs300 == null) return null;
    return computeBenchmarkDelta(todayAccountReturn, hs300.change_pct);
  }, [todayAccountReturn, hs300]);

  // 图表常量
  const G = 'rgba(255,255,255,0.04)';
  const A = 'var(--agent-text-dim, #6a7d9b)';

  return (
    <div className="cp-page">
      <canvas ref={canvasRef} id="cp-bg-canvas" />

      {/* ══════════ STATUS BAR — Row 1 ══════════ */}
      <div className="cp-status-bar">
        <div className="cp-status-sys">
          <span className="cp-status-dot" />
          SYS:ONLINE
        </div>
        <div className="cp-status-divider" />
        {!loadingTickers && tickers.length > 0 && (
          <div className="cp-status-ticker">
            {tickers.map(tk => (
              <div key={tk.name} className="cp-status-ticker-item">
                <span className="cp-status-ticker-name">{tk.name}</span>
                <span className="cp-status-ticker-price">{tk.price.toFixed(2)}</span>
                <span className={`cp-status-ticker-pct ${tk.change_pct >= 0 ? 'up' : 'down'}`}>
                  {tk.change_pct >= 0 ? '+' : ''}{tk.change_pct.toFixed(2)}%
                </span>
              </div>
            ))}
          </div>
        )}
        {loadingTickers && <div style={{ flex: 1 }}><Skel w={200} h={12} /></div>}
        <span className="cp-status-time">{lastUpdate.toLocaleTimeString()}</span>
      </div>

      {/* ══════════ STRATEGIC RESOURCES — Left Column ══════════ */}
      <div className="cp-strategic">
        <div className="cp-strategic-panel">
          <div className="cp-strategic-label">◆ Strategic Resources</div>
          {loadingSummary ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Skel w={140} h={28} /><Skel w={100} h={18} /><Skel w="100%" h={1} /><Skel w={80} h={14} /><Skel w={100} h={14} />
            </div>
          ) : summary && (
            <>
              <div className="cp-strategic-value">¥{fmtMoney(totalAsset)}</div>
              <div className={`cp-strategic-sub ${totalPnl >= 0 ? 'up' : 'down'}`}>
                {totalPnl >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(totalPnl))}
                <span style={{ fontSize: 10, fontWeight: 400, color: 'var(--cc-text-dim)', marginLeft: 6 }}>
                  {total_return_pct >= 0 ? '+' : ''}{total_return_pct.toFixed(2)}%
                </span>
              </div>
              <div className="cp-strategic-divider" />
              <div className="cp-strategic-row">
                <span className="cp-strategic-row-label">可用资金</span>
                <span className="cp-strategic-row-value">¥{fmtMoney(cash)}</span>
              </div>
              <div className="cp-strategic-row">
                <span className="cp-strategic-row-label">持仓市值</span>
                <span className="cp-strategic-row-value">¥{fmtMoney(posVal)}</span>
              </div>
              <div className="cp-strategic-row">
                <span className="cp-strategic-row-label">已实现盈亏</span>
                <span className={`cp-strategic-row-value ${realizedPnl >= 0 ? 'up' : 'down'}`}>
                  {realizedPnl >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(realizedPnl))}</span>
              </div>
              <div className="cp-strategic-row">
                <span className="cp-strategic-row-label">浮动盈亏</span>
                <span className={`cp-strategic-row-value ${floatPnl >= 0 ? 'up' : 'down'}`}>
                  {floatPnl >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(floatPnl))}</span>
              </div>
              {frozen > 0 && (
                <div className="cp-strategic-row">
                  <span className="cp-strategic-row-label" style={{ color: 'var(--cc-amber)' }}>冻结资金</span>
                  <span className="cp-strategic-row-value" style={{ color: 'var(--cc-amber)' }}>¥{fmtMoney(frozen)}</span>
                  <button className="cp-icon-btn" onClick={handleUnfreeze} disabled={unfreezing}
                    title={t('portfolio.unfreezeFunds')} style={{ marginLeft: 6, width: 20, height: 20, fontSize: 8 }}>
                    <i className={`fas fa-${unfreezing ? 'spinner fa-spin' : 'unlock'}`} />
                  </button>
                </div>
              )}
              <div className="cp-ratio-gauge">
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--cc-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  <span>仓位率</span><span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, color: 'var(--cc-text)' }}>{posRatio.toFixed(0)}%</span>
                </div>
                <div className="cp-ratio-gauge-bar">
                  <div className={`cp-ratio-gauge-fill ${posRatio > 80 ? 'danger' : posRatio > 50 ? 'warn' : 'safe'}`}
                    style={{ width: `${Math.min(posRatio, 100)}%` }} />
                </div>
              </div>
              <div className="cp-perf-mini">
                <div className="cp-perf-item">
                  <div className="cp-perf-item-val">{sharpeRatio != null ? sharpeRatio.toFixed(2) : '—'}</div>
                  <div className="cp-perf-item-label">夏普比率</div>
                </div>
                <div className="cp-perf-item">
                  <div className={`cp-perf-item-val ${(monthlyReturns[0]?.returnPct ?? 0) >= 0 ? 'up' : 'down'}`}>
                    {monthlyReturns[0] != null ? `${monthlyReturns[0].returnPct >= 0 ? '+' : ''}${monthlyReturns[0].returnPct.toFixed(1)}%` : '—'}
                  </div>
                  <div className="cp-perf-item-label">本月收益</div>
                </div>
                <div className="cp-perf-item">
                  <div className={`cp-perf-item-val ${(quarterlyReturns[0]?.returnPct ?? 0) >= 0 ? 'up' : 'down'}`}>
                    {quarterlyReturns[0] != null ? `${quarterlyReturns[0].returnPct >= 0 ? '+' : ''}${quarterlyReturns[0].returnPct.toFixed(1)}%` : '—'}
                  </div>
                  <div className="cp-perf-item-label">本季收益</div>
                </div>
                <div className="cp-perf-item">
                  <div className={`cp-perf-item-val ${benchmarkDelta?.label === 'outperform' ? 'up' : benchmarkDelta?.label === 'underperform' ? 'down' : ''}`}>
                    {benchmarkDelta ? `${benchmarkDelta.delta >= 0 ? '+' : ''}${benchmarkDelta.delta.toFixed(1)}%` : '—'}
                  </div>
                  <div className="cp-perf-item-label">vs 沪深300</div>
                </div>
              </div>
              <div style={{ fontSize: 9, color: 'var(--cc-text-dim)', marginTop: 8, textAlign: 'center' }}>
                本周 {weekTotal >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(weekTotal))}
                {' '}(已实现 <span style={{ color: weekRealized >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>{weekRealized >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekRealized))}</span>
                {' '}/ 浮盈 <span style={{ color: weekFloat >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>{weekFloat >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekFloat))}</span>)
              </div>
            </>
          )}
        </div>
      </div>

      {/* ══════════ MAIN VIEW — Center Column (Battlefield Map) ══════════ */}
      <div className="cp-main-view">
        <div className="cp-battlefield">
          <div className="cp-battlefield-scanline" />
          <div className="cp-battlefield-header">
            <span className="cp-battlefield-title">◆ Strategic Overview</span>
            <div className="cp-battlefield-actions">
              <button className="cp-icon-btn" onClick={refreshEquity} title="刷新">
                <i className={`fas fa-sync-alt ${loadingEquity ? 'fa-spin' : ''}`} />
              </button>
            </div>
          </div>
          <div className="cp-battlefield-chart">
            {loadingEquity ? <Skel w="100%" h="100%" /> : equityCurve.length === 0 ? (
              <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--cc-text-dim)' }}>
                <i className="fas fa-chart-line" style={{ marginRight: 8 }} />{t('common.noData')}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={dailyPnlData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.05)" />
                  <XAxis dataKey="date" stroke={A} fontSize={10} tickLine={false} interval={Math.max(0, Math.floor(dailyPnlData.length / 6) - 1)} />
                  <YAxis stroke={A} fontSize={10} tickLine={false} tickFormatter={(v: number) => fmtMoneyShort(v)} width={50} />
                  <Tooltip content={<PTip />} />
                  <Bar dataKey="pnl" radius={[1, 1, 0, 0]} onClick={handleBarClick} cursor="pointer">
                    {dailyPnlData.map((entry, i) => (
                      <Cell key={i} fill={entry.pnl >= 0 ? GREEN : RED} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
        <div className="cp-daily-strip">
          <div className="cp-daily-strip-header">
            <span className="cp-daily-strip-title">◆ Asset Allocation</span>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            {loadingSummary ? <Skel w="100%" h="100%" /> : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, height: '100%' }}>
                <div style={{ position: 'relative', width: 90, height: 90, flexShrink: 0 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={ringData} cx="50%" cy="50%" innerRadius={28} outerRadius={40} paddingAngle={2} dataKey="value" stroke="none">
                        {ringData.map((_, i) => <PieCell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} fillOpacity={0.85} />)}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
                    <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 10, color: 'var(--cc-text)' }}>¥{fmtMoneyShort(posVal)}</span>
                  </div>
                </div>
                <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: '3px 12px' }}>
                  {ringData.slice(0, 5).map((item, i) => (
                    <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10 }}>
                      <span style={{ width: 7, height: 7, borderRadius: 1, background: PIE_COLORS[i % PIE_COLORS.length], flexShrink: 0 }} />
                      <span style={{ color: 'var(--cc-text-secondary)' }}>{item.name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Inline Battle Report — slides open below charts */}
        {modalDate && (
          <div className="cp-battle-report">
            {modalLoading ? (
              <div style={{ textAlign: 'center', padding: 16, color: 'var(--cc-text-dim)' }}>
                <i className="fas fa-spinner fa-spin" /> 加载中...
              </div>
            ) : modalData ? (
              <>
                <div className="cp-battle-report-header">
                  <span className="cp-battle-report-date">{modalDate} 作战报告</span>
                  <div className="cp-battle-report-summary">
                    <span>日盈亏 <span style={{ color: modalData.daily_pnl >= 0 ? GREEN : RED, fontWeight: 700 }}>
                      {modalData.daily_pnl >= 0 ? '+' : ''}¥{Math.abs(modalData.daily_pnl).toFixed(0)}</span></span>
                    <span>已实现 <span style={{ color: modalData.realized_total >= 0 ? GREEN : RED }}>
                      {modalData.realized_total >= 0 ? '+' : ''}¥{Math.abs(modalData.realized_total).toFixed(0)}</span></span>
                    <span>浮盈变动 <span style={{ color: modalData.float_total >= 0 ? GREEN : RED }}>
                      {modalData.float_total >= 0 ? '+' : ''}¥{Math.abs(modalData.float_total).toFixed(0)}</span></span>
                  </div>
                  <button className="cp-battle-report-close" onClick={() => { setModalDate(null); setModalData(null); }}>
                    <i className="fas fa-times" />
                  </button>
                </div>
                {modalData.stocks.length > 0 ? (
                  <table className="cp-table-mini">
                    <thead><tr>
                      <th>代码</th><th>名称</th><th>持仓</th><th>收盘价</th><th>涨跌</th><th>浮盈变动</th><th>已实现</th>
                    </tr></thead>
                    <tbody>
                      {modalData.stocks.map(s => {
                        const chgPct = s.prev_close > 0 ? ((s.close_price / s.prev_close - 1) * 100) : 0;
                        const isSold = s.volume === 0;
                        return (
                          <tr key={s.symbol} style={{ opacity: isSold ? 0.55 : 1 }}>
                            <td style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}>{s.symbol}</td>
                            <td style={{ fontSize: 10 }}>{s.name || '-'}</td>
                            <td style={{ fontSize: 10 }}>{isSold ? <span style={{ color: 'var(--cc-text-dim)' }}>已卖出</span> : `${s.volume}股`}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 10 }}>{s.close_price > 0 ? `¥${s.close_price.toFixed(2)}` : '-'}</td>
                            <td style={{ color: chgPct >= 0 ? 'var(--cc-green)' : 'var(--cc-red)', fontFamily: 'var(--font-display)', fontSize: 10 }}>
                              {s.close_price > 0 && s.prev_close > 0 ? `${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%` : '-'}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 10, color: (s.float_pnl || 0) >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>
                              {(s.float_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.float_pnl || 0).toFixed(0)}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 10, color: (s.realized_pnl || 0) >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>
                              {(s.realized_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.realized_pnl || 0).toFixed(0)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div style={{ fontSize: 11, color: 'var(--cc-text-dim)', textAlign: 'center', padding: 12 }}>当日无持仓数据</div>
                )}
              </>
            ) : (
              <div style={{ fontSize: 11, color: 'var(--cc-text-dim)', textAlign: 'center', padding: 12 }}>加载失败</div>
            )}
          </div>
        )}
      </div>

      {/* ══════════ UNIT DEPLOYMENT — Right Column ══════════ */}
      <div className="cp-deployment">
        <div className="cp-deployment-panel">
          <div className="cp-deployment-header">
            <span className="cp-deployment-title">◆ Unit Deployment</span>
            <span className="cp-deployment-count">{positions.length} UNITS</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
              {flowSummary && (
                <span style={{ fontSize: 9, color: 'var(--cc-text-dim)' }}>
                  {flowSummary.inflow > 0 && <span style={{ color: 'var(--cc-green)' }}>{flowSummary.inflow}↑</span>}
                  {flowSummary.inflow > 0 && flowSummary.outflow > 0 && ' / '}
                  {flowSummary.outflow > 0 && <span style={{ color: 'var(--cc-red)' }}>{flowSummary.outflow}↓</span>}
                </span>
              )}
              <button className="cp-icon-btn" onClick={refreshSummary} title="刷新">
                <i className={`fas fa-sync-alt ${loadingSummary ? 'fa-spin' : ''}`} />
              </button>
            </div>
          </div>
          <div className="cp-deployment-scroll">
            {loadingSummary ? (
              Array.from({ length: 5 }).map((_, i) => <Skel key={i} w="100%" h={52} />)
            ) : sortedPositions.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 20, color: 'var(--cc-text-dim)' }}>
                <i className="fas fa-cube" style={{ fontSize: 20, marginBottom: 8, display: 'block' }} />
                {t('portfolio.noPositions')}
              </div>
            ) : (
              sortedPositions.map(pos => {
                const isUp = (pos.floating_pnl || 0) >= 0;
                const weight = totalAsset > 0 ? (pos.market_value / totalAsset) * 100 : 0;
                const isHeavy = weight > 30;
                const pnlPct = pos.floating_pnl_pct || 0;
                const flow = moneyflowMap[pos.symbol];
                const flowLabel = !flow ? '—' : (flow.main_net || 0) > (pos.market_value * 0.01) ? '主力流入' : (flow.main_net || 0) < -(pos.market_value * 0.01) ? '主力流出' : '平衡';
                const flowClass = flowLabel === '主力流入' ? 'in' : flowLabel === '主力流出' ? 'out' : 'neutral';
                const isExpanded = expandedTradeSymbol === pos.symbol;

                return (
                  <div key={pos.symbol}>
                    <div className={`cp-unit-card ${isUp ? 'pnl-up' : 'pnl-down'} ${isExpanded ? 'trade-expanded' : ''}`}
                      onClick={() => openTradePanel(pos.symbol, isUp ? '卖' : '买', pos.current_price, isUp ? pos.volume : 0)}>
                      <div className="cp-unit-top">
                        <span className="cp-unit-symbol">{pos.symbol}</span>
                        <span className={`cp-unit-pnl ${isUp ? 'up' : 'down'}`}>
                          {isUp ? '+' : ''}{pnlPct.toFixed(2)}%
                        </span>
                      </div>
                      <div className="cp-unit-mid">
                        <span>{cleanStockName(pos.name, pos.symbol)}</span>
                        <span className={`cp-unit-weight ${isHeavy ? 'cp-unit-weight-heavy' : ''}`}>
                          {weight.toFixed(1)}%
                        </span>
                      </div>
                      <div className="cp-unit-bottom">
                        <span className="cp-unit-shares">
                          {pos.volume.toLocaleString()}股 · ¥{(pos.current_price || 0).toFixed(2)}
                        </span>
                        <span className={`cp-unit-flow ${flowClass}`}>{flowLabel}</span>
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="cp-unit-trade-panel" onClick={e => e.stopPropagation()}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--cc-blue)', fontFamily: 'var(--font-display)' }}>
                          {tradeForm.direction === '买' ? 'BUY' : 'SELL'} {pos.symbol} {cleanStockName(pos.name, pos.symbol)}
                        </div>
                        <div className="cp-unit-trade-row">
                          <input type="number" className="cp-unit-trade-input" placeholder="价格"
                            value={tradeForm.price || ''} onChange={e => setTradeForm(f => ({ ...f, price: parseFloat(e.target.value) || 0 }))}
                            step="0.01" min="0" style={{ flex: 1 }} />
                          <input type="number" className="cp-unit-trade-input" placeholder="数量"
                            value={tradeForm.volume || ''} onChange={e => setTradeForm(f => ({ ...f, volume: parseInt(e.target.value) || 0 }))}
                            step="100" min="0" style={{ flex: 1 }} />
                        </div>
                        <input type="text" className="cp-unit-trade-input" placeholder="理由 (选填)"
                          value={tradeForm.reason} onChange={e => setTradeForm(f => ({ ...f, reason: e.target.value }))} />
                        {tradeError && <div className="cp-unit-trade-error"><i className="fas fa-exclamation-circle" /> {tradeError}</div>}
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button className="cp-unit-trade-btn buy" onClick={handleTradeSubmit} disabled={tradeSubmitting}
                            style={{ flex: 1 }}>
                            {tradeSubmitting ? <i className="fas fa-spinner fa-spin" /> : '确认下单'}
                          </button>
                          <button className="cp-unit-trade-btn cancel"
                            onClick={() => { setExpandedTradeSymbol(null); setTradeError(null); }}>取消</button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* ══════════ THREAT ASSESSMENT STRIP — Row 3 ══════════ */}
      <div className="cp-threat-strip">
        {loadingSummary ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="cp-threat-card" style={{ opacity: 0.5 }}>
              <Skel w={36} h={36} br="50%" />
              <div style={{ flex: 1 }}><Skel w={50} h={10} /><Skel w={40} h={16} /></div>
            </div>
          ))
        ) : summary && (
          <>
            <ThreatCard icon="fa-gauge-high" label="仓位率" value={`${posRatio.toFixed(0)}%`}
              sub={posRatio > 80 ? '重仓部署' : posRatio > 50 ? '标准配置' : '轻仓待命'}
              level={posRatio > 80 ? 'danger' : posRatio > 50 ? 'warn' : 'safe'} />
            <ThreatCard icon="fa-arrow-trend-down" label="最大回撤" value={`-${maxDrawdown.toFixed(1)}%`}
              sub="历史极值" level={maxDrawdown > 15 ? 'danger' : maxDrawdown > 8 ? 'warn' : 'safe'} />
            <ThreatCard icon="fa-bullseye" label="胜率" value={`${win_rate.toFixed(1)}%`}
              sub={`${positions.length} 个作战单位`} level={win_rate > 60 ? 'safe' : win_rate > 40 ? 'warn' : 'danger'} />
            <ThreatCard icon="fa-wave-square" label="年化波动" value={`${volatility.toFixed(1)}%`}
              sub="60日滚动" level={volatility > 25 ? 'danger' : volatility > 15 ? 'warn' : 'safe'} />
          </>
        )}
      </div>

      {/* ══════════ BOTTOM ZONE — Row 4 ══════════ */}
      <div className="cp-bottom-zone">
        {/* Defense System (Stop Loss) */}
        <div className="cp-bottom-panel">
          <div className="cp-bottom-header">
            <span className="cp-bottom-title">◆ Defense System</span>
            <button className="cp-icon-btn" onClick={refreshStopLoss} title="刷新">
              <i className={`fas fa-sync-alt ${loadingStopLoss ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-bottom-body">
            {loadingStopLoss ? <Skel w="100%" h={80} /> : stopLoss && (
              <>
                <div className="cp-defense-status">
                  <div className="cp-defense-indicator">
                    <span className={`cp-defense-dot ${stopLoss.running && stopLoss.thread_alive ? 'live' : 'dead'}`} />
                    <span className="cp-defense-text">
                      {stopLoss.interval_seconds === 0 ? 'API 不可达' : stopLoss.running && stopLoss.thread_alive ? 'ACTIVE' : 'STANDBY'}
                    </span>
                  </div>
                  <div className="cp-defense-tags">
                    {stopLoss.is_morning_volatility && <span className="cp-defense-tag warn">早盘冷静期</span>}
                    {!stopLoss.is_trading_time && <span className="cp-defense-tag muted">非交易时段</span>}
                  </div>
                  <button className={`cp-defense-toggle ${stopLoss.running && stopLoss.thread_alive ? 'on' : 'off'}`}
                    onClick={handleToggleSL} disabled={slToggling}>
                    <i className={`fas fa-${slToggling ? 'spinner fa-spin' : stopLoss.running && stopLoss.thread_alive ? 'shield-halved' : 'play'}`} />
                    {' '}{stopLoss.running && stopLoss.thread_alive ? 'SHIELD ON' : 'SHIELD OFF'}
                  </button>
                </div>
                <div className="cp-defense-metrics">
                  <div className={`cp-defense-metric ${stopLoss.triggered_count > 0 ? 'danger' : ''}`}>
                    <div className={`cp-defense-metric-val ${stopLoss.triggered_count > 0 ? 'danger' : ''}`}>{stopLoss.triggered_count}</div>
                    <div className="cp-defense-metric-label">已触发</div>
                  </div>
                  <div className="cp-defense-metric">
                    <div className="cp-defense-metric-val">{stopLoss.position_count}</div>
                    <div className="cp-defense-metric-label">监控中</div>
                  </div>
                  <div className="cp-defense-metric">
                    <div className="cp-defense-metric-val">{stopLoss.today_stops_count}</div>
                    <div className="cp-defense-metric-label">今日止损</div>
                  </div>
                  <div className="cp-defense-metric">
                    <div className="cp-defense-metric-val">{stopLoss.interval_seconds}s</div>
                    <div className="cp-defense-metric-label">扫描间隔</div>
                  </div>
                </div>
                <div className="cp-defense-detail" onClick={() => setSlExpanded(e => !e)}>
                  <i className={`fas fa-chevron-${slExpanded ? 'up' : 'down'}`} /> {slExpanded ? '收起' : '展开'}单位距离详情
                </div>
                {slExpanded && stopLoss.positions.length > 0 && (
                  <table className="cp-table-mini" style={{ marginTop: 6 }}>
                    <thead><tr>
                      <th>代码</th><th>名称</th><th>浮盈</th><th>距离%</th><th>规则</th><th>风险</th>
                    </tr></thead>
                    <tbody>
                      {stopLoss.positions.slice(0, 6).map(p => {
                        const danger = p.nearest_trigger?.danger_level || 'no_rules';
                        const ruleLabels: Record<string, string> = {
                          rul0a_break_low: '破底', rul0b_cost_stop: '成本',
                          rul1_sector: '行业', rul2_iron: '铁律2', rul3_dynamic: '动态',
                        };
                        return (
                          <tr key={p.symbol}>
                            <td style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 10 }}>{p.symbol}</td>
                            <td style={{ fontSize: 10 }}>{p.name || p.symbol}</td>
                            <td style={{ color: p.float_pnl_pct >= 0 ? 'var(--cc-green)' : 'var(--cc-red)', fontFamily: 'var(--font-display)', fontSize: 10 }}>
                              {p.float_pnl_pct >= 0 ? '+' : ''}{p.float_pnl_pct.toFixed(1)}%</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 10 }}>
                              {p.nearest_trigger?.distance_pct != null ? `${p.nearest_trigger.distance_pct.toFixed(1)}%` : '-'}</td>
                            <td style={{ fontSize: 10 }}>{ruleLabels[p.nearest_trigger?.rule || ''] || p.nearest_trigger?.rule || ''}</td>
                            <td style={{ fontSize: 10, color: danger === 'triggered' ? 'var(--cc-red)' : danger === 'critical' ? 'var(--cc-amber)' : 'var(--cc-text-dim)' }}>
                              {danger === 'triggered' ? '触发' : danger === 'critical' ? '危急' : danger === 'warning' ? '警告' : '安全'}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>
        </div>

        {/* Intel Center (Contribution + Sector) */}
        <div className="cp-bottom-panel">
          <div className="cp-bottom-header">
            <span className="cp-bottom-title">◆ Intel Center</span>
            <button className="cp-icon-btn" onClick={refreshBreakdowns} title="刷新">
              <i className={`fas fa-sync-alt ${loadingBreakdowns ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-bottom-body">
            {loadingBreakdowns ? <Skel w="100%" h={80} /> : (
              <>
                <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--cc-text-dim)', marginBottom: 4 }}>
                  个股盈亏贡献 (30日)
                </div>
                {contributionPageItems.slice(0, 5).map((item, i) => (
                  <div key={item.symbol} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 0', fontSize: 10 }}>
                    <span className="cp-intel-rank">{(contributionPage - 1) * CONTRIB_PAGE_SIZE + i + 1}</span>
                    <span className="cp-intel-symbol">{item.symbol}</span>
                    <span className="cp-intel-name">{item.name || item.symbol}</span>
                    <span className={`cp-intel-pnl ${item.totalPnl >= 0 ? 'up' : 'down'}`}>
                      {item.totalPnl >= 0 ? '+' : ''}¥{item.totalPnl.toLocaleString()}
                    </span>
                  </div>
                ))}
                {contributionPageCount > 1 && (
                  <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 4, fontSize: 10 }}>
                    <button className="cp-icon-btn" style={{ width: 20, height: 20, fontSize: 8 }}
                      disabled={contributionPage <= 1} onClick={() => setContributionPage(p => p - 1)}>
                      <i className="fas fa-chevron-left" />
                    </button>
                    <span style={{ color: 'var(--cc-text-dim)', fontFamily: 'var(--font-display)' }}>{contributionPage}/{contributionPageCount}</span>
                    <button className="cp-icon-btn" style={{ width: 20, height: 20, fontSize: 8 }}
                      disabled={contributionPage >= contributionPageCount} onClick={() => setContributionPage(p => p + 1)}>
                      <i className="fas fa-chevron-right" />
                    </button>
                  </div>
                )}
                <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--cc-text-dim)', marginTop: 8, marginBottom: 4 }}>
                  行业集中度
                </div>
                {sectorData && sectorData.sectors ? (
                  <>
                    {sectorData.sectors.slice(0, 4).map(s => (
                      <div key={s.name} className="cp-sector-row">
                        <span className="cp-sector-name">{s.name}</span>
                        <div className="cp-sector-bar-track">
                          <div className="cp-sector-bar-fill" style={{
                            width: `${Math.min(s.weight_pct, 100)}%`,
                            background: s.weight_pct > 50 ? 'var(--cc-red)' : s.weight_pct > 30 ? 'var(--cc-amber)' : 'var(--cc-green)',
                          }} />
                        </div>
                        <span className="cp-sector-pct">{s.weight_pct}%</span>
                      </div>
                    ))}
                    {sectorData.concentration_level && (
                      <div style={{ fontSize: 9, color: 'var(--cc-text-dim)', marginTop: 2 }}>
                        集中度: <span style={{
                          color: sectorData.concentration_level === '集中' ? 'var(--cc-red)' : sectorData.concentration_level === '适中' ? 'var(--cc-amber)' : 'var(--cc-green)',
                          fontWeight: 600,
                        }}>{sectorData.concentration_level}</span>
                        {sectorData.max_sector && ` · 最大: ${sectorData.max_sector.name} (${sectorData.max_sector.weight_pct}%)`}
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ fontSize: 10, color: 'var(--cc-text-dim)', textAlign: 'center', padding: 8 }}>暂无行业数据</div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Mission Log (Recent Trades) */}
        <div className="cp-bottom-panel">
          <div className="cp-bottom-header">
            <span className="cp-bottom-title">◆ Mission Log</span>
            <button className="cp-icon-btn" onClick={refreshTrades} title="刷新">
              <i className={`fas fa-sync-alt ${loadingTrades ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-bottom-body">
            {loadingTrades ? (
              Array.from({ length: 5 }).map((_, i) => <Skel key={i} w="100%" h={18} />)
            ) : recentTrades.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 16, color: 'var(--cc-text-dim)', fontSize: 10 }}>
                <i className="fas fa-history" style={{ display: 'block', fontSize: 16, marginBottom: 4 }} />
                暂无作战记录
              </div>
            ) : (
              recentTrades.map((tr, i) => {
                const isBuy = (tr.direction || '').includes('买') || (tr.direction || '').toLowerCase().includes('buy');
                return (
                  <div key={tr.order_id || i} className="cp-mission-item">
                    <span className={`cp-mission-dir ${isBuy ? 'buy' : 'sell'}`}>{isBuy ? 'BUY' : 'SELL'}</span>
                    <span className="cp-mission-name">{tr.name || tr.symbol}</span>
                    <span className="cp-mission-detail">¥{tr.price?.toFixed(2)} × {tr.volume}</span>
                    <span className="cp-mission-time">{tr.created_at ? new Date(tr.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* ═══ FAB ═══ */}
      <div className="cp-fab">
        <button className="cp-fab-main" onClick={() => setFabOpen(o => !o)} title="快捷操作">
          <i className={`fas fa-${fabOpen ? 'times' : 'ellipsis'}`} />
        </button>
      </div>

      {/* ═══ 错误提示 ═══ */}
      {error && (
        <div className="cp-error"><div className="cp-error-inner">
          <i className="fas fa-exclamation-triangle" />{t('common.error')}: {error}
        </div></div>
      )}
    </div>
  );
}

// ══════════════════ 子组件 ══════════════════

function ThreatCard({ icon, label, value, sub, level }: {
  icon: string; label: string; value: string; sub: string;
  level: 'safe' | 'warn' | 'danger';
}) {
  return (
    <div className={`cp-threat-card ${level}`}>
      <div className={`cp-threat-icon ${level}`}>
        <i className={`fas ${icon}`} />
      </div>
      <div className="cp-threat-info">
        <div className="cp-threat-label">{label}</div>
        <div className="cp-threat-value">{value}</div>
        <div className="cp-threat-sub">{sub}</div>
      </div>
    </div>
  );
}

// ══════════════════ Canvas 背景 ══════════════════
function usePortfolioBackground(canvasRef: React.RefObject<HTMLCanvasElement | null>) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let w = 0, h = 0, animId = 0, time = 0;

    function resize() {
      w = Math.max(1, window.innerWidth);
      h = Math.max(1, window.innerHeight);
      canvas!.width = w;
      canvas!.height = h;
    }
    window.addEventListener('resize', resize);
    resize();

    const particles: { x: number; y: number; size: number; sx: number; sy: number; alpha: number }[] = [];
    for (let i = 0; i < 50; i++) {
      particles.push({
        x: Math.random() * w, y: Math.random() * h,
        size: Math.random() * 2 + 0.5,
        sx: (Math.random() - 0.5) * 0.2,
        sy: (Math.random() - 0.5) * 0.2,
        alpha: Math.random() * 0.3 + 0.08,
      });
    }

    const halos = [
      { x: 0.15, y: 0.08, r: 160, speed: 0.003, phase: 0 },
      { x: 0.85, y: 0.25, r: 120, speed: -0.004, phase: 1.2 },
      { x: 0.50, y: 0.80, r: 180, speed: 0.002, phase: 2.5 },
      { x: 0.08, y: 0.70, r: 100, speed: -0.005, phase: 0.8 },
    ];

    function draw() {
      ctx!.clearRect(0, 0, w, h);
      const minDim = Math.min(w, h);

      for (const hd of halos) {
        const r = hd.r * minDim / 800;
        if (!isFinite(r) || r <= 0) continue;
        const cx = hd.x * w, cy = hd.y * h;
        const angle = time * hd.speed + hd.phase;
        ctx!.save();
        ctx!.translate(cx, cy);
        ctx!.rotate(angle);
        const r0 = Math.max(0.1, r * 0.2), r1 = Math.max(0.1, r);
        const grad = ctx!.createRadialGradient(0, 0, r0, 0, 0, r1);
        grad.addColorStop(0, 'rgba(45,140,240,0)');
        grad.addColorStop(0.7, 'rgba(45,140,240,0.03)');
        grad.addColorStop(1, 'rgba(45,140,240,0.08)');
        ctx!.beginPath();
        ctx!.arc(0, 0, r1, 0, Math.PI * 2);
        ctx!.fillStyle = grad;
        ctx!.fill();
        if (r * 0.5 > 0.5) {
          ctx!.beginPath();
          ctx!.arc(0, 0, r * 0.5, 0, Math.PI * 2);
          ctx!.strokeStyle = 'rgba(45,140,240,0.1)';
          ctx!.lineWidth = 1;
          ctx!.stroke();
        }
        ctx!.restore();
      }

      for (const p of particles) {
        p.x += p.sx; p.y += p.sy;
        if (p.x < 0 || p.x > w) p.sx *= -1;
        if (p.y < 0 || p.y > h) p.sy *= -1;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx!.fillStyle = `rgba(45,140,240,${p.alpha})`;
        ctx!.fill();
      }

      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 100) {
            ctx!.beginPath();
            ctx!.moveTo(particles[i].x, particles[i].y);
            ctx!.lineTo(particles[j].x, particles[j].y);
            ctx!.strokeStyle = `rgba(45,140,240,${(1 - dist / 100) * 0.06})`;
            ctx!.lineWidth = 0.5;
            ctx!.stroke();
          }
        }
      }

      time += 0.008;
      animId = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
    };
  }, [canvasRef]);
}

// ══════════════════ 骨架屏组件 ══════════════════

function Skel({ w, h = 14, br = 6 }: { w: number | string; h?: number | string; br?: number | string }) {
  return <div className="cp-skel" style={{ width: w, height: h, borderRadius: br }} />;
}

// ══════════════════ Tooltip ══════════════════

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PTip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const pnl = payload[0]?.value || 0;
  return <div className="cp-tip-box"><div className="cp-tip-label">{label}</div><div className="cp-tip-row"><span className="l">日盈亏</span><span className="v" style={{ color: pnl >= 0 ? GREEN : RED }}>{pnl >= 0 ? '+' : ''}¥{pnl.toLocaleString()}</span></div></div>;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PieTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  return <div className="cp-tip-box"><div className="cp-tip-label">{p.name}</div><div className="cp-tip-row"><span className="l">市值</span><span className="v">¥{p.value.toLocaleString()}</span></div></div>;
}

