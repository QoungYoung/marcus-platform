import { useEffect, useState, useCallback, useRef } from 'react';
import { RefreshCw, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { goldenPitApi } from '../api/client';
import '../styles/golden-pit-page.css';

// ── TypeScript interfaces ──

interface IndexStatus {
  fund_code: string;
  index_name: string;
  priority: number;
  tier?: string;
  greed: number;
  close: number;
  percentile: number;
  status: 'normal' | 'warning' | 'golden_pit';
  decline_rate: number;
  change_5?: number;
  change_20?: number;
  trend?: 'declining' | 'bottoming' | 'recovering';
  turning_point_confirmed?: boolean;
  days_rising?: number;
  position_tier?: string | null;
  position_tier_label?: string | null;
  position_weight?: number;
  position_multiplier?: number;
  exit_signal?: string | null;
  exit_reason?: string;
  turning_validation?: string;
  turning_validation_reason?: string;
  signal_quality?: string;
  data_source?: string;
  days_to_pit: number | null;
  eta_date: string | null;
  entry_date: string | null;
  days_in_pit: number | null;
}

interface GoldenPitWindow {
  active: boolean;
  phase?: 'idle' | 'waiting' | 'buying';
  start_date: string | null;
  leading_index: string | null;
  leading_tier?: string | null;
  current_day: number;
  pit_count?: number;
  warning_count?: number;
  turning_count?: number;
  midpoint_date: string | null;
  exit_date: string | null;
}

interface TripleLayer {
  label: string;
  status: string;
  confirmed: boolean;
  details?: string[];
}

interface Prediction {
  next_index: string;
  eta_days: number;
  eta_date: string;
  decline_rate: number;
}

interface MarketFlow {
  name: string;
  direction: string;
  direction_label: string;
  consecutive_days: number;
  cumulative_pp: number;
  current_share?: number;
}

interface CapitalFlow {
  markets: Record<string, MarketFlow>;
  summary: string;
}

interface GlobalMacro {
  liquidity_gate: string;
  sentiment_score: number;
  sentiment_label: string;
  global_trend: string;
  global_macro_coefficient: number;
  summary: string;
  capital_flow?: CapitalFlow;
}

interface GoldenPitStatus {
  as_of: string;
  golden_pit_window: GoldenPitWindow;
  indices: IndexStatus[];
  triple_confirmation: {
    layer1: TripleLayer;
    layer2: TripleLayer;
    layer3: TripleLayer;
  };
  prediction: Prediction | null;
  summary: string;
  global_macro: GlobalMacro;
}

interface TrendData {
  as_of: string;
  series: Record<string, { date: string; greed: number; close: number }[]>;
  indices: Record<string, string>;
}

// ── Constants ──

const STATUS_COLORS: Record<string, string> = {
  normal: '#22c55e',
  warning: '#f97316',
  golden_pit: '#ef4444',
};

const STATUS_LABELS: Record<string, string> = {
  normal: '正常',
  warning: '预警',
  golden_pit: '黄金坑',
};

const INDEX_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'];

// ── Sub-components ──

function Skeleton() {
  return (
    <div className="gp-skeleton">
      <div className="gp-skeleton-bar shimmer" />
      <div className="gp-skeleton-grid">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="gp-skeleton-card shimmer" />
        ))}
      </div>
    </div>
  );
}

function ResonanceBadge({ pitCount }: { pitCount: number }) {
  let label: string;
  let color: string;
  if (pitCount >= 4) { label = `${pitCount}指共振 1.3x`; color = '#22c55e'; }
  else if (pitCount >= 3) { label = `${pitCount}指共振 1.2x`; color = '#22c55e'; }
  else if (pitCount >= 2) { label = `${pitCount}指共振 1.0x`; color = '#94a3b8'; }
  else if (pitCount >= 1) { label = `${pitCount}指共振 0.6x`; color = '#f97316'; }
  else { return null; }
  return (
    <span className="gp-resonance-badge" style={{ background: color, color: '#fff', padding: '1px 8px', borderRadius: 10, fontSize: '0.75rem', marginLeft: 8 }}>
      {label}
    </span>
  );
}

