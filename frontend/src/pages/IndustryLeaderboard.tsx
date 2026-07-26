import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, AlertTriangle, Ban, X, Info } from 'lucide-react';
import { marketApi } from '../api/client';
import '../styles/agent-theme.css';
import '../styles/industry-leaderboard.css';

// ── 类型 ──
interface ScoreDetailSubItem {
  label: string;
  score: number;
  max: number;
  reason: string;
}

interface ScoreDetailDimension {
  label: string;
  score: number;
  max: number;
  sub_scores: ScoreDetailSubItem[];
}

interface LeaderboardItem {
  symbol: string;
  name: string;
  industry: string;
  market_cap: number;
  change_pct: number;
  turnover_rate: number;
  turnover_amount: number;
  composite_score: number;
  trend_score: number;
  volume_price_score: number;
  industry_relative_score: number;
  price_residual_score: number;
  capital_score: number;
  capital_data: string;
  warnings: string[];
  data_source: string;
  volume_data: string;
  score_detail?: Record<string, ScoreDetailDimension>;
}

interface LeaderboardResponse {
  items: LeaderboardItem[];
  market_regime: string;
  industries_covered: string[];
  data_source: string;
  volume_data: string;
  trading_days: string[];
  updated_at: string;
}

interface ForwardReturnsData {
  symbol: string;
  name: string;
  benchmark_date: string;
  available: boolean;
  next_day_pct: number | null;
  day3_pct: number | null;
  day5_pct: number | null;
  sparkline_closes: number[];
  sparkline_dates: string[];
  warning: string;
}

type SortKey = 'composite_score' | 'trend_score' | 'volume_price_score' | 'industry_relative_score' | 'price_residual_score' | 'capital_score' | 'change_pct';

const SORT_LABELS: Record<SortKey, string> = {
  composite_score: '综合分',
  trend_score: '趋势分',
  volume_price_score: '量价分',
  industry_relative_score: '行业强度',
  price_residual_score: '价格分',
  capital_score: '资金分',
  change_pct: '涨跌幅',
};

const REGIME_LABELS: Record<string, { label: string }> = {
  trending: { label: '趋势市' },
  ranging: { label: '震荡市' },
  transitional: { label: '过渡期' },
};

// ── 品级系统 (DNA extracted) ──
const TIER_COLORS: Record<string, { label: string; color: string; minScore: number }> = {
  mythic:    { label: '神话', color: '#c6922e', minScore: 80 },
  legendary: { label: '传说', color: '#8a4fc0', minScore: 65 },
  epic:      { label: '史诗', color: '#d4743e', minScore: 50 },
  rare:      { label: '稀有', color: '#3d8cc7', minScore: 35 },
  common:    { label: '普通', color: '#6b7280', minScore: 0 },
};

function getTier(score: number) {
  for (const [key, t] of Object.entries(TIER_COLORS)) {
    if (score >= t.minScore) return { key, ...t };
  }
  return { key: 'common', ...TIER_COLORS.common };
}

function tierTagClass(key: string) {
  return `tier-tag tag-${key}`;
}

function tierCardClass(key: string) {
  return `rank-card tier-${key}`;
}

function getStatRank(pct: number): { letter: string; color: string; label: string } {
  if (pct >= 90) return { letter: 'S', color: '#c6922e', label: '卓越' };
  if (pct >= 75) return { letter: 'A', color: '#8a4fc0', label: '优秀' };
  if (pct >= 60) return { letter: 'B', color: '#3d8cc7', label: '良好' };
  if (pct >= 40) return { letter: 'C', color: '#6b7280', label: '一般' };
  if (pct >= 20) return { letter: 'D', color: '#9ca3af', label: '较弱' };
  return { letter: 'E', color: '#b0b8c4', label: '极弱' };
}

function pct(score: number, max: number): number {
  return Math.min(100, Math.max(0, (score / max) * 100));
}

// ── 工具 ──
function fmtAmount(val: number): string {
  if (val >= 1e8) return `${(val / 1e8).toFixed(1)}亿`;
  if (val >= 1e4) return `${(val / 1e4).toFixed(0)}万`;
  return val.toLocaleString();
}

