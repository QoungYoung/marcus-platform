import { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
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

const UP_COLOR = '#ef4444';
const DOWN_COLOR = '#00c882';
const BG_COLOR = '#0f1117';
const GRID_COLOR = '#1e2233';
const TEXT_COLOR = '#6b7280';

function calcMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j];
    result.push(sum / period);
  }
  return result;
}

export default function KlineChart({ symbol, trades = [], height = 340, className }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [data, setData] = useState<KlineBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Fetch data ──
  useEffect(() => {
    if (!symbol || symbol.length < 6) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    marketApi.getKline(symbol, { limit: 90 })
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
        if (!cancelled) { setError('K线数据加载失败'); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [symbol]);

  // ── Init chart ──
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, undefined, {
      devicePixelRatio: window.devicePixelRatio || 1,
    });
    chartRef.current = chart;

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // ── Build option ──
  const buildOption = useCallback((rawData: KlineBar[], markers: TradeMarker[]) => {
    const dates = rawData.map(d => d.trade_date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3'));
    const ohlc = rawData.map(d => [d.open, d.close, d.low, d.high]);
    const volumes = rawData.map(d => d.vol);
    const closes = rawData.map(d => d.close);

    const ma5 = calcMA(closes, 5);
    const ma10 = calcMA(closes, 10);
    const ma20 = calcMA(closes, 20);

    // ── Trade markers as markPoints ──
    const buyPoints: any[] = [];
    const sellPoints: any[] = [];
    for (const t of markers) {
      const dateStr = t.date.replace(/-/g, '');
      const idx = rawData.findIndex(d => d.trade_date === dateStr);
      if (idx < 0) continue;
      const isBuy = t.direction === '买入';
      const point = {
        name: isBuy ? 'B' : 'S',
        coord: [dates[idx], t.price],
        value: isBuy ? 'B' : 'S',
        symbol: 'pin',
        symbolSize: 28,
        itemStyle: {
          color: isBuy ? UP_COLOR : DOWN_COLOR,
          borderColor: '#fff',
          borderWidth: 1.5,
        },
        label: {
          show: true,
          color: '#fff',
          fontSize: 10,
          fontWeight: 'bold',
          position: isBuy ? 'top' : 'bottom',
          offset: [0, isBuy ? -6 : 6],
        },
      };
      if (isBuy) buyPoints.push(point);
      else sellPoints.push(point);
    }

    return {
      backgroundColor: BG_COLOR,
      animation: false,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(20,22,30,0.95)',
        borderColor: GRID_COLOR,
        textStyle: { color: '#e5e7eb', fontSize: 12, fontFamily: 'monospace' },
        formatter: (params: any[]) => {
          const k = params.find((p: any) => p.seriesName === 'K线');
          if (!k) return '';
          const d = k.data;
          return [
            `<div class="text-gray-400 mb-1">${k.axisValue}</div>`,
            `<span class="text-gray-500">开 </span><span>${d[1].toFixed(2)}</span>`,
            `<span class="text-gray-500"> 收 </span><span class="${d[2] >= d[1] ? 'text-red-400' : 'text-emerald-400'}">${d[2].toFixed(2)}</span>`,
            `<span class="text-gray-500"> 高 </span><span>${d[4].toFixed(2)}</span>`,
            `<span class="text-gray-500"> 低 </span><span>${d[3].toFixed(2)}</span>`,
            `<span class="text-gray-500"> 幅 </span><span>${((d[2] - d[1]) / d[1] * 100).toFixed(2)}%</span>`,
          ].join('&nbsp;&nbsp;');
        },
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: '#1f2937' },
      },
      grid: [
        { left: 56, right: 16, top: 16, height: '55%' },
        { left: 56, right: 16, top: '75%', height: '16%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          gridIndex: 0,
          axisLine: { lineStyle: { color: GRID_COLOR } },
          axisTick: { show: false },
          axisLabel: { color: TEXT_COLOR, fontSize: 10 },
          splitLine: { show: false },
        },
        {
          type: 'category',
          data: dates,
          gridIndex: 1,
          axisLine: { lineStyle: { color: GRID_COLOR } },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value',
          gridIndex: 0,
          scale: true,
          splitNumber: 5,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: TEXT_COLOR, fontSize: 10 },
          splitLine: { lineStyle: { color: GRID_COLOR, type: 'dashed' } },
          position: 'left',
        },
        {
          type: 'value',
          gridIndex: 1,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
          position: 'left',
        },
      ],
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: [0, 1],
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
        },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 4,
          height: 20,
          borderColor: GRID_COLOR,
          backgroundColor: 'rgba(30,34,51,0.5)',
          fillerColor: 'rgba(59,130,246,0.15)',
          handleStyle: { color: '#3b82f6', borderColor: '#3b82f6' },
          textStyle: { color: TEXT_COLOR, fontSize: 10 },
        },
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: ohlc,
          xAxisIndex: 0,
          yAxisIndex: 0,
          itemStyle: {
            color: UP_COLOR,
            color0: DOWN_COLOR,
            borderColor: UP_COLOR,
            borderColor0: DOWN_COLOR,
          },
          markPoint: {
            symbol: 'pin',
            symbolSize: 30,
            animation: false,
            data: [...buyPoints, ...sellPoints],
          },
        },
        {
          name: 'MA5',
          type: 'line',
          data: ma5,
          xAxisIndex: 0,
          yAxisIndex: 0,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#f59e0b', width: 1 },
        },
        {
          name: 'MA10',
          type: 'line',
          data: ma10,
          xAxisIndex: 0,
          yAxisIndex: 0,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#8b5cf6', width: 1 },
        },
        {
          name: 'MA20',
          type: 'line',
          data: ma20,
          xAxisIndex: 0,
          yAxisIndex: 0,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#06b6d4', width: 1 },
        },
        {
          name: '成交量',
          type: 'bar',
          data: volumes.map((v, i) => {
            const up = rawData[i].close >= rawData[i].open;
            return { value: v, itemStyle: { color: up ? `${UP_COLOR}66` : `${DOWN_COLOR}66` } };
          }),
          xAxisIndex: 1,
          yAxisIndex: 1,
        },
      ],
    };
  }, []);

  // ── Update chart ──
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || data.length === 0) return;
    chart.setOption(buildOption(data, trades), true);
  }, [data, trades, buildOption]);

  // ── Resize observer ──
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => chartRef.current?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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
      <div ref={containerRef} style={{ width: '100%', height }} />
    </div>
  );
}
