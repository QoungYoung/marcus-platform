import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { waveApi } from '../api/client';

/* ═══════════════════════════════════════════════════════
 * 上证指数 · 浪型链（portfolio 页卡片）
 *   · K线：/api/v1/wave/index-history（后端优先东财 OHLC；退本地 CSV 时后端会标 csv_close_only ⛔）
 *   · 色带：每个交易日的浪型档位（4浪调整/成功4-5/失败4-5/下跌1浪/主升3-3）连成区间
 *   · 悬停：当日浪型 + 操作建议 + 依据（operation 语义照抄 wave_agent 原表）
 * ═══════════════════════════════════════════════════════ */

interface Bar { date: string; open: number; high: number; low: number; close: number; amount: number }
interface PatMark {
  kind: string; neck: number; confirmed: boolean;
  points: { date: string; value: number }[];
}
interface Wave {
  date: string; rung: number; name: string; level: string; sub_level: string;
  operation: string; mode: string; expect: string; invest: number; main_up: string; reasons: string;
  marks?: PatMark | null;
}

const UP_COLOR = '#e5484d';
const DOWN_COLOR = '#27a06b';
const INK = '#16324f';
const MUTED = '#57718e';
const LINE = '#a8cdee';
const GRID = '#d4e7f9';

// operation 语义（apps/main_line/wave_agent.py:322-327 照抄）
const OP_CN: Record<string, string> = {
  build: '建仓/追', t_only: '只做T·不新开', side: '观望·调仓换股', defense: '防御·不建仓', exit: '兑现降仓',
};
const OP_COLOR: Record<string, string> = {
  build: '#e5484d', t_only: '#c98a12', side: '#2f7cd3', defense: '#e08b3a', exit: '#8a5cf0',
};
// ★ 色带按 **operation** 染色（原来按档位序号 ⇒ W底/末段这类改写档会串色 ✗ 实测发现）
const OP_FILL: Record<string, string> = {
  build: '#2f9e6e',     // 建仓/追
  side: '#2f7cd3',      // 观望·调仓换股
  t_only: '#c98a12',    // 只做T·不新开
  defense: '#8a5cf0',   // 防御·不建仓
  exit: '#d64545',      // 兑现·降仓（防顶/先卖）
};
const OP_ORDER = ['build', 'side', 't_only', 'defense', 'exit'];
const MAINUP_COLOR = '#2f9e6e';   // 主升 3-3 标记色（与 build 同色系 ✓）

function bandColor(w: Wave): string {
  return OP_FILL[(w.operation || '').toLowerCase()] || MUTED;
}

