import { useEffect, useState, useCallback, useRef, Fragment } from 'react';
import { createPortal } from 'react-dom';
import { RefreshCw, AlertTriangle, ChevronDown, ChevronUp, Settings, X } from 'lucide-react';
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
  exit_mode?: string;
  dca_strategy?: string;
  dca_label?: string;
  dca_weight?: number;
  trend_factor?: number;
  trend_label?: string;
  schedule_day?: number;
  pit_greed?: number;
  entry_greed?: number;
  pit_pct?: number;
  entry_pct?: number;
  exit_full_pct?: number;
  pit_greed_threshold?: number;
  entry_greed_threshold?: number;
  exit_greed_threshold?: number;
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
  sector_selection?: {
    carrier?: SectorSelectionCarrier;
  };
  industry_monitor?: IndustryMonitor;
}

interface TrendData {
  as_of: string;
  series: Record<string, { date: string; greed: number; close: number }[]>;
  indices: Record<string, string>;
}

interface TechStatusItem {
  code: string;
  name: string;
  etf_code: string;
  tier: 'broad' | 'sector';
  close: number;
  trend: '多' | '空' | '震荡' | '数据不足';
  ma20: number | null;
  ma20_slope: number | null;
  greed: number | null;
  percentile: number | null;
  chg5: number | null;
  chg20: number | null;
  chg60: number | null;
  dd60: number | null;
  as_of: string | null;
}

interface TechStatus {
  as_of: string | null;
  verdict: string;
  verdict_desc: string;
  trend_up_count: number;
  trend_down_count: number;
  total_count: number;
  oversold_count: number;
  avg_percentile: number | null;
  oversold_pct_threshold: number;
  broad: TechStatusItem[];
  sectors: TechStatusItem[];
  summary: string;
}

interface SectorConfigItem {
  config_key: string;
  value: string | number | boolean;
  label: string;
  description: string;
  value_type: 'bool' | 'number' | 'string' | 'json';
  sort_order: number;
}

interface IndustryMonitorItem {
  id: string;
  name: string;
  greed_code: string;
  etf_code: string;
  priority: number;
  close: number | null;
  greed: number | null;
  greed_pct: number | null;
  drawdown: number;
  in_pit: boolean;
  overheat?: boolean;
  window_day: number;
  planned_amount: number;
  actual_amount: number;
  total_invested: number;
}

interface CashPoolCutItem {
  id: string;
  skipped: number;
  priority: number;
  reason?: string;
}

interface CashPoolView {
  total_nav: number;
  cash: number;
  cash_min_pct: number;
  cash_floor: number;
  available_cash: number;
  planned_total: number;
  actual_total: number;
  cut_items: CashPoolCutItem[];
  enabled?: boolean;
  dry_run?: boolean;
}

interface IndustryMonitor {
  as_of: string;
  enabled: boolean;
  in_pit_count?: number;
  industries: IndustryMonitorItem[];
  cash_pool: CashPoolView;
  notes: string[];
  error?: string;
  reason?: string;
}

interface DcaCarrierTarget {
  fund_code: string;
  mode: string;
  codes: { code: string; weight: number }[];
  reason?: string;
}