function GoldenPitTimeline({ window: w }: { window: GoldenPitWindow }) {
  const phase = w.phase || 'idle';
  const pitCount = w.pit_count || 0;

  if (phase === 'idle') {
    return (
      <div className="gp-timeline inactive">
        <span className="gp-timeline-status">📍 当前无黄金坑信号</span>
      </div>
    );
  }

  if (phase === 'waiting') {
    return (
      <div className="gp-timeline waiting">
        <div className="gp-timeline-header">
          <span className="gp-timeline-badge">🟠 已入坑 · 等待回升确认</span>
          <span className="gp-timeline-leading">领先: {w.leading_index} ({w.leading_tier})</span>
          <ResonanceBadge pitCount={pitCount} />
        </div>
        <div className="gp-timeline-dates">
          <span>{pitCount}个指数已在黄金坑 / {w.warning_count || 0}个预警</span>
          {w.start_date && <span>首个入坑: {w.start_date}</span>}
        </div>
        <div className="gp-timeline-status-text">
          贪婪值仍在下跌中，需等待连续回升确认拐点后开启买入窗口
        </div>
        <div className="gp-timeline-stages">
          <span className="gp-stage done">① 入坑</span>
          <span className="gp-stage-arrow">→</span>
          <span className="gp-stage active">② 拐点确认</span>
          <span className="gp-stage-arrow">→</span>
          <span className="gp-stage pending">③ 买入窗口</span>
        </div>
      </div>
    );
  }

  return (
    <div className="gp-timeline buying">
      <div className="gp-timeline-header">
        <span className="gp-timeline-badge">🔴 买入窗口</span>
        <span className="gp-timeline-leading">
          {w.leading_index} 拐点确认 (第{w.current_day}天)
        </span>
        <ResonanceBadge pitCount={pitCount} />
      </div>
      <div className="gp-timeline-dates">
        <span>拐点: {w.start_date}</span>
        <span>已确认: {w.turning_count || 0}个指数</span>
        <span>回升: {w.current_day}天</span>
      </div>
      <div className="gp-timeline-status-text">
        加仓节奏: 50% → 75% → 100%
      </div>
      <div className="gp-timeline-stages">
        <span className="gp-stage done">① 入坑</span>
        <span className="gp-stage-arrow">→</span>
        <span className="gp-stage done">② 拐点确认</span>
        <span className="gp-stage-arrow">→</span>
        <span className="gp-stage active">③ 买入窗口</span>
      </div>
    </div>
  );
}

const TREND_ICONS: Record<string, string> = {
  declining: '↓',
  bottoming: '→',
  recovering: '↑',
};

const TREND_COLORS: Record<string, string> = {
  declining: '#ef4444',
  bottoming: '#f97316',
  recovering: '#22c55e',
};

const EXIT_LABELS: Record<string, string> = {
  half_exit: '\u{1F7E1} 减持 50%',
  full_exit: '\u{1F534} 清仓',
  stop_profit: '\u{1F7E0} 止盈',
};

const GLOBAL_TREND_LABELS: Record<string, string> = {
  bullish: '看涨',
  declining: '下行',
  flat: '平稳',
  unknown: '未知',
};

const GLOBAL_TREND_COLORS: Record<string, string> = {
  bullish: '#27AE60',
  declining: '#C0392B',
  flat: '#94a3b8',
  unknown: '#94a3b8',
};

const MARKET_ORDER = ['a_share', 'united_states', 'japan', 'south_korea', 'hong_kong'];

