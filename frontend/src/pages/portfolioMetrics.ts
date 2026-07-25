// ── 绩效指标纯函数（客户端计算，无后端依赖）──

export interface MetricCard {
  label: string;
  value: string;
  sub?: string;
  trend?: 'up' | 'down' | 'neutral';
}

export interface PnlContribution {
  symbol: string;
  name: string;
  totalPnl: number;
}

export interface PeriodReturn {
  period: string;
  returnPct: number;
}

interface EquityPoint {
  date: string;
  equity: number;
  daily_pnl?: number;
}

interface DailyStockPnl {
  symbol: string;
  name?: string;
  float_pnl: number;
  realized_pnl: number;
}

interface PnlBreakdownItem {
  date: string;
  stocks: DailyStockPnl[];
}

// ── 夏普比率 ──
export function computeSharpeRatio(equityHistory: EquityPoint[]): number | null {
  if (equityHistory.length < 20) return null;

  const dailyReturns: number[] = [];
  for (let i = 1; i < equityHistory.length; i++) {
    const prev = equityHistory[i - 1].equity;
    if (prev <= 0) continue;
    dailyReturns.push((equityHistory[i].equity - prev) / prev);
  }
  if (dailyReturns.length < 10) return null;

  const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
  const variance =
    dailyReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) /
    dailyReturns.length;
  const std = Math.sqrt(variance);
  if (std === 0) return null;

  return (mean / std) * Math.sqrt(252);
}

// ── 月度收益 ──
export function computeMonthlyReturns(
  equityHistory: EquityPoint[],
  months = 6,
): PeriodReturn[] {
  const byMonth: Record<string, EquityPoint[]> = {};
  for (const pt of equityHistory) {
    const key = pt.date.slice(0, 7); // YYYY-MM
    if (!byMonth[key]) byMonth[key] = [];
    byMonth[key].push(pt);
  }

  const result: PeriodReturn[] = [];
  for (const [period, points] of Object.entries(byMonth)) {
    if (points.length < 2) continue;
    const sorted = points.sort((a, b) => a.date.localeCompare(b.date));
    const first = sorted[0].equity;
    const last = sorted[sorted.length - 1].equity;
    if (first <= 0) continue;
    result.push({ period, returnPct: ((last - first) / first) * 100 });
  }

  result.sort((a, b) => b.period.localeCompare(a.period));
  return result.slice(0, months);
}

// ── 季度收益 ──
export function computeQuarterlyReturns(
  equityHistory: EquityPoint[],
  quarters = 4,
): PeriodReturn[] {
  const byQuarter: Record<string, EquityPoint[]> = {};
  for (const pt of equityHistory) {
    const d = new Date(pt.date);
    const q = Math.floor(d.getMonth() / 3) + 1;
    const key = `${d.getFullYear()}-Q${q}`;
    if (!byQuarter[key]) byQuarter[key] = [];
    byQuarter[key].push(pt);
  }

  const result: PeriodReturn[] = [];
  for (const [period, points] of Object.entries(byQuarter)) {
    if (points.length < 5) continue;
    const sorted = points.sort((a, b) => a.date.localeCompare(b.date));
    const first = sorted[0].equity;
    const last = sorted[sorted.length - 1].equity;
    if (first <= 0) continue;
    result.push({ period, returnPct: ((last - first) / first) * 100 });
  }

  result.sort((a, b) => b.period.localeCompare(a.period));
  return result.slice(0, quarters);
}

// ── 今日基准对比 ──
export function computeBenchmarkDelta(
  accountReturnPct: number,
  indexChangePct: number,
): { delta: number; label: 'outperform' | 'underperform' | 'neutral' } {
  const delta = accountReturnPct - indexChangePct;
  return {
    delta,
    label: delta > 0.01 ? 'outperform' : delta < -0.01 ? 'underperform' : 'neutral',
  };
}

// ── 个股盈亏贡献排名 ──
export function aggregatePnlContributions(
  breakdowns: PnlBreakdownItem[],
  topN = 10,
): PnlContribution[] {
  const map: Record<string, { totalPnl: number; name: string }> = {};

  for (const day of breakdowns) {
    for (const s of day.stocks) {
      if (!map[s.symbol]) {
        map[s.symbol] = { totalPnl: 0, name: s.name || s.symbol };
      }
      map[s.symbol].totalPnl += (s.float_pnl || 0) + (s.realized_pnl || 0);
    }
  }

  return Object.entries(map)
    .map(([symbol, { totalPnl, name }]) => ({ symbol, name, totalPnl }))
    .sort((a, b) => Math.abs(b.totalPnl) - Math.abs(a.totalPnl))
    .slice(0, topN);
}
