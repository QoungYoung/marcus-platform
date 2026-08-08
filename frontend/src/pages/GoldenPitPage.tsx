import { useEffect, useState, useCallback, useRef } from 'react';
import { RefreshCw, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Brush,
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
  entry_strategy?: string;
  exit_strategy?: string;
  dca_strategy?: string;
  dca_label?: string;
  dca_weight?: number;
  trend_factor?: number;
  trend_label?: string;
  schedule_day?: number;
  pit_greed?: number;
  entry_greed?: number;
  prev_greed?: number | null;
  signal_trigger_greed?: number | null;
  dca_fallback?: number;
  turning_validation?: string;
  turning_validation_reason?: string;
  signal_quality?: string;
  data_source?: string;
  days_to_pit: number | null;
  eta_date: string | null;
  entry_date: string | null;
  days_in_pit: number | null;
  arkvol_greed?: number | null;
  price_percentile?: number | null;
}

interface GoldenPitWindow {
  active: boolean;
  phase?: 'idle' | 'waiting' | 'buying';
  start_date: string | null;
  turning_start_date?: string | null;
  days_since_turning?: number;
  leading_index: string | null;
  leading_tier?: string | null;
  current_day: number;
  pit_count?: number;
  warning_count?: number;
  turning_count?: number;
  resonance_multiplier?: number;
  midpoint_date: string | null;
  exit_date: string | null;
  turning_leader_rising?: number;
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
  share_history?: { date: string; [market: string]: number | string }[];
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

// ── Display Config (fetched from backend) ──

interface DisplayConfig {
  status_colors: Record<string, string>;
  status_labels: Record<string, string>;
  strategy_labels: Record<string, string>;
  exit_labels: Record<string, string>;
  trend_icons: Record<string, string>;
  trend_colors: Record<string, string>;
  tier_labels: Record<string, string>;
}

let _cachedDisplayConfig: DisplayConfig | null = null;

async function fetchDisplayConfig(): Promise<DisplayConfig> {
  if (_cachedDisplayConfig) return _cachedDisplayConfig;
  try {
    const res = await goldenPitApi.getDisplayConfig();
    if (res.data?.code === 0 && res.data?.data) {
      _cachedDisplayConfig = res.data.data as DisplayConfig;
      return _cachedDisplayConfig;
    }
  } catch { /* fallback to defaults */ }
  return {
    status_colors: { normal: '#27a06b', warning: '#c98a12', golden_pit: '#e5484d' },
    status_labels: { normal: '正常', warning: '预警', golden_pit: '黄金坑' },
    strategy_labels: {},
    exit_labels: { half_exit: '减持 50%', full_exit: '清仓', stop_profit: '止盈', fallback_exit: '兜底退出' },
    trend_icons: { declining: '↓', bottoming: '→', recovering: '↑' },
    trend_colors: { declining: '#e5484d', bottoming: '#c98a12', recovering: '#27a06b' },
    tier_labels: { core: '核心', satellite: '卫星', defense: '防御', defense_rotation: '防御轮动', semi_boost: '半导体增强', watch: '观察', drop: '放弃' },
  };
}

function useDisplayConfig() {
  const [config, setConfig] = useState<DisplayConfig | null>(_cachedDisplayConfig);
  useEffect(() => { fetchDisplayConfig().then(setConfig); }, []);
  return config;
}

// 与整体蓝色科幻主题协调的序列色板
const INDEX_COLORS = ['#2f7cd3', '#e5484d', '#c98a12', '#27a06b', '#7c5cd6', '#0e9db8'];

// 板块级指数（防御轮动 + 半导体增强），展示在宽基指数状态下方
const SECTOR_TIERS = new Set(['defense_rotation', 'semi_boost']);


const GLOBAL_TREND_LABELS: Record<string, string> = {
  bullish: '看涨',
  declining: '下行',
  flat: '平稳',
  unknown: '未知',
};

const GLOBAL_TREND_COLORS: Record<string, string> = {
  bullish: '#27a06b',
  declining: '#e5484d',
  flat: '#93a9c0',
  unknown: '#93a9c0',
};

const MARKET_ORDER = ['a_share', 'united_states', 'japan', 'south_korea', 'hong_kong'];

// ── Inline SVG icons (sci-fi line style, no emoji) ──

function IconValve() {
  return (
    <svg className="gp-stat-icon" width="24" height="24" viewBox="0 0 26 26" fill="none">
      <circle cx="13" cy="13" r="9" stroke="#2f7cd3" strokeWidth="2" />
      <path d="M13 7v6l4 3" stroke="#2f7cd3" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function IconSentiment() {
  return (
    <svg className="gp-stat-icon" width="24" height="24" viewBox="0 0 26 26" fill="none">
      <rect x="4" y="14" width="4" height="8" fill="#e5484d" />
      <rect x="10" y="10" width="4" height="12" fill="#c98a12" />
      <rect x="16" y="13" width="4" height="9" fill="#2f7cd3" />
    </svg>
  );
}

function IconTrend({ color }: { color: string }) {
  return (
    <svg className="gp-stat-icon" width="24" height="24" viewBox="0 0 26 26" fill="none">
      <circle cx="13" cy="13" r="9" stroke={color} strokeWidth="2" />
      <path d="M8 11l3 3 3-3 4 4" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconPosition() {
  return (
    <svg className="gp-stat-icon" width="24" height="24" viewBox="0 0 26 26" fill="none">
      <path d="M13 5l9 16H4l9-16z" stroke="#27a06b" strokeWidth="2" strokeLinejoin="round" />
      <path d="M13 12v5" stroke="#27a06b" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function IconStar() {
  return (
    <svg width="12" height="12" viewBox="0 0 13 13" style={{ verticalAlign: '-1px', marginRight: 3 }}>
      <path d="M6.5 1l1.6 3.4 3.7.5-2.7 2.6.7 3.7-3.3-1.8-3.3 1.8.7-3.7L.7 4.9l3.7-.5z" fill="#c98a12" />
    </svg>
  );
}

function IconGood() {
  return (
    <svg width="12" height="12" viewBox="0 0 13 13" style={{ verticalAlign: '-1px', marginRight: 3 }}>
      <circle cx="6.5" cy="6.5" r="5.5" fill="none" stroke="#27a06b" strokeWidth="1.5" />
      <path d="M4 6.6l1.7 1.7L9 5" stroke="#27a06b" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconWarn() {
  return (
    <svg width="12" height="12" viewBox="0 0 13 13" style={{ verticalAlign: '-1px', marginRight: 3 }}>
      <path d="M6.5 1.2L12 11H1z" fill="none" stroke="#c98a12" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M6.5 5v3" stroke="#c98a12" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="6.5" cy="9.6" r=".8" fill="#c98a12" />
    </svg>
  );
}

function IconBulb() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" style={{ flex: 'none', marginTop: 1 }}>
      <path d="M7 1.5a4 4 0 0 1 4 4c0 1.5-.8 2.4-1.5 3.2-.3.4-.5.8-.5 1.3H5c0-.5-.2-.9-.5-1.3C3.8 7.9 3 7 3 5.5a4 4 0 0 1 4-4z" stroke="#c98a12" strokeWidth="1.3" />
      <path d="M5.5 11.5h3M6 13h2" stroke="#c98a12" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

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

function ResonanceBadge({ pitCount, multiplier }: { pitCount: number; multiplier?: number }) {
  if (pitCount < 1 || multiplier == null) return null;
  let cls = 'gp-res-pill';
  if (pitCount < 2) cls += ' warn';
  else if (pitCount < 3) cls += ' muted';
  return <span className={cls}>{pitCount}指共振 {multiplier.toFixed(1)}x</span>;
}

function GoldenPitTimeline({ window: w }: { window: GoldenPitWindow }) {
  const phase = w.phase || 'idle';
  const pitCount = w.pit_count || 0;

  if (phase === 'idle') {
    return (
      <section className="gp-panel gp-timeline inactive">
        <span className="gp-dot blue" />
        <span className="gp-timeline-status">当前无黄金坑信号</span>
      </section>
    );
  }

  if (phase === 'waiting') {
    return (
      <section className="gp-panel gp-timeline waiting">
        <div className="gp-tl-left">
          <div className="gp-tl-title"><span className="gp-dot amber" />已入坑 · 等待回升确认</div>
          <div className="gp-tl-meta">
            {w.start_date && <span>首个入坑: <b>{w.start_date}</b></span>}
            <span><b>{pitCount}</b> 个指数已在黄金坑 / <b>{w.warning_count || 0}</b> 个预警</span>
          </div>
          <div className="gp-tl-status">贪婪值仍在下跌中，需等待连续回升确认拐点后开启买入窗口</div>
          <div className="gp-stages">
            <span className="gp-stage done"><i>1</i>入坑</span>
            <span className="gp-stage-arrow">→</span>
            <span className="gp-stage active"><i>2</i>拐点确认</span>
            <span className="gp-stage-arrow">→</span>
            <span className="gp-stage pending"><i>3</i>买入窗口</span>
          </div>
        </div>
        <div className="gp-tl-mid">
          <div className="gp-tl-lead">领先: {w.leading_index}{w.leading_tier ? ` (${w.leading_tier})` : ''}</div>
          <div className="gp-tl-lead-en">WAITING · PIVOT CONFIRMATION</div>
        </div>
        <div className="gp-tl-right">
          <ResonanceBadge pitCount={pitCount} multiplier={w.resonance_multiplier} />
        </div>
      </section>
    );
  }

  return (
    <section className="gp-panel gp-timeline buying">
      <div className="gp-tl-left">
        <div className="gp-tl-title"><span className="gp-dot red" />买入窗口</div>
        <div className="gp-tl-meta">
          <span>拐点: <b>{w.turning_start_date || w.start_date}</b></span>
          <span>加仓节奏: <span className="gold">50% → 75% → 100%</span></span>
        </div>
        <div className="gp-stages">
          <span className="gp-stage done"><i>1</i>入坑</span>
          <span className="gp-stage-arrow">→</span>
          <span className="gp-stage done"><i>2</i>拐点确认</span>
          <span className="gp-stage-arrow">→</span>
          <span className="gp-stage active"><i>3</i>买入窗口</span>
        </div>
      </div>
      <div className="gp-tl-mid">
        <div className="gp-tl-lead">{w.leading_index} <em>拐点确认</em>（第{w.turning_leader_rising || w.current_day}天）</div>
        <div className="gp-tl-lead-en">PIVOT CONFIRMED · DAY {w.turning_leader_rising || w.current_day}</div>
      </div>
      <div className="gp-tl-right">
        <ResonanceBadge pitCount={pitCount} multiplier={w.resonance_multiplier} />
        <div className="gp-tl-back">回升: <b>{w.turning_leader_rising || w.current_day}天</b> · 已确认: <b>{w.turning_count || 0}</b>个指数</div>
      </div>
    </section>
  );
}

function MacroOverview({ macro }: { macro: GlobalMacro }) {
  const gateOpen = macro.liquidity_gate === 'open';
  const trendLabel = GLOBAL_TREND_LABELS[macro.global_trend] || macro.global_trend;
  const trendColor = GLOBAL_TREND_COLORS[macro.global_trend] || '#93a9c0';
  const coefPct = Math.round(macro.global_macro_coefficient * 100);
  const sentimentColor = macro.sentiment_score <= 20 ? '#e5484d' : macro.sentiment_score >= 80 ? '#27a06b' : 'var(--gp-muted)';

  return (
    <section className="gp-panel gp-overview">
      <div className="gp-panel-head">
        <span className="gp-tick" /><h2>看板概览</h2><span className="gp-en">OVERVIEW</span>
      </div>
      <div className="gp-stat-grid">
        <div className="gp-stat">
          <div className="gp-stat-lab">流动性阀门 <span className="gp-stat-idx">01</span></div>
          <div className={`gp-stat-val ${gateOpen ? '' : 'red'}`}>{gateOpen ? '开启' : '关闭'}</div>
          <IconValve />
        </div>
        <div className="gp-stat">
          <div className="gp-stat-lab">情绪指数 <span className="gp-stat-idx">02</span></div>
          <div className="gp-stat-val">{macro.sentiment_score.toFixed(0)}<small style={{ color: sentimentColor }}>{macro.sentiment_label}</small></div>
          <IconSentiment />
        </div>
        <div className="gp-stat">
          <div className="gp-stat-lab">全球趋势 <span className="gp-stat-idx">03</span></div>
          <div className="gp-stat-val" style={{ color: trendColor }}>{trendLabel}</div>
          <IconTrend color={trendColor} />
        </div>
        <div className="gp-stat">
          <div className="gp-stat-lab">仓位系数 <span className="gp-stat-idx">04</span></div>
          <div className={`gp-stat-val ${coefPct < 100 ? 'amber' : ''}`}>{coefPct}<small>%</small></div>
          <IconPosition />
        </div>
      </div>
      {macro.summary && <div className="gp-overview-note">{macro.summary}</div>}
    </section>
  );
}

function TripleConfirmation({ conf, prediction }: {
  conf: GoldenPitStatus['triple_confirmation'];
  prediction: Prediction | null;
}) {
  const layers = [conf.layer1, conf.layer2, conf.layer3];

  return (
    <section className="gp-panel gp-confirmation">
      <div className="gp-panel-head">
        <span className="gp-tick" /><h2>三重确认</h2><span className="gp-en">TRIPLE CHECK</span>
      </div>
      <div className="gp-confirm-list">
        {layers.map((layer, i) => (
          <div key={layer.label} className={`gp-confirm-row ${layer.confirmed ? 'confirmed' : ''}`}>
            <span className="gp-confirm-box">
              {layer.confirmed && (
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                  <path d="M1.5 5.2l2.3 2.3L8.5 2.6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </span>
            <div className="gp-confirm-text">
              <span className="gp-confirm-label">{layer.label}</span>
              <span className="gp-confirm-status">{layer.status}</span>
            </div>
            <span className="gp-confirm-idx">L{i + 1}</span>
          </div>
        ))}
      </div>
      {prediction && (
        <div className="gp-prediction">
          <IconBulb />
          <span>预测: {prediction.next_index} 预计 {prediction.eta_days} 天后入坑 ({prediction.eta_date})</span>
        </div>
      )}
    </section>
  );
}

function CapitalFlowPanel({ macro }: { macro: GlobalMacro }) {
  const cf = macro.capital_flow;
  if (!cf || !cf.markets) return null;
  const gateOpen = macro.liquidity_gate === 'open';
  const trendLabel = GLOBAL_TREND_LABELS[macro.global_trend] || macro.global_trend;

  return (
    <section className="gp-panel gp-flow">
      <div className="gp-panel-head">
        <span className="gp-tick" /><h2>全球资金流向</h2><span className="gp-en">CAPITAL FLOW</span>
        <span className="gp-flow-risk">
          全球风险偏好: <b>{macro.sentiment_label}({macro.sentiment_score.toFixed(0)})</b> · 阀门: <b>{gateOpen ? '开启' : '关闭'}</b> · 趋势: <b>{trendLabel}</b>
        </span>
      </div>
      <div className="gp-flow-markets">
        {MARKET_ORDER.map((key) => {
          const m = cf.markets[key];
          if (!m) return null;
          const isInflow = m.direction === 'inflow';
          const ppAbs = Math.abs(m.cumulative_pp);
          const ppColor = isInflow ? '#27a06b' : '#e5484d';
          const barWidth = Math.min(100, Math.max(8, ppAbs * 10));
          return (
            <div key={key} className="gp-flow-market-chip">
              <span className="gp-flow-market-name">{m.name}</span>
              <span className="gp-flow-direction" style={{ color: ppColor }}>
                {isInflow ? '↑' : '↓'}{m.direction_label}
              </span>
              <span className="gp-flow-days">{m.consecutive_days}日</span>
              <div className="gp-flow-bar-wrap">
                <div className="gp-flow-bar" style={{ width: `${barWidth}%`, background: ppColor }} />
              </div>
              <span className="gp-flow-pp" style={{ color: ppColor }}>
                {isInflow ? '+' : '-'}{ppAbs.toFixed(1)}pp
              </span>
            </div>
          );
        })}
      </div>
      {cf.summary && <div className="gp-flow-summary">{cf.summary}</div>}
    </section>
  );
}

function IndexStatusCard({ idx, displayConfig, tierLabel }: { idx: IndexStatus; displayConfig: DisplayConfig | null; tierLabel?: string }) {
  const statusColors = displayConfig?.status_colors || { normal: '#27a06b', warning: '#c98a12', golden_pit: '#e5484d' };
  const statusLabels = displayConfig?.status_labels || { normal: '正常', warning: '预警', golden_pit: '黄金坑' };
  const trendIcons = displayConfig?.trend_icons || { declining: '↓', bottoming: '→', recovering: '↑' };
  const trendColors = displayConfig?.trend_colors || { declining: '#e5484d', bottoming: '#c98a12', recovering: '#27a06b' };
  const exitLabels = displayConfig?.exit_labels || { half_exit: '减持 50%', full_exit: '清仓', stop_profit: '止盈', fallback_exit: '兜底退出' };
  const color = statusColors[idx.status];
  const greedPct = Math.round(idx.greed * 100);
  const trendIcon = idx.trend ? trendIcons[idx.trend] : '';
  const trendColor = idx.trend ? trendColors[idx.trend] : '';
  const exitLabel = idx.exit_signal ? exitLabels[idx.exit_signal] : '';
  const sqIcon = idx.signal_quality === 'strong' ? <IconStar /> : idx.signal_quality === 'good' ? <IconGood /> : null;
  const weightPct = (idx.position_weight != null && idx.position_weight > 0)
    ? `${(idx.position_weight * 100).toFixed(0)}%`
    : '';
  const isDivergent = idx.turning_validation === 'divergent';
  const isGlobalExit = idx.exit_reason?.startsWith('全球');

  return (
    <div className={`gp-index-card ${idx.status}`}>
      <div className="gp-index-card-top">
        <span className="gp-index-name">
          {isDivergent && (
            <span className="gp-divergent-icon" title={idx.turning_validation_reason || '全球趋势背离'}><IconWarn /></span>
          )}
          {sqIcon}{idx.index_name}
          {tierLabel && <span className="gp-index-tier">{tierLabel}</span>}
          {weightPct && (
            <span className="gp-index-weight" title="仓位上限">上限{weightPct}</span>
          )}
        </span>
        <span className="gp-index-badge" style={{ background: color }}>
          {statusLabels[idx.status]}
        </span>
      </div>
      {isDivergent && idx.turning_validation_reason && (
        <div className="gp-divergent-reason">{idx.turning_validation_reason}</div>
      )}
      <div className="gp-index-greed">
        <span className="gp-index-value" style={{ color }}>{idx.greed.toFixed(4)}</span>
        <span className="gp-index-percentile">P{idx.percentile.toFixed(1)}</span>
        {trendIcon && (
          <span className="gp-index-trend" style={{ color: trendColor }}>
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
          <span style={{ color: idx.change_5 > 0 ? '#27a06b' : '#e5484d' }}>
            5日{idx.change_5 > 0 ? '反弹' : '下跌'} {idx.change_5 > 0 ? '+' : ''}{idx.change_5.toFixed(3)}
          </span>
        )}
        {idx.change_5 == null && idx.decline_rate !== 0 && (
          <span>日跌 {idx.decline_rate > 0 ? '+' : ''}{idx.decline_rate.toFixed(3)}</span>
        )}
        {idx.close > 0 && (
          <span className="gp-index-close">¥{idx.close.toFixed(2)}</span>
        )}
      </div>
      {(idx.entry_strategy || idx.exit_strategy) && (
        <div className="gp-index-strategy">
          {idx.entry_strategy && <span className="gp-strategy-entry">入场: {idx.entry_strategy}</span>}
          {idx.exit_strategy && <span className="gp-strategy-exit">出场: {idx.exit_strategy}</span>}
        </div>
      )}
      {idx.position_tier_label && idx.tier !== 'drop' && idx.tier !== 'watch' && (
        <div className="gp-index-position">
          <span style={{ color: (idx.trend_factor ?? 0) >= 1.0 ? '#27a06b' : (idx.trend_factor ?? 0) >= 0.5 ? '#c98a12' : '#e5484d' }}>
            {idx.trend_label || idx.position_tier_label}
          </span>
          {idx.dca_strategy && (
            <span style={{ color: '#93a9c0', marginLeft: 8 }}>
              DCA: {idx.dca_label || idx.dca_strategy}
            </span>
          )}
          {idx.schedule_day != null && (
            <span style={{ color: '#93a9c0', marginLeft: 8 }}>
              窗口第{idx.schedule_day}天
            </span>
          )}
        </div>
      )}
      {exitLabel && (
        <div className={`gp-index-exit ${isGlobalExit ? 'global-exit' : 'aq-exit'}`}>
          <span className="gp-exit-source">{isGlobalExit ? '宏观' : 'A股'}</span>
          <span className="gp-exit-label">{exitLabel}</span>
          <span className="gp-exit-reason">{idx.exit_reason}</span>
        </div>
      )}
    </div>
  );
}

// ── Charts ──

function GpChartTooltip({ active, payload, label, digits = 4, suffix = '' }: any) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="gp-tooltip">
      <div className="gp-tooltip-title">{label}</div>
      {payload.map((p: any) => (
        <div key={String(p.dataKey)} className="gp-tooltip-row">
          <span className="gp-tooltip-dot" style={{ background: p.stroke || p.color }} />
          <span className="gp-tooltip-name">{p.name}</span>
          <span className="gp-tooltip-val">
            {typeof p.value === 'number' ? p.value.toFixed(digits) : p.value}{suffix}
          </span>
        </div>
      ))}
    </div>
  );
}

function TrendChart({ trendData, visibleCodes, onToggleCode, onToggleAll, minPitGreed, minEntryGreed }: {
  trendData: TrendData | null;
  visibleCodes: Set<string>;
  onToggleCode: (code: string) => void;
  onToggleAll: () => void;
  minPitGreed?: number;
  minEntryGreed?: number;
}) {
  if (!trendData || !trendData.series || Object.keys(trendData.series).length === 0) {
    return <div className="gp-chart-empty">暂无历史数据</div>;
  }

  const allCodes = Object.keys(trendData.series);
  const activeCodes = allCodes.filter((c) => visibleCodes.has(c));
  const allSelected = activeCodes.length === allCodes.length;
  const noneSelected = activeCodes.length === 0;

  // Merge all series by date
  const dateMap: Record<string, Record<string, number | string>> = {};
  activeCodes.forEach((code) => {
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
      <div className="gp-chart-filters">
        <button
          className={`gp-filter-chip gp-filter-all ${allSelected ? 'active' : ''}`}
          onClick={onToggleAll}
        >
          {allSelected ? '取消全选' : '全选'}
        </button>
        {allCodes.map((code, i) => {
          const name = trendData.indices[code] || code;
          const color = INDEX_COLORS[i % INDEX_COLORS.length];
          const active = visibleCodes.has(code);
          return (
            <button
              key={code}
              className={`gp-filter-chip ${active ? 'active' : ''}`}
              onClick={() => onToggleCode(code)}
              style={active ? { borderColor: color, color } : undefined}
            >
              <span className="gp-filter-dot" style={{ background: active ? color : '#c9d8e8' }} />
              {name}
            </button>
          );
        })}
      </div>
      {noneSelected ? (
        <div className="gp-chart-empty">请选择至少一个指数</div>
      ) : (() => {
        // Dynamic YAxis domain based on data range and per-index thresholds
        let dataMin = 1.0, dataMax = 0.0;
        chartData.forEach((row: any) => {
          activeCodes.forEach((code) => {
            const v = row[code];
            if (typeof v === 'number') { dataMin = Math.min(dataMin, v); dataMax = Math.max(dataMax, v); }
          });
        });
        const pitRef = minPitGreed ?? 0.35;
        const entryRef = minEntryGreed ?? 0.40;
        const yMin = Math.max(0.15, Math.min(pitRef - 0.05, dataMin));
        const yMax = Math.min(0.95, Math.max(dataMax + 0.05, 0.50));
        return (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#d4e7f9" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
              tickFormatter={(v) => v.slice(5)}
              axisLine={{ stroke: '#d4e7f9' }}
              tickLine={false}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
              axisLine={false}
              tickLine={false}
              width={34}
            />
            <Tooltip
              content={(props: any) => <GpChartTooltip {...props} digits={4} />}
              cursor={{ stroke: '#a8cdee', strokeDasharray: '4 3' }}
            />
            <ReferenceLine
              y={pitRef} stroke="#e5484d" strokeDasharray="5 4" strokeWidth={1.2}
              label={{ value: `参考线 (${pitRef.toFixed(3)})`, position: 'insideBottomRight', fontSize: 9, fill: '#e5484d' }}
            />
            <ReferenceLine
              y={entryRef} stroke="#c98a12" strokeDasharray="5 4" strokeWidth={1}
              label={{ value: `参考线 (${entryRef.toFixed(3)})`, position: 'insideTopLeft', fontSize: 9, fill: '#c98a12' }}
            />
            {activeCodes.map((code) => (
              <Line
                key={code}
                type="monotone"
                dataKey={code}
                name={trendData.indices[code] || code}
                stroke={INDEX_COLORS[allCodes.indexOf(code) % INDEX_COLORS.length]}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 1.5, stroke: '#fff' }}
              />
            ))}
            <Brush
              dataKey="date"
              height={28}
              stroke="#2f7cd3"
              fill="rgba(47,124,211,0.06)"
              tickFormatter={(v: string) => v.slice(5)}
            />
          </LineChart>
        </ResponsiveContainer>
        );
      })()}
    </div>
  );
}

const SHARE_MARKET_NAMES: Record<string, string> = {
  a_share: 'A股',
  hong_kong: '港股',
  us: '美国',
  japan: '日本',
  south_korea: '韩国',
  united_states: '美国',
};

function ShareHistoryChart({ shareHistory, visibleCodes, onToggleCode, onToggleAll }: {
  shareHistory: { date: string; [market: string]: number | string }[];
  visibleCodes: Set<string>;
  onToggleCode: (code: string) => void;
  onToggleAll: () => void;
}) {
  if (!shareHistory || shareHistory.length === 0) {
    return <div className="gp-chart-empty">暂无份额历史数据</div>;
  }

  // Extract market keys from the first row (exclude 'date')
  const firstRow = shareHistory[0];
  const allCodes = Object.keys(firstRow).filter((k) => k !== 'date');
  const activeCodes = allCodes.filter((c) => visibleCodes.has(c));
  const allSelected = activeCodes.length === allCodes.length;
  const noneSelected = activeCodes.length === 0;

  // Sort by date ascending
  const chartData = [...shareHistory].sort(
    (a, b) => (a.date as string).localeCompare(b.date as string)
  );

  // Compute Y domain from visible series
  let yMin = 100, yMax = 0;
  chartData.forEach((row) => {
    activeCodes.forEach((code) => {
      const v = Number(row[code]);
      if (!isNaN(v)) { yMin = Math.min(yMin, v); yMax = Math.max(yMax, v); }
    });
  });
  const pad = Math.max(2, (yMax - yMin) * 0.1);
  yMin = Math.floor(yMin - pad);
  yMax = Math.ceil(yMax + pad);

  return (
    <div className="gp-chart">
      <div className="gp-chart-filters">
        <button
          className={`gp-filter-chip gp-filter-all ${allSelected ? 'active' : ''}`}
          onClick={onToggleAll}
        >
          {allSelected ? '取消全选' : '全选'}
        </button>
        {allCodes.map((code, i) => {
          const name = SHARE_MARKET_NAMES[code] || code;
          const color = INDEX_COLORS[i % INDEX_COLORS.length];
          const active = visibleCodes.has(code);
          return (
            <button
              key={code}
              className={`gp-filter-chip ${active ? 'active' : ''}`}
              onClick={() => onToggleCode(code)}
              style={active ? { borderColor: color, color } : undefined}
            >
              <span className="gp-filter-dot" style={{ background: active ? color : '#c9d8e8' }} />
              {name}
            </button>
          );
        })}
      </div>
      {noneSelected ? (
        <div className="gp-chart-empty">请选择至少一个市场</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#d4e7f9" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
              tickFormatter={(v) => v.slice(5)}
              axisLine={{ stroke: '#d4e7f9' }}
              tickLine={false}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={{ fontSize: 10, fill: '#6b86a3', fontFamily: 'Rajdhani, sans-serif' }}
              tickFormatter={(v) => `${v}%`}
              axisLine={false}
              tickLine={false}
              width={38}
            />
            <Tooltip
              content={(props: any) => <GpChartTooltip {...props} digits={2} suffix="%" />}
              cursor={{ stroke: '#a8cdee', strokeDasharray: '4 3' }}
            />
            {activeCodes.map((code) => (
              <Line
                key={code}
                type="monotone"
                dataKey={code}
                name={SHARE_MARKET_NAMES[code] || code}
                stroke={INDEX_COLORS[allCodes.indexOf(code) % INDEX_COLORS.length]}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 1.5, stroke: '#fff' }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
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
  const [visibleCodes, setVisibleCodes] = useState<Set<string>>(new Set());
  const [chartTab, setChartTab] = useState<'greed' | 'sector' | 'share'>('greed');
  const [historyDays, setHistoryDays] = useState(60);
  const [shareVisibleCodes, setShareVisibleCodes] = useState<Set<string>>(new Set());
  const [sectorVisibleCodes, setSectorVisibleCodes] = useState<Set<string>>(new Set());
  const [headerCollapsed, setHeaderCollapsed] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useGoldenPitBackground(canvasRef);

  const displayConfig = useDisplayConfig();

  const fetchData = useCallback(async (days?: number) => {
    const d = days ?? historyDays;
    setLoading(true);
    setError(null);
    try {
      const [statusRes, historyRes] = await Promise.all([
        goldenPitApi.getStatus(),
        goldenPitApi.getHistory('all', d),
      ]);
      let sectorCodes: string[] = [];
      if (statusRes.data?.code === 0) {
        const s = statusRes.data.data as GoldenPitStatus;
        setStatus(s);
        sectorCodes = (s?.indices ?? []).filter((i) => SECTOR_TIERS.has(i.tier ?? '')).map((i) => i.fund_code);
        // Initialize share visible codes from share_history on first load
        const sh = s?.global_macro?.capital_flow?.share_history;
        if (sh && sh.length > 0) {
          setShareVisibleCodes((prev) => {
            if (prev.size === 0) {
              const keys = Object.keys(sh[0]).filter((k) => k !== 'date');
              return new Set(keys);
            }
            return prev;
          });
        }
      } else {
        setError(statusRes.data?.msg || '获取数据失败');
      }
      if (historyRes.data?.code === 0) {
        const td = historyRes.data.data;
        setTrendData(td);
        if (td?.series) {
          const sectorSet = new Set(sectorCodes);
          setVisibleCodes((prev) => prev.size === 0 ? new Set(Object.keys(td.series).filter((c) => !sectorSet.has(c))) : prev);
          setSectorVisibleCodes((prev) => prev.size === 0 ? sectorSet : prev);
        }
      }
    } catch (e: any) {
      const msg = e?.response?.data?.msg || e?.message || '网络请求失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [historyDays]);

  useEffect(() => {
    fetchData();
    const timer = setInterval(() => fetchData(), 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [fetchData]);

  if (loading && !status) {
    return (
      <div className="golden-pit-page">
        <div className="gp-content">
          <div className="gp-header">
            <h1 className="gp-title">黄金坑监测<span className="gp-title-en">GOLDEN PIT MONITOR</span></h1>
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
            <h1 className="gp-title">黄金坑监测<span className="gp-title-en">GOLDEN PIT MONITOR</span></h1>
          </div>
          <div className="gp-error">
          <AlertTriangle size={48} />
          <p>数据获取失败</p>
          <p className="gp-error-detail">{error}</p>
          <p className="gp-error-hint">请确认已配置 ArkVol API Key（环境变量 ARKVOL_API_KEY 或 ~/.arkvol/arkvol-entry.json）</p>
          <button className="gp-retry-btn" onClick={() => fetchData()}>
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
            <h1 className="gp-title">黄金坑监测<span className="gp-title-en">GOLDEN PIT MONITOR</span></h1>
          </div>
          <div className="gp-error">
            <p>暂无数据</p>
            <button className="gp-retry-btn" onClick={() => fetchData()}>
              <RefreshCw size={16} /> 刷新
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { golden_pit_window: window, indices, triple_confirmation: conf, prediction, summary, as_of, global_macro } = status;
  const sortedIndices = [...indices].sort((a, b) => a.priority - b.priority);
  const sectorIndices = sortedIndices.filter((i) => SECTOR_TIERS.has(i.tier ?? ''));
  const broadIndices = sortedIndices.filter((i) => !SECTOR_TIERS.has(i.tier ?? ''));
  const sectorCodeSet = new Set(sectorIndices.map((i) => i.fund_code));

  // 贪婪趋势数据按宽基/板块拆分（板块序列同样来自 /golden-pit/history 的 all 数据）
  const filterTrend = (keep: (code: string) => boolean): TrendData | null =>
    trendData ? {
      ...trendData,
      series: Object.fromEntries(Object.entries(trendData.series).filter(([code]) => keep(code))),
      indices: Object.fromEntries(Object.entries(trendData.indices).filter(([code]) => keep(code))),
    } : null;
  const broadTrendData = filterTrend((code) => !sectorCodeSet.has(code));
  const sectorTrendData = filterTrend((code) => sectorCodeSet.has(code));

  const minOf = (list: IndexStatus[], key: 'pit_greed' | 'entry_greed') => list.length > 0
    ? Math.min(...list.map((i) => i[key]).filter((v): v is number => v != null))
    : undefined;
  const broadMinPit = minOf(broadIndices, 'pit_greed');
  const broadMinEntry = minOf(broadIndices, 'entry_greed');
  const sectorMinPit = minOf(sectorIndices, 'pit_greed');
  const sectorMinEntry = minOf(sectorIndices, 'entry_greed');

  const rangeQuick = (
    <div className="gp-range-quick">
      {[30, 90, 180, 365, 2000].map((d) => (
        <button
          key={d}
          className={`gp-range-chip ${historyDays === d ? 'active' : ''}`}
          onClick={() => setHistoryDays(d)}
        >
          {d >= 2000 ? '全部' : `${d}天`}
        </button>
      ))}
    </div>
  );

  return (
    <div className="golden-pit-page">
      <canvas ref={canvasRef} id="gp-bg-canvas" />
      <div className="gp-content">
        <div className={`gp-header ${headerCollapsed ? 'collapsed' : ''}`}>
          <button
            className="gp-header-toggle"
            onClick={() => setHeaderCollapsed(!headerCollapsed)}
            title={headerCollapsed ? '展开' : '收起'}
          >
            <h1 className="gp-title">黄金坑监测<span className="gp-title-en">GOLDEN PIT MONITOR</span></h1>
            {headerCollapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          </button>
          {!headerCollapsed && (
            <>
              <p className="gp-subtitle">宽基指数情绪三重确认底部检测 · 更新于 {as_of}</p>
              <button className="gp-refresh-btn" onClick={() => fetchData()} title="刷新数据">
                <RefreshCw size={16} />
              </button>
            </>
          )}
        </div>

        <div className="gp-layout">
          {/* ── 左侧栏：看板概览 + 三重确认 + 状态条 ── */}
          <aside className="gp-sidebar">
            {global_macro && <MacroOverview macro={global_macro} />}
            <TripleConfirmation conf={conf} prediction={prediction} />
            <div className="gp-status-pill">
              {(() => {
                const confirmed = [conf.layer1.confirmed, conf.layer2.confirmed, conf.layer3.confirmed].filter(Boolean).length;
                return (
                  <>
                    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
                      <circle cx="7" cy="7" r="6" stroke="#fff" strokeWidth="1.6" />
                      <path d="M4.5 7l1.8 1.8L9.8 5.4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    三重确认: {confirmed}/3 层
                    <span className="en">OK</span>
                  </>
                );
              })()}
            </div>
          </aside>

          {/* ── 右侧主区 ── */}
          <main className="gp-main">
            <GoldenPitTimeline window={window} />

            {global_macro && <CapitalFlowPanel macro={global_macro} />}

            <section className="gp-panel gp-section">
              <div className="gp-panel-head">
                <span className="gp-tick" /><h2>宽基指数状态</h2><span className="gp-en">BROAD INDEX STATUS</span>
              </div>
              <div className="gp-index-grid">
                {broadIndices.map((idx) => (
                  <IndexStatusCard key={idx.fund_code} idx={idx} displayConfig={displayConfig} />
                ))}
              </div>
            </section>

            {sectorIndices.length > 0 && (
              <section className="gp-panel gp-section">
                <div className="gp-panel-head">
                  <span className="gp-tick" /><h2>板块指数状态</h2><span className="gp-en">SECTOR ROTATION</span>
                </div>
                <div className="gp-index-grid">
                  {sectorIndices.map((idx) => (
                    <IndexStatusCard
                      key={idx.fund_code}
                      idx={idx}
                      displayConfig={displayConfig}
                      tierLabel={displayConfig?.tier_labels?.[idx.tier ?? '']}
                    />
                  ))}
                </div>
              </section>
            )}

            <section className="gp-panel gp-chart-section">
              <div className="gp-panel-head">
                <span className="gp-tick" />
                <button className="gp-chart-toggle" onClick={() => setShowChart(!showChart)}>
                  <h2>{chartTab === 'greed' ? '贪婪值趋势' : chartTab === 'sector' ? '板块贪婪值趋势' : '全球资金份额'}</h2>
                  {showChart ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
                <span className="gp-en">{chartTab === 'greed' ? 'GREED TREND' : chartTab === 'sector' ? 'SECTOR GREED TREND' : 'CAPITAL SHARE'}</span>
                <div className="gp-chart-tabs">
                  <button
                    className={`gp-chart-tab ${chartTab === 'greed' ? 'active' : ''}`}
                    onClick={() => setChartTab('greed')}
                  >
                    贪婪值
                  </button>
                  <button
                    className={`gp-chart-tab ${chartTab === 'sector' ? 'active' : ''}`}
                    onClick={() => setChartTab('sector')}
                  >
                    板块
                  </button>
                  <button
                    className={`gp-chart-tab ${chartTab === 'share' ? 'active' : ''}`}
                    onClick={() => setChartTab('share')}
                  >
                    资金份额
                  </button>
                </div>
              </div>
              {showChart && chartTab === 'greed' && (
                <>
                  {rangeQuick}
                  <TrendChart
                    trendData={broadTrendData}
                    visibleCodes={visibleCodes}
                    onToggleCode={(code) => {
                      setVisibleCodes((prev) => {
                        const next = new Set(prev);
                        if (next.has(code)) next.delete(code); else next.add(code);
                        return next;
                      });
                    }}
                    onToggleAll={() => {
                      setVisibleCodes((prev) => {
                        if (!broadTrendData?.series) return prev;
                        const all = Object.keys(broadTrendData.series);
                        const allSelected = all.every((c) => prev.has(c));
                        return allSelected ? new Set() : new Set(all);
                      });
                    }}
                    minPitGreed={broadMinPit}
                    minEntryGreed={broadMinEntry}
                  />
                </>
              )}
              {showChart && chartTab === 'sector' && (
                <>
                  {rangeQuick}
                  <TrendChart
                    trendData={sectorTrendData}
                    visibleCodes={sectorVisibleCodes}
                    onToggleCode={(code) => {
                      setSectorVisibleCodes((prev) => {
                        const next = new Set(prev);
                        if (next.has(code)) next.delete(code); else next.add(code);
                        return next;
                      });
                    }}
                    onToggleAll={() => {
                      setSectorVisibleCodes((prev) => {
                        if (!sectorTrendData?.series) return prev;
                        const all = Object.keys(sectorTrendData.series);
                        const allSelected = all.every((c) => prev.has(c));
                        return allSelected ? new Set() : new Set(all);
                      });
                    }}
                    minPitGreed={sectorMinPit}
                    minEntryGreed={sectorMinEntry}
                  />
                </>
              )}
              {showChart && chartTab === 'share' && (
                <ShareHistoryChart
                  shareHistory={global_macro?.capital_flow?.share_history || []}
                  visibleCodes={shareVisibleCodes}
                  onToggleCode={(code) => {
                    setShareVisibleCodes((prev) => {
                      const next = new Set(prev);
                      if (next.has(code)) next.delete(code); else next.add(code);
                      return next;
                    });
                  }}
                  onToggleAll={() => {
                    setShareVisibleCodes((prev) => {
                      const sh = global_macro?.capital_flow?.share_history;
                      if (!sh || sh.length === 0) return prev;
                      const all = Object.keys(sh[0]).filter((k) => k !== 'date');
                      const allSelected = all.every((c) => prev.has(c));
                      return allSelected ? new Set() : new Set(all);
                    });
                  }}
                />
              )}
            </section>

            {summary && (
              <section className="gp-panel gp-summary">
                <div className="gp-panel-head">
                  <span className="gp-tick" /><h2>AI 解读</h2><span className="gp-en">AI INSIGHT</span>
                </div>
                <p>{summary}</p>
              </section>
            )}
          </main>
        </div>
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
        grad.addColorStop(0, 'rgba(47,124,211,0)');
        grad.addColorStop(0.7, 'rgba(47,124,211,0.02)');
        grad.addColorStop(1, 'rgba(47,124,211,0.05)');
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
          if (dist < 90) {
            ctx!.beginPath();
            ctx!.moveTo(particles[i].x, particles[i].y);
            ctx!.lineTo(particles[j].x, particles[j].y);
            ctx!.strokeStyle = `rgba(47,124,211,${(1 - dist / 90) * 0.04})`;
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
