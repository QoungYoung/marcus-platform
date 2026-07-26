import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, TrendingUp, BarChart3, AlertTriangle, Ban } from 'lucide-react';
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

const GREEN = '#2ecc71';
const RED = '#e74c3c';

// ── 工具 ──
function fmtAmount(val: number): string {
  if (val >= 1e8) return `${(val / 1e8).toFixed(1)}亿`;
  if (val >= 1e4) return `${(val / 1e4).toFixed(0)}万`;
  return val.toLocaleString();
}

function scoreColor(score: number): string {
  if (score >= 80) return 'var(--score-hot)';
  if (score >= 60) return 'var(--score-warm)';
  if (score >= 40) return 'var(--score-neutral)';
  if (score >= 20) return 'var(--score-cool)';
  return 'var(--score-cold)';
}

// ── 骨架屏 ──
function SkeletonRow({ idx }: { idx: number }) {
  return (
    <tr className="lb-skeleton-row" style={{ animationDelay: `${idx * 0.05}s` }}>
      <td><div className="lb-skeleton" /></td>
      <td><div className="lb-skeleton" style={{ width: '80%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '70%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '50%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '60%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '60%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '50%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '60%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '60%' }} /></td>
      <td><div className="lb-skeleton" style={{ width: '60%' }} /></td>
    </tr>
  );
}

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

  // 首次加载
  useEffect(() => {
    setLoading(true);
    fetchData(false);
  }, [fetchData]);

  // 5 分钟自动刷新
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
    <div className="lb-page">
      {/* ── 页头 ── */}
      <div className="lb-header">
        <div className="lb-header-left">
          <h1 className="lb-title">
            <BarChart3 size={24} />
            行业龙头排行
          </h1>
          <span className={`lb-regime ${regimeInfo.className}`}>
            <TrendingUp size={14} />
            {regimeInfo.label}
          </span>
          {data?.volume_data === 'degraded' && (
            <span className="lb-degraded-badge">量价降级</span>
          )}
          {data?.data_source === 'tushare' && (
            <span className="lb-degraded-badge">Tushare源</span>
          )}
        </div>

        <div className="lb-header-right">
          {/* 行业筛选 */}
          <select
            className="lb-industry-select"
            value={filterIndustry}
            onChange={(e) => setFilterIndustry(e.target.value)}
          >
            <option value="">全部行业</option>
            {(data?.industries_covered || []).map((ind) => (
              <option key={ind} value={ind}>{ind}</option>
            ))}
          </select>

          <button className="lb-refresh-btn" onClick={handleRefresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? 'spinning' : ''} />
            刷新
          </button>
        </div>
      </div>

      {/* ── 最后更新 ── */}
      {lastUpdate && (
        <div className="lb-meta">
          更新于 {lastUpdate.toLocaleTimeString()} · {data?.items.length || 0} 条结果
          {data?.data_source === 'tushare' ? ' · 腾讯数据源不可用，已降级' : ''}
        </div>
      )}

      {/* ── 错误 ── */}
      {error && (
        <div className="lb-error">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      {/* ── 表格 ── */}
      <div className="lb-table-wrap">
        <table className="lb-table">
          <thead>
            <tr>
              <th className="col-rank">#</th>
              <th className="col-name">股票</th>
              <th className="col-industry">行业</th>
              <th
                className={`col-pct sortable ${sortBy === 'change_pct' ? 'active' : ''}`}
                onClick={() => handleSort('change_pct')}
              >
                涨跌幅{sortBy === 'change_pct' ? ' ▾' : ''}
              </th>
              <th className="col-amount">成交额</th>
              <th
                className={`col-score sortable ${sortBy === 'composite_score' ? 'active' : ''}`}
                onClick={() => handleSort('composite_score')}
              >
                综合分{sortBy === 'composite_score' ? ' ▾' : ''}
              </th>
              <th
                className={`col-score sortable ${sortBy === 'trend_score' ? 'active' : ''}`}
                onClick={() => handleSort('trend_score')}
              >
                趋势{sortBy === 'trend_score' ? ' ▾' : ''}
              </th>
              <th
                className={`col-score sortable ${sortBy === 'capital_score' ? 'active' : ''}`}
                onClick={() => handleSort('capital_score')}
              >
                资金{sortBy === 'capital_score' ? ' ▾' : ''}
              </th>
              <th
                className={`col-score sortable ${sortBy === 'volume_price_score' ? 'active' : ''}`}
                onClick={() => handleSort('volume_price_score')}
              >
                量价{sortBy === 'volume_price_score' ? ' ▾' : ''}
              </th>
              <th
                className={`col-score sortable ${sortBy === 'industry_relative_score' ? 'active' : ''}`}
                onClick={() => handleSort('industry_relative_score')}
              >
                行业强度{sortBy === 'industry_relative_score' ? ' ▾' : ''}
              </th>
              <th
                className={`col-score sortable ${sortBy === 'price_residual_score' ? 'active' : ''}`}
                onClick={() => handleSort('price_residual_score')}
              >
                价格{sortBy === 'price_residual_score' ? ' ▾' : ''}
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              Array.from({ length: 10 }).map((_, i) => <SkeletonRow key={i} idx={i} />)
            ) : (
              (data?.items || []).map((item, idx) => (
                <React.Fragment key={item.symbol}>
                  <tr
                    className={`lb-row ${expandedSymbol === item.symbol ? 'lb-row--active' : ''}`}
                    onClick={() => handleRowClick(item.symbol)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="col-rank">{idx + 1}</td>
                    <td className="col-name">
                      <div className="lb-stock-name">
                        <span className="lb-stock-symbol">{item.symbol.split('.')[0]}</span>
                        <span className="lb-stock-cname">{item.name}</span>
                      </div>
                      <div className="lb-stock-warnings">
                        {item.warnings.includes('untradeable') && (
                          <span className="lb-warn-tag untradeable"><Ban size={10} /> 一字板</span>
                        )}
                        {item.warnings.includes('overheat') && (
                          <span className="lb-warn-tag overheat"><AlertTriangle size={10} /> 过热</span>
                        )}
                        {item.warnings.includes('high_pe') && (
                          <span className="lb-warn-tag high-pe"><AlertTriangle size={10} /> 高PE</span>
                        )}
                      </div>
                    </td>
                    <td className="col-industry">{item.industry}</td>
                    <td className="col-pct" style={{ color: item.change_pct >= 0 ? RED : GREEN }}>
                      {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </td>
                    <td className="col-amount">{fmtAmount(item.turnover_amount)}</td>
                    <td className="col-score">
                      <span
                        className="lb-score-badge"
                        style={{ backgroundColor: scoreColor(item.composite_score) }}
                      >
                        {item.composite_score.toFixed(1)}
                      </span>
                    </td>
                    <td className="col-score dim">{item.trend_score.toFixed(1)}</td>
                    <td className="col-score dim">
                      {item.capital_score.toFixed(1)}
                      {item.capital_data === 'neutral' && (
                        <span className="lb-capital-hint" title="非Top10，资金分取中性值">~</span>
                      )}
                      {item.capital_data === 'unavailable' && (
                        <span className="lb-capital-hint" title="东方财富接口不可用">!</span>
                      )}
                    </td>
                    <td className="col-score dim">{item.volume_price_score.toFixed(1)}</td>
                    <td className="col-score dim">{item.industry_relative_score.toFixed(1)}</td>
                    <td className="col-score dim">{item.price_residual_score.toFixed(1)}</td>
                  </tr>
                  {expandedSymbol === item.symbol && item.score_detail && (
                    <tr className="lb-detail-row">
                      <td colSpan={11} className="lb-detail-cell">
                        <div className="lb-detail-grid">
                          {Object.entries(item.score_detail).map(([key, dim]) => (
                            <div key={key} className="lb-detail-card">
                              <div className="lb-detail-card-header">
                                <span className="lb-detail-card-label">{dim.label}</span>
                                <span className="lb-detail-card-score" style={{ color: scoreColor((dim.score / dim.max) * 100) }}>
                                  {dim.score.toFixed(1)} / {dim.max}
                                </span>
                              </div>
                              <div className="lb-detail-card-subs">
                                {dim.sub_scores.map((sub, si) => (
                                  <div key={si} className="lb-detail-sub-row">
                                    <div className="lb-detail-sub-header">
                                      <span className="lb-detail-sub-label">{sub.label}</span>
                                      <span className="lb-detail-sub-score">{sub.score.toFixed(1)} / {sub.max.toFixed(1)}</span>
                                    </div>
                                    <div className="lb-detail-sub-reason">{sub.reason}</div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
