import { useEffect, useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { marketApi } from '../api/client';
import '../styles/agent-theme.css';
import '../styles/market-page.css';

// ── 类型定义 ──
interface Index {
  symbol: string;
  name: string;
  current_price: number;
  change: number;
  change_pct: number;
  volume: number;
  history?: number[]; // 可选历史价格用于 sparkline
}

interface Sector {
  name: string;
  ts_code: string;
  pct_change: number;
  vol: number;
  amount: number;
  turnover_rate: number;
}

interface GlobalIndex {
  name: string;
  symbol: string;
  current: number;
  change: number;
  change_pct: number;
  update_time: string;
}

interface Commodity {
  name: string;
  symbol: string;
  current: number;
  change: number;
  change_pct: number;
  update_time: string;
}

interface Breadth {
  advancing: number;
  declining: number;
  unchanged: number;
  limit_up: number;
  limit_down: number;
  total_amount: number; // 亿元
}

interface Mover {
  symbol: string;
  name: string;
  current_price: number;
  change_pct: number;
}

type MoverTab = 'gainers' | 'losers' | 'active';

// ── Sparkline 迷你图表 ──
function Sparkline({ data, color }: { data: number[]; color: 'up' | 'down' }) {
  if (!data || data.length < 2) return null;
  const w = 56, h = 24, padX = 1, padY = 3;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data.map((v, i) => {
    const x = padX + (i / (data.length - 1)) * (w - padX * 2);
    const y = padY + ((max - v) / range) * (h - padY * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const strokeColor = color === 'up'
    ? 'var(--agent-green, #2ecc71)'
    : 'var(--agent-red, #e74c3c)';
  return (
    <div className="mp-sparkline">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <polyline
          points={points.join(' ')}
          fill="none"
          stroke={strokeColor}
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.8"
        />
        <linearGradient id={`sg-${color}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.15" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
        </linearGradient>
        <polygon
          points={`${points[0]} ${points[points.length - 1]} ${w - padX},${h - padY} ${padX},${h - padY}`}
          fill={`url(#sg-${color})`}
        />
      </svg>
    </div>
  );
}

// ── 生成稳定的模拟历史数据（基于 seed 的确定性伪随机）──
function generateMockHistory(currentPct: number, seedOffset = 0, length = 12): number[] {
  const baseSeed = Math.abs(currentPct) * 1000 + seedOffset * 137;
  const result: number[] = [];
  const trend = currentPct / length;
  let val = 50;
  for (let i = 0; i < length; i++) {
    // 确定性伪随机
    const noise = Math.sin(baseSeed + i * 42 + i * i * 3) * 0.6;
    val += trend + noise;
    result.push(Number(val.toFixed(2)));
  }
  return result;
}

// ── 主组件 ──
export default function MarketPage() {
  const { t } = useTranslation();
  const [indices, setIndices] = useState<Index[]>([]);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [globalData, setGlobalData] = useState<{ us_indices: GlobalIndex[]; commodities: Commodity[] }>({
    us_indices: [],
    commodities: [],
  });
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [topMovers, setTopMovers] = useState<Record<MoverTab, Mover[]>>({
    gainers: [], losers: [], active: [],
  });
  const [activeMoverTab, setActiveMoverTab] = useState<MoverTab>('gainers');
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [indicesRes, sectorsRes, globalRes, breadthRes, moversRes] = await Promise.all([
        marketApi.getIndices().catch(() => null),
        marketApi.getSectors().catch(() => null),
        marketApi.getGlobalMarket().catch(() => null),
        marketApi.getBreadth().catch(() => null),
        marketApi.getTopMovers({ type: 'gainers', limit: 10 }).catch(() => null),
      ]);

      if (indicesRes?.data?.indices) {
        const raw: Index[] = indicesRes.data.indices;
        setIndices(raw.map(idx => ({
          ...idx,
          history: idx.history || generateMockHistory(idx.change_pct),
        })));
      }
      if (sectorsRes?.data?.sectors) setSectors(sectorsRes.data.sectors);
      if (globalRes?.data) {
        setGlobalData({
          us_indices: globalRes.data.us_indices || [],
          commodities: globalRes.data.commodities || [],
        });
      }
      if (breadthRes?.data) setBreadth(breadthRes.data);
      if (moversRes?.data?.movers) {
        setTopMovers(prev => ({ ...prev, gainers: moversRes.data.movers }));
      }
      setLastUpdate(new Date());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // 切换 mover tab 时按需加载
  useEffect(() => {
    if (activeMoverTab === 'gainers') return; // 已加载
    const fetchMovers = async () => {
      try {
        const res = await marketApi.getTopMovers({ type: activeMoverTab, limit: 10 });
        if (res?.data?.movers) {
          setTopMovers(prev => ({ ...prev, [activeMoverTab]: res.data.movers }));
        }
      } catch { /* ignore */ }
    };
    fetchMovers();
  }, [activeMoverTab]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchData();
  };

  // ── 计算市场情绪 ──
  const sentiment = useMemo(() => {
    if (!breadth) return 'neutral';
    const ratio = breadth.advancing / (breadth.declining || 1);
    if (ratio > 1.8) return 'bullish';
    if (ratio < 0.55) return 'bearish';
    return 'neutral';
  }, [breadth]);

  // ── 为全球指数和商品生成稳定的 mock history ──
  const globalIndicesWithHistory = useMemo(() =>
    globalData.us_indices.map((idx, i) => ({
      ...idx,
      _history: generateMockHistory(idx.change_pct, i * 10),
    })), [globalData.us_indices]
  );

  const commoditiesWithHistory = useMemo(() =>
    globalData.commodities.map((cmdty, i) => ({
      ...cmdty,
      _history: generateMockHistory(cmdty.change_pct, i * 20 + 500),
    })), [globalData.commodities]
  );

  // ── 加载态 ──
  if (loading) {
    return (
      <div className="mp-page" style={{ justifyContent: 'center', alignItems: 'center' }}>
        <i className="fas fa-spinner fa-spin" style={{ fontSize: '24px', color: 'var(--agent-gold)' }} />
        <span style={{ color: 'var(--agent-text-dim)', marginTop: '12px', fontSize: '13px' }}>
          {t('common.loading')}
        </span>
      </div>
    );
  }

  return (
    <div className="mp-page">
      {/* ═══ 页面头部 ═══ */}
      <header className="mp-header">
        <div className="mp-header-left">
          <div className="mp-header-icon">
            <i className="fas fa-chart-line" />
          </div>
          <div>
            <h1 className="mp-header-title">{t('market.title')}</h1>
            <div className="mp-header-meta">
              <span className="mp-live-dot" />
              <span className="mp-update-time">
                {t('market.lastUpdate')} {lastUpdate.toLocaleTimeString()}
              </span>
            </div>
          </div>
        </div>
        <div className="mp-header-right">
          {/* 市场情绪 */}
          <span className={`mp-sentiment-badge ${sentiment}`}>
            <span className="mp-sentiment-dot" />
            {sentiment === 'bullish' ? t('market.statusBullish') :
             sentiment === 'bearish' ? t('market.statusBearish') :
             t('market.statusNeutral')}
          </span>
          {/* 刷新按钮 */}
          <button className="mp-refresh-btn" onClick={handleRefresh} disabled={refreshing}>
            <i className={`fas fa-sync-alt ${refreshing ? 'fa-spin' : ''}`} />
            <span>{t('common.refresh')}</span>
          </button>
        </div>
      </header>

      {/* ═══ 市场宽度 KPI 卡片 ═══ */}
      {breadth && (
        <div className="mp-breadth-row">
          <BreadthCard
            label={t('market.advancing')}
            value={breadth.advancing}
            type="up"
          />
          <BreadthCard
            label={t('market.declining')}
            value={breadth.declining}
            type="down"
          />
          <BreadthCard
            label={t('market.unchanged')}
            value={breadth.unchanged}
            type="neutral"
          />
          <BreadthCard
            label={t('market.limitUp')}
            value={breadth.limit_up}
            type="up"
          />
          <BreadthCard
            label={t('market.limitDown')}
            value={breadth.limit_down}
            type="down"
          />
          <div className="mp-breadth-card">
            <div className="mp-breadth-label">{t('market.totalAmount')}</div>
            <div className="mp-breadth-value neutral">
              {breadth.total_amount >= 10000
                ? `${(breadth.total_amount / 10000).toFixed(2)}万亿`
                : `${breadth.total_amount.toFixed(0)}亿`}
            </div>
            <div className="mp-breadth-sub">沪深两市</div>
          </div>
        </div>
      )}

      {/* ═══ 三列面板：A股 / 全球 / 大宗商品 ═══ */}
      <div className="mp-three-col" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
        {/* A股指数 */}
        <div className="mp-panel">
          <div className="mp-panel-header">
            <i className="fas fa-globe-asia" />
            <span className="mp-panel-title">{t('market.indices')}</span>
          </div>
          <div className="mp-panel-body">
            {indices.length === 0 ? (
              <EmptyState />
            ) : (
              indices.map(idx => {
                const isUp = idx.change_pct >= 0;
                const history = idx.history || generateMockHistory(idx.change_pct);
                return (
                  <div key={idx.symbol} className="mp-idx-row">
                    <div className="mp-idx-left">
                      <Sparkline data={history} color={isUp ? 'up' : 'down'} />
                      <div>
                        <span className="mp-idx-name">{idx.name}</span>
                        <span className="mp-idx-code">{idx.symbol}</span>
                      </div>
                    </div>
                    <div className="mp-idx-right">
                      <span className="mp-idx-price">
                        {idx.current_price.toFixed(2)}
                      </span>
                      <span className={`mp-idx-change-badge ${isUp ? 'up' : 'down'}`}>
                        {isUp ? '+' : ''}{idx.change_pct.toFixed(2)}%
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 全球市场 */}
        <div className="mp-panel">
          <div className="mp-panel-header">
            <i className="fas fa-globe-americas" />
            <span className="mp-panel-title">{t('market.usIndices')}</span>
          </div>
          <div className="mp-panel-body">
            {globalIndicesWithHistory.length === 0 ? (
              <EmptyState />
            ) : (
              globalIndicesWithHistory.map(idx => {
                const isUp = idx.change_pct >= 0;
                return (
                  <div key={idx.name} className="mp-idx-row">
                    <div className="mp-idx-left">
                      <Sparkline data={idx._history} color={isUp ? 'up' : 'down'} />
                      <div>
                        <span className="mp-idx-name">{idx.name}</span>
                        <span className="mp-idx-code">{idx.symbol}</span>
                      </div>
                    </div>
                    <div className="mp-idx-right">
                      <span className="mp-idx-price">
                        {idx.current.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </span>
                      <span className={`mp-idx-change-badge ${isUp ? 'up' : 'down'}`}>
                        {idx.change_pct >= 0 ? '+' : ''}{idx.change_pct.toFixed(2)}%
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 大宗商品 */}
        <div className="mp-panel">
          <div className="mp-panel-header">
            <i className="fas fa-cubes" />
            <span className="mp-panel-title">{t('market.commodities')}</span>
          </div>
          <div className="mp-panel-body">
            {commoditiesWithHistory.length === 0 ? (
              <EmptyState />
            ) : (
              commoditiesWithHistory.map(cmdty => {
                const isUp = cmdty.change_pct >= 0;
                const isGold = cmdty.name.includes('黄金') || cmdty.name.toLowerCase().includes('gold');
                const isOil = cmdty.name.includes('原油') || cmdty.name.toLowerCase().includes('oil');
                const iconClass = isGold ? 'gold' : isOil ? 'oil' : 'copper';
                const iconFa = isGold ? 'fa-crown' : isOil ? 'fa-oil-can' : 'fa-cube';
                return (
                  <div key={cmdty.name} className="mp-cmdty-row">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <div className={`mp-cmdty-icon ${iconClass}`}>
                        <i className={`fas ${iconFa}`} />
                      </div>
                      <div>
                        <span className="mp-idx-name">{cmdty.name}</span>
                      </div>
                    </div>
                    <div className="mp-idx-right">
                      <span className="mp-idx-price">
                        {cmdty.current.toFixed(2)}
                      </span>
                      <span className={`mp-idx-change-badge ${isUp ? 'up' : 'down'}`}>
                        {isUp ? '+' : ''}{cmdty.change_pct.toFixed(2)}%
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* ═══ 两列：热门板块 + 涨跌榜 ═══ */}
      <div className="mp-two-col" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        {/* 热门板块 — 概念资金流向 */}
        <div className="mp-panel">
          <div className="mp-panel-header">
            <i className="fas fa-fire" />
            <span className="mp-panel-title">概念板块行情</span>
          </div>
          <div className="mp-panel-body">
            {sectors.length === 0 ? (
              <EmptyState />
            ) : (
              sectors.slice(0, 10).map((sector, i) => {
                const isUp = sector.pct_change >= 0;
                const amountYi = sector.amount / 100000000;
                const rankClass = i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : 'normal';
                const barWidth = Math.min(Math.abs(sector.pct_change) * 12, 100);
                return (
                  <div key={sector.name} className="mp-sector-row">
                    <span className={`mp-sector-rank ${rankClass}`}>{i + 1}</span>
                    <div className="mp-sector-info">
                      <div className="mp-sector-name">{sector.name}</div>
                      <div className="mp-sector-bar-wrap">
                        <div
                          className={`mp-sector-bar-fill ${isUp ? 'up' : 'down'}`}
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                    </div>
                    <div className="mp-sector-right">
                      <div className={`mp-sector-pct ${isUp ? 'up' : 'down'}`}>
                        {isUp ? '+' : ''}{sector.pct_change}%
                      </div>
                      <div className="mp-sector-flow">
                        {amountYi.toFixed(0)}亿
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 涨跌榜 — Tab 切换 */}
        <div className="mp-panel">
          <div className="mp-panel-header">
            <i className="fas fa-sort-amount-up" />
            <span className="mp-panel-title">
              {activeMoverTab === 'gainers' ? t('market.topGainers') :
               activeMoverTab === 'losers' ? t('market.topLosers') :
               t('market.mostActive')}
            </span>
          </div>
          <div className="mp-panel-body">
            {/* Tab 切换 */}
            <div className="mp-mover-tabs">
              {(['gainers', 'losers', 'active'] as MoverTab[]).map(tab => (
                <button
                  key={tab}
                  className={`mp-mover-tab ${activeMoverTab === tab ? `active ${tab}` : ''}`}
                  onClick={() => setActiveMoverTab(tab)}
                >
                  {tab === 'gainers' ? t('market.topGainers') :
                   tab === 'losers' ? t('market.topLosers') :
                   t('market.mostActive')}
                </button>
              ))}
            </div>
            {/* 列表 */}
            <div className="mp-mover-list">
              {topMovers[activeMoverTab].length === 0 ? (
                <EmptyState />
              ) : (
                topMovers[activeMoverTab].slice(0, 10).map((mover, i) => {
                  const isUp = mover.change_pct >= 0;
                  return (
                    <div key={mover.symbol} className="mp-mover-item">
                      <span className={`mp-mover-rank-sm ${i < 3 ? 'top' : ''}`}>
                        {i + 1}
                      </span>
                      <div className="mp-mover-info">
                        <div className="mp-mover-name">{mover.name}</div>
                        <div className="mp-mover-code">{mover.symbol}</div>
                      </div>
                      <span className="mp-mover-price">
                        {mover.current_price?.toFixed(2) || '--'}
                      </span>
                      <span className={`mp-mover-change-tag ${isUp ? 'up' : 'down'}`}>
                        {isUp ? '+' : ''}{mover.change_pct.toFixed(2)}%
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 子组件 ──

function BreadthCard({ label, value, type }: {
  label: string;
  value: number;
  type: 'up' | 'down' | 'neutral';
}) {
  return (
    <div className="mp-breadth-card">
      <div className="mp-breadth-label">{label}</div>
      <div className={`mp-breadth-value ${type}`}>
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function EmptyState() {
  const { t } = useTranslation();
  return (
    <div className="mp-empty">
      <i className="fas fa-inbox" />
      <span>{t('common.noData')}</span>
    </div>
  );
}
