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
      {/* ═══ 行情 Ticker ═══ */}
      {!loadingTickers && tickers.length > 0 && (
        <div className="cp-ticker-bar">
          {tickers.map(tk => (
            <div key={tk.name} className="cp-ticker-item">
              <span className="cp-ticker-name">{tk.name}</span>
              <span className="cp-ticker-price">{tk.price.toFixed(2)}</span>
              <span className={`cp-ticker-pct ${tk.change_pct >= 0 ? 'up' : 'down'}`}>
                {tk.change_pct >= 0 ? '+' : ''}{tk.change_pct.toFixed(2)}%
              </span>
            </div>
          ))}
        </div>
      )}
      {loadingTickers && <SkeletonTicker />}

      {/* ═══ 头部 ═══ */}
      <header className="cp-header">
        <div className="cp-header-left">
          <div className="cp-header-icon"><i className="fas fa-wallet" /></div>
          <div>
            <h1 className="cp-header-title">{t('portfolio.title')}</h1>
            <div className="cp-header-meta">
              <span className="cp-live-dot" />
              <span className="cp-update-time">{t('common.refresh')}: {lastUpdate.toLocaleTimeString()}</span>
            </div>
          </div>
        </div>
        <button className="cp-refresh-btn" onClick={refreshSummary} title="刷新资产">
          <i className={`fas fa-sync-alt ${loadingSummary ? 'fa-spin' : ''}`} />
        </button>
      </header>

      {/* ═══ 概览组：资产 + 风险 ═══ */}
      <div className="cp-section-group">

      {/* ═══ 资产 Hero 卡片 ═══ */}
      {loadingSummary ? <SkeletonHero /> : summary && (
        <div className="cp-hero-card">
          <div className="cp-hero-left">
            <div className="cp-hero-label">{t('portfolio.totalAsset')}</div>
            <div className="cp-hero-value">¥{fmtMoney(totalAsset)}</div>
            <div className="cp-hero-label" style={{ marginTop: 12 }}>{t('portfolio.totalPnL')}</div>
            <div className={`cp-hero-pnl ${totalPnl >= 0 ? 'up' : 'down'}`}>
              {totalPnl >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(totalPnl))}
            </div>
            <div className={`cp-hero-change ${totalPnl >= 0 ? 'up' : 'down'}`}>
              <i className={`fas fa-caret-${totalPnl >= 0 ? 'up' : 'down'}`} />
              {total_return_pct >= 0 ? '+' : ''}{total_return_pct.toFixed(2)}%
            </div>
            <div className={`cp-hero-change ${weekTotal >= 0 ? 'up' : 'down'}`} style={{ marginTop: 8, display: 'block' }}>
              <span style={{ fontWeight: 500, marginRight: 4 }}>本周盈亏</span>
              {weekTotal >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(weekTotal))}
              <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.8, marginLeft: 4 }}>
                (已实现<span style={{ color: weekRealized >= 0 ? 'var(--agent-up, #2ecc71)' : 'var(--agent-down, #e74c3c)' }}>{weekRealized >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekRealized))}</span>
                {' / '}浮盈<span style={{ color: weekFloat >= 0 ? 'var(--agent-up, #2ecc71)' : 'var(--agent-down, #e74c3c)' }}>{weekFloat >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekFloat))}</span>)
              </span>
            </div>
          </div>
          <div className="cp-hero-right">
            <div className="cp-hero-kpi">
              <div className="cp-hero-kpi-label">{t('portfolio.availableCash')}</div>
              <div className="cp-hero-kpi-value">¥{fmtMoney(cash)}</div>
              {frozen > 0 && (
                <div className="cp-hero-kpi-sub" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <span style={{ color: 'var(--agent-warn, #f0b90b)', fontSize: 10 }}>
                    <i className="fas fa-lock" style={{ marginRight: 3 }} />
                    {t('portfolio.frozenCash')}: ¥{fmtMoney(frozen)}
                  </span>
                  <button className="cp-unfreeze-btn" onClick={handleUnfreeze} disabled={unfreezing}
                    title={t('portfolio.unfreezeFunds')}>
                    {unfreezing ? <><i className="fas fa-spinner fa-spin" style={{ fontSize: 9 }} /> 解冻中</>
                      : <><i className="fas fa-unlock" style={{ fontSize: 9 }} /> {t('portfolio.unfreezeFunds')}</>}
                  </button>
                </div>
              )}
            </div>
            <HeroKpi label={t('portfolio.positionValue')} value={`¥${fmtMoney(posVal)}`} sub={`${posRatio.toFixed(1)}%`} />
            <HeroKpi label={t('portfolio.realizedPnL')} value={`${realizedPnl >= 0 ? '+' : ''}¥${fmtMoneyShort(Math.abs(realizedPnl))}`} trend={realizedPnl >= 0 ? 'up' : 'down'} />
            <HeroKpi label={t('portfolio.floatingPnL')} value={`${floatPnl >= 0 ? '+' : ''}¥${fmtMoneyShort(Math.abs(floatPnl))}`} trend={floatPnl >= 0 ? 'up' : 'down'} />
          </div>
        </div>
      )}

      {/* ═══ 绩效指标卡片行 ═══ */}
      {summary && (
        <div className="cp-metrics-row">
          <div className="cp-metric-card">
            <div className="cp-metric-label">夏普比率</div>
            <div className="cp-metric-value">{sharpeRatio != null ? sharpeRatio.toFixed(2) : 'N/A'}</div>
            <div className="cp-metric-sub">{sharpeRatio != null ? (sharpeRatio > 1 ? '优秀' : sharpeRatio > 0.5 ? '良好' : '一般') : '需要更多数据'}</div>
          </div>
          <div className="cp-metric-card">
            <div className="cp-metric-label">本月收益</div>
            <div className={`cp-metric-value ${(monthlyReturns[0]?.returnPct ?? 0) >= 0 ? 'up' : 'down'}`}>
              {monthlyReturns[0] != null ? `${monthlyReturns[0].returnPct >= 0 ? '+' : ''}${monthlyReturns[0].returnPct.toFixed(2)}%` : 'N/A'}
            </div>
            <div className="cp-metric-sub">{monthlyReturns[0]?.period ?? '—'}</div>
          </div>
          <div className="cp-metric-card">
            <div className="cp-metric-label">本季收益</div>
            <div className={`cp-metric-value ${(quarterlyReturns[0]?.returnPct ?? 0) >= 0 ? 'up' : 'down'}`}>
              {quarterlyReturns[0] != null ? `${quarterlyReturns[0].returnPct >= 0 ? '+' : ''}${quarterlyReturns[0].returnPct.toFixed(2)}%` : 'N/A'}
            </div>
            <div className="cp-metric-sub">{quarterlyReturns[0]?.period ?? '—'}</div>
          </div>
          <div className="cp-metric-card">
            <div className="cp-metric-label">今日 vs 沪深300</div>
            {benchmarkDelta ? (
              <>
                <div className={`cp-metric-value ${benchmarkDelta.label === 'outperform' ? 'up' : benchmarkDelta.label === 'underperform' ? 'down' : ''}`}>
                  {benchmarkDelta.label === 'outperform' ? '跑赢' : benchmarkDelta.label === 'underperform' ? '跑输' : '持平'}
                  <span style={{ fontSize: 12, marginLeft: 4 }}>
                    {benchmarkDelta.delta >= 0 ? '+' : ''}{benchmarkDelta.delta.toFixed(2)}%
                  </span>
                </div>
                <div className="cp-metric-sub">沪深300 {hs300?.change_pct != null ? `${hs300.change_pct >= 0 ? '+' : ''}${hs300.change_pct.toFixed(2)}%` : '—'}</div>
              </>
            ) : (
              <>
                <div className="cp-metric-value" style={{ color: 'var(--agent-text-dim)' }}>—</div>
                <div className="cp-metric-sub">等待指数数据</div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ═══ 风险仪表 4 连 ═══ */}
      {loadingSummary ? <SkeletonRisk /> : summary && (
        <div className="cp-risk-strip">
          <RiskCard icon="fa-gauge-high" label={t('portfolio.positionRatio')}
            value={`${posRatio.toFixed(0)}%`}
            sub={posRatio > 80 ? '重仓' : posRatio > 50 ? '中性' : '轻仓'}
            level={posRatio > 80 ? 'danger' : posRatio > 50 ? 'warn' : 'safe'} />
          <RiskCard icon="fa-arrow-trend-down" label="最大回撤"
            value={`-${maxDrawdown.toFixed(1)}%`} sub="历史最大"
            level={maxDrawdown > 15 ? 'danger' : maxDrawdown > 8 ? 'warn' : 'safe'} />
          <RiskCard icon="fa-bullseye" label={t('analytics.winRate')}
            value={`${win_rate.toFixed(1)}%`} sub={`${positions.length} 只持仓`}
            level={win_rate > 60 ? 'safe' : win_rate > 40 ? 'warn' : 'danger'} />
          <RiskCard icon="fa-wave-square" label="年化波动"
            value={`${volatility.toFixed(1)}%`} sub="60日滚动"
            level={volatility > 25 ? 'danger' : volatility > 15 ? 'warn' : 'safe'} />
        </div>
      )}
      </div>

      {/* ═══ 止损监控卡片 ═══ */}
      <div className="cp-section-group">
      {loadingStopLoss ? <SkeletonSL /> : stopLoss && (
        <div className="cp-sl-strip">
          <div className="cp-sl-card" onClick={() => setSlExpanded(e => !e)} style={{ cursor: 'pointer' }}>
            <div className="cp-sl-indicator">
              <span className={`cp-sl-dot ${stopLoss.running && stopLoss.thread_alive ? 'live' : 'dead'}`} />
              <span className="cp-sl-status-text">
                {stopLoss.interval_seconds === 0 ? 'API 不可达' : stopLoss.running && stopLoss.thread_alive ? '运行中' : '已停止'}
              </span>
              {stopLoss.is_morning_volatility && <span className="cp-sl-tag warn">早盘冷静期</span>}
              {!stopLoss.is_trading_time && <span className="cp-sl-tag muted">非交易时段</span>}
              <button className={`cp-sl-toggle ${stopLoss.running && stopLoss.thread_alive ? 'on' : 'off'}`}
                onClick={handleToggleSL} disabled={slToggling}
                title={stopLoss.running && stopLoss.thread_alive ? '停止监控' : '启动监控'}>
                <i className={`fas fa-${slToggling ? 'spinner fa-spin' : stopLoss.running && stopLoss.thread_alive ? 'stop' : 'play'}`} />
              </button>
              <button className="cp-refresh-btn" onClick={(e) => { e.stopPropagation(); refreshStopLoss(); }}
                title="刷新止损" style={{ marginLeft: 4 }}>
                <i className={`fas fa-sync-alt ${loadingStopLoss ? 'fa-spin' : ''}`} style={{ fontSize: 10 }} />
              </button>
            </div>
            <div className="cp-sl-metrics">
              <div className={`cp-sl-metric ${stopLoss.triggered_count > 0 ? 'danger' : 'safe'}`}>
                <span className="cp-sl-metric-val">{stopLoss.triggered_count}</span>
                <span className="cp-sl-metric-label">已触发</span>
              </div>
              <div className="cp-sl-metric">
                <span className="cp-sl-metric-val">{stopLoss.position_count}</span>
                <span className="cp-sl-metric-label">监控中</span>
              </div>
              <div className="cp-sl-metric">
                <span className="cp-sl-metric-val">{stopLoss.today_stops_count}</span>
                <span className="cp-sl-metric-label">今日止损</span>
              </div>
              <div className="cp-sl-metric">
                <span className="cp-sl-metric-val">{stopLoss.interval_seconds}s</span>
                <span className="cp-sl-metric-label">扫描间隔</span>
              </div>
            </div>
            <div style={{ fontSize: 10, color: A, textAlign: 'center', marginTop: 4 }}>
              <i className={`fas fa-chevron-${slExpanded ? 'up' : 'down'}`} /> {slExpanded ? '收起' : '展开'}持仓距离
            </div>
          </div>
          {slExpanded && stopLoss.positions.length > 0 && (
            <div className="cp-sl-detail">
              <table className="cp-sl-table">
                <thead><tr>
                  <th>代码</th><th>名称</th><th className="right">成本价</th><th className="right">现价</th><th className="right">浮盈</th>
                  <th className="right">距离%</th><th className="right">最近规则</th><th className="right">风险</th>
                </tr></thead>
                <tbody>
                  {stopLoss.positions.map(p => {
                    const danger = p.nearest_trigger?.danger_level || 'no_rules';
                    const ruleLabels: Record<string, string> = {
                      rul0a_break_low: '破底', rul0b_cost_stop: '成本',
                      rul1_sector: '行业', rul2_iron: '铁律2', rul3_dynamic: '动态',
                    };
                    const ruleLabel = ruleLabels[p.nearest_trigger?.rule || ''] || p.nearest_trigger?.rule || '';
                    return (
                      <tr key={p.symbol} className={danger === 'triggered' ? 'sl-row-danger' : danger === 'critical' ? 'sl-row-critical' : ''}>
                        <td className="mono bold">{p.symbol}</td>
                        <td className="dim">{p.name || p.symbol}</td>
                        <td className="num mono right">¥{p.avg_price.toFixed(2)}</td>
                        <td className="num mono right">¥{p.current_price.toFixed(2)}</td>
                        <td className={`num right ${p.float_pnl_pct >= 0 ? 'pnl-up' : 'pnl-down'}`}>
                          {p.float_pnl_pct >= 0 ? '+' : ''}{p.float_pnl_pct.toFixed(2)}%</td>
                        <td className="num mono right">
                          {p.nearest_trigger?.distance_pct != null
                            ? `${p.nearest_trigger.distance_pct >= 0 ? '+' : ''}${p.nearest_trigger.distance_pct.toFixed(2)}%` : '-'}</td>
                        <td className="num dim right">{ruleLabel}</td>
                        <td className="num right">
                          <span className={`cp-sl-badge ${danger}`}>
                            {danger === 'triggered' ? '触发' : danger === 'critical' ? '危急' : danger === 'warning' ? '警告' : danger === 'caution' ? '关注' : '安全'}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      </div>

      {/* ═══ 分析组：图表 + 持仓 + 交易 ═══ */}
      <div className="cp-section-group">

      {/* ═══ 图表行 ═══ */}
      <div className="cp-row-charts">
        {/* 每日盈亏柱状图 */}
        <div className="cp-panel" style={{ minHeight: 280 }}>
          <div className="cp-panel-header">
            <i className="fas fa-chart-bar" />
            <span className="cp-panel-title">{t('portfolio.dailyPnL')}</span>
            <button className="cp-refresh-btn" onClick={refreshEquity} title="刷新" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-sync-alt ${loadingEquity ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-panel-body" style={{ padding: '4px 8px 8px' }}>
            {loadingEquity ? <SkeletonBlock h={220} /> : dailyPnlData.length === 0 ? (
              <div className="cp-empty" style={{ height: 220 }}><i className="fas fa-chart-bar" /><span>{t('common.noData')}</span></div>
            ) : (
              <div className="cp-chart-h240">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyPnlData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={G} />
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
              </div>
            )}
          </div>
        </div>

        {/* 持仓环形图 */}
        <div className="cp-panel" style={{ minHeight: 280 }}>
          <div className="cp-panel-header">
            <i className="fas fa-chart-pie" />
            <span className="cp-panel-title">{t('portfolio.assetAllocation')}</span>
          </div>
          <div className="cp-panel-body" style={{ padding: '8px 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {loadingSummary ? <SkeletonBlock h={200} /> : (
              <>
                <div style={{ position: 'relative', height: 180 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={ringData} cx="50%" cy="50%" innerRadius={48} outerRadius={72} paddingAngle={2} dataKey="value" stroke="none">
                        {ringData.map((_, i) => <PieCell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} fillOpacity={0.85} />)}
                      </Pie>
                      <Tooltip content={<PieTip />} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="cp-ring-center">
                    <div className="cp-ring-center-val">¥{fmtMoney(posVal)}</div>
                    <div className="cp-ring-center-label">持仓市值</div>
                  </div>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', justifyContent: 'center' }}>
                  {ringData.slice(0, 6).map((item, i) => (
                    <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length], flexShrink: 0 }} />
                      <span style={{ color: 'var(--agent-text-dim)' }}>{item.name}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ═══ 持仓表格 + 交易记录 ═══ */}
      <div className="cp-row-2col">
        <div className="cp-panel">
          <div className="cp-panel-header">
            <i className="fas fa-table" />
            <span className="cp-panel-title">{t('portfolio.positions')} ({positions.length})</span>
            <button className="cp-refresh-btn" onClick={refreshMoneyflow} title="刷新资金流" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-coins ${loadingFlow ? 'fa-spin' : ''}`} />
            </button>
            <button className="cp-refresh-btn" onClick={refreshSummary} title="刷新持仓">
              <i className={`fas fa-sync-alt ${loadingSummary ? 'fa-spin' : ''}`} />
            </button>
          </div>
          {/* 资金流汇总条 */}
          {flowSummary && (
            <div className="cp-flow-summary">
              <i className="fas fa-chart-waterfall" />
              {flowSummary.inflow > 0 && flowSummary.outflow > 0 ? (
                <span><span className="cp-flow-in">{flowSummary.inflow}只流入</span> / <span className="cp-flow-out">{flowSummary.outflow}只流出</span></span>
              ) : flowSummary.outflow === 0 ? (
                <span className="cp-flow-in">主力全部流入</span>
              ) : flowSummary.inflow === 0 ? (
                <span className="cp-flow-out">主力全部流出</span>
              ) : null}
            </div>
          )}
          <div className="cp-table-wrap" style={{ maxHeight: 340 }}>
            {loadingSummary ? <SkeletonTable rows={5} /> : (
              <table className="cp-table">
                <thead><tr>
                  <th>{t('portfolio.symbol')}</th><th>{t('portfolio.name')}</th>
                  <th className="right">{t('portfolio.volume')}</th><th className="right">{t('portfolio.avgPrice')}</th>
                  <th className="right">{t('portfolio.currentPrice')}</th>
                  <th className="right">{t('portfolio.todayPnL')}</th>
                  <th className={`right sortable ${sortKey === 'floating_pnl' ? 'sorted' : ''}`} onClick={() => handleSort('floating_pnl')}>
                    {t('portfolio.floatingPnL')} {sortKey === 'floating_pnl' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'floating_pnl_pct' ? 'sorted' : ''}`} onClick={() => handleSort('floating_pnl_pct')}>
                    {t('portfolio.profitRate')} {sortKey === 'floating_pnl_pct' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'market_value' ? 'sorted' : ''}`} onClick={() => handleSort('market_value')}>
                    {t('portfolio.marketValue')} {sortKey === 'market_value' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'weight' ? 'sorted' : ''}`} onClick={() => handleSort('weight')}>
                    {t('portfolio.weight')} {sortKey === 'weight' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className="center">资金流</th>
                  <th className="center">操作</th>
                </tr></thead>
                <tbody>
                  {sortedPositions.length === 0 ? (
                    <tr><td colSpan={13}><div className="cp-empty"><i className="fas fa-chart-pie" /><span>{t('portfolio.noPositions')}</span></div></td></tr>
                  ) : sortedPositions.flatMap(pos => {
                    const isUp = (pos.floating_pnl || 0) >= 0;
                    const weight = totalAsset > 0 ? (pos.market_value / totalAsset) * 100 : 0;
                    const isHeavy = weight > 30; const isWarn = weight > 20 && weight <= 30;
                    const pnlPct = pos.floating_pnl_pct || 0;
                    const pnlMag = Math.abs(pnlPct) > 5 ? 'strong' : Math.abs(pnlPct) < 2 ? 'mild' : '';
                    const flow = moneyflowMap[pos.symbol];
                    const flowLabel = !flow ? '—' : (flow.main_net || 0) > (pos.market_value * 0.01) ? '主力流入' : (flow.main_net || 0) < -(pos.market_value * 0.01) ? '主力流出' : '平衡';
                    const flowClass = flowLabel === '主力流入' ? 'in' : flowLabel === '主力流出' ? 'out' : 'neutral';
                    const isExpanded = expandedTradeSymbol === pos.symbol;

                    const rows = [
                      <tr key={pos.symbol} className={`${isHeavy ? 'risk-high' : isWarn ? 'risk-warn' : ''} ${isExpanded ? 'trade-expanded' : ''}`}>
                        <td className="symbol mono">{pos.symbol}</td>
                        <td className="bold">{cleanStockName(pos.name, pos.symbol)}</td>
                        <td className="num mono dim">{pos.volume.toLocaleString()}</td>
                        <td className="num mono">¥{(pos.avg_price || 0).toFixed(2)}</td>
                        <td className="num mono">¥{(pos.current_price || 0).toFixed(2)}</td>
                        <td className={`num mono ${(pos.today_pnl || 0) >= 0 ? 'pnl-up' : 'pnl-down'}`}>
                          {(pos.today_pnl || 0) >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(pos.today_pnl || 0))}</td>
                        <td className={`num mono ${isUp ? 'pnl-up' : 'pnl-down'}`}>{isUp ? '+' : ''}¥{fmtMoney(Math.abs(pos.floating_pnl || 0))}</td>
                        <td className="num"><span className={`cp-pnl-tag ${isUp ? 'up' : 'down'} ${pnlMag ? `pnl-${pnlMag}-${isUp ? 'up' : 'down'}` : ''}`}>{isUp ? '+' : ''}{pnlPct.toFixed(2)}%</span></td>
                        <td className="num mono bold">¥{fmtMoney(pos.market_value)}</td>
                        <td className="num">
                          <span className={`cp-wt-tag ${isHeavy ? 'danger' : isWarn ? 'warn' : ''}`}>{weight.toFixed(1)}%</span></td>
                        <td className="num center">
                          <span className={`cp-flow-badge ${flowClass}`}>{flowLabel}</span></td>
                        <td className="num center">
                          <div className="cp-trade-actions">
                            <button className="cp-trade-btn buy" title="买入" onClick={() => openTradePanel(pos.symbol, '买', pos.current_price, 0)}>+</button>
                            <button className="cp-trade-btn sell" title="卖出" onClick={() => openTradePanel(pos.symbol, '卖', pos.current_price, pos.volume)}>−</button>
                          </div>
                        </td>
                      </tr>,
                    ];

                    // 内联交易面板
                    if (isExpanded) {
                      rows.push(
                        <tr key={`${pos.symbol}-trade`} className="cp-trade-panel-row">
                          <td colSpan={13} className="cp-trade-panel-cell">
                            <div className="cp-trade-panel">
                              <span className={`cp-trade-panel-dir ${tradeForm.direction === '买' ? 'buy' : 'sell'}`}>{tradeForm.direction === '买' ? '买入' : '卖出'} {pos.symbol} {cleanStockName(pos.name, pos.symbol)}</span>
                              <div className="cp-trade-panel-fields">
                                <label>价格 <input type="number" className="cp-trade-input" value={tradeForm.price || ''} onChange={e => setTradeForm(f => ({ ...f, price: parseFloat(e.target.value) || 0 }))} step="0.01" min="0" /></label>
                                <label>数量(股) <input type="number" className="cp-trade-input" value={tradeForm.volume || ''} onChange={e => setTradeForm(f => ({ ...f, volume: parseInt(e.target.value) || 0 }))} step="100" min="0" /></label>
                                <label>理由 <input type="text" className="cp-trade-input" value={tradeForm.reason} onChange={e => setTradeForm(f => ({ ...f, reason: e.target.value }))} placeholder="选填" /></label>
                              </div>
                              {tradeError && <div className="cp-trade-error"><i className="fas fa-exclamation-circle" /> {tradeError}</div>}
                              <div className="cp-trade-panel-actions">
                                <button className="cp-trade-submit" onClick={handleTradeSubmit} disabled={tradeSubmitting}>
                                  {tradeSubmitting ? <><i className="fas fa-spinner fa-spin" /> 提交中</> : '确认下单'}
                                </button>
                                <button className="cp-trade-cancel" onClick={() => { setExpandedTradeSymbol(null); setTradeError(null); }}>取消</button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      );
                    }

                    return rows;
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* 近期交易 */}
        <div className="cp-panel">
          <div className="cp-panel-header">
            <i className="fas fa-exchange-alt" />
            <span className="cp-panel-title">近期交易</span>
            <button className="cp-refresh-btn" onClick={refreshTrades} title="刷新交易" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-sync-alt ${loadingTrades ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-panel-body" style={{ padding: '8px 12px' }}>
            {loadingTrades ? <SkeletonList n={5} /> : recentTrades.length === 0 ? (
              <div className="cp-empty"><i className="fas fa-history" /><span>暂无交易记录</span></div>
            ) : (
              <div className="cp-trade-list">
                {recentTrades.map((tr, i) => {
                  const isBuy = (tr.direction || '').includes('买') || (tr.direction || '').toLowerCase().includes('buy');
                  return (
                    <div key={tr.order_id || i} className="cp-trade-item">
                      <span className={`cp-trade-dir ${isBuy ? 'buy' : 'sell'}`}>{isBuy ? '买' : '卖'}</span>
                      <span className="cp-trade-name">{tr.name || tr.symbol}</span>
                      <span className="cp-trade-detail">¥{tr.price?.toFixed(2)} × {tr.volume}</span>
                      <span className="cp-trade-time">{tr.created_at ? new Date(tr.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ═══ 行业集中度 + 个股盈亏贡献 ═══ */}
      <div className="cp-row-charts">
        {/* 个股盈亏贡献排名 */}
        <div className="cp-panel">
          <div className="cp-panel-header">
            <i className="fas fa-ranking-star" />
            <span className="cp-panel-title">个股盈亏贡献 (30日)</span>
            <button className="cp-refresh-btn" onClick={refreshBreakdowns} title="刷新贡献数据" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-sync-alt ${loadingBreakdowns ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-panel-body" style={{ padding: '0' }}>
            {loadingBreakdowns ? (
              <SkeletonTable rows={10} />
            ) : pnlContributions.length === 0 ? (
              <div className="cp-empty" style={{ height: 200 }}>
                <i className="fas fa-chart-bar" />
                <span>暂无盈亏明细数据</span>
              </div>
            ) : (
              <>
                <table className="cp-table cp-contribution-table">
                  <thead>
                    <tr>
                      <th style={{ width: 40 }}>#</th>
                      <th className={`sortable ${contribSortKey === 'symbol' ? 'sorted' : ''}`} onClick={() => { setContribSortKey('symbol'); setContribSortDir(prev => contribSortKey === 'symbol' ? (prev === 'asc' ? 'desc' : 'asc') : 'desc'); setContributionPage(1); }}>
                        代码{contribSortKey === 'symbol' && <i className={`fas fa-sort-${contribSortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                      <th className={`sortable ${contribSortKey === 'name' ? 'sorted' : ''}`} onClick={() => { setContribSortKey('name'); setContribSortDir(prev => contribSortKey === 'name' ? (prev === 'asc' ? 'desc' : 'asc') : 'desc'); setContributionPage(1); }}>
                        名称{contribSortKey === 'name' && <i className={`fas fa-sort-${contribSortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                      <th className={`right sortable ${contribSortKey === 'totalPnl' ? 'sorted' : ''}`} onClick={() => { setContribSortKey('totalPnl'); setContribSortDir(prev => contribSortKey === 'totalPnl' ? (prev === 'asc' ? 'desc' : 'asc') : 'desc'); setContributionPage(1); }}>
                        30日贡献{contribSortKey === 'totalPnl' && <i className={`fas fa-sort-${contribSortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {contributionPageItems.map((item, i) => {
                      const rank = (contributionPage - 1) * CONTRIB_PAGE_SIZE + i + 1;
                      return (
                        <tr key={item.symbol}>
                          <td className="cp-contrib-rank">{rank}</td>
                          <td className="cp-contrib-code">{item.symbol}</td>
                          <td className="cp-contrib-name">{item.name || item.symbol}</td>
                          <td className={`right cp-contrib-pnl ${item.totalPnl >= 0 ? 'pnl-up' : 'pnl-down'}`}>
                            {item.totalPnl >= 0 ? '+' : ''}¥{item.totalPnl.toLocaleString()}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {contributionPageCount > 1 && (
                  <div className="cp-contrib-pager">
                    <button disabled={contributionPage <= 1} onClick={() => setContributionPage(p => p - 1)}>
                      <i className="fas fa-chevron-left" />
                    </button>
                    <span>{contributionPage} / {contributionPageCount}</span>
                    <button disabled={contributionPage >= contributionPageCount} onClick={() => setContributionPage(p => p + 1)}>
                      <i className="fas fa-chevron-right" />
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* 行业集中度 */}
        <div className="cp-panel">
          <div className="cp-panel-header">
            <i className="fas fa-layer-group" />
            <span className="cp-panel-title">行业集中度</span>
          </div>
          <div className="cp-panel-body" style={{ padding: '12px 16px' }}>
            {sectorData && sectorData.sectors && sectorData.sectors.length > 0 ? (
              <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                <div style={{ width: 160, height: 160, flexShrink: 0 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={sectorData.sectors} cx="50%" cy="50%" innerRadius={36} outerRadius={64} paddingAngle={2} dataKey="weight_pct" nameKey="name" stroke="none">
                        {sectorData.sectors.map((_, i) => (
                          <PieCell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} fillOpacity={0.85} />
                        ))}
                      </Pie>
                      <Tooltip content={({ active, payload }: any) => {
                        if (!active || !payload?.length) return null;
                        const d = payload[0]?.payload;
                        return <div className="cp-tip-box"><div className="cp-tip-label">{d?.name}</div><div className="cp-tip-row"><span className="l">权重</span><span className="v">{d?.weight_pct}%</span></div><div className="cp-tip-row"><span className="l">持仓数</span><span className="v">{d?.stock_count}只</span></div></div>;
                      }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ marginBottom: 6 }}>
                    <span className={`cp-sector-level ${sectorData.concentration_level === '集中' ? 'danger' : sectorData.concentration_level === '适中' ? 'warn' : 'safe'}`}>
                      {sectorData.concentration_level}
                    </span>
                    {sectorData.max_sector && (
                      <span style={{ fontSize: 11, color: 'var(--agent-text-dim)', marginLeft: 8 }}>
                        最大: {sectorData.max_sector.name} ({sectorData.max_sector.weight_pct}%)
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {sectorData.sectors.slice(0, 6).map(s => (
                      <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                        <span style={{ width: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--agent-text-secondary)' }}>{s.name}</span>
                        <div style={{ flex: 1, height: 6, background: 'var(--agent-bg-hover)', borderRadius: 3, overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${Math.min(s.weight_pct, 100)}%`, background: s.weight_pct > 50 ? 'var(--agent-red)' : s.weight_pct > 30 ? 'var(--agent-gold)' : 'var(--agent-green)', borderRadius: 3, transition: 'width 0.3s ease' }} />
                        </div>
                        <span style={{ width: 40, textAlign: 'right', fontFamily: 'var(--font-display)', fontSize: 10, color: 'var(--agent-text-dim)' }}>{s.weight_pct}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="cp-empty" style={{ height: 160 }}>
                <i className="fas fa-layer-group" />
                <span>暂无行业数据</span>
              </div>
            )}
          </div>
        </div>
      </div>

      </div>

      {/* ═══ FAB ═══ */}
      <div className="cp-fab">
        <button className="cp-fab-main" onClick={() => setFabOpen(o => !o)} title="快捷操作">
          <i className={`fas fa-${fabOpen ? 'times' : 'ellipsis'}`} />
        </button>
      </div>

      {/* ═══ 日盈亏弹窗 ═══ */}
      {modalDate && (
        <div className="cp-modal-overlay" onClick={() => { setModalDate(null); setModalData(null); }}>
          <div className="cp-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <span className="cp-modal-corner cp-modal-corner--tl" />
            <span className="cp-modal-corner cp-modal-corner--tr" />
            <span className="cp-modal-corner cp-modal-corner--bl" />
            <span className="cp-modal-corner cp-modal-corner--br" />
            <div className="cp-modal-scanline" />
            <div className="cp-modal-header">
              <span>{modalDate} 个股盈亏明细</span>
              <button className="cp-modal-close" onClick={() => { setModalDate(null); setModalData(null); }}>
                <i className="fas fa-times" />
              </button>
            </div>
            <div className="cp-modal-body" style={{ padding: '12px 16px' }}>
              {modalLoading ? (
                <div style={{ textAlign: 'center', padding: 24, color: 'var(--agent-text-dim)' }}>
                  <i className="fas fa-spinner fa-spin" /> 加载中...
                </div>
              ) : modalData ? (
                <>
                  <div style={{ fontSize: 13, marginBottom: 10, display: 'flex', gap: 16 }}>
                    <span>日盈亏 <span style={{ color: modalData.daily_pnl >= 0 ? GREEN : RED, fontWeight: 700 }}>
                      {modalData.daily_pnl >= 0 ? '+' : ''}¥{Math.abs(modalData.daily_pnl).toFixed(0)}</span></span>
                    <span style={{ fontSize: 11, color: 'var(--agent-text-dim)' }}>
                      已实现 <span style={{ color: modalData.realized_total >= 0 ? GREEN : RED }}>{modalData.realized_total >= 0 ? '+' : ''}¥{Math.abs(modalData.realized_total).toFixed(0)}</span>
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--agent-text-dim)' }}>
                      浮盈变动 <span style={{ color: modalData.float_total >= 0 ? GREEN : RED }}>{modalData.float_total >= 0 ? '+' : ''}¥{Math.abs(modalData.float_total).toFixed(0)}</span>
                    </span>
                  </div>
                  {modalData.stocks.length > 0 ? (
                    <table className="cp-table" style={{ fontSize: 11 }}>
                      <thead><tr>
                        <th>代码</th><th>名称</th><th className="right">持仓</th><th className="right">收盘价</th><th className="right">涨跌</th><th className="right">浮盈变动</th><th className="right">已实现</th>
                      </tr></thead>
                      <tbody>
                        {modalData.stocks.map(s => {
                          const chgPct = s.prev_close > 0 ? ((s.close_price / s.prev_close - 1) * 100) : 0;
                          const isSold = s.volume === 0;
                          return (
                            <tr key={s.symbol} style={{ opacity: isSold ? 0.55 : 1 }}>
                              <td className="mono bold">{s.symbol}</td>
                              <td className="dim" style={{ fontSize: 10 }}>{s.name || '-'}</td>
                              <td className="num dim">{isSold ? <span style={{color: 'var(--agent-text-dim)', fontSize: 9}}>已卖出</span> : `${s.volume}股`}</td>
                              <td className="num mono">{s.close_price > 0 ? `¥${s.close_price.toFixed(2)}` : '-'}</td>
                              <td className={`num ${chgPct >= 0 ? 'pnl-up' : 'pnl-down'}`}>{s.close_price > 0 && s.prev_close > 0 ? `${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%` : '-'}</td>
                              <td className={`num mono ${(s.float_pnl || 0) >= 0 ? 'pnl-up' : 'pnl-down'}`}>{(s.float_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.float_pnl || 0).toFixed(0)}</td>
                              <td className={`num mono ${(s.realized_pnl || 0) >= 0 ? 'pnl-up' : 'pnl-down'}`}>{(s.realized_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.realized_pnl || 0).toFixed(0)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : (
                    <div style={{ fontSize: 11, color: 'var(--agent-text-dim)', textAlign: 'center', padding: 16 }}>当日无持仓数据</div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 11, color: 'var(--agent-text-dim)', textAlign: 'center', padding: 16 }}>加载失败</div>
              )}
            </div>
          </div>
        </div>
      )}

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

function HeroKpi({ label, value, sub, trend }: { label: string; value: string; sub?: string; trend?: 'up' | 'down' }) {
  return (
    <div className="cp-hero-kpi">
      <div className="cp-hero-kpi-label">{label}</div>
      <div className={`cp-hero-kpi-value ${trend === 'up' ? 'up' : trend === 'down' ? 'down' : ''}`}>{value}</div>
      {sub && <div className="cp-hero-kpi-sub">{sub}</div>}
    </div>
  );
}

function RiskCard({ icon, label, value, sub, level }: {
  icon: string; label: string; value: string; sub: string;
  level: 'safe' | 'warn' | 'danger';
}) {
  return (
    <div className={`cp-risk-card ${level}`}>
      <i className={`fas ${icon} cp-risk-icon`} />
      <div className="cp-risk-info">
        <div className="cp-risk-label">{label}</div>
        <div className="cp-risk-value">{value}</div>
        <div className="cp-risk-sub">{sub}</div>
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
        grad.addColorStop(0, 'rgba(240,185,11,0)');
        grad.addColorStop(0.7, 'rgba(240,185,11,0.03)');
        grad.addColorStop(1, 'rgba(240,185,11,0.08)');
        ctx!.beginPath();
        ctx!.arc(0, 0, r1, 0, Math.PI * 2);
        ctx!.fillStyle = grad;
        ctx!.fill();
        if (r * 0.5 > 0.5) {
          ctx!.beginPath();
          ctx!.arc(0, 0, r * 0.5, 0, Math.PI * 2);
          ctx!.strokeStyle = 'rgba(240,185,11,0.1)';
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
        ctx!.fillStyle = `rgba(240,185,11,${p.alpha})`;
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
            ctx!.strokeStyle = `rgba(240,185,11,${(1 - dist / 100) * 0.06})`;
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

function Skel({ w, h = 14, br = 6 }: { w: number | string; h?: number; br?: number | string }) {
  return <div className="cp-skel" style={{ width: w, height: h, borderRadius: br }} />;
}

function SkeletonTicker() {
  return (
    <div className="cp-ticker-bar" style={{ gap: 18 }}>
      {[1, 2, 3, 4, 5, 6].map(i => (
        <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Skel w={32} h={12} /><Skel w={48} h={12} /><Skel w={44} h={12} />
        </div>
      ))}
    </div>
  );
}

function SkeletonHero() {
  return (
    <div className="cp-hero-card" style={{ opacity: 0.6 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Skel w={80} h={12} />
        <Skel w={180} h={32} br={8} />
        <Skel w={120} h={14} />
      </div>
      <div className="cp-hero-right" style={{ gap: 12 }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Skel w={48} h={10} /><Skel w={72} h={18} />
          </div>
        ))}
      </div>
    </div>
  );
}

function SkeletonRisk() {
  return (
    <div className="cp-risk-strip">
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="cp-risk-card" style={{ opacity: 0.5 }}>
          <Skel w={64} h={64} br="50%" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
            <Skel w={48} h={10} /><Skel w={56} h={18} /><Skel w={40} h={10} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SkeletonSL() {
  return (
    <div className="cp-sl-card" style={{ opacity: 0.5, padding: '12px 16px' }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}><Skel w={8} h={8} br="50%" /><Skel w={48} h={12} /></div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8 }}>
        {[1, 2, 3, 4].map(i => <Skel key={i} w="100%" h={44} br={8} />)}
      </div>
    </div>
  );
}

function SkeletonBlock({ h = 240 }: { h?: number }) {
  return <div style={{ width: '100%', height: h, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
    <Skel w="90%" h={h - 20} br={10} />
  </div>;
}

function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <table className="cp-table" style={{ opacity: 0.5 }}>
      <thead><tr>{Array.from({ length: 10 }).map((_, i) => <th key={i}><Skel w={48} h={10} /></th>)}</tr></thead>
      <tbody>
        {Array.from({ length: rows }).map((_, r) => (
          <tr key={r}>{Array.from({ length: 10 }).map((_, c) => <td key={c}><Skel w={40 + Math.round(Math.random() * 30)} h={12} /></td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

function SkeletonList({ n = 5 }: { n?: number }) {
  return (
    <div className="cp-trade-list">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="cp-trade-item" style={{ opacity: 0.5, gap: 10 }}>
          <Skel w={24} h={18} br={5} /><Skel w={72} h={12} /><Skel w={80} h={12} /><Skel w={40} h={12} />
        </div>
      ))}
    </div>
  );
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

