import { useEffect, useState, useCallback } from 'react';
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
  trend?: 'declining' | 'bottoming' | 'recovering';
  turning_point_confirmed?: boolean;
  days_rising?: number;
  position_tier?: string | null;
  position_tier_label?: string | null;
  exit_signal?: string | null;
  exit_reason?: string;
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
          <span className="gp-timeline-badge">🟠 等待拐点确认</span>
          <span className="gp-timeline-leading">领先: {w.leading_index} ({w.leading_tier})</span>
          <ResonanceBadge pitCount={pitCount} />
        </div>
        <div className="gp-timeline-dates">
          <span>{pitCount}个指数在坑 / {w.warning_count || 0}个预警</span>
          {w.start_date && <span>首个信号: {w.start_date}</span>}
        </div>
        <div className="gp-timeline-status-text">
          拐点前轻仓累积 (单次≤3%/累计≤15%)，等待贪婪值连续回升
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

function IndexStatusCard({ idx }: { idx: IndexStatus }) {
  const color = STATUS_COLORS[idx.status];
  const greedPct = Math.round(idx.greed * 100);
  const trendIcon = idx.trend ? TREND_ICONS[idx.trend] : '';
  const trendColor = idx.trend ? TREND_COLORS[idx.trend] : '';
  const exitLabel = idx.exit_signal ? EXIT_LABELS[idx.exit_signal] : '';
  const sqLabel = idx.signal_quality === 'strong' ? '⭐' : idx.signal_quality === 'good' ? '✅' : '';

  return (
    <div className={`gp-index-card ${idx.status}`} style={{ borderColor: color }}>
      <div className="gp-index-card-top">
        <span className="gp-index-name">
          {sqLabel} {idx.index_name}
        </span>
        <span className="gp-index-badge" style={{ background: color }}>
          {STATUS_LABELS[idx.status]}
        </span>
      </div>
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
        {idx.status === 'normal' && idx.decline_rate !== 0 && (
          <span>日跌 {idx.decline_rate > 0 ? '+' : ''}{idx.decline_rate.toFixed(3)}</span>
        )}
        {idx.close > 0 && (
          <span className="gp-index-close">¥{idx.close.toFixed(2)}</span>
        )}
      </div>
      {idx.position_tier_label && idx.tier !== 'drop' && idx.tier !== 'watch' && (
        <div className="gp-index-position" style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: 4 }}>
          {idx.position_tier_label}
        </div>
      )}
      {exitLabel && (
        <div className="gp-index-exit" style={{ fontSize: '0.75rem', color: '#f97316', marginTop: 2, fontWeight: 600 }}>
          {exitLabel}: {idx.exit_reason}
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
          <CartesianGrid strokeDasharray="3 3" stroke="var(--agent-border)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: 'var(--agent-text-secondary)' }}
            tickFormatter={(v) => v.slice(5)}
          />
          <YAxis
            domain={[0.2, 0.9]}
            tick={{ fontSize: 10, fill: 'var(--agent-text-secondary)' }}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--agent-bg-deep)',
              border: '1px solid var(--agent-border)',
              borderRadius: 8,
              fontSize: 12,
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
        <div className="gp-header">
          <h1 className="gp-title">黄金坑监测</h1>
          <p className="gp-subtitle">宽基指数情绪三重确认底部检测</p>
        </div>
        <Skeleton />
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="golden-pit-page">
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
    );
  }

  if (!status) {
    return (
      <div className="golden-pit-page">
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
    );
  }

  const { golden_pit_window: window, indices, triple_confirmation: conf, prediction, summary, as_of } = status;
  const sortedIndices = [...indices].sort((a, b) => a.priority - b.priority);

  return (
    <div className="golden-pit-page">
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
  );
}