function GlobalMacroCard({ macro }: { macro: GlobalMacro }) {
  const gateOpen = macro.liquidity_gate === 'open';
  const trendLabel = GLOBAL_TREND_LABELS[macro.global_trend] || macro.global_trend;
  const trendColor = GLOBAL_TREND_COLORS[macro.global_trend] || '#94a3b8';
  const coefPct = Math.round(macro.global_macro_coefficient * 100);
  const cf = macro.capital_flow;

  return (
    <div className={`gp-macro-card ${gateOpen ? 'gate-open' : 'gate-closed'}`}>
      <div className="gp-macro-top-row">
        <div className="gp-macro-item gate">
          <span className="gp-macro-icon">{gateOpen ? '\u{1F513}' : '\u{1F512}'}</span>
          <div className="gp-macro-text">
            <span className="gp-macro-label">流动性闸门</span>
            <span className={`gp-macro-value ${gateOpen ? 'text-green' : 'text-red'}`}>
              {gateOpen ? '开启' : '关闭'}
            </span>
          </div>
        </div>
        <div className="gp-macro-item">
          <span className="gp-macro-icon">{'\u{1F4CA}'}</span>
          <div className="gp-macro-text">
            <span className="gp-macro-label">情绪指数</span>
            <span className="gp-macro-value">{macro.sentiment_score.toFixed(0)}</span>
            <span className="gp-macro-sub" style={{ color: macro.sentiment_score <= 20 ? '#C0392B' : macro.sentiment_score >= 80 ? '#27AE60' : 'var(--gp-text-dim)' }}>
              {macro.sentiment_label}
            </span>
          </div>
        </div>
        <div className="gp-macro-item">
          <span className="gp-macro-icon">{'\u{1F30D}'}</span>
          <div className="gp-macro-text">
            <span className="gp-macro-label">全球趋势</span>
            <span className="gp-macro-value" style={{ color: trendColor }}>{trendLabel}</span>
          </div>
        </div>
        <div className="gp-macro-item">
          <span className="gp-macro-icon">{'\u{2696}'}</span>
          <div className="gp-macro-text">
            <span className="gp-macro-label">仓位系数</span>
            <span className={`gp-macro-value ${coefPct < 100 ? 'text-amber' : ''}`}>
              {coefPct}%
            </span>
          </div>
        </div>
        <div className="gp-macro-summary">{macro.summary}</div>
      </div>

      {cf && cf.markets && (
        <div className="gp-capital-flow">
          <div className="gp-capital-flow-header">
            <span className="gp-macro-label">全球资金流向</span>
          </div>
          <div className="gp-flow-markets">
            {MARKET_ORDER.map((key) => {
              const m = cf.markets[key];
              if (!m) return null;
              const isInflow = m.direction === 'inflow';
              const ppAbs = Math.abs(m.cumulative_pp);
              const ppColor = isInflow ? '#27AE60' : '#C0392B';
              const barWidth = Math.min(100, Math.max(8, ppAbs * 10));
              return (
                <div key={key} className="gp-flow-market-chip">
                  <span className="gp-flow-market-name">{m.name}</span>
                  <span className="gp-flow-direction" style={{ color: isInflow ? '#27AE60' : '#C0392B' }}>
                    {isInflow ? '↑' : '↓'}{m.direction_label}
                  </span>
                  <span className="gp-flow-days">{m.consecutive_days}日</span>
                  <div className="gp-flow-bar-wrap">
                    <div
                      className="gp-flow-bar"
                      style={{ width: `${barWidth}%`, background: ppColor }}
                    />
                  </div>
                  <span className="gp-flow-pp" style={{ color: ppColor }}>
                    {isInflow ? '+' : '-'}{ppAbs.toFixed(1)}pp
                  </span>
                </div>
              );
            })}
          </div>
          {cf.summary && (
            <div className="gp-flow-summary">{cf.summary}</div>
          )}
        </div>
      )}
    </div>
  );
}

