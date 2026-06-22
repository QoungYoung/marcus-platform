import { useEffect, useState, useMemo, useCallback } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ComposedChart, Legend, Area, Line,
  PieChart, Pie, Cell as PieCell,
} from 'recharts';
import { portfolioApi, marketApi, tradesApi, schedulerApi } from '../api/client';
import '../styles/agent-theme.css';
import '../styles/portfolio-page.css';

// ── 类型 ──
interface Position {
  symbol: string; name: string; volume: number;
  avg_price: number; current_price: number;
  market_value: number; floating_pnl: number; floating_pnl_pct: number;
}
interface Account {
  initial_capital: number; available_cash: number; frozen_cash?: number;
  position_value: number; total_asset: number; realized_pnl: number;
  float_pnl: number; total_pnl: number; position_ratio: number; positions: Position[];
}
interface PortfolioSummary {
  account: Account; total_return: number; total_return_pct: number; win_rate: number;
}
interface EquityPoint { date: string; value: number; benchmark: number; }
interface DailyPnl { date: string; pnl: number; }
interface IndexTicker { name: string; price: number; change_pct: number; }
interface TradeRecord { order_id?: string; symbol: string; name?: string; direction: string; price: number; volume: number; created_at?: string; }

// ── 止损监控类型 ──
interface StopDistance {
  symbol: string; avg_price: number; current_price: number; volume: number;
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

// ── Mock ──
function generateEquityCurve(initialCapital: number, totalReturnPct: number, days = 60): EquityPoint[] {
  const result: EquityPoint[] = [];
  const seed = Math.abs(totalReturnPct) * 1000 + initialCapital * 0.01;
  let equity = initialCapital; let bench = 1000;
  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now); d.setDate(d.getDate() - (days - 1 - i));
    const dateStr = `${d.getMonth() + 1}/${d.getDate()}`;
    const noise = Math.sin(seed + i * 2.7 + i * i * 0.03) * 0.008;
    const t = totalReturnPct / (days * 100);
    equity *= (1 + t + noise); bench *= (1 + t * 0.6 + noise * 0.7);
    result.push({ date: dateStr, value: Math.round(equity), benchmark: Math.round(bench) });
  }
  return result;
}
function generateDailyPnl(days = 60): DailyPnl[] {
  const result: DailyPnl[] = [];
  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now); d.setDate(d.getDate() - (days - 1 - i));
    const dateStr = `${d.getMonth() + 1}/${d.getDate()}`;
    const pnl = Math.round(Math.sin(i * 1.7 + i * i * 0.05) * 1500 + (i < 30 ? 200 : -100) + Math.sin(i * 3.1) * 800);
    result.push({ date: dateStr, pnl });
  }
  return result;
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
  const [realEquity, setRealEquity] = useState<{ date: string; equity: number }[]>([]);
  const [stopLoss, setStopLoss] = useState<StopLossStatus | null>(null);

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
      const res = await portfolioApi.getEquityHistory(60);
      if (res.data && Array.isArray(res.data) && res.data.length > 0) {
        flushSync(() => { setRealEquity(res.data); setLoadingEquity(false); });
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

  const equityCurve: EquityPoint[] = useMemo(() => {
    if (realEquity.length > 0) {
      return realEquity.map(p => ({ date: p.date.slice(5), value: p.equity, benchmark: 0 }));
    }
    return generateEquityCurve(initialCap, totalReturnPct, 60);
  }, [realEquity, initialCap, totalReturnPct]);

  const maxDrawdown = useMemo(() => calcMaxDrawdown(equityCurve), [equityCurve]);
  const dailyPnlData = useMemo(() => generateDailyPnl(60), []);
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

  // 图表常量
  const G = 'rgba(255,255,255,0.04)';
  const A = 'var(--agent-text-dim, #6a7d9b)';
  const GOLD = '#f0b90b'; const GREEN = '#2ecc71'; const RED = '#e74c3c';

  return (
    <div className="cp-page">
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

      {/* ═══ 资产 Hero 卡片 ═══ */}
      {loadingSummary ? <SkeletonHero /> : summary && (
        <div className="cp-hero-card">
          <div className="cp-hero-left">
            <div className="cp-hero-label">{t('portfolio.totalAsset')}</div>
            <div className="cp-hero-value">¥{fmtMoney(totalAsset)}</div>
            <div className={`cp-hero-change ${totalPnl >= 0 ? 'up' : 'down'}`}>
              <i className={`fas fa-caret-${totalPnl >= 0 ? 'up' : 'down'}`} />
              {totalPnl >= 0 ? '+' : ''}¥{fmtMoneyShort(Math.abs(totalPnl))}
              <span style={{ fontWeight: 400, fontSize: 12 }}>
                ({total_return_pct >= 0 ? '+' : ''}{total_return_pct.toFixed(2)}%)
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

      {/* ═══ 风险仪表 4 连 ═══ */}
      {loadingSummary ? <SkeletonRisk /> : summary && (
        <div className="cp-risk-strip">
          <RiskCard icon="fa-gauge-high" label={t('portfolio.positionRatio')}
            value={`${posRatio.toFixed(0)}%`}
            sub={posRatio > 80 ? '重仓' : posRatio > 50 ? '中性' : '轻仓'}
            level={posRatio > 80 ? 'danger' : posRatio > 50 ? 'warn' : 'safe'}
            ringColor={posRatio > 80 ? RED : posRatio > 50 ? GOLD : GREEN} ringPct={posRatio} />
          <RiskCard icon="fa-arrow-trend-down" label="最大回撤"
            value={`-${maxDrawdown.toFixed(1)}%`} sub="历史最大"
            level={maxDrawdown > 15 ? 'danger' : maxDrawdown > 8 ? 'warn' : 'safe'}
            ringColor={maxDrawdown > 15 ? RED : maxDrawdown > 8 ? GOLD : GREEN} ringPct={Math.min(maxDrawdown * 2, 100)} />
          <RiskCard icon="fa-bullseye" label={t('analytics.winRate')}
            value={`${win_rate.toFixed(1)}%`} sub={`${positions.length} 只持仓`}
            level={win_rate > 60 ? 'safe' : win_rate > 40 ? 'warn' : 'danger'}
            ringColor={win_rate > 60 ? GREEN : win_rate > 40 ? GOLD : RED} ringPct={win_rate} />
          <RiskCard icon="fa-wave-square" label="年化波动"
            value={`${volatility.toFixed(1)}%`} sub="60日滚动"
            level={volatility > 25 ? 'danger' : volatility > 15 ? 'warn' : 'safe'}
            ringColor={volatility > 25 ? RED : volatility > 15 ? GOLD : GREEN} ringPct={Math.min(volatility * 2.5, 100)} />
        </div>
      )}

      {/* ═══ 止损监控卡片 ═══ */}
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
                  <th>股票</th><th className="right">现价</th><th className="right">浮盈</th>
                  <th className="right">距离%</th><th className="right">最近规则</th><th className="right">风险</th>
                </tr></thead>
                <tbody>
                  {stopLoss.positions.map(p => {
                    const danger = p.nearest_trigger?.danger_level || 'no_rules';
                    const ruleLabels: Record<string, string> = {
                      rul0a_break_low: '破底', rul0b_cost_stop: '成本',
                      rul1_sector: '板块', rul2_iron: '铁律2', rul3_dynamic: '动态',
                    };
                    const ruleLabel = ruleLabels[p.nearest_trigger?.rule || ''] || p.nearest_trigger?.rule || '';
                    return (
                      <tr key={p.symbol} className={danger === 'triggered' ? 'sl-row-danger' : danger === 'critical' ? 'sl-row-critical' : ''}>
                        <td className="mono bold">{p.symbol}</td>
                        <td className="num mono right">¥{p.current_price.toFixed(2)}</td>
                        <td className={`num right ${p.float_pnl_pct >= 0 ? 'pnl-up' : 'pnl-down'}`}>
                          {p.float_pnl_pct >= 0 ? '+' : ''}{p.float_pnl_pct.toFixed(2)}%</td>
                        <td className="num mono right">
                          {p.nearest_trigger?.distance_pct != null
                            ? `${p.nearest_trigger.distance_pct >= 0 ? '+' : ''}${p.nearest_trigger.distance_pct.toFixed(2)}%` : '-'}</td>
                        <td className="num dim right">{ruleLabel}</td>
                        <td className="num right">
                          <span className={`cp-sl-badge ${danger}`}>
                            {danger === 'triggered' ? '🔴触发' : danger === 'critical' ? '🟠危急' : danger === 'warning' ? '🟡警告' : danger === 'caution' ? '⚪关注' : '🟢安全'}</span>
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

      {/* ═══ 图表行 ═══ */}
      <div className="cp-row-charts">
        {/* 权益曲线 */}
        <div className="cp-panel" style={{ minHeight: 280 }}>
          <div className="cp-panel-header">
            <i className="fas fa-chart-area" />
            <span className="cp-panel-title">{t('portfolio.equityCurve')}</span>
            {realEquity.length === 0 && <span style={{ fontSize: 10, color: A, marginLeft: 'auto' }}>{t('portfolio.vsBenchmark')}</span>}
            <button className="cp-refresh-btn" onClick={refreshEquity} title="刷新曲线" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-sync-alt ${loadingEquity ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-panel-body" style={{ padding: '4px 8px 8px' }}>
            {loadingEquity ? <SkeletonBlock h={220} /> : (
              <div className="cp-chart-h240">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={equityCurve}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={GOLD} stopOpacity={0.18} />
                        <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={G} />
                    <XAxis dataKey="date" stroke={A} fontSize={10} tickLine={false} interval={Math.max(0, Math.floor(equityCurve.length / 6) - 1)} />
                    <YAxis stroke={A} fontSize={10} tickLine={false} domain={['auto', 'auto']} tickFormatter={(v: number) => v >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : String(v)} width={50} />
                    <Tooltip content={<ETip />} />
                    <Area type="monotone" dataKey="value" name="账户权益" stroke={GOLD} strokeWidth={2} fill="url(#eqGrad)" dot={false} activeDot={{ r: 4, fill: GOLD, strokeWidth: 0 }} />
                    {realEquity.length === 0 && <Line type="monotone" dataKey="benchmark" name="上证基准" stroke="rgba(141,155,181,0.5)" strokeWidth={1} strokeDasharray="4 4" dot={false} />}
                    <Legend wrapperStyle={{ fontSize: 10, color: A }} iconType="line" />
                  </ComposedChart>
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
            <button className="cp-refresh-btn" onClick={refreshSummary} title="刷新持仓" style={{ marginLeft: 'auto' }}>
              <i className={`fas fa-sync-alt ${loadingSummary ? 'fa-spin' : ''}`} />
            </button>
          </div>
          <div className="cp-table-wrap" style={{ maxHeight: 340 }}>
            {loadingSummary ? <SkeletonTable rows={5} /> : (
              <table className="cp-table">
                <thead><tr>
                  <th>{t('portfolio.symbol')}</th><th>{t('portfolio.name')}</th>
                  <th className="right">{t('portfolio.volume')}</th><th className="right">{t('portfolio.avgPrice')}</th>
                  <th className="right">{t('portfolio.currentPrice')}</th>
                  <th className={`right sortable ${sortKey === 'market_value' ? 'sorted' : ''}`} onClick={() => handleSort('market_value')}>
                    {t('portfolio.marketValue')} {sortKey === 'market_value' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'floating_pnl' ? 'sorted' : ''}`} onClick={() => handleSort('floating_pnl')}>
                    {t('portfolio.profitAmount')} {sortKey === 'floating_pnl' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'floating_pnl_pct' ? 'sorted' : ''}`} onClick={() => handleSort('floating_pnl_pct')}>
                    {t('portfolio.profitRate')} {sortKey === 'floating_pnl_pct' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className={`right sortable ${sortKey === 'weight' ? 'sorted' : ''}`} onClick={() => handleSort('weight')}>
                    {t('portfolio.weight')} {sortKey === 'weight' && <i className={`fas fa-sort-${sortDir === 'desc' ? 'down' : 'up'}`} style={{ fontSize: 9 }} />}</th>
                  <th className="right">风险</th>
                </tr></thead>
                <tbody>
                  {sortedPositions.length === 0 ? (
                    <tr><td colSpan={10}><div className="cp-empty"><i className="fas fa-chart-pie" /><span>{t('portfolio.noPositions')}</span></div></td></tr>
                  ) : sortedPositions.map(pos => {
                    const isUp = (pos.floating_pnl || 0) >= 0;
                    const weight = totalAsset > 0 ? (pos.market_value / totalAsset) * 100 : 0;
                    const isHeavy = weight > 30; const isWarn = weight > 20 && weight <= 30;
                    return (
                      <tr key={pos.symbol} className={isHeavy ? 'risk-high' : isWarn ? 'risk-warn' : ''}>
                        <td className="symbol mono">{pos.symbol}</td>
                        <td className="bold">{cleanStockName(pos.name, pos.symbol)}</td>
                        <td className="num mono dim">{pos.volume.toLocaleString()}</td>
                        <td className="num mono">¥{(pos.avg_price || 0).toFixed(2)}</td>
                        <td className="num mono">¥{(pos.current_price || 0).toFixed(2)}</td>
                        <td className="num mono bold">¥{fmtMoney(pos.market_value)}</td>
                        <td className={`num mono ${isUp ? 'pnl-up' : 'pnl-down'}`}>{isUp ? '+' : ''}¥{(pos.floating_pnl || 0).toFixed(2)}</td>
                        <td className="num"><span className={`cp-pnl-tag ${isUp ? 'up' : 'down'}`}>{isUp ? '+' : ''}{(pos.floating_pnl_pct || 0).toFixed(2)}%</span></td>
                        <td className="num"><span className="cp-wt-tag">{weight.toFixed(1)}%</span></td>
                        <td className="num">
                          {isHeavy ? <span className="cp-risk-badge danger"><i className="fas fa-exclamation-triangle" style={{ fontSize: 8 }} /> 重仓</span>
                           : isWarn ? <span className="cp-risk-badge warn">偏重</span>
                           : <span style={{ color: 'var(--agent-text-dim)', fontSize: 10 }}>-</span>}</td>
                      </tr>
                    );
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

      {/* ═══ FAB ═══ */}
      <div className="cp-fab">
        {fabOpen && (
          <div className="cp-fab-menu">
            <button className="cp-fab-item" onClick={() => {}}><i className="fas fa-plus-circle" /> 手工下单</button>
            <button className="cp-fab-item" onClick={() => {}}><i className="fas fa-arrow-right-to-bracket" /> 转入资金</button>
            <button className="cp-fab-item danger" onClick={() => {}}><i className="fas fa-skull" /> 紧急平仓</button>
          </div>
        )}
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

function HeroKpi({ label, value, sub, trend }: { label: string; value: string; sub?: string; trend?: 'up' | 'down' }) {
  return (
    <div className="cp-hero-kpi">
      <div className="cp-hero-kpi-label">{label}</div>
      <div className={`cp-hero-kpi-value ${trend === 'up' ? 'up' : trend === 'down' ? 'down' : ''}`}>{value}</div>
      {sub && <div className="cp-hero-kpi-sub">{sub}</div>}
    </div>
  );
}

function RiskCard({ icon, label, value, sub, level, ringColor, ringPct }: {
  icon: string; label: string; value: string; sub: string;
  level: 'safe' | 'warn' | 'danger'; ringColor: string; ringPct: number;
}) {
  return (
    <div className="cp-risk-card">
      <div className="cp-risk-gauge" style={{ background: `conic-gradient(${ringColor} ${ringPct * 3.6}deg, rgba(255,255,255,0.04) ${ringPct * 3.6}deg)` }}>
        <div style={{ width: 40, height: 40, borderRadius: '50%', background: 'var(--agent-bg-card, #0d121b)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <i className={`fas ${icon}`} style={{ color: ringColor, opacity: 0.8 }} />
        </div>
      </div>
      <div className="cp-risk-info">
        <div className="cp-risk-label">{label}</div>
        <div className={`cp-risk-value ${level}`}>{value}</div>
        <div className="cp-risk-sub">{sub}</div>
      </div>
    </div>
  );
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
function ETip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return <div className="cp-tip-box"><div className="cp-tip-label">{label}</div>{(payload as Array<{ name: string; value: number; color: string }>).map((p, i) => <div key={i} className="cp-tip-row"><span className="l" style={{ color: p.color }}>{p.name}</span><span className="v">¥{p.value.toLocaleString()}</span></div>)}</div>;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PieTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  return <div className="cp-tip-box"><div className="cp-tip-label">{p.name}</div><div className="cp-tip-row"><span className="l">市值</span><span className="v">¥{p.value.toLocaleString()}</span></div></div>;
}
