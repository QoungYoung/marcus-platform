import { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, AreaChart, Area, ComposedChart, Legend,
} from 'recharts';
import { portfolioApi, marketApi } from '../api/client';
import '../styles/agent-theme.css';
import '../styles/portfolio-page.css';

// ── 类型定义 ──
interface Position {
  symbol: string;
  name: string;
  volume: number;
  avg_price: number;
  current_price: number;
  market_value: number;
  floating_pnl: number;
  floating_pnl_pct: number;
}

interface Account {
  initial_capital: number;
  available_cash: number;
  frozen_cash?: number;
  position_value: number;
  total_asset: number;
  realized_pnl: number;
  float_pnl: number;
  total_pnl: number;
  position_ratio: number;
  positions: Position[];
}

interface PortfolioSummary {
  account: Account;
  total_return: number;
  total_return_pct: number;
  win_rate: number;
}

interface EquityPoint {
  date: string;
  value: number;
  benchmark: number;
}

interface DailyPnl {
  date: string;
  pnl: number;
}

interface KlineBar {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  ma5?: number;
  ma10?: number;
}

// ── 工具函数 ──
function fmtMoney(val: number): string {
  const abs = Math.abs(val);
  if (abs >= 1e8) return `${(val / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(val / 1e4).toFixed(2)}万`;
  return val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtMoneyShort(val: number): string {
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e8) return `${sign}¥${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}¥${(abs / 1e4).toFixed(1)}万`;
  return `${sign}¥${abs.toFixed(0)}`;
}

function cleanStockName(name: string | undefined, symbol: string): string {
  if (!name) return symbol;
  return name.replace(/^(SH|SZ|BJ)\d+/, '').trim() || symbol;
}

// ── 确定性 Mock 数据生成 ──
function generateEquityCurve(initialCapital: number, totalReturnPct: number, days = 60): EquityPoint[] {
  const result: EquityPoint[] = [];
  const seed = Math.abs(totalReturnPct) * 1000 + initialCapital * 0.01;
  let equity = initialCapital;
  let bench = 1000;

  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() - (days - 1 - i));
    const dateStr = `${d.getMonth() + 1}/${d.getDate()}`;

    const noise = Math.sin(seed + i * 2.7 + i * i * 0.03) * 0.008;
    const dailyTrend = totalReturnPct / (days * 100);
    equity *= (1 + dailyTrend + noise);
    bench *= (1 + dailyTrend * 0.6 + noise * 0.7);
    result.push({ date: dateStr, value: Math.round(equity), benchmark: Math.round(bench) });
  }
  return result;
}

function generateDailyPnl(days = 60): DailyPnl[] {
  const result: DailyPnl[] = [];
  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() - (days - 1 - i));
    const dateStr = `${d.getMonth() + 1}/${d.getDate()}`;
    const noise = Math.sin(i * 1.7 + i * i * 0.05) * 1500;
    const trend = (i < 30 ? 200 : -100);
    const pnl = Math.round(noise + trend + (Math.sin(i * 3.1) * 800));
    result.push({ date: dateStr, pnl });
  }
  return result;
}

function computeMA(data: KlineBar[], period: number): (number | undefined)[] {
  const result: (number | undefined)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(undefined); continue; }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j].close;
    result.push(Number((sum / period).toFixed(2)));
  }
  return result;
}

