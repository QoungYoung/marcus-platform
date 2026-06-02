import { useEffect, useState } from 'react';
import '../styles/agent-theme.css';
import { type StockInfo } from './StockDetailPanel';

const MARCUS_API = '/api/v1';

interface IndexData {
  symbol: string;
  name: string;
  current_price: string;
  change_pct: number;
  open_price?: number;
  high?: number;
  low?: number;
  volume?: number;
}

interface HotSector {
  name: string;
  ts_code: string;
  pct_change: number;           // 涨跌幅(%)
  vol: number;                  // 成交量(股)
  amount: number;               // 成交额(元)
  turnover_rate: number;        // 换手率(%)
}

interface AgentSidebarProps {
  onStockSelect?: (stock: StockInfo) => void;
  selectedSymbol?: string;
}

export default function AgentSidebar({ onStockSelect, selectedSymbol }: AgentSidebarProps) {
  const [indices, setIndices] = useState<IndexData[]>([]);
  const [watchlist, setWatchlist] = useState<StockInfo[]>([]);
  const [hotSectors, setHotSectors] = useState<HotSector[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    setRefreshing(true);

    // 并行请求，sectors 慢也不会阻塞 indices 和 portfolio
    const [indicesRes, sectorsRes, portfolioRes] = await Promise.allSettled([
      fetch(`${MARCUS_API}/market/indices`),
      fetch(`${MARCUS_API}/market/concept-fund-flow`),
      fetch(`${MARCUS_API}/portfolio`),
    ]);

    // indices
    if (indicesRes.status === 'fulfilled' && indicesRes.value.ok) {
      try {
        const indicesData = await indicesRes.value.json();
        setIndices(indicesData.indices || []);
      } catch (e) {
        console.log('Failed to parse indices:', e);
      }
    } else {
      console.log('Failed to fetch indices');
    }

    // sectors
    if (sectorsRes.status === 'fulfilled' && sectorsRes.value.ok) {
      try {
        const sectorsData = await sectorsRes.value.json();
        setHotSectors(sectorsData.sectors || []);
      } catch (e) {
        console.log('Failed to parse sectors:', e);
      }
    } else {
      console.log('Failed to fetch sectors');
    }

    // portfolio
    if (portfolioRes.status === 'fulfilled' && portfolioRes.value.ok) {
      try {
        const portfolioData = await portfolioRes.value.json();
        const positions = portfolioData.account?.positions || [];
        setWatchlist(positions.slice(0, 5).map((p: any) => ({
          symbol: p.symbol,
          name: p.name,
          current_price: p.current_price,
          change_pct: p.change_pct || 0,
        })));
      } catch (e) {
        console.log('Failed to parse portfolio:', e);
      }
    } else {
      console.log('Failed to fetch portfolio');
    }

    setRefreshing(false);
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 180000); // 3 分钟
    return () => clearInterval(interval);
  }, []);

  const handleStockClick = (stock: StockInfo) => {
    console.log('Stock clicked:', stock);
    onStockSelect?.(stock);
  };

  return (
    <aside className="agent-sidebar" style={sidebarStyle}>
      {/* Market Indices Section */}
      <div className="agent-panel-section">
        <div className="agent-sec-title">
          <i className="fas fa-globe-asia"></i> 市场指数
          <button
            onClick={fetchData}
            disabled={refreshing}
            style={{
              marginLeft: 'auto',
              background: 'none',
              border: 'none',
              cursor: refreshing ? 'not-allowed' : 'pointer',
              color: 'var(--agent-text-dim)',
              fontSize: '12px',
              padding: '2px 6px',
              opacity: refreshing ? 0.5 : 1,
            }}
            title="刷新"
          >
            <i className={`fas fa-sync-alt ${refreshing ? 'fa-spin' : ''}`}></i>
          </button>
        </div>
        {indices.length > 0 ? (
          indices.slice(0, 5).map((idx) => (
            <div
              key={idx.name}
              className="agent-index-row"
              onClick={() => {
                console.log('Index clicked:', idx);
                handleStockClick({
                  symbol: idx.symbol,
                  name: idx.name,
                  current_price: parseFloat(String(idx.current_price).replace(/,/g, '')) || 0,
                  change_pct: idx.change_pct,
                  open: idx.open_price,
                  high: idx.high,
                  low: idx.low,
                  volume: idx.volume,
                });
              }}
            >
              <span className="idx-name">{idx.name}</span>
              <span className="idx-price">{idx.current_price}</span>
              <span className={`agent-idx-change ${idx.change_pct >= 0 ? 'up' : 'down'}`}>
                {idx.change_pct >= 0 ? '+' : ''}{idx.change_pct.toFixed(2)}%
              </span>
            </div>
          ))
        ) : (
          <div style={{ color: 'var(--agent-text-dim)', fontSize: '12px', padding: '8px' }}>
            加载中...
          </div>
        )}
      </div>

      {/* Watchlist Section */}
      <div className="agent-panel-section">
        <div className="agent-sec-title">
          <i className="fas fa-star"></i> 自选股
        </div>
        {/* Actual watchlist */}
        {watchlist.length > 0 ? (
          watchlist.map((stock) => (
            <div
              key={stock.symbol}
              className={`agent-watchlist-item ${selectedSymbol === stock.symbol ? 'selected' : ''}`}
              onClick={() => handleStockClick(stock)}
            >
              <div className="wl-info">
                <span className="wl-name">{stock.name}</span>
                <span className="wl-code">{stock.symbol}</span>
              </div>
              <div>
                <span className="wl-price">¥{(stock.current_price || 0).toFixed(2)}</span>
                <span
                  className={`agent-wl-change ${(stock.change_pct || 0) >= 0 ? 'up' : 'down'}`}
                  style={{ display: 'block', marginTop: '2px' }}
                >
                  {(stock.change_pct || 0) >= 0 ? '+' : ''}{(stock.change_pct || 0).toFixed(2)}%
                </span>
              </div>
            </div>
          ))
        ) : (
          <div style={{ color: 'var(--agent-text-dim)', fontSize: '12px', padding: '8px' }}>
            暂无自选股
          </div>
        )}
      </div>

      {/* Hot Sectors Section — 概念板块行情排行（按涨幅排序） */}
      <div className="agent-panel-section">
        <div className="agent-sec-title">
          <i className="fas fa-fire"></i> 概念板块行情
        </div>
        {hotSectors.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {hotSectors.map((sector, idx) => {
              const amountYi = sector.amount / 100000000;
              const isUp = sector.pct_change >= 0;
              return (
              <div
                key={sector.name}
                className={`agent-sector-row ${isUp ? 'rise' : 'fall'}`}
                title={
                  `涨跌: ${isUp ? '+' : ''}${sector.pct_change}% | ` +
                  `成交额: ${amountYi.toFixed(1)}亿 | ` +
                  `换手率: ${sector.turnover_rate}%`
                }
              >
                <span style={{ fontSize: '10px', fontWeight: 600, color: 'var(--agent-text-dim)', width: '16px', textAlign: 'right', flexShrink: 0, marginRight: '4px' }}>
                  {idx + 1}
                </span>
                <span style={{ flex: 1, fontSize: '12px', fontWeight: 500, color: 'var(--agent-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {sector.name}
                </span>
                <span style={{
                  fontSize: '10px',
                  fontWeight: 600,
                  color: isUp ? 'var(--agent-green)' : 'var(--agent-red)',
                  whiteSpace: 'nowrap',
                  marginLeft: '6px',
                  minWidth: '48px',
                  textAlign: 'right',
                }}>
                  {isUp ? '+' : ''}{sector.pct_change}%
                </span>
              </div>
            )})}
          </div>
        ) : (
          <div style={{ color: 'var(--agent-text-dim)', fontSize: '12px', padding: '8px' }}>
            暂无数据
          </div>
        )}
      </div>
    </aside>
  );
}

const sidebarStyle: React.CSSProperties = {
  width: 'var(--agent-sidebar-width)',
  minWidth: 'var(--agent-sidebar-width)',
  background: 'var(--agent-bg-card)',
  borderRight: '1px solid var(--agent-border-light)',
  display: 'flex',
  flexDirection: 'column',
  overflowY: 'auto',
  overflowX: 'hidden',
  flexShrink: 0,
};