function IndexStatusCard({ idx }: { idx: IndexStatus }) {
  const color = STATUS_COLORS[idx.status];
  const greedPct = Math.round(idx.greed * 100);
  const trendIcon = idx.trend ? TREND_ICONS[idx.trend] : '';
  const trendColor = idx.trend ? TREND_COLORS[idx.trend] : '';
  const exitLabel = idx.exit_signal ? EXIT_LABELS[idx.exit_signal] : '';
  const sqLabel = idx.signal_quality === 'strong' ? '⭐' : idx.signal_quality === 'good' ? '✅' : '';
  const weightPct = (idx.position_weight != null && idx.position_weight > 0)
    ? `${(idx.position_weight * 100).toFixed(0)}%`
    : '';
  const isDivergent = idx.turning_validation === 'divergent';
  const isGlobalExit = idx.exit_reason?.startsWith('全球');

  return (
    <div className={`gp-index-card ${idx.status}`} style={{ borderColor: color }}>
      <div className="gp-index-card-top">
        <span className="gp-index-name">
          {isDivergent && (
            <span className="gp-divergent-icon" title={idx.turning_validation_reason || '全球趋势背离'}>{'⚠️'}</span>
          )}
          {sqLabel} {idx.index_name}
          {weightPct && (
            <span className="gp-index-weight" title="仓位上限"> 上限{weightPct}</span>
          )}
        </span>
        <span className="gp-index-badge" style={{ background: color }}>
          {STATUS_LABELS[idx.status]}
        </span>
      </div>
      {isDivergent && idx.turning_validation_reason && (
        <div className="gp-divergent-reason">{idx.turning_validation_reason}</div>
      )}
      <div className="gp-index-greed">
        <span className="gp-index-value" style={{ color }}>{idx.greed.toFixed(4)}</span>
        <span className="gp-index-percentile">P{idx.percentile.toFixed(1)}</span>
        {trendIcon && (
          <span className="gp-index-trend" style={{ color: trendColor, marginLeft: 8 }}>
            {trendIcon}
          </span>
        )}
      </div>
      <div className="gp-index-bar-wrap">
        <div className="gp-index-bar">
          <div
            className="gp-index-bar-fill"
            style={{ width: `${greedPct}%`, background: color }}
          />
        </div>
      </div>
      <div className="gp-index-meta">
        {idx.status === 'golden_pit' && idx.entry_date && (
          <span>{idx.entry_date} 入坑 · 第{idx.days_in_pit}天</span>
        )}
        {idx.status === 'warning' && idx.days_to_pit && (
          <span>预计 {idx.eta_date} 入坑 ({idx.days_to_pit}天)</span>
        )}
        {idx.change_5 != null && idx.change_5 !== 0 && (
          <span style={{ color: idx.change_5 > 0 ? '#27AE60' : '#C0392B' }}>
            5日{idx.change_5 > 0 ? '反弹' : '下跌'} {idx.change_5 > 0 ? '+' : ''}{idx.change_5.toFixed(3)}
          </span>
        )}
        {idx.change_5 == null && idx.decline_rate !== 0 && (
          <span>日跌 {idx.decline_rate > 0 ? '+' : ''}{idx.decline_rate.toFixed(3)}</span>
        )}
        {idx.close > 0 && (
          <span className="gp-index-close">{'¥'}{idx.close.toFixed(2)}</span>
        )}
      </div>
      {idx.position_tier_label && idx.tier !== 'drop' && idx.tier !== 'watch' && (
        <div className="gp-index-position" style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: 4 }}>
          {idx.position_tier_label}
        </div>
      )}
      {exitLabel && (
        <div className={`gp-index-exit ${isGlobalExit ? 'global-exit' : 'aq-exit'}`}>
          <span className="gp-exit-source">{isGlobalExit ? '🌍 宏观' : '🇨🇳 A股'}</span>
          <span className="gp-exit-label">{exitLabel}</span>
          <span className="gp-exit-reason">{idx.exit_reason}</span>
        </div>
      )}
    </div>
  );
}

function TripleConfirmation({ conf, prediction }: {
  conf: GoldenPitStatus['triple_confirmation'];
  prediction: Prediction | null;
}) {
  const layers = [conf.layer1, conf.layer2, conf.layer3];

  return (
    <div className="gp-confirmation">
      <h3 className="gp-section-title">三重确认</h3>
      {layers.map((layer) => (
        <div key={layer.label} className={`gp-confirm-row ${layer.confirmed ? 'confirmed' : ''}`}>
          <span className="gp-confirm-icon">{layer.confirmed ? '☑' : '☐'}</span>
          <div className="gp-confirm-text">
            <span className="gp-confirm-label">{layer.label}</span>
            <span className="gp-confirm-status">{layer.status}</span>
          </div>
        </div>
      ))}
      {prediction && (
        <div className="gp-prediction">
          💡 预测: {prediction.next_index} 预计 {prediction.eta_days} 天后入坑 ({prediction.eta_date})
        </div>
      )}
    </div>
  );
}