// ── 主组件 ──
export default function PortfolioPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<PortfolioSummary | null>(null);
  const [klineData, setKlineData] = useState<KlineBar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate] = useState<Date>(new Date());

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      portfolioApi.getSummary(),
      marketApi.getKline('000001.SH', { limit: 60 }).catch(() => null),
    ])
      .then(([portfolioRes, klineRes]) => {
        if (cancelled) return;
        setData(portfolioRes.data);

        if (klineRes?.data?.length) {
          const bars: KlineBar[] = klineRes.data.map((item: Record<string, unknown>) => ({
            date: String(item.trade_date || item.date || '').slice(4),
            open: Number(item.open ?? 0),
            close: Number(item.close ?? 0),
            high: Number(item.high ?? 0),
            low: Number(item.low ?? 0),
            volume: Number(item.vol ?? item.volume ?? 0),
          })).filter((b: KlineBar) => b.close > 0);
          const ma5 = computeMA(bars, 5);
          const ma10 = computeMA(bars, 10);
          bars.forEach((b, i) => { b.ma5 = ma5[i]; b.ma10 = ma10[i]; });
          setKlineData(bars);
        } else {
          setKlineData(generateMockKline(60));
        }
        setLoading(false);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setLoading(false);
      });

    return () => { cancelled = true; };
  }, []);

  // ── 派生数据 ──
  const equityCurve = useMemo(() => {
    if (!data) return [];
    return generateEquityCurve(data.account.initial_capital, data.total_return_pct, 60);
  }, [data]);

  const dailyPnlData = useMemo(() => generateDailyPnl(60), []);

  // ── 加载 / 错误态 ──
  if (loading) {
    return (
      <div className="pp-loading">
        <i className="fas fa-spinner fa-spin" />
        <span>{t('common.loading')}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="pp-page">
        <div className="pp-error">
          <div className="pp-error-inner">
            <i className="fas fa-exclamation-triangle" />
            {t('common.error')}: {error}
          </div>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { account, total_return, total_return_pct } = data;
  const positions = account?.positions || [];
  const totalAssetValue = account?.total_asset || 0;
  const cashValue = account?.available_cash || 0;
  const frozenValue = account?.frozen_cash || 0;
  const positionValue = account?.position_value || 0;
  const totalPnl = account?.total_pnl || 0;
  const realizedPnl = account?.realized_pnl || 0;
  const floatPnl = account?.float_pnl || 0;
  const initialCapital = account?.initial_capital || 0;
  const winRate = data?.win_rate || 0;

  const cashPct = totalAssetValue > 0 ? (cashValue / totalAssetValue) * 100 : 0;
  const frozenPct = totalAssetValue > 0 ? (frozenValue / totalAssetValue) * 100 : 0;
  const positionPct = totalAssetValue > 0 ? (positionValue / totalAssetValue) * 100 : 0;

  const sortedPositions = [...positions].sort((a, b) => (b.market_value || 0) - (a.market_value || 0));

  const CHART_GRID = 'rgba(255,255,255,0.04)';
  const CHART_AXIS = 'var(--agent-text-dim, #6a7d9b)';
  const CHART_TOOLTIP_BG = 'var(--agent-bg-card, #0d121b)';
  const CHART_TOOLTIP_BORDER = '1px solid rgba(240,185,11,0.12)';
  const GOLD = '#f0b90b';
  const GREEN = '#2ecc71';
  const RED = '#e74c3c';

  return (
    <div className="pp-page">
      {/* ═══ 头部 ═══ */}
      <header className="pp-header">
        <div className="pp-header-left">
          <div className="pp-header-icon"><i className="fas fa-wallet" /></div>
          <div>
            <h1 className="pp-header-title">{t('portfolio.title')}</h1>
            <div className="pp-header-meta">
              <span className="pp-live-dot" />
              <span className="pp-update-time">{t('common.refresh')}: {lastUpdate.toLocaleTimeString()}</span>
            </div>
          </div>
        </div>
      </header>

      {/* ═══ KPI 6连 ═══ */}
      <div className="pp-kpi-row">
        <KpiCard label={t('portfolio.totalAsset')} value={`¥${fmtMoney(totalAssetValue)}`} icon="fa-coins" />
        <KpiCard label={t('portfolio.availableCash')} value={`¥${fmtMoney(cashValue)}`} icon="fa-hand-holding-usd" sub={`${cashPct.toFixed(1)}%`} />
        <KpiCard label={t('portfolio.positionValue')} value={`¥${fmtMoney(positionValue)}`} icon="fa-chart-bar" sub={`${(account?.position_ratio || 0).toFixed(1)}%`} />
        <KpiCard label={t('portfolio.totalPnL')} value={`${totalPnl >= 0 ? '+' : ''}¥${fmtMoney(Math.abs(totalPnl))}`} icon="fa-chart-line" trend={totalPnl >= 0 ? 'up' : 'down'} />
        <KpiCard label={t('portfolio.totalReturn')} value={`${total_return_pct >= 0 ? '+' : ''}${(total_return_pct || 0).toFixed(2)}%`} icon="fa-percentage" trend={total_return_pct >= 0 ? 'up' : 'down'} />
        <KpiCard label={t('portfolio.initialCapital')} value={`¥${fmtMoney(initialCapital)}`} icon="fa-piggy-bank" gold />
      </div>

      {/* ═══ 图表行 1：权益曲线(大) + 每日盈亏(小) ═══ */}
      <div className="pp-row-charts">
        {/* 权益曲线 */}
        <div className="pp-panel" style={{ minHeight: 300 }}>
          <div className="pp-panel-header">
            <i className="fas fa-chart-area" />
            <span className="pp-panel-title">{t('portfolio.equityCurve')}</span>
            <span style={{ fontSize: 10, color: 'var(--agent-text-dim)', marginLeft: 'auto' }}>
              {t('portfolio.vsBenchmark')}
            </span>
          </div>
          <div className="pp-panel-body" style={{ padding: '8px 8px 12px' }}>
            <div className="pp-chart-wrap full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={equityCurve}>
                  <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={GOLD} stopOpacity={0.2} />
                      <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="benchGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="rgba(255,255,255,0.1)" stopOpacity={0.15} />
                      <stop offset="100%" stopColor="rgba(255,255,255,0)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="date" stroke={CHART_AXIS} fontSize={10} tickLine={false} interval="preserveStartEnd" />
                  <YAxis
                    stroke={CHART_AXIS} fontSize={10} tickLine={false}
                    tickFormatter={(v: number) => v >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : v}
                    width={55}
                  />
                  <Tooltip content={<EquityTooltip />} />
                  <Area
                    type="monotone" dataKey="value" name="账户权益"
                    stroke={GOLD} strokeWidth={2} fill="url(#equityGrad)"
                    dot={false} activeDot={{ r: 4, fill: GOLD, strokeWidth: 0 }}
                  />
                  <Line
                    type="monotone" dataKey="benchmark" name="上证指数(拟合)"
                    stroke="rgba(141,155,181,0.6)" strokeWidth={1.2} strokeDasharray="4 4"
                    dot={false}
                  />
                  <Legend
                    wrapperStyle={{ fontSize: 11, color: 'var(--agent-text-dim)' }}
                    iconType="line"
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* 每日盈亏 / 胜率 */}
        <div className="pp-panel" style={{ minHeight: 300 }}>
          <div className="pp-panel-header">
            <i className="fas fa-chart-bar" />
            <span className="pp-panel-title">{t('portfolio.dailyPnL')}</span>
            <span style={{ fontSize: 10, color: CHART_AXIS, marginLeft: 'auto' }}>
              {t('analytics.winRate')}: {winRate.toFixed(1)}%
            </span>
          </div>
          <div className="pp-panel-body" style={{ padding: '8px 8px 12px' }}>
            <div className="pp-chart-wrap full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={dailyPnlData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="date" stroke={CHART_AXIS} fontSize={10} tickLine={false} interval="preserveStartEnd" />
                  <YAxis
                    stroke={CHART_AXIS} fontSize={10} tickLine={false}
                    tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}
                    width={45}
                  />
                  <Tooltip content={<DailyPnlTooltip />} />
                  <Bar dataKey="pnl" radius={[2, 2, 0, 0]} maxBarSize={6}>
                    {dailyPnlData.map((entry, i) => (
                      <Cell key={i} fill={entry.pnl >= 0 ? GREEN : RED} fillOpacity={0.75} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>

      {/* ═══ 图表行 2：大盘K线(等宽) + 资产配置/P&L ═══ */}
      <div className="pp-row-charts">
        {/* 大盘K线 */}
        <div className="pp-panel" style={{ minHeight: 300 }}>
          <div className="pp-panel-header">
            <i className="fas fa-chart-line" />
            <span className="pp-panel-title">{t('portfolio.indexKline')}</span>
            <span style={{ fontSize: 10, color: CHART_AXIS, marginLeft: 8 }}>上证指数</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, fontSize: 10, color: CHART_AXIS }}>
              <span style={{ color: '#3498db' }}>━ MA5</span>
              <span style={{ color: '#e67e22' }}>━ MA10</span>
            </div>
          </div>
          <div className="pp-panel-body" style={{ padding: '8px 8px 12px' }}>
            <div className="pp-chart-wrap full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={klineData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="date" stroke={CHART_AXIS} fontSize={10} tickLine={false} interval="preserveStartEnd" />
                  <YAxis
                    stroke={CHART_AXIS} fontSize={10} tickLine={false}
                    tickFormatter={(v: number) => v.toFixed(0)}
                    width={55} domain={['dataMin - 50', 'dataMax + 50']}
                  />
                  <Tooltip content={<KlineTooltip />} />
                  {/* 收盘价面积 */}
                  <Area
                    type="monotone" dataKey="close" name="收盘价"
                    stroke={GOLD} strokeWidth={1.5} fillOpacity={0}
                    dot={false}
                  />
                  {/* MA5 */}
                  <Line
                    type="monotone" dataKey="ma5" name="MA5"
                    stroke="#3498db" strokeWidth={1.2} dot={false}
                    connectNulls
                  />
                  {/* MA10 */}
                  <Line
                    type="monotone" dataKey="ma10" name="MA10"
                    stroke="#e67e22" strokeWidth={1.2} dot={false}
                    connectNulls
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* 资产配置 + P&L 概览 */}
        <div className="pp-panel" style={{ minHeight: 300 }}>
          <div className="pp-panel-header">
            <i className="fas fa-balance-scale" />
            <span className="pp-panel-title">{t('portfolio.assetAllocation')}</span>
          </div>
          <div className="pp-panel-body">
            <div className="pp-alloc-bar-wrap">
              {cashPct > 0 && <div className="pp-alloc-seg cash" style={{ width: `${cashPct}%` }}>{cashPct > 12 ? `${cashPct.toFixed(0)}%` : ''}</div>}
              {positionPct > 0 && <div className="pp-alloc-seg position" style={{ width: `${positionPct}%` }}>{positionPct > 12 ? `${positionPct.toFixed(0)}%` : ''}</div>}
              {frozenPct > 0 && <div className="pp-alloc-seg frozen" style={{ width: `${frozenPct}%` }}>{frozenPct > 12 ? `${frozenPct.toFixed(0)}%` : ''}</div>}
            </div>
            <div className="pp-alloc-legend">
              <AllocLegend cls="cash" label={t('portfolio.availableCash')} value={`¥${fmtMoney(cashValue)}`} pct={cashPct} />
              <AllocLegend cls="position" label={t('portfolio.positionValue')} value={`¥${fmtMoney(positionValue)}`} pct={positionPct} />
              {frozenValue > 0 && <AllocLegend cls="frozen" label={t('portfolio.frozenCash')} value={`¥${fmtMoney(frozenValue)}`} pct={frozenPct} />}
            </div>

            <div className="pp-pnl-divider" style={{ margin: '16px 0 10px' }} />

            {/* P&L 概览 */}
            <div className="pp-pnl-overview">
              <PnlRow label={t('portfolio.realizedPnL')} amount={realizedPnl} />
              <PnlRow label={t('portfolio.floatingPnL')} amount={floatPnl} />
              <div className="pp-pnl-divider" />
              <PnlRow label={t('portfolio.totalPnL')} amount={totalPnl} />
              <PnlRow label={t('portfolio.cumulativeReturn')} amount={total_return} showPct pctValue={total_return_pct} />
            </div>
          </div>
        </div>
      </div>

      {/* ═══ 持仓分布 + 持仓明细 同行 ═══ */}
      <div className="pp-row-half">
        {/* 持仓分布 */}
        <div className="pp-panel">
          <div className="pp-panel-header">
            <i className="fas fa-list-ol" />
            <span className="pp-panel-title">{t('portfolio.topHoldings')}</span>
          </div>
          <div className="pp-panel-body">
            {sortedPositions.length === 0 ? (
              <div className="pp-empty"><i className="fas fa-chart-pie" /><span>{t('portfolio.noPositions')}</span></div>
            ) : (
              sortedPositions.slice(0, 8).map(pos => {
                const isUp = (pos.floating_pnl || 0) >= 0;
                const barWidth = Math.min((pos.market_value / Math.max(sortedPositions[0]?.market_value || 1, 1)) * 100, 100);
                const weight = positionValue > 0 ? (pos.market_value / positionValue) * 100 : 0;
                return (
                  <div key={pos.symbol} className="pp-holding-item">
                    <span className="pp-holding-name">{cleanStockName(pos.name, pos.symbol)}</span>
                    <span className="pp-holding-code">{pos.symbol}</span>
                    <div className="pp-holding-bar-wrap">
                      <div className={`pp-holding-bar-fill ${isUp ? 'up' : 'down'}`} style={{ width: `${barWidth}%` }} />
                    </div>
                    <span className="pp-holding-pct"><span className="pp-weight-tag">{weight.toFixed(1)}%</span></span>
                    <span className="pp-holding-val">¥{fmtMoney(pos.market_value)}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 持仓明细 */}
        <div className="pp-panel">
          <div className="pp-panel-header">
            <i className="fas fa-table" />
            <span className="pp-panel-title">
              {t('portfolio.positions')}
              {positions.length > 0 && (
                <span style={{ color: CHART_AXIS, fontSize: 11, fontWeight: 400, marginLeft: 6 }}>({positions.length})</span>
              )}
            </span>
          </div>
          <div className="pp-table-wrap" style={{ maxHeight: 360, overflowY: 'auto' }}>
            <table className="pp-table">
              <thead>
                <tr>
                  <th>{t('portfolio.symbol')}</th>
                  <th>{t('portfolio.name')}</th>
                  <th className="right">{t('portfolio.volume')}</th>
                  <th className="right">{t('portfolio.avgPrice')}</th>
                  <th className="right">{t('portfolio.currentPrice')}</th>
                  <th className="right">{t('portfolio.marketValue')}</th>
                  <th className="right">{t('portfolio.profitAmount')}</th>
                  <th className="right">{t('portfolio.profitRate')}</th>
                  <th className="right">{t('portfolio.weight')}</th>
                </tr>
              </thead>
              <tbody>
                {sortedPositions.length === 0 ? (
                  <tr><td colSpan={9}>
                    <div className="pp-empty"><i className="fas fa-chart-pie" /><span>{t('portfolio.noPositions')}</span></div>
                  </td></tr>
                ) : (
                  sortedPositions.map(pos => {
                    const isUp = (pos.floating_pnl || 0) >= 0;
                    const weight = positionValue > 0 ? (pos.market_value / positionValue) * 100 : 0;
                    return (
                      <tr key={pos.symbol}>
                        <td className="symbol mono">{pos.symbol}</td>
                        <td className="bold">{cleanStockName(pos.name, pos.symbol)}</td>
                        <td className="num mono dim">{pos.volume.toLocaleString()}</td>
                        <td className="num mono">¥{(pos.avg_price || 0).toFixed(2)}</td>
                        <td className="num mono">¥{(pos.current_price || 0).toFixed(2)}</td>
                        <td className="num mono bold">¥{(pos.market_value || 0).toFixed(2)}</td>
                        <td className={`num mono ${isUp ? 'pnl-up' : 'pnl-down'}`}>
                          {isUp ? '+' : ''}¥{(pos.floating_pnl || 0).toFixed(2)}
                        </td>
                        <td className="num">
                          <span className={`pp-pnl-badge ${isUp ? 'up' : 'down'}`}>
                            {isUp ? '+' : ''}{(pos.floating_pnl_pct || 0).toFixed(2)}%
                          </span>
                        </td>
                        <td className="num"><span className="pp-weight-tag">{weight.toFixed(1)}%</span></td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════
// 子组件
// ══════════════════════════════════════════════════════

function KpiCard({ label, value, icon, trend, sub, gold }: {
  label: string; value: string; icon: string;
  trend?: 'up' | 'down'; sub?: string; gold?: boolean;
}) {
  return (
    <div className="pp-kpi-card">
      <div className="pp-kpi-label"><i className={`fas ${icon}`} />{label}</div>
      <div className={`pp-kpi-value ${trend === 'up' ? 'up' : trend === 'down' ? 'down' : gold ? 'gold' : ''}`}>
        {value}
      </div>
      {sub && <div className="pp-kpi-sub">{sub}</div>}
    </div>
  );
}

function PnlRow({ label, amount, showPct, pctValue }: {
  label: string; amount: number; showPct?: boolean; pctValue?: number;
}) {
  const isUp = amount >= 0;
  return (
    <div className="pp-pnl-item">
      <span className="pp-pnl-label">{label}</span>
      <div>
        <div className={`pp-pnl-val ${isUp ? 'up' : 'down'}`}>
          {isUp ? '+' : ''}{fmtMoneyShort(Math.abs(amount))}
        </div>
        {showPct && pctValue != null && (
          <div className={`pp-pnl-sub ${isUp ? 'up' : 'down'}`}>
            {pctValue >= 0 ? '+' : ''}{pctValue.toFixed(2)}%
          </div>
        )}
      </div>
    </div>
  );
}

function AllocLegend({ cls, label, value, pct }: { cls: string; label: string; value: string; pct: number }) {
  return (
    <div className="pp-alloc-l-item">
      <div className={`pp-alloc-l-dot ${cls}`} />
      <span className="pp-alloc-l-text">{label}</span>
      <span className="pp-alloc-l-val">{value}</span>
      <span style={{ color: 'var(--agent-text-dim)', fontSize: 10 }}>({pct.toFixed(1)}%)</span>
    </div>
  );
}

// ── 自定义 Tooltip ──
function EquityTooltip({ active, payload, label }: Record<string, unknown>) {
  const { t } = useTranslation();
  if (!active || !payload?.length) return null;
  return (
    <div className="pp-tooltip-box">
      <div className="pp-tooltip-label">{label}</div>
      {(payload as Array<{ name: string; value: number; color: string }>).map((p, i) => (
        <div key={i} className="pp-tooltip-item">
          <span className="label" style={{ color: p.color }}>{p.name}</span>
          <span className="value" style={{ color: 'var(--agent-text-primary)' }}>¥{p.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function DailyPnlTooltip({ active, payload, label }: Record<string, unknown>) {
  if (!active || !payload?.length) return null;
  const pnl = (payload as Array<{ value: number }>)[0]?.value ?? 0;
  const isUp = pnl >= 0;
  return (
    <div className="pp-tooltip-box">
      <div className="pp-tooltip-label">{label}</div>
      <div className="pp-tooltip-item">
        <span className="label">日盈亏</span>
        <span className="value" style={{ color: isUp ? '#2ecc71' : '#e74c3c' }}>
          {isUp ? '+' : ''}¥{pnl.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

function KlineTooltip({ active, payload, label }: Record<string, unknown>) {
  if (!active || !payload?.length) return null;
  const pl = payload as Array<{ dataKey: string; value: number; name: string; color: string }>;
  const findVal = (key: string) => pl.find(p => p.dataKey === key)?.value;
  const open = findVal('open');
  const close = findVal('close');
  const high = findVal('high');
  const low = findVal('low');
  return (
    <div className="pp-tooltip-box">
      <div className="pp-tooltip-label">{label}</div>
      {open != null && <div className="pp-tooltip-item"><span className="label">开</span><span className="value">{open.toFixed(2)}</span></div>}
      {close != null && <div className="pp-tooltip-item"><span className="label">收</span><span className="value" style={{ color: '#f0b90b' }}>{close.toFixed(2)}</span></div>}
      {high != null && <div className="pp-tooltip-item"><span className="label">高</span><span className="value" style={{ color: '#2ecc71' }}>{high.toFixed(2)}</span></div>}
      {low != null && <div className="pp-tooltip-item"><span className="label">低</span><span className="value" style={{ color: '#e74c3c' }}>{low.toFixed(2)}</span></div>}
    </div>
  );
}

// ── 模拟 K 线 ──
function generateMockKline(days = 60): KlineBar[] {
  const result: KlineBar[] = [];
  let price = 3300;
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const dateStr = `${d.getMonth() + 1}/${d.getDate()}`;
    const change = (Math.sin(i * 0.3 + i * i * 0.001) * 25 + (Math.random() - 0.5) * 15);
    const open = price;
    const close = Number((price + change).toFixed(2));
    const high = Number((Math.max(open, close) + Math.random() * 12).toFixed(2));
    const low = Number((Math.min(open, close) - Math.random() * 12).toFixed(2));
    result.push({ date: dateStr, open, close, high, low, volume: 0 });
    price = close;
  }
  const ma5 = computeMA(result, 5);
  const ma10 = computeMA(result, 10);
  result.forEach((b, i) => { b.ma5 = ma5[i]; b.ma10 = ma10[i]; });
  return result;
}