// days 默认 750（≈3 年）：形态标注（W底/双头）很稀有 —— 250 天窗口内往往一个都没有 ✗
// （最近一次 W底确认是 2024-09-30 ✓，需要 ~490 个交易日才能覆盖 ✓）
export default function IndexWaveCard({ days = 750, height = 340 }: { days?: number; height?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [waves, setWaves] = useState<Wave[]>([]);
  const [src, setSrc] = useState('');
  const [dispPats, setDispPats] = useState<any[]>([]);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    waveApi.getIndexHistory(days)
      .then((r) => {
        if (!alive) return;
        const d = r.data || {};
        setBars(d.bars || []);
        setWaves(d.waves || []);
        setDispPats(d.display_patterns || []);   // 显示专用形态（用长序列算，不改链的状态 ✓）
        setSrc(d.ohlc_source || '');
        setNote(d.note || '');
        setErr((d.bars || []).length ? '' : '无指数数据');
      })
      .catch((e) => { if (alive) setErr(String((e && e.message) || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days]);

  const last = useMemo(() => (waves.length ? waves[waves.length - 1] : null), [waves]);

  useEffect(() => {
    if (!ref.current || !bars.length) return;
    if (!chartRef.current) chartRef.current = echarts.init(ref.current);
    const ch = chartRef.current;
    const wmap: Record<string, Wave> = {};
    waves.forEach((w) => { wmap[w.date] = w; });

    const dates = bars.map((b) => b.date);
    const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high]);
    const prevClose = (i: number) => (i > 0 ? bars[i - 1].close : bars[0].open);

    // 浪型色带：把连续同档/同操作的交易日并成区间（稀有档在带顶打标签 ✓）
    const RARE_LABEL: Record<string, { text: string; color: string }> = {
      末段: { text: '末段·兑现降仓', color: '#d64545' },
      '4-3': { text: '4-3筑底·可分批接', color: '#2f7cd3' },
      '4-1': { text: '4-1回调初·观望降仓', color: '#8a5cf0' },
      W底: { text: 'W底确认·建仓', color: '#2f9e6e' },
      '3-3': { text: '主升3-3·建仓', color: '#2f9e6e' },
      双头: { text: '双头/M顶·见顶防跌', color: '#d64545' },
    };
    const areas: any[] = [];
    let start = 0;
    for (let i = 1; i <= bars.length; i++) {
      const cur = wmap[bars[start].date];
      const nxt = i < bars.length ? wmap[bars[i].date] : null;
      const sameKey = !!nxt && !!cur && bandColor(nxt) === bandColor(cur) && nxt.operation === cur.operation;
      if (!sameKey) {
        if (cur) {
          // 这一带里出现的稀有档（取带内第一个命中的 ✓，带长 ≥3 天才显示，避免碎片化 ✗）
          let tag: { text: string; color: string } | null = null;
          for (let k2 = start; k2 < i && k2 < bars.length; k2++) {
            const ww = wmap[bars[k2].date];
            if (!ww) continue;
            const hit = RARE_LABEL[ww.sub_level || ''];
            if (hit) { tag = hit; break; }
          }
          const band = { xAxis: dates[start], itemStyle: { color: bandColor(cur), opacity: 0.09 } };
          const bandEnd: any = { xAxis: dates[Math.min(i, bars.length - 1)] };
          if (tag && (i - start) >= 3) {
            bandEnd.label = {
              show: true,
              position: 'insideTop',
              formatter: tag.text,
              color: tag.color,
              fontSize: 9,
              fontWeight: 600,
            };
          }
          areas.push([band, bandEnd]);
        }
        start = i;
      }
    }

    // ── 形态标注：W底（两个低点+颈线）／双头（两个高点+颈线）—— 按「形态指纹」去重，每个只画一次 ✓
    const bmap: Record<string, { high: number; low: number; close: number }> = {};
    bars.forEach((b) => { bmap[b.date] = { high: b.high, low: b.low, close: b.close }; });
    const patPoints: any[] = [];
    const patLines: any[] = [];
    const seenPat: Record<string, boolean> = {};
    // 显示专用形态（后端用长序列算 ✓）与逐日 marks 合并，按指纹去重 ✓
    const allPats: { m: PatMark; date: string }[] = [];
    dispPats.forEach((p) => { allPats.push({ m: p as PatMark, date: p.first_date }); });
    waves.forEach((w) => { if (w.marks) allPats.push({ m: w.marks, date: w.date }); });
    allPats.forEach(({ m, date }) => {
      if (!m || !m.points || m.points.length < 2) return;
      const w = { date };
      const key = m.kind + '|' + m.points.map((p) => p.date).join('|');
      if (seenPat[key]) return;
      seenPat[key] = true;
      const isW = m.kind === 'w_bottom';
      const pc = isW ? '#2f9e6e' : '#d64545';
      m.points.forEach((p, i) => {
        const b = bmap[p.date];
        if (!b) return;
        patPoints.push({
          coord: [p.date, isW ? b.low : b.high],
          value: (isW ? 'W' : '顶') + (i + 1),
          symbol: 'circle',
          symbolSize: 9,
          itemStyle: { color: pc, borderColor: '#fff', borderWidth: 1 },
          label: { show: true, position: isW ? 'bottom' : 'top', color: pc, fontSize: 9, fontWeight: 600 },
        });
      });
      const cb = bmap[w.date];
      patPoints.push({
        coord: [w.date, isW ? (cb ? cb.low : m.neck) : (cb ? cb.high : m.neck)],
        value: isW ? 'W底确认' : '双头',
        symbol: 'pin',
        symbolSize: 26,
        itemStyle: { color: pc },
        label: { show: true, color: '#fff', fontSize: 8, fontWeight: 700 },
      });
      if (m.neck && bmap[m.points[0].date]) {
        patLines.push([
          { coord: [m.points[0].date, m.neck], lineStyle: { color: pc, type: 'dashed', width: 1, opacity: 0.75 } },
          { coord: [w.date, m.neck], label: { show: true, position: 'insideEndTop', formatter: '颈线 ' + m.neck, color: pc, fontSize: 8 } },
        ]);
      }
    });

    ch.setOption({
      animation: false,
      backgroundColor: 'transparent',
      grid: { left: 52, right: 16, top: 26, bottom: 34 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', lineStyle: { color: LINE } },
        backgroundColor: 'rgba(255,255,255,0.97)',
        borderColor: LINE,
        textStyle: { color: INK, fontSize: 11 },
        formatter: (ps: any) => {
          const p = Array.isArray(ps) ? ps.find((x: any) => x.seriesType === 'candlestick') || ps[0] : ps;
          const i = p.dataIndex;
          const b = bars[i];
          if (!b) return '';
          const w = wmap[b.date];
          const chg = ((b.close / prevClose(i) - 1) * 100);
          const chgColor = chg >= 0 ? UP_COLOR : DOWN_COLOR;
          let html = '<div style="min-width:200px">';
          html += '<div style="font-weight:600;color:' + INK + '">' + b.date + '</div>';
          html += '<div>开 ' + b.open.toFixed(2) + ' 高 ' + b.high.toFixed(2) + ' 低 ' + b.low.toFixed(2) +
                  ' 收 <b>' + b.close.toFixed(2) + '</b> <span style="color:' + chgColor + '">' +
                  (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span></div>';
          if (w) {
            const oc = OP_COLOR[w.operation] || MUTED;
            html += '<div style="margin-top:4px;border-top:1px dashed ' + GRID + ';padding-top:4px">';
            html += '<div>浪型 <b>' + (w.name || '-') + '</b>　级别 <b>' + (w.level || '-') +
                    (w.sub_level ? '/' + w.sub_level : '') + '</b>' + (w.main_up ? '　<span style="color:' + MAINUP_COLOR + '">主升3-3</span>' : '') + '</div>';
            html += '<div>操作 <b style="color:' + oc + '">' + (OP_CN[w.operation] || w.operation || '-') + '</b>' +
                    (w.invest != null ? '　投入系数 ' + Number(w.invest).toFixed(2) : '') +
                    (w.mode ? '　模式 <b>' + w.mode + '</b>' : '') + '</div>';
            // 「当日许可」：说明买/卖两侧究竟哪边被拦（卖类永不拦 ✓ 见 wave_gate.py:229-231）
            const perm = w.operation === 'build' ? '买类放行；卖出不拦'
              : w.operation === 'side' ? '买类需人工确认；卖出不拦'
              : w.operation === 't_only' ? '买类拦（仅 probe ≤3%）；卖出不拦'
              : '买类拦（禁新建）；卖出不拦';
            html += '<div style="color:' + MUTED + '">当日许可：' + perm + '</div>';
            if (w.expect) html += '<div style="color:' + MUTED + '">预期：' + w.expect + '</div>';
            if (w.reasons) html += '<div style="color:' + MUTED + '">依据：' + w.reasons + '</div>';
            html += '</div>';
          } else {
            html += '<div style="color:' + MUTED + '">（该日无浪型记录）</div>';
          }
          return html + '</div>';
        },
      },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: LINE } },
        axisLabel: { color: MUTED, fontSize: 9, formatter: (v: string) => v.slice(4, 6) + '/' + v.slice(6, 8) },
      },
      yAxis: {
        scale: true,
        position: 'left',
        axisLine: { lineStyle: { color: LINE } },
        splitLine: { lineStyle: { color: GRID, type: 'dashed' } },
        axisLabel: { color: MUTED, fontSize: 9 },
      },
      dataZoom: [
        { type: 'inside', start: Math.max(0, 100 - (120 / Math.max(bars.length, 1)) * 100), end: 100 },
        { type: 'slider', height: 14, bottom: 6, borderColor: LINE, textStyle: { color: MUTED, fontSize: 8 } },
      ],
      series: [{
        type: 'candlestick',
        name: '上证指数',
        data: ohlc,
        itemStyle: {
          color: UP_COLOR, color0: DOWN_COLOR,
          borderColor: UP_COLOR, borderColor0: DOWN_COLOR,
        },
        markArea: { silent: true, data: areas },
        markPoint: { silent: true, data: patPoints },
        markLine: { silent: true, symbol: 'none', data: patLines },
      }],
    }, true);
    return () => { /* 保留实例，随组件卸载由 effect 清理 */ };
  }, [bars, waves]);

  useEffect(() => () => { if (chartRef.current) { chartRef.current.dispose(); chartRef.current = null; } }, []);
  useEffect(() => {
    const onResize = () => chartRef.current && chartRef.current.resize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const lastColor = last ? (OP_COLOR[last.operation] || MUTED) : MUTED;
  return (
    <div className="cp-threat-card" style={{ padding: '10px 12px' }}>
      <div className="cp-threat-head">
        <span className="cp-threat-label">上证指数 · 浪型链</span>
        <span className="cp-threat-en">SSE INDEX · WAVE LADDER</span>
        {last && <span className="cp-threat-dot safe" />}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', margin: '2px 0 6px' }}>
        {last ? (
          <>
            <span style={{ fontWeight: 700, color: INK }}>{last.name}</span>
            <span style={{ color: MUTED, fontSize: 11 }}>
              级别 {last.level}{last.sub_level ? ' / ' + last.sub_level : ''}
              {last.main_up ? ' · 主升3-3' : ''}
            </span>
            <span style={{ color: lastColor, fontWeight: 700, fontSize: 12 }}>
              {OP_CN[last.operation] || last.operation || '-'}
            </span>
            {last.mode && <span style={{ color: '#c98a12', fontWeight: 700 }}>模式 {last.mode}</span>}
            {last.invest != null && <span style={{ color: MUTED, fontSize: 11 }}>投入系数 {Number(last.invest).toFixed(2)}</span>}
          </>
        ) : (
          <span style={{ color: MUTED, fontSize: 11 }}>{loading ? '加载中…' : (err || '无数据')}</span>
        )}
      </div>
      <div ref={ref} style={{ width: '100%', height, opacity: loading ? 0.4 : 1 }} />
      {/* 图例：色带 = 当日 operation（敞口许可），不是「能不能交易」 ✓ */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 2 }}>
        {OP_ORDER.map((k) => (
          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, color: MUTED }}>
            <i style={{ width: 8, height: 8, borderRadius: 2, background: OP_FILL[k], opacity: 0.75 }} />
            {OP_CN[k]}
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 8, color: MUTED }}>
          {bars.length ? bars[0].date + ' ~ ' + bars[bars.length - 1].date + '（' + bars.length + ' 根）' : ''}
          {' '}色带＝当日浪型档位；悬停看当日浪型与操作建议
        </span>
        <span style={{ fontSize: 8, color: MUTED, textAlign: 'right' }}>
          {src}{note ? '　' + note : ''}
        </span>
      </div>
    </div>
  );
}
