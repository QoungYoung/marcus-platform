import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { waveApi } from '../api/client';

/* ═══════════════════════════════════════════════════════
 * 低位超额 · 反弹线（portfolio 页，IndexWaveCard 右侧的正方形卡）
 *   · 数据：GET /api/v1/wave/low-position-card（paper-only 观察线，**不下单** ✓）
 *   · 语义：`status=ready` ⇒ 今日有候选；`disabled` ⇒ 未启用；其余 ⇒ 今日无候选
 *   · 盘面：本日候选只数（数字滚动）+ 上一有效样本/上次收益 + 主线（含 20 日超额）+ 理由前 2 条
 *   · 节律：60s 轮询（与浪型卡片同口径 ✓）；接口异常 ⇒ 卡片显式写「数据异常」✗ 不静默空白
 *   ★ 2026-10-23：本文件曾丢失（CSS 与已发布 bundle 仍在），按 bundle 逐字段复原 ✓
 * ═══════════════════════════════════════════════════════ */

interface LowPos {
  status?: string;                    // ready | disabled | 其它（无候选）
  candidates?: number;                // 本日候选只数
  signal_date?: string | null;
  mainline?: string | null;           // 主线名（低位超额所在方向）
  mainline_r20?: number | null;       // 主线 20 日超额（小数，1.2% ⇒ 0.012）
  mainline_sideways?: boolean | null;
  reasons?: string[];                 // 选股/拦截理由（只显示前 2 条 ✓）
  last_valid_date?: string | null;    // 上一有效样本日（YYYYMMDD）
  last_return_pct?: number | null;    // 上次收益（%）
  updated_at?: string;
}

// YYYYMMDD → MM-DD（后端给 8 位；其它形状原样显示，取不到给 —）
function fmt8(d?: string | null): string {
  const s = String(d ?? '');
  return s.length === 8 ? `${s.slice(4, 6)}-${s.slice(6, 8)}` : s || '—';
}

export default function LowPositionCard() {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const introRef = useRef(false);          // 入场动画只跑一次（轮询刷新不重播 ✓）
  const [data, setData] = useState<LowPos | null>(null);
  const [count, setCount] = useState(0);   // 数字滚动用（从旧值补间到本日候选 ✓）
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const load = () => {
      waveApi.getLowPositionCard()
        .then((r) => {
          if (!alive) return;
          setData(((r.data || {}) as LowPos));
          setErr('');
        })
        .catch((e) => { if (alive) setErr(String((e && e.message) || e)); });
    };
    load();
    const t = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  // 入场动画（首屏一次）+ 候选数滚动
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !data) return;
    if (!introRef.current) {
      introRef.current = true;
      gsap.fromTo(el, { y: 18, scale: 0.97 }, { y: 0, scale: 1, duration: 0.65, ease: 'power3.out' });
      gsap.fromTo('.cp-lowpos-stat', { opacity: 0, y: 8 },
        { opacity: 1, y: 0, duration: 0.45, stagger: 0.08, delay: 0.18, ease: 'power2.out' });
      gsap.fromTo('.cp-lowpos-ring', { rotate: -22, opacity: 0 },
        { rotate: 0, opacity: 1, duration: 0.9, delay: 0.1, ease: 'power3.out' });
    }
    const proxy = { v: count };
    gsap.to(proxy, {
      v: Number(data.candidates || 0), duration: 0.75, ease: 'power2.out', overwrite: true,
      onUpdate: () => setCount(Math.round(proxy.v)),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // 悬停微交互
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const enter = () => gsap.to(el, { y: -4, scale: 1.012, duration: 0.28, ease: 'power2.out' });
    const leave = () => gsap.to(el, { y: 0, scale: 1, duration: 0.35, ease: 'power3.out' });
    el.addEventListener('mouseenter', enter);
    el.addEventListener('mouseleave', leave);
    return () => { el.removeEventListener('mouseenter', enter); el.removeEventListener('mouseleave', leave); };
  }, []);

  const status = data?.status || 'loading';
  const ready = status === 'ready';
  const ret = data?.last_return_pct;
  const up = ret != null && ret >= 0;
  const statusText = ready ? '今日有候选' : status === 'disabled' ? '未启用' : '今日无候选';

  return (
    <div ref={rootRef} className={`cp-lowpos-card ${ready ? 'ready' : 'idle'}`}>
      <div className="cp-lowpos-grid" />
      <div className="cp-lowpos-ring" />

      <div className="cp-lowpos-head">
        <div>
          <div className="cp-lowpos-eyebrow">LOW POSITION REBOUND</div>
          <div className="cp-lowpos-title">低位超额 · 反弹线</div>
        </div>
        <span className={`cp-lowpos-dot ${ready ? 'ready' : 'idle'}`} />
      </div>

      <div className="cp-lowpos-main">
        <div className="cp-lowpos-stat cp-lowpos-label">本日候选</div>
        <div className="cp-lowpos-number-row">
          <span className="cp-lowpos-number">{count}</span>
          <span className="cp-lowpos-unit">只</span>
        </div>
        <div className={`cp-lowpos-status ${ready ? 'ready' : 'idle'}`}>{err ? '数据异常' : statusText}</div>
      </div>

      <div className="cp-lowpos-meta">
        <div className="cp-lowpos-stat">
          <span>上一有效样本</span>
          <b>{fmt8(data?.last_valid_date)}</b>
        </div>
        <div className="cp-lowpos-stat">
          <span>上次收益</span>
          <b className={ret == null ? 'muted' : up ? 'up' : 'down'}>
            {ret == null ? '—' : `${up ? '+' : ''}${ret.toFixed(2)}%`}
          </b>
        </div>
      </div>

      <div className="cp-lowpos-foot cp-lowpos-stat">
        <span>主线</span>
        <b>{data?.mainline || '—'}</b>
        {data?.mainline_r20 != null && (
          <em className={data.mainline_r20 >= 0 ? 'up' : 'down'}>
            {data.mainline_r20 >= 0 ? '+' : ''}{(data.mainline_r20 * 100).toFixed(2)}%
          </em>
        )}
      </div>

      <div className="cp-lowpos-reasons">
        {(data?.reasons || []).slice(0, 2).map((r) => <span key={r}>{r}</span>)}
      </div>
    </div>
  );
}
