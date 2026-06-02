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

interface MarketFlowData {
  net_amount: number;           // 主力净流入金额(元)
  net_amount_fmt: string;       // 格式化: "+1.26亿"
  net_amount_rate: number;      // 主力净流入占比(%)
  flow_nature: string;          // 资金性质: 主力建仓/温和流入/主力出货/温和流出/平衡
  buy_elg_amount: number;       // 超大单净流入
  buy_lg_amount: number;        // 大单净流入
  buy_md_amount: number;        // 中单净流入
  buy_sm_amount: number;        // 小单净流入
  pct_change_sh: number;        // 上证涨跌幅
  pct_change_sz: number;        // 深证涨跌幅
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
  const [marketFlow, setMarketFlow] = useState<MarketFlowData | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    setRefreshing(true);

    // 并行请求，sectors 慢也不会阻塞 indices 和 portfolio
    const [indicesRes, sectorsRes, portfolioRes, flowRes] = await Promise.allSettled([
      fetch(`${MARCUS_API}/market/indices`),
      fetch(`${MARCUS_API}/market/concept-fund-flow`),
      fetch(`${MARCUS_API}/portfolio`),
      fetch(`${MARCUS_API}/market/moneyflow-mkt`),
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

    // market moneyflow
    if (flowRes.status === 'fulfilled' && flowRes.value.ok) {
      try {
        const flowData = await flowRes.value.json();
        const d = flowData.data;
        if (d) {
          setMarketFlow({
            net_amount: d.net_amount || 0,
            net_amount_fmt: d.net_amount_fmt || '',
            net_amount_rate: d.net_amount_rate || 0,
            flow_nature: d.flow_nature || '--',
            buy_elg_amount: d.buy_elg_amount || 0,
            buy_lg_amount: d.buy_lg_amount || 0,
            buy_md_amount: d.buy_md_amount || 0,
            buy_sm_amount: d.buy_sm_amount || 0,
            pct_change_sh: d.pct_change_sh || 0,
            pct_change_sz: d.pct_change_sz || 0,
          });
        }
      } catch (e) {
        console.log('Failed to parse market flow:', e);
      }
    } else {
      console.log('Failed to fetch market flow');
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

      {/* Market Moneyflow Section — 大盘资金流向 */}
      {marketFlow && (
        <div className="agent-panel-section">
          <div className="agent-sec-title">
            <i className="fas fa-coins"></i> 大盘资金流向
          </div>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '4px 0 6px 0',
          }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--agent-text-primary)' }}>
              主力净流入
            </span>
            <span style={{
              fontSize: '14px',
              fontWeight: 700,
              color: marketFlow.net_amount >= 0 ? 'var(--agent-green)' : 'var(--agent-red)',
            }}>
              {marketFlow.net_amount_fmt || (marketFlow.net_amount / 100000000).toFixed(2) + '亿'}
            </span>
          </div>
          {/* 资金性质标签 */}
          <div style={{
            display: 'inline-block',
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '11px',
            fontWeight: 600,
            background: marketFlow.net_amount >= 0
              ? 'rgba(0, 200, 100, 0.12)'
              : 'rgba(255, 80, 80, 0.12)',
            color: marketFlow.net_amount >= 0
              ? 'var(--agent-green)'
              : 'var(--agent-red)',
            marginBottom: '8px',
          }}>
            {marketFlow.flow_nature}
          </div>
          {/* 分类资金流向 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '11px' }}>
            {[
              { label: '超大单', amount: marketFlow.buy_elg_amount },
              { label: '大单', amount: marketFlow.buy_lg_amount },
              { label: '中单', amount: marketFlow.buy_md_amount },
              { label: '小单', amount: marketFlow.buy_sm_amount },
            ].map(item => {
              const yi = item.amount / 100000000;
              const isIn = yi >= 0;
              return (
                <div key={item.label} style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}>
                  <span style={{ color: 'var(--agent-text-dim)', width: '40px' }}>{item.label}</span>
                  {/* mini bar */}
                  <div style={{
                    flex: 1,
                    height: '6px',
                    background: 'rgba(255,255,255,0.06)',
                    borderRadius: '3px',
                    margin: '0 8px',
                    overflow: 'hidden',
                  }}>
                    <div style={{
                      width: `${Math.min(100, Math.abs(yi) / 5 * 100)}%`,
                      height: '100%',
                      borderRadius: '3px',
                      background: isIn ? 'var(--agent-green)' : 'var(--agent-red)',
                      marginLeft: isIn ? 'auto' : '0',
                      marginRight: isIn ? '0' : 'auto',
                      float: isIn ? 'right' : 'left',
                    }} />
                  </div>
                  <span style={{
                    color: isIn ? 'var(--agent-green)' : 'var(--agent-red)',
                    fontWeight: 600,
                    width: '48px',
                    textAlign: 'right',
                    whiteSpace: 'nowrap',
                  }}>
                    {isIn ? '+' : ''}{yi.toFixed(2)}亿
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

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