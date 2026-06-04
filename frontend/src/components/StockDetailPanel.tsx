import { useEffect, useState, useRef } from 'react';
import '../styles/agent-theme.css';

const MARCUS_API = '/api/v1';

function formatTimeAgo(dateStr: string): string {
  if (!dateStr) return '';
  const now = Date.now();
  const pub = new Date(dateStr).getTime();
  if (isNaN(pub)) return '';
  const diffMs = now - pub;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return '刚刚';
  if (diffMins < 60) return `${diffMins}分钟前`;
  if (diffHours < 24) return `${diffHours}小时前`;
  if (diffDays < 2) return '昨日';
  if (diffDays < 7) return `${diffDays}天前`;
  const d = new Date(dateStr);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export interface StockInfo {
  symbol: string;
  name: string;
  current_price?: number;
  change_pct?: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  amount?: number;
  turnover_rate?: number;
  pe_ratio?: number;
  net_inflow?: number;
  inflow_ratio?: number;
}

interface NewsItem {
  id: number;
  title: string;
  source: string;
  publish_time: string;
  sentiment: string;
  url?: string;
}

interface StockDetailPanelProps {
  stock: StockInfo | null;
}

export default function StockDetailPanel({ stock }: StockDetailPanelProps) {
  const [quote, setQuote] = useState<StockInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<string>('');
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [newsList, setNewsList] = useState<NewsItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    console.log('StockDetailPanel render, stock:', stock?.symbol, 'aiAnalysis:', aiAnalysis);

    if (!stock?.symbol) {
      setQuote(null);
      setAiAnalysis('');
      setNewsList([]);
      return;
    }

    const fetchQuote = async () => {
      setLoading(true);
      try {
        const [quoteRes, flowRes] = await Promise.all([
          fetch(`${MARCUS_API}/market/quote/${stock.symbol}`),
          fetch(`${MARCUS_API}/market/moneyflow/${stock.symbol}?limit=1`),
        ]);

        let net_inflow: number | undefined;
        let inflow_ratio: number | undefined;

        // 解析资金流向数据（主力净流入）
        if (flowRes.ok) {
          const flowData = await flowRes.json();
          console.log('Moneyflow API response:', flowData);
          const flows = flowData.flows || [];
          if (flows.length > 0) {
            const latest = flows[0];
            // net_mf_amount 单位是万元，转换为亿 (1亿 = 10000万)
            net_inflow = (latest.net_mf_amount || 0) / 10000;

            // 计算流入占比（用于流柱图）
            const totalBuy =
              (latest.buy_sm_amount || 0) +
              (latest.buy_md_amount || 0) +
              (latest.buy_lg_amount || 0) +
              (latest.buy_elg_amount || 0);
            const totalSell =
              (latest.sell_sm_amount || 0) +
              (latest.sell_md_amount || 0) +
              (latest.sell_lg_amount || 0) +
              (latest.sell_elg_amount || 0);
            const total = totalBuy + totalSell;
            inflow_ratio = total > 0 ? Math.round((totalBuy / total) * 100) : 50;
          }
        }

        if (quoteRes.ok) {
          const data = await quoteRes.json();
          console.log('Quote API response:', data);
          setQuote({
            symbol: data.symbol,
            name: data.name || stock.name,
            current_price: data.current,
            change_pct: data.percent || 0,
            open: data.open || data.last_close,
            high: data.high,
            low: data.low,
            volume: data.volume,
            amount: data.amount,
            turnover_rate: data.turnover_rate,
            pe_ratio: data.pe_ttm,
            net_inflow,
            inflow_ratio,
          });
        }
      } catch (e) {
        console.log('Failed to fetch quote:', e);
      } finally {
        setLoading(false);
      }
    };

    if (stock.open && stock.high && stock.low) {
      setQuote(stock);
      setLoading(false);
    } else {
      fetchQuote();
    }

    const fetchAnalysis = async () => {
      console.log('fetchAnalysis START');
      setAnalysisLoading(true);
      setAiAnalysis('');

      // 如果切换了股票，取消上一次的分析请求
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      try {
        console.log('Fetching /agent/analyze for', stock.symbol);
        const res = await fetch(`/api/v1/agent/analyze`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol: stock.symbol }),
          signal: abort.signal,
        });

        console.log('fetchAnalysis response status:', res.status, 'ok:', res.ok);

        if (!res.ok) {
          setAiAnalysis('分析获取失败');
          setAnalysisLoading(false);
          return;
        }

        // 🔥 使用 ReadableStream 逐块消费 SSE —— 真正的流式输出
        const reader = res.body?.getReader();
        if (!reader) {
          setAiAnalysis('分析获取失败（不支持流式）');
          setAnalysisLoading(false);
          return;
        }

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // 按行分割，处理完整的 SSE 行
          const lines = buffer.split('\n');
          // 最后一行可能不完整，保留在 buffer 中
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6).trim();
            if (data === '[DONE]') {
              setAnalysisLoading(false);
              return;
            }
            try {
              const obj = JSON.parse(data);
              if (obj.content) {
                // 增量追加到已有内容上，实现逐字显现的流式效果
                setAiAnalysis(prev => prev + obj.content);
              }
            } catch {
              // 忽略无法解析的行
            }
          }
        }

        // 流结束
        setAnalysisLoading(false);
      } catch (e: any) {
        if (e.name === 'AbortError') {
          console.log('fetchAnalysis aborted (stock changed)');
          return;
        }
        console.log('fetchAnalysis error:', e);
        setAiAnalysis(prev => prev || '网络错误');
        setAnalysisLoading(false);
      }
    };

    fetchAnalysis();

    // 获取个股相关新闻
    const fetchNews = async () => {
      setNewsLoading(true);
      try {
        const res = await fetch(`${MARCUS_API}/news?symbol=${stock.symbol}&limit=5`);
        if (res.ok) {
          const data = await res.json();
          const items = (data.news || []).map((n: any) => ({
            id: n.id,
            title: n.title,
            source: n.source || '财经媒体',
            publish_time: n.publish_time,
            sentiment: n.sentiment || 'neutral',
            url: n.url,
          }));
          setNewsList(items);
        }
      } catch (e) {
        console.log('Failed to fetch news:', e);
      } finally {
        setNewsLoading(false);
      }
    };
    fetchNews();
  }, [stock?.symbol]);

  const displayStock = stock ? (quote || stock) : null;
  const changePct = displayStock?.change_pct || 0;
  const isUp = changePct >= 0;

  // Debug: log aiAnalysis state changes
  console.log('aiAnalysis updated:', aiAnalysis);
  console.log('analysisLoading:', analysisLoading);

  // Calculate flow bar percentages
  const inflowRatio = displayStock?.inflow_ratio || 50;
  const flowInWidth = inflowRatio;
  const flowOutWidth = 100 - inflowRatio;

  return (
    <aside className="agent-panel" style={panelStyle}>
      {displayStock ? (
        <>
          {/* Stock Hero Section */}
          <div className="agent-panel-section">
            <div className="agent-sec-title">
              <i className="fas fa-chart-bar"></i> 当前关注
            </div>
            <div className="agent-stock-hero">
              <div>
                <div className="hero-name">{displayStock.name}</div>
                <div className="hero-code">{displayStock.symbol}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="hero-price">
                  ¥{displayStock.current_price?.toFixed(2) || '--'}
                </div>
                <span className={`agent-hero-change-badge ${isUp ? 'up' : 'down'}`}>
                  {isUp ? '+' : ''}{changePct.toFixed(2)}%
                </span>
              </div>
            </div>
          </div>

          {/* Key Metrics Section */}
          <div className="agent-panel-section">
            <div className="agent-sec-title">
              <i className="fas fa-info-circle"></i> 关键指标
            </div>
            <div className="agent-detail-row">
              <span className="dl">开盘价</span>
              <span className="dv">¥{displayStock.open?.toFixed(2) || '--'}</span>
            </div>
            <div className="agent-detail-row">
              <span className="dl">最高价</span>
              <span className="dv green">¥{displayStock.high?.toFixed(2) || '--'}</span>
            </div>
            <div className="agent-detail-row">
              <span className="dl">最低价</span>
              <span className="dv red">¥{displayStock.low?.toFixed(2) || '--'}</span>
            </div>
            <div className="agent-detail-row">
              <span className="dl">成交量</span>
              <span className="dv">{(displayStock.volume || 0).toLocaleString()} 手</span>
            </div>
            <div className="agent-detail-row">
              <span className="dl">换手率</span>
              <span className="dv">{displayStock.turnover_rate?.toFixed(2) || '--'}%</span>
            </div>
            <div className="agent-detail-row">
              <span className="dl">市盈率</span>
              <span className="dv">{displayStock.pe_ratio || '--'}</span>
            </div>
            <div className="agent-detail-row" style={{ borderBottom: 'none' }}>
              <span className="dl">主力净流入</span>
              <span className={`dv ${(displayStock.net_inflow || 0) >= 0 ? 'green' : 'red'}`}>
                {(displayStock.net_inflow || 0) >= 0 ? '+' : ''}{(displayStock.net_inflow || 0).toFixed(2)} 亿
              </span>
            </div>
            <div className="agent-flow-bar-wrap">
              <span style={{ color: 'var(--agent-green)' }}>流入</span>
              <div className="agent-flow-bar">
                <div className="flow-in" style={{ width: `${flowInWidth}%` }}></div>
                <div className="flow-out" style={{ width: `${flowOutWidth}%` }}></div>
              </div>
              <span style={{ color: 'var(--agent-red)' }}>流出</span>
            </div>
          </div>

          {/* AI Note Section */}
          <div className="agent-panel-section">
            <div className="agent-sec-title">
              <i className="fas fa-brain"></i> AI 简评
            </div>
            <div className="agent-ai-note">
              {analysisLoading ? '正在分析...' : aiAnalysis || '暂无分析'}
            </div>
          </div>

          {/* News Section */}
          <div className="agent-panel-section">
            <div className="agent-sec-title">
              <i className="fas fa-newspaper"></i> 相关资讯
            </div>
            {newsLoading ? (
              <div style={{ color: 'var(--agent-text-dim)', fontSize: '12px', padding: '8px' }}>
                加载中...
              </div>
            ) : newsList.length > 0 ? (
              newsList.map((item, idx) => {
                const timeAgo = formatTimeAgo(item.publish_time);
                const sentColor =
                  item.sentiment === 'positive' ? 'var(--agent-green)' :
                  item.sentiment === 'negative' ? 'var(--agent-red)' :
                  'var(--agent-text-dim)';
                return (
                  <div
                    key={item.id}
                    className="agent-news-item"
                    style={{ borderBottom: idx < newsList.length - 1 ? undefined : 'none', cursor: item.url ? 'pointer' : 'default' }}
                    onClick={() => { if (item.url) window.open(item.url, '_blank'); }}
                    title={item.url ? '点击查看原文' : undefined}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                      <span style={{
                        display: 'inline-block',
                        width: '6px',
                        height: '6px',
                        borderRadius: '50%',
                        background: sentColor,
                        marginTop: '6px',
                        flexShrink: 0,
                      }} />
                      <span style={{ flex: 1, lineHeight: '1.4' }}>{item.title}</span>
                    </div>
                    <span className="news-time">
                      {item.source} · {timeAgo}
                    </span>
                  </div>
                );
              })
            ) : (
              <div style={{ color: 'var(--agent-text-dim)', fontSize: '12px', padding: '8px' }}>
                暂无相关资讯
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="agent-panel-section">
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '40px 20px',
            color: 'var(--agent-text-dim)',
            textAlign: 'center'
          }}>
            <i className="fas fa-chart-line" style={{ fontSize: '32px', marginBottom: '12px', opacity: 0.5 }}></i>
            <p style={{ fontSize: '13px' }}>点击左侧股票查看详情</p>
          </div>
        </div>
      )}
    </aside>
  );
}

const panelStyle: React.CSSProperties = {
  width: 'var(--agent-panel-width)',
  minWidth: 'var(--agent-panel-width)',
  background: 'var(--agent-bg-card)',
  borderLeft: '1px solid var(--agent-border-light)',
  display: 'flex',
  flexDirection: 'column',
  overflowY: 'auto',
  overflowX: 'hidden',
  flexShrink: 0,
};
