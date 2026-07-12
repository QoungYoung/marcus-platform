import { useEffect, useRef, useState } from 'react';
import { marketApi } from '../api/client';

interface KlineBar {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  vol: number;
}

interface TradeMarker {
  price: number;
  date: string;
  direction: string;
}

interface Props {
  symbol: string;
  trades?: TradeMarker[];
  height?: number;
  className?: string;
}

const BAR_WIDTH = 8;
const BAR_GAP = 2;
const PADDING = { top: 20, right: 12, bottom: 36, left: 52 };

export default function KlineChart({ symbol, trades = [], height = 240, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [data, setData] = useState<KlineBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; bar: KlineBar } | null>(null);

  useEffect(() => {
    if (!symbol || symbol.length < 6) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    marketApi.getKline(symbol, { limit: 60 })
      .then(res => {
        if (cancelled) return;
        const klines: KlineBar[] = (res.data.klines || [])
          .map((k: any) => ({
            trade_date: k.trade_date,
            open: k.open,
            high: k.high,
            low: k.low,
            close: k.close,
            vol: k.vol,
          }))
          .reverse();
        setData(klines);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError('K线数据加载失败');
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [symbol]);

  // ── Draw ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || data.length === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = rect.width;
    const h = height;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d')!;
    ctx.scale(dpr, dpr);

    // Clear
    ctx.fillStyle = '#0f1117';
    ctx.fillRect(0, 0, w, h);

    const chartW = w - PADDING.left - PADDING.right;
    const chartH = h - PADDING.top - PADDING.bottom;
    const step = BAR_WIDTH + BAR_GAP;
    const count = Math.min(data.length, Math.floor(chartW / step));
    const visible = data.slice(-count);

    if (visible.length === 0) return;

    const high = Math.max(...visible.map(d => d.high));
    const low = Math.min(...visible.map(d => d.low));
    const range = high - low || 1;

    const toX = (i: number) => PADDING.left + i * step + BAR_WIDTH / 2;
    const toY = (v: number) => PADDING.top + chartH * (1 - (v - low) / range);

    // ── Grid lines ──
    ctx.strokeStyle = '#1e2233';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = PADDING.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(PADDING.left, y);
      ctx.lineTo(w - PADDING.right, y);
      ctx.stroke();

      // Price label
      const price = high - (range / 4) * i;
      ctx.fillStyle = '#5a6070';
      ctx.font = '10px monospace';
      ctx.textAlign = 'right';
      ctx.fillText(price.toFixed(2), PADDING.left - 6, y + 3);
    }

    // Date labels
    ctx.fillStyle = '#5a6070';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    const labelStep = Math.max(1, Math.floor(visible.length / 5));
    for (let i = 0; i < visible.length; i += labelStep) {
      const date = visible[i].trade_date;
      const label = date.length === 8 ? `${date.slice(4, 6)}/${date.slice(6, 8)}` : date;
      ctx.fillText(label, toX(i), h - 8);
    }

    // ── Volume bars (thin) ──
    const maxVol = Math.max(...visible.map(d => d.vol));
    const volH = chartH * 0.15;
    const volY = h - PADDING.bottom - volH - 2;
    for (let i = 0; i < visible.length; i++) {
      const d = visible[i];
      const barH = (d.vol / maxVol) * volH;
      ctx.fillStyle = d.close >= d.open ? 'rgba(0,200,130,0.3)' : 'rgba(239,68,68,0.3)';
      ctx.fillRect(toX(i) - BAR_WIDTH / 2 + 1, volY + volH - barH, BAR_WIDTH - 2, barH);
    }

    // ── MA5 line ──
    ctx.strokeStyle = '#f0a030';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    for (let i = 4; i < visible.length; i++) {
      const ma5 = visible.slice(i - 4, i + 1).reduce((s, d) => s + d.close, 0) / 5;
      const x = toX(i);
      const y = toY(ma5);
      if (i === 4) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // ── Candles ──
    for (let i = 0; i < visible.length; i++) {
      const d = visible[i];
      const x = toX(i);
      const isUp = d.close >= d.open;
      const bodyTop = toY(Math.max(d.open, d.close));
      const bodyBot = toY(Math.min(d.open, d.close));
      const bodyH = Math.max(1, bodyBot - bodyTop);
      const wickTop = toY(d.high);
      const wickBot = toY(d.low);
      const color = isUp ? '#ef4444' : '#00c882';

      ctx.strokeStyle = color;
      ctx.fillStyle = isUp ? color : '#0f1117';

      // Wick
      ctx.beginPath();
      ctx.moveTo(x, wickTop);
      ctx.lineTo(x, wickBot);
      ctx.stroke();

      // Body
      ctx.fillRect(x - BAR_WIDTH / 2 + 0.5, bodyTop, BAR_WIDTH - 1, bodyH);
      if (!isUp) {
        ctx.strokeRect(x - BAR_WIDTH / 2 + 0.5, bodyTop, BAR_WIDTH - 1, bodyH);
      }
    }

    // ── Buy/Sell markers ──
    for (const t of trades) {
      // Find closest bar date
      let bestIdx = -1;
      let bestDiff = Infinity;
      for (let i = 0; i < visible.length; i++) {
        const diff = Math.abs(parseInt(visible[i].trade_date) - parseInt(t.date.replace(/-/g, '')));
        if (diff < bestDiff) { bestDiff = diff; bestIdx = i; }
      }
      if (bestIdx < 0) continue;

      const x = toX(bestIdx);
      const y = toY(t.price);
      const isBuy = t.direction === '买入';
      const r = 5;

      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = isBuy ? '#ef4444' : '#00c882';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Arrow
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 8px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(isBuy ? 'B' : 'S', x, y + 3);
    }
  }, [data, height, trades]);

  // ── Mouse move for tooltip ──
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || data.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const chartW = rect.width - PADDING.left - PADDING.right;
    const step = BAR_WIDTH + BAR_GAP;
    const count = Math.min(data.length, Math.floor(chartW / step));
    const visible = data.slice(-count);

    const idx = Math.round((mx - PADDING.left - BAR_WIDTH / 2) / step);
    if (idx >= 0 && idx < visible.length) {
      const bar = visible[idx];
      const toY = (v: number) => PADDING.top + (height - PADDING.top - PADDING.bottom) * (1 - (v - Math.min(...visible.map(d => d.low))) / (Math.max(...visible.map(d => d.high)) - Math.min(...visible.map(d => d.low)) || 1));
      setTooltip({ x: mx + 10, y: Math.min(my, height - 80), bar });
    } else {
      setTooltip(null);
    }
  };

  if (!symbol || symbol.length < 6) {
    return <div className={`text-xs text-gray-600 text-center py-6 ${className}`}>输入股票代码后显示K线</div>;
  }

  if (error) {
    return <div className={`text-xs text-red-400 text-center py-6 ${className}`}>{error}</div>;
  }

  return (
    <div className={`relative ${className}`}>
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center z-10 bg-dark-200/60 rounded-lg">
          <div className="text-xs text-gray-500 animate-pulse">加载K线...</div>
        </div>
      )}
      <canvas
        ref={canvasRef}
        style={{ width: '100%', height, cursor: 'crosshair' }}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setTooltip(null)}
      />
      {tooltip && (
        <div
          className="absolute z-20 bg-dark-100 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs shadow-xl pointer-events-none"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="text-gray-400 mb-0.5">
            {tooltip.bar.trade_date.length === 8
              ? `${tooltip.bar.trade_date.slice(0, 4)}-${tooltip.bar.trade_date.slice(4, 6)}-${tooltip.bar.trade_date.slice(6, 8)}`
              : tooltip.bar.trade_date}
          </div>
          <div className="font-mono space-y-0.5">
            <div><span className="text-gray-500">开 </span><span className="text-white">{tooltip.bar.open.toFixed(2)}</span></div>
            <div><span className="text-gray-500">高 </span><span className="text-white">{tooltip.bar.high.toFixed(2)}</span></div>
            <div><span className="text-gray-500">低 </span><span className="text-white">{tooltip.bar.low.toFixed(2)}</span></div>
            <div><span className="text-gray-500">收 </span><span className={tooltip.bar.close >= tooltip.bar.open ? 'text-red-400' : 'text-emerald-400'}>{tooltip.bar.close.toFixed(2)}</span></div>
          </div>
        </div>
      )}
    </div>
  );
}