function TrendChart({ trendData }: { trendData: TrendData | null }) {
  if (!trendData || !trendData.series || Object.keys(trendData.series).length === 0) {
    return (
      <div className="gp-chart">
        <h3 className="gp-section-title">贪婪值趋势</h3>
        <div className="gp-chart-empty">暂无历史数据</div>
      </div>
    );
  }

  // Merge all series by date
  const codes = Object.keys(trendData.series);
  const dateMap: Record<string, Record<string, number | string>> = {};

  codes.forEach((code) => {
    const series = trendData.series[code];
    if (!series) return;
    series.forEach((point) => {
      if (!dateMap[point.date]) dateMap[point.date] = { date: point.date };
      dateMap[point.date][code] = point.greed;
    });
  });

  const chartData = Object.values(dateMap).sort(
    (a, b) => (a.date as string).localeCompare(b.date as string)
  );

  return (
    <div className="gp-chart">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(17,137,249,0.10)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: 'var(--gp-text-dim)' }}
            tickFormatter={(v) => v.slice(5)}
          />
          <YAxis
            domain={[0.2, 0.9]}
            tick={{ fontSize: 10, fill: 'var(--gp-text-dim)' }}
          />
          <Tooltip
            contentStyle={{
              background: 'rgba(255,255,255,0.95)',
              border: '1px solid rgba(231,231,231,0.75)',
              borderRadius: 8,
              fontSize: 12,
              boxShadow: '0 4px 16px rgba(167,216,234,0.4)',
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine y={0.35} stroke="#ef4444" strokeDasharray="4 4" strokeWidth={1.5} />
          <ReferenceLine y={0.40} stroke="#f97316" strokeDasharray="4 4" strokeWidth={1} />
          {codes.map((code, i) => (
            <Line
              key={code}
              type="monotone"
              dataKey={code}
              name={trendData.indices[code] || code}
              stroke={INDEX_COLORS[i % INDEX_COLORS.length]}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="gp-chart-legend-custom">
        <span className="gp-legend-item"><span className="gp-legend-dot" style={{ background: '#ef4444' }} /> 0.35 黄金坑线</span>
        <span className="gp-legend-item"><span className="gp-legend-dot" style={{ background: '#f97316' }} /> 0.40 预警线</span>
      </div>
    </div>
  );
}

// ── Main page ──

export default function GoldenPitPage() {
  const [status, setStatus] = useState<GoldenPitStatus | null>(null);
  const [trendData, setTrendData] = useState<TrendData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showChart, setShowChart] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useGoldenPitBackground(canvasRef);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, historyRes] = await Promise.all([
        goldenPitApi.getStatus(),
        goldenPitApi.getHistory('all', 60),
      ]);
      if (statusRes.data?.code === 0) {
        setStatus(statusRes.data.data);
      } else {
        setError(statusRes.data?.msg || '获取数据失败');
      }
      if (historyRes.data?.code === 0) {
        setTrendData(historyRes.data.data);
      }
    } catch (e: any) {
      const msg = e?.response?.data?.msg || e?.message || '网络请求失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [fetchData]);

  if (loading && !status) {
    return (
      <div className="golden-pit-page">
        <div className="gp-content">
          <div className="gp-header">
            <h1 className="gp-title">黄金坑监测</h1>
            <p className="gp-subtitle">宽基指数情绪三重确认底部检测</p>
          </div>
          <Skeleton />
        </div>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="golden-pit-page">
        <div className="gp-content">
          <div className="gp-header">
            <h1 className="gp-title">黄金坑监测</h1>
          </div>
          <div className="gp-error">
          <AlertTriangle size={48} />
          <p>数据获取失败</p>
          <p className="gp-error-detail">{error}</p>
          <p className="gp-error-hint">请确认已配置 ArkVol API Key（环境变量 ARKVOL_API_KEY 或 ~/.arkvol/arkvol-entry.json）</p>
          <button className="gp-retry-btn" onClick={fetchData}>
            <RefreshCw size={16} /> 重试
          </button>
        </div>
        </div>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="golden-pit-page">
        <div className="gp-content">
          <div className="gp-header">
            <h1 className="gp-title">黄金坑监测</h1>
          </div>
          <div className="gp-error">
            <p>暂无数据</p>
            <button className="gp-retry-btn" onClick={fetchData}>
              <RefreshCw size={16} /> 刷新
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { golden_pit_window: window, indices, triple_confirmation: conf, prediction, summary, as_of, global_macro } = status;
  const sortedIndices = [...indices].sort((a, b) => a.priority - b.priority);

  return (
    <div className="golden-pit-page">
      <canvas ref={canvasRef} id="gp-bg-canvas" />
      <div className="gp-content">
        <div className="gp-header">
        <div>
          <h1 className="gp-title">黄金坑监测</h1>
          <p className="gp-subtitle">宽基指数情绪三重确认底部检测 · 更新于 {as_of}</p>
        </div>
        <button className="gp-refresh-btn" onClick={fetchData} title="刷新数据">
          <RefreshCw size={16} />
        </button>
      </div>

      <GoldenPitTimeline window={window} />

      {global_macro && <GlobalMacroCard macro={global_macro} />}

      <div className="gp-section">
        <h3 className="gp-section-title">宽基指数状态</h3>
        <div className="gp-index-grid">
          {sortedIndices.map((idx) => (
            <IndexStatusCard key={idx.fund_code} idx={idx} />
          ))}
        </div>
      </div>

      <div className="gp-bottom-row">
        <TripleConfirmation conf={conf} prediction={prediction} />

        <div className="gp-chart-section">
          <button className="gp-chart-toggle" onClick={() => setShowChart(!showChart)}>
            {showChart ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            <h3 className="gp-section-title" style={{ margin: 0 }}>贪婪值趋势</h3>
          </button>
          {showChart && <TrendChart trendData={trendData} />}
        </div>
      </div>

      {summary && (
        <div className="gp-summary">
          <h3 className="gp-section-title">AI 解读</h3>
          <p>{summary}</p>
        </div>
      )}
      </div>
    </div>
  );
}

function useGoldenPitBackground(canvasRef: React.RefObject<HTMLCanvasElement | null>) {
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
    for (let i = 0; i < 40; i++) {
      particles.push({
        x: Math.random() * w, y: Math.random() * h,
        size: Math.random() * 1.8 + 0.4,
        sx: (Math.random() - 0.5) * 0.15,
        sy: (Math.random() - 0.5) * 0.15,
        alpha: Math.random() * 0.25 + 0.06,
        tint: Math.random(),
      });
    }

    const halos = [
      { x: 0.50, y: 0.12, r: 140, speed: 0.004, phase: 0 },
      { x: 0.80, y: 0.30, r: 100, speed: -0.003, phase: 1.5 },
      { x: 0.20, y: 0.75, r: 160, speed: 0.002, phase: 2.8 },
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
        grad.addColorStop(0, 'rgba(17,137,249,0)');
        grad.addColorStop(0.7, 'rgba(17,137,249,0.02)');
        grad.addColorStop(1, 'rgba(17,137,249,0.05)');
        ctx!.beginPath();
        ctx!.arc(0, 0, r1, 0, Math.PI * 2);
        ctx!.fillStyle = grad;
        ctx!.fill();
        ctx!.restore();
      }

      for (const p of particles) {
        p.x += p.sx; p.y += p.sy;
        if (p.x < 0 || p.x > w) p.sx *= -1;
        if (p.y < 0 || p.y > h) p.sy *= -1;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        const r = p.tint < 0.3 ? 17 : p.tint < 0.6 ? 167 : 17;
        const g = p.tint < 0.3 ? 137 : p.tint < 0.6 ? 216 : 137;
        const b = p.tint < 0.3 ? 249 : p.tint < 0.6 ? 234 : 249;
        ctx!.fillStyle = `rgba(${r},${g},${b},${p.alpha})`;
        ctx!.fill();
      }

      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 90) {
            ctx!.beginPath();
            ctx!.moveTo(particles[i].x, particles[i].y);
            ctx!.lineTo(particles[j].x, particles[j].y);
            ctx!.strokeStyle = `rgba(17,137,249,${(1 - dist / 90) * 0.04})`;
            ctx!.lineWidth = 0.5;
            ctx!.stroke();
          }
        }
      }

      time += 0.007;
      animId = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
    };
  }, [canvasRef]);
}
