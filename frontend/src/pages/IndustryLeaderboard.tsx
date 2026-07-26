import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, TrendingUp, BarChart3, AlertTriangle, Ban, ChevronDown, Zap, Star, Diamond, Shield, Circle } from 'lucide-react';
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
  updated_at: string;
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

const REGIME_LABELS: Record<string, { label: string; className: string }> = {
  trending: { label: '趋势市', className: 'regime-trending' },
  ranging: { label: '震荡市', className: 'regime-ranging' },
  transitional: { label: '过渡期', className: 'regime-transitional' },
};

// ── 品级系统 ──
type Tier = 'mythic' | 'legendary' | 'epic' | 'rare' | 'common';

interface TierConfig {
  key: Tier;
  label: string;
  minScore: number;
  icon: React.FC<{ size?: number }>;
  className: string;
}

const TIERS: TierConfig[] = [
  { key: 'mythic',    label: '神话', minScore: 80, icon: ({ size = 16 }) => <Diamond size={size} />, className: 'tier-mythic' },
  { key: 'legendary', label: '传说', minScore: 65, icon: ({ size = 16 }) => <Star size={size} />,   className: 'tier-legendary' },
  { key: 'epic',      label: '史诗', minScore: 50, icon: ({ size = 16 }) => <Zap size={size} />,    className: 'tier-epic' },
  { key: 'rare',      label: '稀有', minScore: 35, icon: ({ size = 16 }) => <Shield size={size} />, className: 'tier-rare' },
  { key: 'common',    label: '普通', minScore: 0,  icon: ({ size = 16 }) => <Circle size={size} />, className: 'tier-common' },
];

function getTier(score: number): TierConfig {
  for (const t of TIERS) {
    if (score >= t.minScore) return t;
  }
  return TIERS[TIERS.length - 1];
}

// score to 0-100 normalized for bar widths
function pct(score: number, max: number): number {
  return Math.min(100, Math.max(0, (score / max) * 100));
}

// ── 工具 ──
function fmtAmount(val: number): string {
  if (val >= 1e8) return `${(val / 1e8).toFixed(1)}亿`;
  if (val >= 1e4) return `${(val / 1e4).toFixed(0)}万`;
  return val.toLocaleString();
}

const GREEN = '#2ecc71';
const RED = '#e74c3c';

// ── 骨架屏 ──
function SkeletonRow({ idx }: { idx: number }) {
  return (
    <div className="lb-card lb-skeleton-card" style={{ animationDelay: `${idx * 0.06}s` }}>
      <div className="lb-card-rank"><div className="lb-skeleton-circle" /></div>
      <div className="lb-card-body">
        <div className="lb-skeleton" style={{ width: '30%', height: 16 }} />
        <div className="lb-skeleton" style={{ width: '50%', height: 12, marginTop: 6 }} />
      </div>
      <div className="lb-card-scores">
        <div className="lb-skeleton" style={{ width: 60, height: 20 }} />
      </div>
    </div>
  );
}

// ── 维度名称映射 ──
const DIM_ICONS: Record<string, string> = {
  '趋势综合': '📈',
  '量价配合': '📊',
  '行业相对强度': '🏭',
  '价格残差': '💹',
  '资金持续性': '💰',
};

