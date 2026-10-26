import { useCallback, useEffect, useState } from 'react';
import { disciplineApi } from '../api/client';

interface DefenseBuckets {
  base: number;
  t: number;
  cash: number;
}

interface DefenseState {
  level: string;
  level_num: number;
  target_pct: number | null;
  buckets?: DefenseBuckets | null;
  allow_new: boolean;
  allow_add: boolean;
  allow_t: boolean | string;
  reasons?: string[];
  asof?: string;
}

const LEVEL_META: Record<string, { label: string; tone: string; color: string }> = {
  G0: { label: '正常', tone: 'g0', color: '#27a06b' },
  G1: { label: '警戒', tone: 'g1', color: '#c98a12' },
  G2: { label: '防御', tone: 'g2', color: '#d97706' },
  G3: { label: '空仓', tone: 'g3', color: '#e5484d' },
};

function targetText(v: number | null | undefined): string {
  if (v == null) return '不设上限';
  return `≤ ${Number(v).toFixed(0)}%`;
}

function timeText(s?: string): string {
  if (!s) return '—';
  // 后端给的是 "YYYY-MM-DD HH:MM:SS"，只显示时分秒
  const m = s.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : s;
}

export default function DefenseLevelCard() {
  const [state, setState] = useState<DefenseState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await disciplineApi.getDefenseState();
      setState(res.data as DefenseState);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const meta = LEVEL_META[state?.level || 'G0'] || LEVEL_META.G0;
  const targetPct = state?.target_pct == null ? 100 : Math.max(0, Math.min(100, state.target_pct));
  const buckets = state?.buckets || null;
  const reasons = (state?.reasons || []).slice(0, 4);
  const allowT = state?.allow_t === true ? '可做' : state?.allow_t === 'reduce_only' ? '只减' : '禁止';

  return (
    <div className="cp-strategic-panel cp-defense-card">
      <div className="cp-panel-head">
        <span className="cp-tick" />
        <h2>防御等级</h2>
        <span className="cp-en">DEFENSE LEVEL</span>
        <div className="cp-panel-actions">
          <button className="cp-icon-btn" onClick={load} disabled={loading} title="刷新防御状态">
            <i className={`fas fa-sync-alt ${loading ? 'fa-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading && !state ? (
        <div className="cp-defense-skeleton">
          <div className="cp-skel" style={{ width: 58, height: 34 }} />
          <div className="cp-skel" style={{ width: '70%', height: 10 }} />
          <div className="cp-skel" style={{ width: '100%', height: 5 }} />
        </div>
      ) : error && !state ? (
        <div className="cp-defense-error">
          <i className="fas fa-exclamation-circle" /> 防御状态获取失败
        </div>
      ) : state ? (
        <>
          <div className="cp-defense-head">
            <div className={`cp-defense-level ${meta.tone}`}>{state.level}</div>
            <div className="cp-defense-heading">
              <div className="cp-defense-label" style={{ color: meta.color }}>{meta.label}</div>
              <div className="cp-defense-target">总仓目标 {targetText(state.target_pct)}</div>
            </div>
            <span className="cp-defense-dot" style={{ background: meta.color }} />
          </div>

          <div className="cp-defense-bar">
            <div className="cp-defense-bar-fill" style={{
              width: `${targetPct}%`,
              background: meta.color,
            }} />
          </div>
          <div className="cp-defense-scale">
            <span>0%</span>
            <span>{state.target_pct == null ? '不设上限' : `${Number(state.target_pct).toFixed(0)}%`}</span>
            <span>100%</span>
          </div>

          <div className="cp-defense-chips">
            <span className={`cp-defense-chip ${state.allow_new ? 'ok' : 'no'}`}>
              新开 {state.allow_new ? '允许' : '禁止'}
            </span>
            <span className={`cp-defense-chip ${state.allow_add ? 'ok' : 'no'}`}>
              加仓 {state.allow_add ? '允许' : '禁止'}
            </span>
            <span className={`cp-defense-chip ${allowT === '可做' ? 'ok' : allowT === '只减' ? 'warn' : 'no'}`}>
              做T {allowT}
            </span>
          </div>

          {buckets && (
            <>
              <div className="cp-defense-stack" title="底仓 / T / 现金结构">
                <span style={{ width: `${buckets.base}%`, background: 'var(--cc-blue)' }} />
                <span style={{ width: `${buckets.t}%`, background: 'var(--cc-amber)' }} />
                <span style={{ width: `${buckets.cash}%`, background: 'var(--cc-line-soft)' }} />
              </div>
              <div className="cp-defense-legend">
                <span>底仓 {buckets.base}%</span>
                <span>T {buckets.t}%</span>
                <span>现金 {buckets.cash}%</span>
              </div>
            </>
          )}

          {reasons.length > 0 && (
            <ul className="cp-defense-reasons">
              {reasons.map((r, i) => (
                <li key={i} title={r}>{r}</li>
              ))}
            </ul>
          )}

          <div className="cp-defense-foot">
            <span>更新 {timeText(state.asof)}</span>
            <span>{state.level_num >= 2 ? '防守模式' : '正常模式'}</span>
          </div>
        </>
      ) : null}
    </div>
  );
}