// ── 骨架屏 ──
function SkeletonCard({ idx }: { idx: number }) {
  return (
    <div className="rank-card tier-common skeleton-card" style={{ animationDelay: `${idx * 0.06}s` }}>
      <div className="rank-num"><div className="skel-circle" /></div>
      <div className="avatar-sq"><div className="skel-block" /></div>
      <div className="info-area">
        <div className="skel-line" style={{ width: '40%' }} />
        <div className="skel-line" style={{ width: '65%', marginTop: 4 }} />
      </div>
      <div className="score-area">
        <div className="skel-line" style={{ width: 50, height: 18, marginLeft: 'auto' }} />
      </div>
      <div className="tier-tag tag-common"><div className="skel-line" style={{ width: 28, height: 12 }} /></div>
    </div>
  );
}

// ── 维度图标 ──
const DIM_ICONS: Record<string, string> = {
  '趋势综合': '↑',
  '量价配合': '↕',
  '行业相对强度': '◎',
  '价格残差': '◇',
  '资金持续性': '◆',
};

// ── 日期格式化 ──
function fmtDate(ymd: string): string {
  if (ymd.length !== 8) return ymd;
  return `${ymd.substring(4, 6)}/${ymd.substring(6, 8)}`;
}

// ══════════════════════════════════════════════════════════════
//  背景 Canvas
// ══════════════════════════════════════════════════════════════
function useBackgroundCanvas(canvasRef: React.RefObject<HTMLCanvasElement | null>) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let w = 0, h = 0;
    let animId = 0;
    let time = 0;

    function resize() {
      w = Math.max(1, window.innerWidth);
      h = Math.max(1, window.innerHeight);
      canvas!.width = w;
      canvas!.height = h;
    }
    window.addEventListener('resize', resize);
    resize();

    // 粒子
    const particles: { x: number; y: number; size: number; sx: number; sy: number; alpha: number }[] = [];
    for (let i = 0; i < 80; i++) {
      particles.push({
        x: Math.random() * w,
        y: Math.random() * h,
        size: Math.random() * 2.5 + 0.8,
        sx: (Math.random() - 0.5) * 0.25,
        sy: (Math.random() - 0.5) * 0.25,
        alpha: Math.random() * 0.4 + 0.1,
      });
    }

    // 光环
    const halos = [
      { x: 0.15, y: 0.10, r: 180, speed: 0.004, phase: 0 },
      { x: 0.85, y: 0.20, r: 140, speed: -0.005, phase: 1.2 },
      { x: 0.50, y: 0.85, r: 200, speed: 0.003, phase: 2.5 },
      { x: 0.08, y: 0.75, r: 120, speed: -0.006, phase: 0.8 },
      { x: 0.92, y: 0.70, r: 100, speed: 0.007, phase: 1.8 },
    ];

    function draw() {
      ctx!.clearRect(0, 0, w, h);

      // 光环
      const minDim = Math.min(w, h);
      for (const hd of halos) {
        const r = hd.r * minDim / 800;
        if (!isFinite(r) || r <= 0) continue;
        const cx = hd.x * w;
        const cy = hd.y * h;
        const angle = time * hd.speed + hd.phase;

        ctx!.save();
        ctx!.translate(cx, cy);
        ctx!.rotate(angle);

        const r0 = Math.max(0.1, r * 0.2);
        const r1 = Math.max(0.1, r);
        const grad = ctx!.createRadialGradient(0, 0, r0, 0, 0, r1);
        grad.addColorStop(0, 'rgba(45,140,240,0)');
        grad.addColorStop(0.7, 'rgba(45,140,240,0.04)');
        grad.addColorStop(1, 'rgba(45,140,240,0.12)');
        ctx!.beginPath();
        ctx!.arc(0, 0, r1, 0, Math.PI * 2);
        ctx!.fillStyle = grad;
        ctx!.fill();

        if (r * 0.7 > 0.5) {
          ctx!.beginPath();
          ctx!.arc(0, 0, r * 0.7, 0, Math.PI * 2);
          ctx!.strokeStyle = 'rgba(45,140,240,0.15)';
          ctx!.lineWidth = 1.5;
          ctx!.stroke();
        }

        if (r * 0.45 > 0.5) {
          ctx!.beginPath();
          ctx!.arc(0, 0, r * 0.45, 0, Math.PI * 2);
          ctx!.strokeStyle = 'rgba(45,140,240,0.08)';
          ctx!.lineWidth = 1;
          ctx!.setLineDash([6, 8]);
          ctx!.stroke();
          ctx!.setLineDash([]);
        }

        const dotR = r * 0.55;
        if (dotR > 2) {
          for (let i = 0; i < 8; i++) {
            const a = (i / 8) * Math.PI * 2 + time * 0.02;
            ctx!.beginPath();
            ctx!.arc(Math.cos(a) * dotR, Math.sin(a) * dotR, 2, 0, Math.PI * 2);
            ctx!.fillStyle = 'rgba(45,140,240,0.2)';
            ctx!.fill();
          }
        }
        ctx!.restore();
      }

      // 粒子
      for (const p of particles) {
        p.x += p.sx;
        p.y += p.sy;
        if (p.x < 0 || p.x > w) p.sx *= -1;
        if (p.y < 0 || p.y > h) p.sy *= -1;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx!.fillStyle = `rgba(45,140,240,${p.alpha})`;
        ctx!.fill();
      }

      // 连接线
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 120) {
            ctx!.beginPath();
            ctx!.moveTo(particles[i].x, particles[i].y);
            ctx!.lineTo(particles[j].x, particles[j].y);
            ctx!.strokeStyle = `rgba(45,140,240,${(1 - dist / 120) * 0.08})`;
            ctx!.lineWidth = 0.6;
            ctx!.stroke();
          }
        }
      }

      time += 0.01;
      animId = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
    };
  }, [canvasRef]);
}

