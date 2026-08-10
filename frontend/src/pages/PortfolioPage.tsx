import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
  PieChart, Pie, Cell as PieCell,
} from 'recharts';
import { portfolioApi, marketApi, tradesApi, schedulerApi, goldenPitApi } from '../api/client';
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

const GREEN = '#27a06b'; const RED = '#e5484d';

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

// ── 面板头（蓝色刻度 + 中文标题 + EN 小标）──
function PanelHead({ title, en, children }: { title: string; en: string; children?: React.ReactNode }) {
  return (
    <div className="cp-panel-head">
      <span className="cp-tick" /><h2>{title}</h2><span className="cp-en">{en}</span>
      {children && <div className="cp-panel-actions">{children}</div>}
    </div>
  );
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
  // ── 资金调整 ──
  const [capOpen, setCapOpen] = useState(false);
  const [capMode, setCapMode] = useState<'set' | 'deposit' | 'withdraw'>('set');
  const [capAmount, setCapAmount] = useState('');
  const [capNote, setCapNote] = useState('');
  const [capError, setCapError] = useState<string | null>(null);
  const [capSubmitting, setCapSubmitting] = useState(false);
  const [slExpanded, setSlExpanded] = useState(false);
  const [slToggling, setSlToggling] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  usePortfolioBackground(canvasRef);

  // ── 黄金坑信号 ──
  const [gpSignal, setGpSignal] = useState<{
    phase: string; pit_count: number; warning_count: number;
    min_greed: number | null; min_greed_index: string | null;
    position_tier_label: string | null; as_of: string;
    panic_count: number;
    pit_threshold: number;
    warn_threshold: number;
    max_entry_greed: number;
  } | null>(null);
  const [gpError, setGpError] = useState(false);
  const [loadingGp, setLoadingGp] = useState(true);

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

  const refreshGoldenPit = useCallback(async () => {
    setLoadingGp(true);
    setGpError(false);
    try {
      const res = await goldenPitApi.getStatus();
      const data = res.data?.data || res.data;
      if (data?.indices?.length > 0) {
        const gw = data.golden_pit_window || {};
        const indices = data.indices as any[];
        // 找贪婪值最低的指数 — 它最接近恐慌线
        let minIdx = indices[0];
        for (const idx of indices) {
          if ((idx.greed ?? 1) < (minIdx.greed ?? 1)) minIdx = idx;
        }
        const phase = gw.phase || (gw.active ? 'waiting' : 'idle');
        // Extract per-index thresholds
        const allPitGreeds = indices.map((i: any) => i.pit_greed).filter((v: any) => v != null) as number[];
        const allEntryGreeds = indices.map((i: any) => i.entry_greed).filter((v: any) => v != null) as number[];
        const pitThreshold = allPitGreeds.length > 0 ? Math.min(...allPitGreeds) : 0.35;
        const warnThreshold = allEntryGreeds.length > 0 ? Math.min(...allEntryGreeds) : 0.40;
        const maxEntryGreed = allEntryGreeds.length > 0 ? Math.max(...allEntryGreeds) : 0.50;
        setGpSignal({
          phase,
          pit_count: indices.filter((i: any) => i.status === 'golden_pit').length,
          warning_count: indices.filter((i: any) => i.status === 'warning').length,
          min_greed: minIdx?.greed ?? null,
          min_greed_index: minIdx?.index_name || minIdx?.fund_code || null,
          position_tier_label: minIdx?.position_tier_label || null,
          as_of: data.as_of || '',
          panic_count: gw.pit_count ?? indices.filter((i: any) => i.status === 'golden_pit').length,
          pit_threshold: pitThreshold,
          warn_threshold: warnThreshold,
          max_entry_greed: maxEntryGreed,
        });
      } else {
        setGpError(true);
      }
    } catch {
      setGpError(true);
    } finally { setLoadingGp(false); }
  }, []);

  // ── 首次并行加载 ──
  useEffect(() => {
    refreshSummary();
    refreshTickers();
    refreshEquity();
    refreshTrades();
    refreshStopLoss();
    refreshGoldenPit();
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

  // ── 资金调整 ──
  const capPreview = useMemo(() => {
    const num = Number(capAmount);
    if (!capAmount.trim() || !Number.isFinite(num) || num <= 0) return totalAsset;
    if (capMode === 'set') return num;
    return totalAsset + (capMode === 'deposit' ? num : -num);
  }, [capAmount, capMode, totalAsset]);

  const handleCapitalSubmit = useCallback(async () => {
    setCapError(null);
    const num = Number(capAmount);
    if (!capAmount.trim() || !Number.isFinite(num) || num <= 0) {
      setCapError(t('portfolio.capitalInvalidAmount'));
      return;
    }
    let amount = num;
    if (capMode === 'withdraw') amount = -num;
    else if (capMode === 'set') amount = num - totalAsset;
    if (Math.abs(amount) < 0.005) {
      setCapError(t('portfolio.capitalNoChange'));
      return;
    }
    setCapSubmitting(true);
    try {
      const res = await portfolioApi.adjustCapital({ amount, note: capNote.trim() || undefined });
      if (res.data?.success) {
        setCapOpen(false);
        setCapAmount('');
        setCapNote('');
        setCapError(null);
        await refreshSummary();
        alert(t('portfolio.capitalSuccess') + ': ¥' + Number(res.data.available_cash ?? 0).toLocaleString());
      } else {
        setCapError((res.data?.message as string) || t('portfolio.capitalFailed'));
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setCapError(detail || (err instanceof Error ? err.message : t('portfolio.capitalFailed')));
    } finally {
      setCapSubmitting(false);
    }
  }, [capAmount, capMode, capNote, totalAsset, t, refreshSummary]);

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

  const ringTotal = useMemo(() => ringData.reduce((s, i) => s + i.value, 0), [ringData]);

  // 与主题协调的环图色板
  const PIE_COLORS = ['#2f7cd3', '#c98a12', '#27a06b', '#7c5cd6', '#0e9db8', '#e5484d', '#93a9c0'];

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

  return (
    <div className="cp-page">
      <canvas ref={canvasRef} id="cp-bg-canvas" />

      {/* ══════════ STATUS BAR ══════════ */}
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

      <div className="cp-body">
        {/* ══════════ 左列：战况总览 + 黄金坑信号 ══════════ */}
        <aside className="cp-strategic">
          <div className="cp-strategic-panel">
            <PanelHead title="战况总览" en="BATTLEFIELD OVERVIEW" />
            {loadingSummary ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <Skel w={140} h={28} /><Skel w={100} h={18} /><Skel w="100%" h={1} /><Skel w={80} h={14} /><Skel w={100} h={14} />
              </div>
            ) : summary && (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div className="cp-strategic-value">¥{fmtMoney(totalAsset)}</div>
                  <button className="cp-icon-btn" onClick={() => { setCapMode('set'); setCapAmount(''); setCapNote(''); setCapError(null); setCapOpen(true); }}
                    title={t('portfolio.adjustCapital')} style={{ width: 22, height: 22, fontSize: 9 }}>
                    <i className="fas fa-pen" />
                  </button>
                </div>
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
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--cc-text-dim)', letterSpacing: '0.06em' }}>
                    <span>仓位率</span><span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, color: 'var(--cc-text)', fontSize: 11 }}>{posRatio.toFixed(0)}%</span>
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
                <div className="cp-week-note">
                  本周 {weekTotal >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(weekTotal))}
                  {' '}(已实现 <span style={{ color: weekRealized >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>{weekRealized >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekRealized))}</span>
                  {' '}/ 浮盈 <span style={{ color: weekFloat >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>{weekFloat >= 0 ? '+' : ''}{fmtMoneyShort(Math.abs(weekFloat))}</span>)
                </div>
              </>
            )}
          </div>

          {/* 黄金坑信号 */}
          <div className="cp-strategic-panel">
            <PanelHead title="黄金坑信号" en="GOLDEN PIT SIGNAL" />
            {loadingGp ? (
              <Skel w="100%" h={40} />
            ) : gpError || !gpSignal ? (
              <div style={{ fontSize: 10, color: 'var(--cc-text-dim)', textAlign: 'center', padding: '8px 0' }}>
                <i className="fas fa-exclamation-circle" style={{ marginRight: 4 }} />
                信号获取失败
              </div>
            ) : gpSignal.min_greed == null ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
                <span className="cp-gp-dot" style={{ background: '#93a9c0' }} />
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--cc-text)' }}>无信号</span>
                <span style={{ fontSize: 10, color: 'var(--cc-text-dim)' }}>
                  {gpSignal.warning_count > 0 ? `${gpSignal.warning_count}预警` : '静默'}
                </span>
              </div>
            ) : (() => {
              const greed = gpSignal.min_greed;
              const panicLine = gpSignal.pit_threshold;
              const warnLine = gpSignal.warn_threshold;
              const safeCeil = Math.max(gpSignal.max_entry_greed + 0.10, 0.50);
              const rawPct = ((safeCeil - greed) / (safeCeil - panicLine)) * 100;
              const dangerPct = Math.max(0, Math.min(100, rawPct));
              const level = greed <= panicLine ? 'panic' : greed <= warnLine ? 'warn' : 'greedy';
              const levelLabel = level === 'panic' ? '恐慌' : level === 'warn' ? '预警' : '贪婪';
              const levelColor = level === 'panic' ? 'var(--cc-red)' : level === 'warn' ? 'var(--cc-amber)' : 'var(--cc-green)';
              return (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2, marginBottom: 6 }}>
                    <span className={`cp-gp-dot ${level}`} />
                    <span style={{ fontSize: 14, fontWeight: 800, color: 'var(--cc-text)' }}>
                      {levelLabel}
                    </span>
                    <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, color: levelColor, marginLeft: 'auto' }}>
                      {greed.toFixed(4)}
                    </span>
                    {gpSignal.panic_count >= 3 && (
                      <span className="cp-gp-resonance">
                        {gpSignal.panic_count}指共振
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, marginBottom: 2 }}>
                    <span style={{ color: 'var(--cc-text-dim)' }}>
                      最低: {gpSignal.min_greed_index}
                    </span>
                  </div>
                  <div className="cp-gp-gauge">
                    <div className="cp-gp-gauge-fill" style={{ width: `${dangerPct}%` }} />
                    <span className="cp-gp-gauge-mark" style={{ left: `${((warnLine - panicLine) / (safeCeil - panicLine)) * 100}%` }} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8, color: 'var(--cc-text-dim)', marginTop: 2 }}>
                    <span>pit≤{panicLine.toFixed(3)}</span>
                    <span style={{ color: levelColor, fontWeight: 700, fontSize: 9 }}>{levelLabel}</span>
                    <span>warn≤{warnLine.toFixed(3)}</span>
                  </div>
                  {gpSignal.position_tier_label && (
                    <div style={{ fontSize: 10, color: 'var(--cc-blue)', fontWeight: 600, marginTop: 5 }}>
                      {gpSignal.position_tier_label}
                    </div>
                  )}
                </>
              );
            })()}
            {gpSignal?.as_of && (
              <div style={{ fontSize: 8, color: 'var(--cc-text-dim)', marginTop: 6, textAlign: 'right' }}>
                更新 {gpSignal.as_of}
              </div>
            )}
          </div>
        </aside>

        {/* ══════════ 中列：收益图 + 指标行 + 三面板 ══════════ */}
        <main className="cp-main-view">
          <div className="cp-battlefield">
            <div className="cp-battlefield-scanline" />
            <PanelHead title="战略收益概览" en="STRATEGIC OVERVIEW">
              <button className="cp-icon-btn" onClick={refreshEquity} title="刷新">
                <i className={`fas fa-sync-alt ${loadingEquity ? 'fa-spin' : ''}`} />
              </button>
            </PanelHead>
            <div className="cp-battlefield-chart">
              {loadingEquity ? <Skel w="100%" h="100%" /> : equityCurve.length === 0 ? (
                <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--cc-text-dim)' }}>
                  <i className="fas fa-chart-line" style={{ marginRight: 8 }} />{t('common.noData')}
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyPnlData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#d4e7f9" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
                      tickLine={false}
                      axisLine={{ stroke: '#d4e7f9' }}
                      interval={Math.max(0, Math.floor(dailyPnlData.length / 6) - 1)}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => fmtMoneyShort(v)}
                      width={44}
                    />
                    <Tooltip content={<PTip />} cursor={{ fill: 'rgba(47,124,211,0.06)' }} />
                    <Bar dataKey="pnl" radius={[2, 2, 0, 0]} onClick={handleBarClick} cursor="pointer">
                      {dailyPnlData.map((entry, i) => (
                        <Cell key={i} fill={entry.pnl >= 0 ? GREEN : RED} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* 指标行：资产配置 + 四张指标卡 */}
          <div className="cp-metrics-row">
            <div className="cp-daily-strip">
              <PanelHead title="资产配置" en="ALLOCATION" />
              <div className="cp-alloc-body">
                {loadingSummary ? <Skel w="100%" h={90} /> : (
                  <>
                    <div className="cp-alloc-donut">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie data={ringData} cx="50%" cy="50%" innerRadius="58%" outerRadius="88%" paddingAngle={2} dataKey="value" stroke="none">
                            {ringData.map((_, i) => <PieCell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} fillOpacity={0.9} />)}
                          </Pie>
                          <Tooltip content={<PieTip />} />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="cp-alloc-center">
                        <span>¥{fmtMoneyShort(posVal)}</span>
                      </div>
                    </div>
                    <div className="cp-alloc-legend">
                      {ringData.slice(0, 5).map((item, i) => (
                        <div key={item.name} className="cp-alloc-item">
                          <span className="cp-alloc-sq" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                          <span className="cp-alloc-name">{item.name}</span>
                          <span className="cp-alloc-pct">
                            {ringTotal > 0 ? `${((item.value / ringTotal) * 100).toFixed(1)}%` : '—'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
            {loadingSummary ? (
              Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="cp-threat-card" style={{ opacity: 0.5 }}>
                  <div style={{ flex: 1 }}><Skel w={50} h={10} /><Skel w={60} h={20} /><Skel w={40} h={10} /></div>
                </div>
              ))
            ) : summary && (
              <>
                <ThreatCard label="仓位率" en="POSITION" value={`${posRatio.toFixed(0)}%`}
                  sub={posRatio > 80 ? '重仓部署' : posRatio > 50 ? '标准配置' : '轻仓待命'}
                  level={posRatio > 80 ? 'danger' : posRatio > 50 ? 'warn' : 'safe'} />
                <ThreatCard label="最大回撤" en="DRAWDOWN" value={`-${maxDrawdown.toFixed(1)}%`}
                  sub="历史极值" level={maxDrawdown > 15 ? 'danger' : maxDrawdown > 8 ? 'warn' : 'safe'} />
                <ThreatCard label="胜率" en="WIN RATE" value={`${win_rate.toFixed(1)}%`}
                  sub={`${positions.length} 个作战单位`} level={win_rate > 60 ? 'safe' : win_rate > 40 ? 'warn' : 'danger'} />
                <ThreatCard label="年化波动" en="VOLATILITY" value={`${volatility.toFixed(1)}%`}
                  sub="60日滚动" level={volatility > 25 ? 'danger' : volatility > 15 ? 'warn' : 'safe'} />
              </>
            )}
          </div>

          {/* 底部三面板 */}
          <div className="cp-bottom-zone">
            {/* 防御系统 */}
            <div className="cp-bottom-panel">
              <PanelHead title="防御系统" en="DEFENSE SYSTEM">
                <button className="cp-icon-btn" onClick={refreshStopLoss} title="刷新">
                  <i className={`fas fa-sync-alt ${loadingStopLoss ? 'fa-spin' : ''}`} />
                </button>
              </PanelHead>
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

            {/* 情报中心 */}
            <div className="cp-bottom-panel">
              <PanelHead title="情报中心" en="INTEL CENTER">
                <button className="cp-icon-btn" onClick={refreshBreakdowns} title="刷新">
                  <i className={`fas fa-sync-alt ${loadingBreakdowns ? 'fa-spin' : ''}`} />
                </button>
              </PanelHead>
              <div className="cp-bottom-body">
                {loadingBreakdowns ? <Skel w="100%" h={80} /> : (
                  <>
                    <div className="cp-sub-head">个股盈亏贡献 (30日)</div>
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
                    <div className="cp-sub-head" style={{ marginTop: 8 }}>行业集中度</div>
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

            {/* 任务日志 */}
            <div className="cp-bottom-panel">
              <PanelHead title="任务日志" en="MISSION LOG">
                <button className="cp-icon-btn" onClick={refreshTrades} title="刷新">
                  <i className={`fas fa-sync-alt ${loadingTrades ? 'fa-spin' : ''}`} />
                </button>
              </PanelHead>
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
        </main>

        {/* ══════════ 右列：部署单位 ══════════ */}
        <aside className="cp-deployment">
          <div className="cp-deployment-panel">
            <PanelHead title="部署单位" en={`DEPLOYED · ${positions.length} UNITS`}>
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
            </PanelHead>
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
                        <div className={`cp-unit-chip ${isUp ? 'up' : 'down'}`}>
                          {cleanStockName(pos.name, pos.symbol).slice(0, 1)}
                        </div>
                        <div className="cp-unit-info">
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
        </aside>
      </div>

      {/* HUD 作战报告弹窗 */}
      {modalDate && (
        <div
          className="cp-modal-overlay"
          onClick={() => { setModalDate(null); setModalData(null); }}
          style={{ position: 'fixed', inset: 0, zIndex: 200 }}
        >
          <div className="cp-hud-report" onClick={e => e.stopPropagation()}>
            <span className="cp-hud-report-scanline" />
            <span className="cp-hud-report-corner cp-hud-report-corner--tl" />
            <span className="cp-hud-report-corner cp-hud-report-corner--tr" />
            <span className="cp-hud-report-corner cp-hud-report-corner--bl" />
            <span className="cp-hud-report-corner cp-hud-report-corner--br" />
            {modalLoading ? (
              <div style={{ textAlign: 'center', padding: 24, color: 'var(--cc-text-dim)' }}>
                <i className="fas fa-spinner fa-spin" /> 加载中...
              </div>
            ) : modalData ? (
              <>
                <div className="cp-hud-report-header">
                  <span className="cp-hud-report-date">{modalDate} 作战报告</span>
                  <div className="cp-hud-report-summary">
                    <span>日盈亏 <span style={{ color: modalData.daily_pnl >= 0 ? GREEN : RED, fontWeight: 700 }}>
                      {modalData.daily_pnl >= 0 ? '+' : ''}¥{Math.abs(modalData.daily_pnl).toFixed(0)}</span></span>
                    <span>已实现 <span style={{ color: modalData.realized_total >= 0 ? GREEN : RED }}>
                      {modalData.realized_total >= 0 ? '+' : ''}¥{Math.abs(modalData.realized_total).toFixed(0)}</span></span>
                    <span>浮盈变动 <span style={{ color: modalData.float_total >= 0 ? GREEN : RED }}>
                      {modalData.float_total >= 0 ? '+' : ''}¥{Math.abs(modalData.float_total).toFixed(0)}</span></span>
                  </div>
                  <button className="cp-hud-report-close" onClick={() => { setModalDate(null); setModalData(null); }}>
                    <i className="fas fa-times" />
                  </button>
                </div>
                {modalData.stocks.length > 0 ? (
                  <table className="cp-table-mini" style={{ position: 'relative', zIndex: 2 }}>
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
                            <td style={{ fontSize: 13 }}>{s.name || '-'}</td>
                            <td style={{ fontSize: 13 }}>{isSold ? <span style={{ color: 'var(--cc-text-dim)' }}>已卖出</span> : `${s.volume}股`}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 13 }}>{s.close_price > 0 ? `¥${s.close_price.toFixed(2)}` : '-'}</td>
                            <td style={{ color: chgPct >= 0 ? 'var(--cc-green)' : 'var(--cc-red)', fontFamily: 'var(--font-display)', fontSize: 13 }}>
                              {s.close_price > 0 && s.prev_close > 0 ? `${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%` : '-'}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 13, color: (s.float_pnl || 0) >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>
                              {(s.float_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.float_pnl || 0).toFixed(0)}</td>
                            <td style={{ fontFamily: 'var(--font-display)', fontSize: 13, color: (s.realized_pnl || 0) >= 0 ? 'var(--cc-green)' : 'var(--cc-red)' }}>
                              {(s.realized_pnl || 0) >= 0 ? '+' : ''}¥{Math.abs(s.realized_pnl || 0).toFixed(0)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div style={{ fontSize: 14, color: 'var(--cc-text-dim)', textAlign: 'center', padding: 16 }}>当日无持仓数据</div>
                )}
              </>
            ) : (
              <div style={{ fontSize: 14, color: 'var(--cc-text-dim)', textAlign: 'center', padding: 16 }}>加载失败</div>
            )}
          </div>
        </div>
      )}

      {/* 资金调整弹窗 */}
      {capOpen && (
        <div
          className="cp-modal-overlay"
          onClick={() => { if (!capSubmitting) setCapOpen(false); }}
          style={{ position: 'fixed', inset: 0, zIndex: 210 }}
        >
          <div className="cp-hud-report cp-cap-modal" onClick={e => e.stopPropagation()}>
            <span className="cp-hud-report-scanline" />
            <span className="cp-hud-report-corner cp-hud-report-corner--tl" />
            <span className="cp-hud-report-corner cp-hud-report-corner--tr" />
            <span className="cp-hud-report-corner cp-hud-report-corner--bl" />
            <span className="cp-hud-report-corner cp-hud-report-corner--br" />
            <div className="cp-hud-report-header">
              <span className="cp-hud-report-date">{t('portfolio.adjustCapital')}</span>
              <div className="cp-hud-report-summary">
                <span>{t('portfolio.totalAsset')}: ¥{fmtMoney(totalAsset)}</span>
                <span>{t('portfolio.availableCash')}: ¥{fmtMoney(cash)}</span>
              </div>
              <button className="cp-hud-report-close" onClick={() => { if (!capSubmitting) setCapOpen(false); }}>
                <i className="fas fa-times" />
              </button>
            </div>
            <div style={{ position: 'relative', zIndex: 2 }}>
              <div className="cp-cap-mode-row">
                {(['set', 'deposit', 'withdraw'] as const).map(m => (
                  <button key={m} className={capMode === m ? 'active' : ''} onClick={() => { setCapMode(m); setCapError(null); }}>
                    {t('portfolio.capitalMode.' + m)}
                  </button>
                ))}
              </div>
              <label className="cp-cap-label">{t('portfolio.capitalAmountLabel')}</label>
              <input
                className="cp-cap-input"
                type="number"
                min="0"
                step="1000"
                placeholder="0"
                value={capAmount}
                onChange={e => { setCapAmount(e.target.value); setCapError(null); }}
                disabled={capSubmitting}
                autoFocus
              />
              <div className="cp-cap-hint">
                <span>{t('portfolio.capitalPreview')}:</span>
                <span className="cp-cap-preview">¥{fmtMoney(capPreview)}</span>
              </div>
              <label className="cp-cap-label">{t('portfolio.capitalNoteLabel')}</label>
              <input
                className="cp-cap-input"
                type="text"
                maxLength={200}
                placeholder={t('portfolio.capitalNotePlaceholder')}
                value={capNote}
                onChange={e => setCapNote(e.target.value)}
                disabled={capSubmitting}
              />
              {capError && (
                <div className="cp-cap-error"><i className="fas fa-exclamation-circle" /> {capError}</div>
              )}
              <div className="cp-cap-actions">
                <button className="cp-cap-btn" onClick={() => setCapOpen(false)} disabled={capSubmitting}>
                  {t('portfolio.capitalCancel')}
                </button>
                <button className="cp-cap-btn primary" onClick={handleCapitalSubmit} disabled={capSubmitting}>
                  {capSubmitting ? <><i className="fas fa-spinner fa-spin" /> {t('portfolio.capitalSubmitting')}</> : t('portfolio.capitalConfirm')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

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

function ThreatCard({ label, en, value, sub, level }: {
  label: string; en: string; value: string; sub: string;
  level: 'safe' | 'warn' | 'danger';
}) {
  return (
    <div className={`cp-threat-card ${level}`}>
      <div className="cp-threat-head">
        <span className="cp-threat-label">{label}</span>
        <span className="cp-threat-en">{en}</span>
        <span className={`cp-threat-dot ${level}`} />
      </div>
      <div className="cp-threat-value">{value}</div>
      <div className="cp-threat-sub">{sub}</div>
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

    const particles: { x: number; y: number; size: number; sx: number; sy: number; alpha: number; tint: number }[] = [];
    for (let i = 0; i < 50; i++) {
      particles.push({
        x: Math.random() * w, y: Math.random() * h,
        size: Math.random() * 2 + 0.5,
        sx: (Math.random() - 0.5) * 0.2,
        sy: (Math.random() - 0.5) * 0.2,
        alpha: Math.random() * 0.3 + 0.08,
        tint: Math.random(),
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
        grad.addColorStop(0, 'rgba(47,124,211,0)');
        grad.addColorStop(0.7, 'rgba(47,124,211,0.025)');
        grad.addColorStop(1, 'rgba(47,124,211,0.06)');
        ctx!.beginPath();
        ctx!.arc(0, 0, r1, 0, Math.PI * 2);
        ctx!.fillStyle = grad;
        ctx!.fill();
        if (r * 0.5 > 0.5) {
          ctx!.beginPath();
          ctx!.arc(0, 0, r * 0.5, 0, Math.PI * 2);
          ctx!.strokeStyle = 'rgba(47,124,211,0.08)';
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
        const r = p.tint < 0.3 ? 47 : p.tint < 0.6 ? 167 : 47;
        const g = p.tint < 0.3 ? 124 : p.tint < 0.6 ? 216 : 124;
        const b = p.tint < 0.3 ? 211 : p.tint < 0.6 ? 234 : 211;
        ctx!.fillStyle = `rgba(${r},${g},${b},${p.alpha})`;
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
            ctx!.strokeStyle = `rgba(47,124,211,${(1 - dist / 100) * 0.05})`;
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