export default function IndustryLeaderboardPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortKey>('composite_score');
  const [filterIndustry, setFilterIndustry] = useState('');
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);

  const handleRowClick = (symbol: string) => {
    setExpandedSymbol(expandedSymbol === symbol ? null : symbol);
  };

  const fetchData = useCallback(async (refresh = false) => {
    try {
      setError(null);
      const resp = await marketApi.getIndustryLeaderboard({
        limit: 50,
        sort_by: sortBy,
        industry: filterIndustry || undefined,
        refresh,
      });
      setData(resp.data as LeaderboardResponse);
      setLastUpdate(new Date());
    } catch (e: any) {
      setError(e?.message || 'Failed to fetch leaderboard');
    } finally {
      setLoading(false);
    }
  }, [sortBy, filterIndustry]);

  useEffect(() => {
    setLoading(true);
    fetchData(false);
  }, [fetchData]);

  useEffect(() => {
    const timer = setInterval(() => fetchData(true), 300000);
    return () => clearInterval(timer);
  }, [fetchData]);

  const handleSort = (key: SortKey) => {
    setSortBy(key);
  };

  const handleRefresh = () => {
    setLoading(true);
    fetchData(true);
  };

  const regime = data?.market_regime || 'transitional';
  const regimeInfo = REGIME_LABELS[regime] || REGIME_LABELS.transitional;

  return (
    <div className="ba-page">
      {/* ── 背景装饰 ── */}
      <div className="ba-bg-grid" />
      <div className="ba-bg-glow ba-bg-glow--top" />
      <div className="ba-bg-glow ba-bg-glow--bottom" />

      {/* ── 顶部导航栏 ── */}
      <header className="ba-header">
        <div className="ba-header-left">
          <div className="ba-logo">
            <div className="ba-logo-icon">
              <BarChart3 size={20} />
            </div>
            <div className="ba-logo-text">
              <span className="ba-logo-title">行业龙头排行</span>
              <span className="ba-logo-sub">SECTOR LEADERBOARD</span>
            </div>
          </div>
          <div className={`ba-regime ${regimeInfo.className}`}>
            <span className="ba-regime-dot" />
            {regimeInfo.label}
          </div>
          {data?.volume_data === 'degraded' && (
            <span className="ba-badge ba-badge--warn">量价降级</span>
          )}
          {data?.data_source === 'tushare' && (
            <span className="ba-badge ba-badge--warn">Tushare源</span>
          )}
        </div>

        <div className="ba-header-right">
          <div className="ba-tier-legend">
            {TIERS.map((tier) => (
              <span key={tier.key} className={`ba-tier-tag ${tier.className}`}>
                <tier.icon size={12} />
                {tier.label}
              </span>
            ))}
          </div>
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
            <RefreshCw size={15} className={loading ? 'ba-spin' : ''} />
            刷新
          </button>
        </div>
      </header>

      {/* ── 排序栏 ── */}
      <div className="ba-sort-bar">
        <div className="ba-sort-left">
          {lastUpdate && (
            <span className="ba-meta">
              更新于 {lastUpdate.toLocaleTimeString()} · {data?.items.length || 0} 条结果
              {data?.data_source === 'tushare' ? ' · 腾讯数据源不可用，已降级' : ''}
            </span>
          )}
        </div>
        <div className="ba-sort-tabs">
          {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
            <button
              key={key}
              className={`ba-sort-tab ${sortBy === key ? 'active' : ''}`}
              onClick={() => handleSort(key)}
            >
              {SORT_LABELS[key]}
              {sortBy === key && <ChevronDown size={12} className="ba-sort-arrow" />}
            </button>
          ))}
        </div>
      </div>

      {/* ── 错误 ── */}
      {error && (
        <div className="ba-error">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      {/* ── 排行榜列表 ── */}
      <div className="ba-leaderboard">
        {loading && !data ? (
          Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} idx={i} />)
        ) : (
          (data?.items || []).map((item, idx) => {
            const tier = getTier(item.composite_score);
            const isExpanded = expandedSymbol === item.symbol;
            return (
              <React.Fragment key={item.symbol}>
                <div
                  className={`ba-card ${tier.className} ${isExpanded ? 'ba-card--expanded' : ''}`}
                  onClick={() => handleRowClick(item.symbol)}
                >
                  {/* 排名区 */}
                  <div className="ba-card-rank">
                    <div className="ba-rank-badge">
                      <span className="ba-rank-num">{idx + 1}</span>
                      <tier.icon size={14} />
                    </div>
                    <span className={`ba-tier-label ${tier.className}`}>{tier.label}</span>
                  </div>

                  {/* 主体信息 */}
                  <div className="ba-card-body">
                    <div className="ba-card-name-row">
                      <span className="ba-card-symbol">{item.symbol.split('.')[0]}</span>
                      <span className="ba-card-cname">{item.name}</span>
                      <span className="ba-card-industry">{item.industry}</span>
                    </div>
                    <div className="ba-card-meta-row">
                      <span className="ba-card-amount">成交 {fmtAmount(item.turnover_amount)}</span>
                      {item.warnings.includes('untradeable') && (
                        <span className="ba-warn ba-warn--lock"><Ban size={10} /> 一字板</span>
                      )}
                      {item.warnings.includes('overheat') && (
                        <span className="ba-warn ba-warn--hot"><AlertTriangle size={10} /> 过热</span>
                      )}
                      {item.warnings.includes('high_pe') && (
                        <span className="ba-warn ba-warn--pe"><AlertTriangle size={10} /> 高PE</span>
                      )}
                      {item.capital_data === 'neutral' && (
                        <span className="ba-warn ba-warn--info">资金中性</span>
                      )}
                      {item.capital_data === 'unavailable' && (
                        <span className="ba-warn ba-warn--info">资金不可用</span>
                      )}
                    </div>
                  </div>

                  {/* 分数区 */}
                  <div className="ba-card-scores">
                    <div className="ba-card-change" style={{ color: item.change_pct >= 0 ? RED : GREEN }}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </div>
                    <div className="ba-score-bar-wrap">
                      <div
                        className={`ba-score-bar ${tier.className}`}
                        style={{ width: `${item.composite_score}%` }}
                      />
                    </div>
                    <div className={`ba-score-main ${tier.className}`}>
                      {item.composite_score.toFixed(1)}
                    </div>
                    <div className="ba-score-subs">
                      <span className="ba-sub-score" title="趋势">{item.trend_score.toFixed(1)}</span>
                      <span className="ba-sub-score" title="资金">{item.capital_score.toFixed(1)}</span>
                      <span className="ba-sub-score" title="量价">{item.volume_price_score.toFixed(1)}</span>
                      <span className="ba-sub-score" title="强度">{item.industry_relative_score.toFixed(1)}</span>
                      <span className="ba-sub-score" title="价格">{item.price_residual_score.toFixed(1)}</span>
                    </div>
                    <ChevronDown
                      size={16}
                      className={`ba-expand-arrow ${isExpanded ? 'ba-expand-arrow--open' : ''}`}
                    />
                  </div>
                </div>

                {/* 展开详情面板 */}
                {isExpanded && item.score_detail && (
                  <div className="ba-detail-panel">
                    <div className="ba-detail-inner">
                      {Object.entries(item.score_detail).map(([key, dim]) => (
                        <div key={key} className="ba-detail-dim">
                          <div className="ba-detail-dim-head">
                            <span className="ba-detail-dim-icon">{DIM_ICONS[dim.label] || '◆'}</span>
                            <span className="ba-detail-dim-label">{dim.label}</span>
                            <span className="ba-detail-dim-score">{dim.score.toFixed(1)} <i>/ {dim.max}</i></span>
                          </div>
                          <div className="ba-detail-dim-bar">
                            <div
                              className="ba-detail-dim-fill"
                              style={{ width: `${pct(dim.score, dim.max)}%` }}
                            />
                          </div>
                          <div className="ba-detail-subs">
                            {dim.sub_scores.map((sub, si) => (
                              <div key={si} className="ba-detail-sub">
                                <div className="ba-detail-sub-head">
                                  <span className="ba-detail-sub-label">{sub.label}</span>
                                  <span className="ba-detail-sub-score">{sub.score.toFixed(1)}/{sub.max.toFixed(1)}</span>
                                </div>
                                <p className="ba-detail-sub-reason">{sub.reason}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </React.Fragment>
            );
          })
        )}
      </div>
    </div>
  );
}
