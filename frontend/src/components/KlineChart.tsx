import { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { marketApi } from '../api/client';

/* ═══════════════════════════════════════════════════════
 * Hallmark · macrostructure: Instrument-panel candlestick (light sci-fi academy)
 * theme: Blue Archive · accent #2f7cd3 · studied-DNA: golden-pit-page.css
 * GP tokens: ink #16324f · muted #57718e · line #a8cdee / #d4e7f9
 * up #e5484d (涨) · down #27a06b (跌) · gold #c98a12 · EN micro labels: Rajdhani
 * ═══════════════════════════════════════════════════════ */

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

// ── Golden Pit / Blue Archive 风格色板（与 golden-pit-page.css 对齐）──
const UP_COLOR = '#e5484d';      // gp-red · 涨
const DOWN_COLOR = '#27a06b';    // gp-green · 跌
const BG_COLOR = 'transparent';  // 融入宿主面板，不自带底色
const LINE_COLOR = '#a8cdee';    // gp-line · 坐标轴线
const GRID_COLOR = '#d4e7f9';    // gp-line-soft · 网格虚线
const TEXT_COLOR = '#57718e';    // gp-muted · 刻度/次要文字
const INK_COLOR = '#16324f';     // gp-ink · 主文字
const BLUE_COLOR = '#2f7cd3';    // gp-blue · 主强调色
const GOLD_COLOR = '#c98a12';    // gp-gold · MA5
const VIOLET_COLOR = '#7c5cd6';  // 紫色 · MA20（与 regime 强调一致）
const EN_FONT = "'Rajdhani', 'Microsoft YaHei', sans-serif";

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
          fontFamily: EN_FONT,
          position: isBuy ? 'top' : 'bottom',
          offset: [0, isBuy ? -6 : 6],
        },
      };
      if (isBuy) buyPoints.push(point);
      else sellPoints.push(point);
    }

    const tipRow = (label: string, value: string, color?: string) =>
      `<div style="display:flex;align-items:center;gap:6px;padding:1.5px 0;">` +
      `<span style="color:${TEXT_COLOR}">${label}</span>` +
      `<span style="margin-left:auto;padding-left:14px;font-family:${EN_FONT};font-weight:700;color:${color || INK_COLOR};">${value}</span>` +
      `</div>`;

    return {
      backgroundColor: BG_COLOR,
      animation: false,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#fff',
        borderColor: LINE_COLOR,
        borderWidth: 1,
        borderRadius: 5,
        padding: [8, 10],
        extraCssText: 'box-shadow:0 6px 18px rgba(31,90,150,0.18);',
        textStyle: { color: INK_COLOR, fontSize: 11 },
        formatter: (params: any[]) => {
          const k = params.find((p: any) => p.seriesName === 'K线');
          if (!k) return '';
          const d = k.data;
          const up = d[2] >= d[1];
          const valColor = up ? UP_COLOR : DOWN_COLOR;
          const chg = ((d[2] - d[1]) / d[1] * 100).toFixed(2);
          return (
            `<div style="font-family:${EN_FONT};font-weight:700;letter-spacing:1px;color:${INK_COLOR};` +
            `border-bottom:1px dashed ${GRID_COLOR};padding-bottom:4px;margin-bottom:5px;font-size:12px;">${k.axisValue}</div>` +
            tipRow('开', d[1].toFixed(2)) +
            tipRow('收', d[2].toFixed(2), valColor) +
            tipRow('高', d[4].toFixed(2)) +
            tipRow('低', d[3].toFixed(2)) +
            tipRow('幅', `${chg}%`, valColor)
          );
        },
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: BLUE_COLOR, color: '#fff' },
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
          axisLine: { lineStyle: { color: LINE_COLOR } },
          axisTick: { show: false },
          axisLabel: { color: TEXT_COLOR, fontSize: 10, fontFamily: EN_FONT },
          splitLine: { show: false },
        },
        {
          type: 'category',
          data: dates,
          gridIndex: 1,
          axisLine: { lineStyle: { color: LINE_COLOR } },
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
          axisLabel: { color: TEXT_COLOR, fontSize: 10, fontFamily: EN_FONT },
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
          borderColor: LINE_COLOR,
          backgroundColor: 'rgba(47,124,211,0.06)',
          fillerColor: 'rgba(47,124,211,0.18)',
          handleStyle: { color: BLUE_COLOR, borderColor: BLUE_COLOR },
          textStyle: { color: TEXT_COLOR, fontSize: 10, fontFamily: EN_FONT },
          dataBackground: {
            lineStyle: { color: '#7fb2e5' },
            areaStyle: { color: 'rgba(47,124,211,0.10)' },
          },
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
          lineStyle: { color: GOLD_COLOR, width: 1 },
        },
        {
          name: 'MA10',
          type: 'line',
          data: ma10,
          xAxisIndex: 0,
          yAxisIndex: 0,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: BLUE_COLOR, width: 1 },
        },
        {
          name: 'MA20',
          type: 'line',
          data: ma20,
          xAxisIndex: 0,
          yAxisIndex: 0,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: VIOLET_COLOR, width: 1 },
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

  return (
    <div className={`relative ${className}`}>
      {/* 始终渲染 chart 容器，确保 containerRef 在 mount 时就能绑定 */}
      <div ref={containerRef} style={{ width: '100%', height }} />
      {/* 无 symbol / error / loading 用覆盖层展示（浅色 GP 面板风格） */}
      {(!symbol || symbol.length < 6) ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-white/70 border border-[#d4e7f9]">
          <span className="text-xs" style={{ color: '#57718e' }}>输入股票代码后显示K线</span>
        </div>
      ) : error ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-white/70 border border-[#f3b6b8]">
          <span className="text-xs" style={{ color: '#e5484d' }}>{error}</span>
        </div>
      ) : loading ? (
        <div className="absolute inset-0 flex items-center justify-center z-10 rounded-lg bg-white/70">
          <div className="text-xs animate-pulse" style={{ color: '#57718e' }}>加载K线...</div>
        </div>
      ) : null}
    </div>
  );
}