interface SectorSelectionCarrier {
  enabled: boolean;
  regime_carrier_enabled?: boolean;
  regime_mode?: string;
  regime_reason?: string;
  targets: DcaCarrierTarget[];
  note: string;
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

// 徽章底色加深版，保证 10px 白字满足 AA 对比度（>=4.5:1）
const BADGE_COLORS: Record<string, string> = {
  normal: '#1d7a4e',
  warning: '#8a650b',
  golden_pit: '#c23a3f',
};


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

function CashPoolBar({ cp }: { cp: CashPoolView }) {
  if (!cp || !cp.total_nav) return null;
  const pct = (v: number) => `${(cp.total_nav ? (v / cp.total_nav) * 100 : 0).toFixed(1)}%`;
  return (
    <div className="gp-pool-bar">
      <div className="gp-pool-item"><b>净值</b><span>¥{(cp.total_nav ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div>
      <div className="gp-pool-item"><b>现金</b><span>¥{(cp.cash ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div>
      <div className="gp-pool-item"><b>可用(扣{Math.round((cp.cash_min_pct ?? 0) * 100)}%下限)</b><span>¥{(cp.available_cash ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div>
      <div className="gp-pool-item"><b>今日计划</b><span>¥{(cp.planned_total ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div>
      <div className="gp-pool-item"><b>实际分配</b><span>¥{(cp.actual_total ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span></div>
      <div className="gp-pool-item"><b>被裁剪</b><span>{(cp.cut_items?.length ?? 0)} 项</span></div>
      <div className="gp-pool-note">利用 {pct(cp.available_cash ?? 0)} / 现金 {pct(cp.cash ?? 0)}</div>
    </div>
  );
}

function IndustryMonitorPanel({ monitor }: { monitor: IndustryMonitor | null }) {
  const [open, setOpen] = useState(true);
  const [filter, setFilter] = useState<'all' | 'in_pit' | 'monitor'>('all');
  if (!monitor) return null;

  const inPitCount = monitor.in_pit_count ?? monitor.industries.filter((i) => i.in_pit).length;
  const rows = (filter === 'in_pit' ? monitor.industries.filter((i) => i.in_pit)
    : filter === 'monitor' ? monitor.industries.filter((i) => !i.in_pit)
    : monitor.industries).slice().sort((a, b) =>
      Number(b.in_pit) - Number(a.in_pit) || a.priority - b.priority);

  const greedTone = (pct: number | null) => {
    if (pct == null) return 'gp-greed-na';
    if (pct <= 0.15) return 'gp-greed-pit';
    if (pct <= 0.3) return 'gp-greed-warn';
    return 'gp-greed-ok';
  };

  return (
    <section className="gp-panel gp-section">
      <div className="gp-panel-head">
        <span className="gp-tick" />
        <h2>全行业监测 · 资金池</h2>
        <span className="gp-en">INDUSTRY POOL</span>
        <span className={`gp-ind-badge ${monitor.enabled ? 'on' : 'off'}`}>
          {monitor.enabled ? '已启用' : '已关闭'}
          {monitor.cash_pool?.dry_run ? ' · dry-run' : ''}
        </span>
        <span className="gp-count">{inPitCount}/{monitor.industries.length} 入坑</span>
        <button className="gp-fold-btn" onClick={() => setOpen(!open)} title={open ? '收起' : '展开'} aria-label={open ? '收起全行业监测' : '展开全行业监测'}>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {open ? '收起' : '展开'}
        </button>
      </div>
      {open && (
        <div className="gp-industry-body">
          {monitor.error && <div className="gp-industry-error">⚠ {monitor.error}</div>}
          {!monitor.enabled && (
            <div className="gp-industry-empty">
              全行业监测已关闭（golden_pit_sector_config → industry_pool_enabled=false）。现有指数级黄金坑 DCA 不受影响。
            </div>
          )}
          {monitor.enabled && monitor.industries.length === 0 && (
            <div className="gp-industry-empty">暂无行业数据</div>
          )}
          <CashPoolBar cp={monitor.cash_pool} />
          <div className="gp-industry-filters">
            {([['all', '全部'], ['in_pit', '入坑中'], ['monitor', '监测中']] as const).map(([k, label]) => (
              <button key={k} className={`gp-range-chip ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>
          <div className="gp-industry-table-wrap">
            <table className="gp-industry-table">
              <thead>
                <tr>
                  <th>行业</th><th>贪婪分位</th><th>60日回撤</th><th>状态</th><th>DCA窗口</th><th>今日计划→实际</th><th>累计投入</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.id} className={i.in_pit ? 'gp-ind-row-pit' : ''}>
                    <td>
                      <span className="gp-ind-name">{i.name}</span>
                      <span className="gp-ind-pri">P{i.priority}</span>
                    </td>
                    <td>
                      {i.greed_pct != null ? (
                        <span className="gp-greed-cell">
                          <span className={`gp-greed-bar ${greedTone(i.greed_pct)}`} style={{ width: `${Math.min(100, i.greed_pct * 100)}%` }} />
                          <em>{i.greed_pct.toFixed(2)}</em>
                        </span>
                      ) : <span className="gp-greed-na">—</span>}
                    </td>
                    <td className={i.drawdown <= -0.2 ? 'gp-dd-pit' : ''}>{(i.drawdown * 100).toFixed(1)}%</td>
                    <td>
                      {i.in_pit ? <span className="gp-inpit-badge">入坑</span>
                        : i.overheat ? <span className="gp-overheat-badge">过热</span> : <span className="gp-monitor-badge">监测</span>}
                    </td>
                    <td>{i.window_day > 0 ? `第${i.window_day}天` : '—'}</td>
                    <td>¥{i.planned_amount.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} → ¥{i.actual_amount.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</td>
                    <td>¥{i.total_invested.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {monitor.cash_pool?.cut_items && monitor.cash_pool.cut_items.length > 0 && (
            <div className="gp-cut-list">
              <b>资金池裁剪（低优先级让位）：</b>
              {monitor.cash_pool.cut_items.map((c) => (
                <span key={c.id} className="gp-cut-item">{c.id} 跳过¥{c.skipped.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}(P{c.priority})</span>
              ))}
            </div>
          )}
          {monitor.notes && monitor.notes.length > 0 && (
            <div className="gp-industry-notes">{monitor.notes.slice(-5).map((n) => <div key={n}>{n}</div>)}</div>
          )}
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
  const flowMaxAbs = Math.max(
    1,
    ...MARKET_ORDER.map((key) => {
      const m = cf.markets[key];
      return m ? Math.abs(m.cumulative_pp) : 0;
    })
  );

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
          const barWidth = Math.max(8, Math.round((ppAbs / flowMaxAbs) * 100));
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
  const badgeColor = BADGE_COLORS[idx.status] || color;

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
        <span className="gp-index-badge" style={{ background: badgeColor }}>
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
        {idx.tier === 'defense_rotation' && (
          <span className="gp-src-chip price" title="入坑/撤场信号以价格分位为准">价格分位信号</span>
        )}
        {idx.tier === 'semi_boost' && (
          <span className="gp-src-chip tech" title="贪婪值来自 ArkVol 科技贪婪接口">ArkVol贪婪</span>
        )}
        {idx.change_5 != null && idx.change_5 !== 0 && (
          <span style={{ color: idx.change_5 > 0 ? '#27a06b' : '#e5484d' }}>
            5日{idx.change_5 > 0 ? '反弹' : '下跌'} {idx.change_5 > 0 ? '+' : ''}{idx.change_5.toFixed(3)}
          </span>
        )}
        {idx.change_5 == null && idx.decline_rate !== 0 && (
          <span style={{ color: idx.decline_rate > 0 ? '#e5484d' : '#27a06b' }}>
            {idx.tier === 'defense_rotation'
              ? (() => {
                  const pct = Math.abs(idx.decline_rate * 100);
                  const pctStr = pct >= 0.1 ? pct.toFixed(1) : pct.toFixed(2);
                  return idx.decline_rate > 0 ? `价格日跌 ${pctStr}%` : `价格日涨 ${pctStr}%`;
                })()
              : (idx.decline_rate > 0
                  ? `贪婪日降 +${idx.decline_rate.toFixed(3)}`
                  : `贪婪日升 ${(-idx.decline_rate).toFixed(3)}`)}
          </span>
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

function TrendChart({ trendData, visibleCodes, onToggleCode, onToggleAll, minPitGreed, minEntryGreed, groupLabel, thresholds }: {
  trendData: TrendData | null;
  visibleCodes: Set<string>;
  onToggleCode: (code: string) => void;
  onToggleAll: () => void;
  minPitGreed?: number;
  minEntryGreed?: number;
  groupLabel?: string;
  thresholds?: Record<string, { pit_pct?: number; entry_pct?: number; exit_full_pct?: number; pit_greed?: number; entry_greed?: number; pit_greed_threshold?: number; entry_greed_threshold?: number; exit_greed_threshold?: number; exit_mode?: string }>;
}) {
  const [legendOpen, setLegendOpen] = useState(true);
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

  // 单指数视图：用该指数自己的阈值画 入场/预警/出场 参考线
  const soloCode = activeCodes.length === 1 ? activeCodes[0] : null;
  const soloSeries = soloCode ? (trendData.series[soloCode] ?? []).map((p) => p.greed) : [];
  const pctValue = (values: number[], pct: number): number | null => {
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const idx = Math.max(0, Math.min(sorted.length - 1, Math.floor((pct / 100) * sorted.length)));
    return sorted[idx];
  };
  const soloLines: { key: string; value: number; label: string; color: string; pos: 'insideBottomRight' | 'insideRight' | 'insideTopRight' }[] = [];
  if (soloCode && soloSeries.length >= 20) {
    const th = thresholds?.[soloCode];
    if (th) {
      // ????????????????? 500 ????????????????????????????
      const pit = th.pit_greed_threshold ?? th.pit_greed ?? (th.pit_pct != null ? pctValue(soloSeries, th.pit_pct) : null);
      const entry = th.entry_greed_threshold ?? th.entry_greed ?? (th.entry_pct != null ? pctValue(soloSeries, th.entry_pct) : null);
      const exit = th.exit_greed_threshold ?? (th.exit_full_pct != null ? pctValue(soloSeries, th.exit_full_pct) : null);
      const pctTxt = (p?: number) => (p != null ? ` (P${p})` : '');
      if (pit != null) soloLines.push({ key: 'pit', value: pit, label: `入场线 ${pit.toFixed(3)}${pctTxt(th.pit_pct)}`, color: '#e5484d', pos: 'insideBottomRight' });
      if (entry != null) soloLines.push({ key: 'entry', value: entry, label: `预警线 ${entry.toFixed(3)}${pctTxt(th.entry_pct)}`, color: '#c98a12', pos: 'insideRight' });
      // 二次拐点离场模式: 出场不依赖 P 分位，不画 P 出场线（出场策略见 exit_strategy 文案）
      if (exit != null && th.exit_mode !== 'down_turn') soloLines.push({ key: 'exit', value: exit, label: `出场线 ${exit.toFixed(3)}${pctTxt(th.exit_full_pct)}`, color: '#2f7cd3', pos: 'insideTopRight' });
    }
  }
  soloLines.sort((a, b) => a.value - b.value);
  const linePositions = ['insideBottomRight', 'insideRight', 'insideTopRight'] as const;
  soloLines.forEach((ln, i) => { ln.pos = linePositions[Math.min(i, linePositions.length - 1)]; });

  return (
    <div className="gp-chart">
      <div className="gp-legend-bar">
        <span className="gp-legend-title">
          {groupLabel || '指数'}
          <em>{activeCodes.length}/{allCodes.length}</em>
        </span>
        <button
          className={`gp-legend-action ${allSelected ? 'all' : ''}`}
          onClick={onToggleAll}
        >
          {allSelected ? '清空' : '全选'}
        </button>
        <button className="gp-legend-fold" onClick={() => setLegendOpen(!legendOpen)}>
          {legendOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {legendOpen ? '收起图例' : '展开图例'}
        </button>
      </div>
      {legendOpen && (
        <div className="gp-chart-filters">
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
      )}
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
        const pitRef = minPitGreed;
        const entryRef = minEntryGreed;
        const showAggRefs = activeCodes.length > 1 && (pitRef != null || entryRef != null);
        const refValues = soloLines.length > 0
          ? soloLines.map((l) => l.value)
          : (showAggRefs ? [pitRef, entryRef].filter((v): v is number => v != null) : []);
        const yMin = Math.max(0.15, (refValues.length > 0 ? Math.min(dataMin, ...refValues) : dataMin) - 0.05);
        const yMax = Math.min(0.95, Math.max(dataMax + 0.05, ...(refValues.length > 0 ? refValues : [0.50])));
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
            {soloLines.length > 0 ? (
              soloLines.map((ln) => (
                <ReferenceLine
                  key={ln.key}
                  y={ln.value}
                  stroke={ln.color}
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                  label={{ value: ln.label, position: ln.pos, fontSize: 9, fill: ln.color }}
                />
              ))
            ) : showAggRefs ? (
              <>
                {pitRef != null && (
                  <ReferenceLine
                    y={pitRef} stroke="#e5484d" strokeDasharray="4 4" strokeWidth={1.5}
                    label={{ value: `参考线 (${pitRef.toFixed(3)})`, position: 'insideBottomRight', fontSize: 9, fill: '#e5484d' }}
                  />
                )}
                {entryRef != null && (
                  <ReferenceLine
                    y={entryRef} stroke="#c98a12" strokeDasharray="4 4" strokeWidth={1.5}
                    label={{ value: `参考线 (${entryRef.toFixed(3)})`, position: 'insideTopLeft', fontSize: 9, fill: '#c98a12' }}
                  />
                )}
              </>
            ) : null}
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

function TrendChip({ trend }: { trend: string }) {
  const cls = trend === '多' ? 'tech-bull' : trend === '空' ? 'tech-bear' : 'tech-flat';
  return <span className={`gp-tech-trend ${cls}`}>{trend}</span>;
}

function TechStatusPanel({ tech, sectorConfig, carrier }: { tech: TechStatus | null; sectorConfig: SectorConfigItem[]; carrier?: SectorSelectionCarrier | null }) {
  const [techTableOpen, setTechTableOpen] = useState(false);
  if (!tech) return null;
  // 生效选筹模式（与后端 resolve_regime_mode 口径一致: auto 按趋势腿激活数>=阈值切 trend）
  const regimeMode = String(sectorConfig.find((c) => c.config_key === 'regime_mode')?.value ?? 'oversold');
  const regimeThreshold = Number(sectorConfig.find((c) => c.config_key === 'regime_trend_threshold')?.value ?? 5);
  const effectiveMode = regimeMode === 'auto'
    ? ((tech.trend_up_count ?? 0) >= regimeThreshold ? 'trend' : 'oversold')
    : regimeMode;
  const regimeLabels: Record<string, string> = {
    oversold: '超跌(贪婪)选筹',
    trend: '趋势(动量)选筹',
    bh: '宽基躺平',
    auto: '自动',
  };
  const regimeLabel = (m: string) => regimeLabels[m] ?? m;
  const modeTxt = regimeMode === 'auto'
    ? `${regimeLabel('auto')} → ${regimeLabel(effectiveMode)}`
    : regimeLabel(effectiveMode);
  // 执行载体（来自 /golden-pit/status sector_selection.carrier）
  const carrierModeLabels: Record<string, string> = { sector_selection: '板块选筹', fixed_combo: '高弹性组合', broad: '宽基' };
  let carrierTxt = '';
  if (carrier?.targets?.length) {
    const modes = [...new Set(carrier.targets.map((t) => t.mode))].map((m) => carrierModeLabels[m] ?? m);
    carrierTxt = carrier.regime_carrier_enabled ? modes.join('+') : `${modes.join('+')}(静态)`;
  }
  const vCls =
    tech.verdict.includes('牛') ? 'tech-bull' :
    tech.verdict.includes('熊') ? 'tech-bear' : 'tech-flat';
  const fmt = (v: number | null, pct = true, digits = 1) => {
    if (v == null || Number.isNaN(v)) return '—';
    return `${pct ? (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%' : v.toFixed(digits)}`;
  };
  const rows = [...tech.broad, ...tech.sectors];
  return (
    <section className="gp-panel gp-section gp-tech-status">
      <div className="gp-panel-head">
        <span className="gp-tick" /><h2>牛熊判断 · 科技现状</h2><span className="gp-en">TECH REGIME</span>
        <span className={`gp-tech-verdict ${vCls}`}>{tech.verdict}</span>
        {tech.as_of && <span className="gp-tech-asof">K线截至 {tech.as_of}</span>}
        <button
          className="gp-fold-btn"
          onClick={() => setTechTableOpen(!techTableOpen)}
          title={techTableOpen ? '收起' : '展开'}
          aria-label={techTableOpen ? '收起牛熊标的列表' : '展开牛熊标的列表'}
          aria-expanded={techTableOpen}
        >
          {techTableOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {techTableOpen ? '收起' : '展开'}
        </button>
      </div>
      <p className="gp-tech-summary">{tech.summary}</p>
      <div className="gp-tech-stats">
        <div className="gp-tech-stat"><b>{tech.trend_up_count}</b><span>趋势腿激活(MA20多头)</span></div>
        <div className="gp-tech-stat"><b>{tech.trend_down_count}</b><span>空头排列</span></div>
        <div className="gp-tech-stat"><b>{tech.oversold_count}</b><span>贪婪超跌区(&le;{tech.oversold_pct_threshold})</span></div>
        <div className="gp-tech-stat"><b>{tech.avg_percentile != null ? (tech.avg_percentile * 100).toFixed(0) + '%' : '—'}</b><span>贪婪250日分位均值</span></div>
        <div className="gp-tech-stat gp-tech-regime"><b>{modeTxt}{carrierTxt ? ` → ${carrierTxt}` : ''}</b><span>生效模式 → 执行载体</span></div>
      </div>
      {techTableOpen && (
        <div className="gp-tech-table-wrap">
          <table className="gp-tech-table">
          <thead>
            <tr>
              <th>标的</th><th>趋势</th><th>MA20</th><th>贪婪值</th><th>250日分位</th>
              <th>5日</th><th>20日</th><th>60日</th><th>距60日高</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((it) => (
              <tr key={it.code}>
                <td className="gp-tech-name">
                  {it.name}
                  {it.tier === 'broad' && <span className="gp-tech-tag">宽基</span>}
                </td>
                <td><TrendChip trend={it.trend} /></td>
                <td>{it.ma20 != null ? it.ma20.toFixed(3) : '—'}</td>
                <td>{it.greed != null ? it.greed.toFixed(3) : '—'}</td>
                <td>{it.percentile != null ? (it.percentile * 100).toFixed(0) + '%' : '—'}</td>
                <td className={it.chg5 != null && it.chg5 >= 0 ? 'tech-up' : 'tech-down'}>{fmt(it.chg5)}</td>
                <td className={it.chg20 != null && it.chg20 >= 0 ? 'tech-up' : 'tech-down'}>{fmt(it.chg20)}</td>
                <td className={it.chg60 != null && it.chg60 >= 0 ? 'tech-up' : 'tech-down'}>{fmt(it.chg60)}</td>
                <td className={it.dd60 != null && it.dd60 >= 0 ? 'tech-up' : 'tech-down'}>{fmt(it.dd60)}</td>
              </tr>
            ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

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
  const [broadOpen, setBroadOpen] = useState(true);
  const [sectorOpen, setSectorOpen] = useState(true);
  const [headerCollapsed, setHeaderCollapsed] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [configItems, setConfigItems] = useState<SectorConfigItem[]>([]);
  const [configSaving, setConfigSaving] = useState(false);
  const [configMsg, setConfigMsg] = useState<string | null>(null);
  const [techStatus, setTechStatus] = useState<TechStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const statusRef = useRef<GoldenPitStatus | null>(null);
  const configTriggerRef = useRef<HTMLButtonElement>(null);
  const configModalRef = useRef<HTMLDivElement>(null);
  const configCloseRef = useRef<HTMLButtonElement>(null);
  const configSavingRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useGoldenPitBackground(canvasRef);

  const displayConfig = useDisplayConfig();

  const fetchData = useCallback(async (days?: number) => {
    const d = days ?? historyDays;
    const hadStatus = !!statusRef.current;
    if (hadStatus) setRefreshing(true);
    else setLoading(true);
    setError(null);
    setRefreshError(null);
    try {
      const [statusRes, historyRes, techRes, sectorCfgRes] = await Promise.allSettled([
        goldenPitApi.getStatus(),
        goldenPitApi.getHistory('all', d),
        goldenPitApi.getTechStatus(),
        goldenPitApi.getSectorConfig(),
      ]);
      const failures: string[] = [];
      let statusFailed = false;
      let statusMsg = '获取数据失败';
      if (statusRes.status === 'fulfilled' && statusRes.value.data?.code === 0) {
        const s = statusRes.value.data.data as GoldenPitStatus;
        setStatus(s);
        statusRef.current = s;
        const sectorCodes = (s?.indices ?? []).filter((i) => SECTOR_TIERS.has(i.tier ?? '')).map((i) => i.fund_code);
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
        if (historyRes.status === 'fulfilled' && historyRes.value.data?.code === 0) {
          const td = historyRes.value.data.data;
          setTrendData(td);
          if (td?.series) {
            const sectorSet = new Set(sectorCodes);
            setVisibleCodes((prev) => prev.size === 0 ? new Set(Object.keys(td.series).filter((c) => !sectorSet.has(c))) : prev);
            setSectorVisibleCodes((prev) => prev.size === 0 ? sectorSet : prev);
          }
        } else {
          failures.push('历史数据');
        }
      } else {
        statusFailed = true;
        statusMsg = statusRes.status === 'fulfilled' ? (statusRes.value.data?.msg || '获取数据失败') : '网络请求失败';
      }
      if (techRes.status === 'fulfilled' && techRes.value.data?.code === 0 && techRes.value.data?.data) {
        setTechStatus(techRes.value.data.data as TechStatus);
      } else {
        failures.push('技术面数据');
      }
      if (sectorCfgRes.status === 'fulfilled' && sectorCfgRes.value.data?.code === 0 && Array.isArray(sectorCfgRes.value.data?.data)) {
        setConfigItems(sectorCfgRes.value.data.data as SectorConfigItem[]);
      }
      if (statusFailed) {
        if (hadStatus) {
          setRefreshError(`刷新失败，展示上次成功数据（更新于 ${statusRef.current?.as_of ?? '—'}）`);
        } else {
          setError(statusMsg);
        }
      } else if (failures.length > 0) {
        setRefreshError(hadStatus
          ? `数据部分更新失败：${failures.join('、')}不可用，图表保留上次数据`
          : `数据部分不可用：${failures.join('、')}`);
      }
    } catch (e: any) {
      const msg = e?.response?.data?.msg || e?.message || '网络请求失败';
      if (hadStatus) {
        setRefreshError(`刷新失败，展示上次成功数据（更新于 ${statusRef.current?.as_of ?? '—'}）`);
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [historyDays]);

  const openConfig = useCallback(async () => {
    setConfigMsg(null);
    setConfigItems([]);
    setConfigOpen(true); // 点击后立即打开弹窗，再加载数据
    try {
      const res = await goldenPitApi.getSectorConfig();
      if (res.data?.code === 0 && Array.isArray(res.data?.data)) {
        setConfigItems(res.data.data as SectorConfigItem[]);
      } else {
        setConfigMsg(res.data?.msg || '加载配置失败');
      }
    } catch (e: any) {
      setConfigMsg(e?.response?.data?.msg || e?.message || '加载配置失败');
    }
  }, []);

  const closeConfig = useCallback(() => {
    if (!configSavingRef.current) {
      setConfigOpen(false);
      configTriggerRef.current?.focus();
    }
  }, []);

  const onConfigItemChange = useCallback((key: string, value: string | number | boolean) => {
    setConfigItems((prev) => prev.map((it) => (it.config_key === key ? { ...it, value } : it)));
  }, []);

  const saveConfig = useCallback(async () => {
    setConfigSaving(true);
    configSavingRef.current = true;
    setConfigMsg(null);
    try {
      const values: Record<string, string | number | boolean> = {};
      for (const it of configItems) {
        if (it.value_type === 'bool') values[it.config_key] = !!it.value;
        else if (it.value_type === 'string' || it.value_type === 'json') values[it.config_key] = String(it.value);
        else values[it.config_key] = Number(it.value);
      }
      const res = await goldenPitApi.updateSectorConfig(values);
      if (res.data?.code === 0) {
        setConfigOpen(false);
        configTriggerRef.current?.focus();
        fetchData();
      } else {
        setConfigMsg(res.data?.msg || '保存失败');
      }
    } catch (e: any) {
      setConfigMsg(e?.response?.data?.msg || e?.message || '保存失败');
    } finally {
      setConfigSaving(false);
      configSavingRef.current = false;
    }
  }, [configItems, fetchData]);

  // 弹窗：聚焦、Esc 关闭、Tab 圈定、背景滚动锁定
  useEffect(() => {
    if (!configOpen) return;
    configCloseRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeConfig();
      } else if (e.key === 'Tab') {
        const modal = configModalRef.current;
        if (!modal) return;
        const focusables = modal.querySelectorAll<HTMLElement>(
          'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])'
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [configOpen, closeConfig]);

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

  const minOf = (list: IndexStatus[], key: 'pit_greed' | 'entry_greed' | 'pit_greed_threshold' | 'entry_greed_threshold') => {
    const vals = list.map((i) => i[key]).filter((v): v is number => v != null);
    return vals.length > 0 ? Math.min(...vals) : undefined;
  };
  // 聚合参考线取各指数真实入场/预警阈值最小值；防御轮动为价格分位信号，不参与贪婪图参考线
  const broadMinPit = minOf(broadIndices, 'pit_greed_threshold') ?? minOf(broadIndices, 'pit_greed');
  const broadMinEntry = minOf(broadIndices, 'entry_greed_threshold') ?? minOf(broadIndices, 'entry_greed');
  const sectorMinPit = minOf(sectorIndices, 'pit_greed_threshold');
  const sectorMinEntry = minOf(sectorIndices, 'entry_greed_threshold');

  // 单指数参考线阈值（防御轮动为价格分位信号，不映射到贪婪图）
  const thresholdsByCode = (list: IndexStatus[]) => Object.fromEntries(
    list.map((i) => [i.fund_code, {
        pit_pct: i.pit_pct, entry_pct: i.entry_pct, exit_full_pct: i.exit_full_pct, exit_mode: i.exit_mode,
        pit_greed: i.pit_greed, entry_greed: i.entry_greed,
        pit_greed_threshold: i.pit_greed_threshold, entry_greed_threshold: i.entry_greed_threshold,
        exit_greed_threshold: i.exit_greed_threshold,
      }]),
  );
  const broadThresholds = thresholdsByCode(broadIndices);
  const sectorThresholds = thresholdsByCode(sectorIndices);

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
            aria-label={headerCollapsed ? '展开页头' : '收起页头'}
          >
            <h1 className="gp-title">黄金坑监测<span className="gp-title-en">GOLDEN PIT MONITOR</span></h1>
            {headerCollapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          </button>
          {!headerCollapsed && (
            <p className="gp-subtitle">宽基指数情绪三重确认底部检测 · 更新于 {as_of}</p>
          )}
          {headerCollapsed && (
            <span className="gp-header-asof">更新于 {as_of}</span>
          )}
          <div className="gp-header-actions">
            <button
              className="gp-refresh-btn"
              onClick={() => fetchData()}
              title="刷新数据"
              aria-label="刷新数据"
              disabled={refreshing}
            >
              <RefreshCw size={16} className={refreshing ? 'gp-spin' : ''} />
            </button>
            <button className="gp-config-btn" onClick={openConfig} title="板块拆分配置" aria-label="板块拆分配置" ref={configTriggerRef}>
              <Settings size={16} />
            </button>
          </div>
        </div>
        {refreshError && (
          <div className="gp-refresh-banner" role="alert">
            <AlertTriangle size={14} />
            <span>{refreshError}</span>
            <button className="gp-refresh-banner-close" onClick={() => setRefreshError(null)} aria-label="关闭提示">
              <X size={12} />
            </button>
          </div>
        )}

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
            <TechStatusPanel tech={techStatus} sectorConfig={configItems} carrier={status?.sector_selection?.carrier} />

            {global_macro && <CapitalFlowPanel macro={global_macro} />}

            <IndustryMonitorPanel monitor={status?.industry_monitor ?? null} />

            <section className="gp-panel gp-section">
              <div className="gp-panel-head">
                <span className="gp-tick" /><h2>宽基指数状态</h2><span className="gp-en">BROAD INDEX STATUS</span>
                <span className="gp-count">{broadIndices.length}</span>
                <button className="gp-fold-btn" onClick={() => setBroadOpen(!broadOpen)} title={broadOpen ? '收起' : '展开'} aria-label={broadOpen ? '收起宽基指数状态' : '展开宽基指数状态'}>
                  {broadOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  {broadOpen ? '收起' : '展开'}
                </button>
              </div>
              {broadOpen && (
                <div className="gp-index-grid">
                  {broadIndices.map((idx) => (
                    <IndexStatusCard key={idx.fund_code} idx={idx} displayConfig={displayConfig} />
                  ))}
                </div>
              )}
            </section>

            {sectorIndices.length > 0 && (
              <section className="gp-panel gp-section">
                <div className="gp-panel-head">
                  <span className="gp-tick" /><h2>板块指数状态</h2><span className="gp-en">SECTOR ROTATION</span>
                  <span className="gp-count">{sectorIndices.length}</span>
                  <button className="gp-fold-btn" onClick={() => setSectorOpen(!sectorOpen)} title={sectorOpen ? '收起' : '展开'} aria-label={sectorOpen ? '收起板块指数状态' : '展开板块指数状态'}>
                    {sectorOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                    {sectorOpen ? '收起' : '展开'}
                  </button>
                </div>
                {sectorOpen && (
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
                )}
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
                <div className="gp-head-right">
                  {chartTab !== 'share' && rangeQuick}
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
              </div>
              {showChart && chartTab === 'greed' && (
                <>
                  <TrendChart
                    groupLabel="宽基指数"
                    thresholds={broadThresholds}
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
                  <TrendChart
                    groupLabel="板块指数"
                    thresholds={sectorThresholds}
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
      {configOpen && createPortal(
        <div className="gp-config-overlay" onClick={closeConfig}>
          <div
            className="gp-config-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="gp-config-title"
            ref={configModalRef}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="gp-config-head">
              <div className="gp-config-title">
                <h3 id="gp-config-title">板块拆分配置</h3>
                <p>科创50/创业板 拆分为板块 ETF 的运行时参数，保存后即时生效</p>
              </div>
              <button className="gp-config-close" onClick={closeConfig} title="关闭" aria-label="关闭" ref={configCloseRef}>
                <X size={16} />
              </button>
            </div>
            <div className="gp-config-body">
              {status?.sector_selection?.carrier && (
                <div className="gp-config-carrier-preview">
                  <b>DCA 执行载体：</b>{status.sector_selection.carrier.note}
                  <br />
                  {status.sector_selection.carrier.targets.map((t) => (
                    <span key={t.fund_code}>
                      {t.fund_code} → {t.mode}
                      {t.codes && t.codes.length > 0 ? `（${t.codes.map((c) => `${c.code} ${Math.round(c.weight * 100)}%`).join(' + ')}）` : ''}
                      {'　'}
                    </span>
                  ))}
                </div>
              )}
              {configItems.map((item) => (
                <Fragment key={item.config_key}>
                  {item.config_key === 'dca_carrier_enabled' && (
                    <div className="gp-config-divider">DCA 执行载体</div>
                  )}
                  {item.config_key === 'industry_pool_enabled' && (
                    <div className="gp-config-divider">全行业监测 · 资金池</div>
                  )}
                  <div className="gp-config-row">
                    <div className="gp-config-info">
                      <span className="gp-config-label">{item.label}</span>
                      <span className="gp-config-desc">{item.description}</span>
                    </div>
                    {item.value_type === 'bool' ? (
                      <label className="gp-config-switch">
                        <input
                          type="checkbox"
                          checked={!!item.value}
                          onChange={(e) => onConfigItemChange(item.config_key, e.target.checked)}
                        />
                        <span className="gp-config-switch-track" />
                      </label>
                    ) : item.value_type === 'json' ? (
                      <textarea
                        className="gp-config-textarea"
                        value={String(item.value)}
                        spellCheck={false}
                        onChange={(e) => onConfigItemChange(item.config_key, e.target.value)}
                      />
                    ) : item.value_type === 'string' ? (
                      <input
                        type="text"
                        className="gp-config-input"
                        style={{ textAlign: 'left', width: 180 }}
                        value={String(item.value)}
                        onChange={(e) => onConfigItemChange(item.config_key, e.target.value)}
                      />
                    ) : (
                      <input
                        type="number"
                        className="gp-config-input"
                        value={String(item.value)}
                        step="any"
                        onChange={(e) => onConfigItemChange(item.config_key, e.target.value)}
                      />
                    )}
                  </div>
                </Fragment>
              ))}
            </div>
            <div className="gp-config-foot">
              <span className="gp-config-msg">{configMsg}</span>
              <button className="gp-config-cancel" onClick={closeConfig} disabled={configSaving}>
                取消
              </button>
              <button className="gp-config-save" onClick={saveConfig} disabled={configSaving}>
                {configSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
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
    let ready = false;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function resize() {
      w = Math.max(1, window.innerWidth);
      h = Math.max(1, window.innerHeight);
      canvas!.width = w;
      canvas!.height = h;
      if (reducedMotion && ready) drawFrame();
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

    function drawFrame() {
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

    }

    function animate() {
      drawFrame();
      time += 0.007;
      animId = requestAnimationFrame(animate);
    }

    ready = true;
    if (reducedMotion) {
      drawFrame();
    } else {
      animate();
    }

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
    };
  }, [canvasRef]);
}