// ══════════════════════════════════════════════════════════════
//  主组件
// ══════════════════════════════════════════════════════════════
export default function IndustryLeaderboardPage() {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortKey>('composite_score');
  const [filterIndustry, setFilterIndustry] = useState('');
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [modalItem, setModalItem] = useState<LeaderboardItem | null>(null);
  const [entranceDone, setEntranceDone] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [forwardReturns, setForwardReturns] = useState<ForwardReturnsData | null>(null);
  const [forwardLoading, setForwardLoading] = useState(false);

  useBackgroundCanvas(canvasRef);

  // 入场动画
  useEffect(() => {
    if (!loading && data?.items.length) {
      const timer = setTimeout(() => setEntranceDone(true), 100 + data.items.length * 60 + 500);
      return () => clearTimeout(timer);
    }
  }, [loading, data?.items.length]);

  const openModal = async (item: LeaderboardItem) => {
    setModalItem(item);
    // If viewing historical date, fetch forward returns
    if (selectedDate && data?.trading_days?.length && selectedDate < data.trading_days[0]) {
      setForwardLoading(true);
      setForwardReturns(null);
      try {
        const resp = await marketApi.getForwardReturns(item.symbol, selectedDate);
        setForwardReturns(resp.data as ForwardReturnsData);
      } catch {
        setForwardReturns(null);
      } finally {
        setForwardLoading(false);
      }
    } else {
      setForwardReturns(null);
    }
  };
  const closeModal = () => { setModalItem(null); setForwardReturns(null); };

  const fetchData = useCallback(async (refresh = false) => {
    try {
      setError(null);
      const resp = await marketApi.getIndustryLeaderboard({
        limit: 50,
        sort_by: sortBy,
        industry: filterIndustry || undefined,
        refresh,
        date: selectedDate || undefined,
      });
      setData(resp.data as LeaderboardResponse);
      setLastUpdate(new Date());
      setEntranceDone(false);
    } catch (e: any) {
      setError(e?.message || 'Failed to fetch leaderboard');
    } finally {
      setLoading(false);
    }
  }, [sortBy, filterIndustry, selectedDate]);

  useEffect(() => {
    setLoading(true);
    fetchData(false);
  }, [fetchData]);

  useEffect(() => {
    const timer = setInterval(() => fetchData(true), 300000);
    return () => clearInterval(timer);
  }, [fetchData]);

  const handleSort = (key: SortKey) => setSortBy(key);
  const handleRefresh = () => { setLoading(true); fetchData(true); };

  const regime = data?.market_regime || 'transitional';
  const regimeLabel = REGIME_LABELS[regime]?.label || '过渡期';

  const now = new Date();
  const timeStr = `${now.getFullYear()}/${String(now.getMonth()+1).padStart(2,'0')}/${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;

  return (
    <div className="ba-page">
      {/* 背景 Canvas */}
      <canvas ref={canvasRef} id="bgCanvas" />

      {/* 主容器 */}
      <div className="main-container">
        {/* ── 标题 ── */}
        <div className="header-section">
          <div className="header-accent-line">
            <span className="accent-dash" />
            <span className="header-label">SECTOR · TACTICAL DATA</span>
            <span className="accent-dash" />
          </div>
          <h1 className="header-title">
            行业龙头<span className="ba-icon" />排行榜
          </h1>
          <div className="header-sub">
            —— 实时战术数据 · 五阶品级 ——
            <span className="header-regime">【{regimeLabel}】</span>
          </div>
          {data?.volume_data === 'degraded' && (
            <span className="header-degraded">量价降级</span>
          )}
          {data?.data_source === 'tushare' && (
            <span className="header-degraded">Tushare源</span>
          )}
          <hr className="divider-hr" />
        </div>

        {/* ── 时间线 ── */}
        {data?.trading_days && data.trading_days.length > 0 && (
          <div className="timeline-bar">
            <button
              className={`timeline-pill ${!selectedDate ? 'timeline-pill--active' : ''}`}
              onClick={() => setSelectedDate(null)}
            >
              最新
            </button>
            {data.trading_days.map((d) => (
              <button
                key={d}
                className={`timeline-pill ${selectedDate === d ? 'timeline-pill--active' : ''}`}
                onClick={() => setSelectedDate(d)}
              >
                {fmtDate(d)}
              </button>
            ))}
          </div>
        )}

        {/* ── 工具栏 ── */}
        <div className="toolbar">
          <div className="toolbar-left">
            <div className="tier-legend">
              {Object.entries(TIER_COLORS).map(([key, t]) => (
                <span key={key} className={tierTagClass(key)}>{t.label}</span>
              ))}
            </div>
          </div>
          <div className="toolbar-right">
            <select
              className="ba-select"
              value={filterIndustry}
              onChange={(e) => setFilterIndustry(e.target.value)}
            >
              <option value="">全部行业</option>
              {(data?.industries_covered || []).map((ind) => (
                <option key={ind} value={ind}>{ind}</option>
              ))}
            </select>
            <button className="ba-btn" onClick={handleRefresh} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'ba-spin' : ''} />
              刷新
            </button>
          </div>
        </div>

        {/* ── 排序栏 ── */}
        <div className="sort-bar">
          {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
            <button
              key={key}
              className={`sort-tab ${sortBy === key ? 'active' : ''}`}
              onClick={() => handleSort(key)}
            >
              {SORT_LABELS[key]}
            </button>
          ))}
        </div>

        {/* ── 错误 ── */}
        {error && (
          <div className="ba-error">
            <AlertTriangle size={15} /> {error}
          </div>
        )}

        {/* ── 排行榜列表 ── */}
        <div className="ranking-list">
          {loading && !data ? (
            Array.from({ length: 8 }).map((_, i) => <SkeletonCard key={i} idx={i} />)
          ) : (
            (data?.items || []).map((item, idx) => {
              const tier = getTier(item.composite_score);
              const stockChar = item.name.charAt(0);
              const delay = 80 + idx * 60;

              return (
                <div
                  key={item.symbol}
                  className={tierCardClass(tier.key)}
                  style={{
                    opacity: entranceDone ? undefined : 0,
                    transform: entranceDone ? undefined : 'translateY(18px)',
                    animationDelay: `${delay}ms`,
                  }}
                  onClick={() => openModal(item)}
                >
                  <div className="rank-num">{idx + 1}</div>

                  <div
                    className="avatar-sq"
                    style={{ background: `${tier.color}18`, color: tier.color, borderColor: `${tier.color}30` }}
                  >
                    {stockChar}
                  </div>

                  <div className="info-area">
                    <div className="char-name">
                      {item.name}
                      <span className="char-symbol">{item.symbol.split('.')[0]}</span>
                    </div>
                    <div className="char-detail">
                      {item.industry} · 成交 {fmtAmount(item.turnover_amount)}
                      {item.warnings.includes('untradeable') && (
                        <span className="warn-tag warn-lock"><Ban size={9} /> 一字板</span>
                      )}
                      {item.warnings.includes('overheat') && (
                        <span className="warn-tag warn-hot"><AlertTriangle size={9} /> 过热</span>
                      )}
                      {item.warnings.includes('high_pe') && (
                        <span className="warn-tag warn-pe"><AlertTriangle size={9} /> 高PE</span>
                      )}
                      {item.capital_data === 'neutral' && (
                        <span className="warn-tag warn-neutral">资金中性</span>
                      )}
                      {item.capital_data === 'unavailable' && (
                        <span className="warn-tag warn-neutral">资金N/A</span>
                      )}
                    </div>
                  </div>

                  <div className="score-area">
                    <div className="score-change" style={{ color: item.change_pct >= 0 ? '#e74c3c' : '#2ecc71' }}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </div>
                    <div className="score-bar-wrap">
                      <div
                        className="score-bar-fill"
                        style={{ width: `${item.composite_score}%`, background: tier.color }}
                      />
                    </div>
                    <div className="score-value" style={{ color: tier.color }}>
                      {item.composite_score.toFixed(1)}
                    </div>
                    <div className="score-subs">
                      <span title="趋势">{item.trend_score.toFixed(1)}</span>
                      <span title="资金">{item.capital_score.toFixed(1)}</span>
                      <span title="量价">{item.volume_price_score.toFixed(1)}</span>
                      <span title="强度">{item.industry_relative_score.toFixed(1)}</span>
                      <span title="价格">{item.price_residual_score.toFixed(1)}</span>
                    </div>
                  </div>

                  <span className={tierTagClass(tier.key)}>{tier.label}</span>

                  <Info size={14} className="card-info-icon" />
                </div>
              );
            })
          )}
        </div>

        {/* ── 底部 ── */}
        <div className="footer-note">
          <span>DATA UPDATED</span>
          <span className="footer-dot" />
          <span>{lastUpdate ? lastUpdate.toLocaleTimeString() : '--'}</span>
          <span className="footer-dot" />
          <span>MARCUS · SECTOR LEADERBOARD</span>
        </div>
      </div>

      {/* ── 战斗参数弹窗 ── */}
      {modalItem && modalItem.score_detail && (
        <div className="bp-overlay" onClick={closeModal}>
          <div className="bp-panel" onClick={(e) => e.stopPropagation()}>
            {/* 四角锁定框 */}
            <span className="bp-corner bp-corner--tl" />
            <span className="bp-corner bp-corner--tr" />
            <span className="bp-corner bp-corner--bl" />
            <span className="bp-corner bp-corner--br" />

            {/* 扫描线 */}
            <div className="bp-scanline" />

            {/* 头部 HUD */}
            <div className="bp-header">
              <div className="bp-header-top">
                <span className="bp-hud-label">TARGET ANALYSIS</span>
                <span className="bp-hud-id">ID::{modalItem.symbol.split('.')[0]}</span>
              </div>
              <div className="bp-header-main">
                <div className="bp-target-name">{modalItem.name}</div>
                <div className="bp-header-right">
                  <span className={`bp-threat bp-threat--${getTier(modalItem.composite_score).key}`}>
                    THREAT · {getTier(modalItem.composite_score).label}
                  </span>
                  <button className="bp-close" onClick={closeModal}>
                    <X size={16} />
                    <span>ESC</span>
                  </button>
                </div>
              </div>
              <div className="bp-header-bar">
                <div className="bp-header-bar-label">COMPOSITE SCORE</div>
                <div className="bp-header-bar-track">
                  <div
                    className="bp-header-bar-fill"
                    style={{
                      width: `${modalItem.composite_score}%`,
                      background: `var(--bp-accent, ${getTier(modalItem.composite_score).color})`,
                      boxShadow: `0 0 12px ${getTier(modalItem.composite_score).color}66`,
                    }}
                  />
                </div>
                <div className="bp-header-bar-val" style={{ color: getTier(modalItem.composite_score).color }}>
                  {modalItem.composite_score.toFixed(1)}
                </div>
              </div>
            </div>

            {/* ── 战斗参数 ── */}
            <div className="bp-params">
              {(() => {
                const entries = Object.entries(modalItem.score_detail);
                const topRow = entries.slice(0, 2);
                const bottomRow = entries.slice(2, 5);
                return (
                  <>
                    <div className="bp-stat-row bp-stat-row--top">
                      {topRow.map(([key, dim], idx) => {
                        const dimPct = pct(dim.score, dim.max);
                        const rank = getStatRank(dimPct);
                        return (
                          <div key={key} className="bp-stat-card bp-stat-card--large" style={{ animationDelay: `${idx * 0.08}s` }}>
                            <div className="bp-stat-card-inner">
                              <div className="bp-stat-top">
                                <div className="bp-stat-info">
                                  <span className="bp-stat-icon">{DIM_ICONS[dim.label] || '·'}</span>
                                  <span className="bp-stat-label">{dim.label}</span>
                                </div>
                                <div className="bp-stat-rank" style={{ borderColor: rank.color, color: rank.color }}>
                                  <span className="bp-stat-rank-letter">{rank.letter}</span>
                                  <span className="bp-stat-rank-label">{rank.label}</span>
                                </div>
                              </div>
                              <div className="bp-stat-value-row">
                                <span className="bp-stat-value" style={{ color: rank.color }}>
                                  {dim.score.toFixed(1)}
                                </span>
                                <span className="bp-stat-max">/ {dim.max}</span>
                              </div>
                              <div className="bp-stat-gauge">
                                <div className="bp-stat-gauge-track">
                                  {[20, 40, 60, 75, 90].map((threshold) => (
                                    <div key={threshold} className="bp-stat-gauge-notch" style={{ left: `${threshold}%` }} />
                                  ))}
                                  <div
                                    className="bp-stat-gauge-fill"
                                    style={{
                                      width: `${dimPct}%`,
                                      background: rank.color,
                                      boxShadow: dimPct >= 60 ? `0 0 8px ${rank.color}66` : 'none',
                                    }}
                                  />
                                </div>
                                <div className="bp-stat-gauge-labels">
                                  <span>E</span><span>D</span><span>C</span><span>B</span><span>A</span><span>S</span>
                                </div>
                              </div>
                              <div className="bp-stat-attributes">
                                {dim.sub_scores.map((sub, si) => (
                                  <div key={si} className="bp-attr">
                                    <div className="bp-attr-head">
                                      <span className="bp-attr-dot" />
                                      <span className="bp-attr-name">{sub.label}</span>
                                      <span className="bp-attr-val">{sub.score.toFixed(1)}</span>
                                    </div>
                                    <p className="bp-attr-desc">{sub.reason}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="bp-stat-row bp-stat-row--bottom">
                      {bottomRow.map(([key, dim], idx) => {
                        const dimPct = pct(dim.score, dim.max);
                        const rank = getStatRank(dimPct);
                        return (
                          <div key={key} className="bp-stat-card bp-stat-card--small" style={{ animationDelay: `${(idx + 2) * 0.08}s` }}>
                            <div className="bp-stat-card-inner">
                              <div className="bp-stat-top">
                                <div className="bp-stat-info">
                                  <span className="bp-stat-icon">{DIM_ICONS[dim.label] || '·'}</span>
                                  <span className="bp-stat-label">{dim.label}</span>
                                </div>
                                <div className="bp-stat-rank" style={{ borderColor: rank.color, color: rank.color }}>
                                  <span className="bp-stat-rank-letter">{rank.letter}</span>
                                </div>
                              </div>
                              <div className="bp-stat-value-row">
                                <span className="bp-stat-value bp-stat-value--sm" style={{ color: rank.color }}>
                                  {dim.score.toFixed(1)}
                                </span>
                                <span className="bp-stat-max">/ {dim.max}</span>
                              </div>
                              <div className="bp-stat-gauge">
                                <div className="bp-stat-gauge-track">
                                  {[20, 40, 60, 75, 90].map((threshold) => (
                                    <div key={threshold} className="bp-stat-gauge-notch" style={{ left: `${threshold}%` }} />
                                  ))}
                                  <div
                                    className="bp-stat-gauge-fill"
                                    style={{
                                      width: `${dimPct}%`,
                                      background: rank.color,
                                      boxShadow: dimPct >= 60 ? `0 0 8px ${rank.color}66` : 'none',
                                    }}
                                  />
                                </div>
                              </div>
                              <div className="bp-stat-attributes">
                                {dim.sub_scores.map((sub, si) => (
                                  <div key={si} className="bp-attr">
                                    <div className="bp-attr-head">
                                      <span className="bp-attr-dot" />
                                      <span className="bp-attr-name">{sub.label}</span>
                                      <span className="bp-attr-val">{sub.score.toFixed(1)}</span>
                                    </div>
                                    <p className="bp-attr-desc">{sub.reason}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                );
              })()}
            </div>

            {/* ── 前瞻验证 (仅历史日期显示) ── */}
            {selectedDate && data?.trading_days?.length && selectedDate < data.trading_days[0] && (
              <div className="bp-forward">
                <div className="bp-forward-header">
                  <span className="bp-forward-label">FORWARD VALIDATION</span>
                  <span className="bp-forward-date">基准日: {selectedDate}</span>
                </div>

                {forwardLoading ? (
                  <div className="bp-forward-loading">加载前瞻数据...</div>
                ) : forwardReturns && forwardReturns.available ? (
                  <>
                    <div className="bp-forward-cards">
                      {[
                        { label: '次日涨幅', pct: forwardReturns.next_day_pct },
                        { label: '3日涨幅', pct: forwardReturns.day3_pct },
                        { label: '5日涨幅', pct: forwardReturns.day5_pct },
                      ].map((m) => (
                        <div key={m.label} className="bp-forward-card">
                          <div className="bp-forward-card-label">{m.label}</div>
                          <div
                            className="bp-forward-card-value"
                            style={{ color: m.pct != null ? (m.pct >= 0 ? '#e74c3c' : '#2ecc71') : '#6b7280' }}
                          >
                            {m.pct != null ? `${m.pct > 0 ? '+' : ''}${m.pct.toFixed(2)}%` : '—'}
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* Sparkline */}
                    {forwardReturns.sparkline_closes.length >= 2 && (
                      <div className="bp-sparkline">
                        <svg
                          viewBox={`0 0 ${forwardReturns.sparkline_closes.length * 30} 60`}
                          className="bp-sparkline-svg"
                        >
                          <polyline
                            fill="none"
                            stroke="var(--bp-accent, #2d8cf0)"
                            strokeWidth="1.5"
                            points={(() => {
                              const prices = forwardReturns.sparkline_closes;
                              const min = Math.min(...prices);
                              const max = Math.max(...prices);
                              const range = max - min || 1;
                              return prices
                                .map((p, i) => {
                                  const x = i * 30 + 10;
                                  const y = 55 - ((p - min) / range) * 45;
                                  return `${x},${y.toFixed(1)}`;
                                })
                                .join(' ');
                            })()}
                          />
                        </svg>
                        <div className="bp-sparkline-dates">
                          {forwardReturns.sparkline_dates.map((d, i) => (
                            <span key={i}>{fmtDate(d)}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="bp-forward-empty">
                    {forwardReturns?.warning || '暂无前瞻数据'}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
