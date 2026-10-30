import { useCallback, useEffect, useState } from 'react';
import { portfolioApi } from '../api/client';

interface LevelRef {
  price?: number | null;
  state?: string;
  status_text?: string;
  type?: string;
  candidate_ids?: string[];
}
interface AgentPosition {
  symbol: string;
  volume: number;
  avg_price: number;
  current_price: number | null;
  status: string;
  action_mode?: string | null;
  break_trigger?: LevelRef | null;
  next_support?: LevelRef | null;
  rebound_zone?: { zone?: number[]; candidate_ids?: string[] } | null;
  confidence?: number | null;
  no_confident_level?: boolean;
  phase?: string | null;
  updated_at?: string | null;
  source?: string;
}
interface AgentLevels {
  date: string;
  generated_at?: string | null;
  positions: AgentPosition[];
  watches: Array<Record<string, unknown>>;
  message?: string;
}

const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  holding: { text: '持有观察', cls: 'ok' },
  broken: { text: '已破位', cls: 'danger' },
  watching: { text: '等反抽', cls: 'warn' },
  pending: { text: '待执行', cls: 'warn' },
  executed: { text: '已执行', cls: 'ok' },
  invalidated: { text: '已修复', cls: 'muted' },
  expired: { text: '已过期', cls: 'muted' },
  agent: { text: 'Agent 已判', cls: 'ok' },
  no_agent: { text: '未判定', cls: 'muted' },
};

function fmt(v: number | null | undefined, digits = 2): string {
  return v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(digits);
}
function short(s: string): string { return s.replace(/^(SH|SZ|BJ)/, ''); }
function statusMeta(s: string) { return STATUS_LABEL[s] || STATUS_LABEL.no_agent; }

export default function AgentLevelsCard() {
  const [data, setData] = useState<AgentLevels | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await portfolioApi.getAgentLevels('stock');
      setData(res.data as AgentLevels);
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

  return (
    <div className="cp-strategic-panel cp-agent-levels">
      <div className="cp-panel-head">
        <span className="cp-tick" />
        <h2>关键位 · Agent</h2>
        <span className="cp-en">KEY LEVELS</span>
        <div className="cp-panel-actions">
          <button className="cp-icon-btn" onClick={load} disabled={loading} title="刷新 Agent 关键位">
            <i className={`fas fa-sync-alt ${loading ? 'fa-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="cp-agent-skeleton">
          <div className="cp-skel" style={{ width: '55%', height: 14 }} />
          <div className="cp-skel" style={{ width: '100%', height: 8 }} />
          <div className="cp-skel" style={{ width: '85%', height: 8 }} />
        </div>
      ) : error && !data ? (
        <div className="cp-agent-empty"><i className="fas fa-exclamation-circle" /> 关键位获取失败</div>
      ) : !data || data.positions.length === 0 ? (
        <div className="cp-agent-empty">
          <i className="fas fa-crosshairs" />
          <div>{data?.message || '今日无持仓，agent 未触发'}</div>
          <small>窗口 09:45 / 10:30 / 14:30 / 14:55</small>
        </div>
      ) : (
        <>
          <div className="cp-agent-summary">
            <span><b>{data.positions.length}</b> 持仓</span>
            <span><b>{data.positions.filter(p => p.break_trigger).length}</b> 有关键位</span>
            <span className="muted">{data.generated_at ? data.generated_at.slice(11, 19) : '—'}</span>
          </div>
          <div className="cp-agent-list">
            {data.positions.map(p => {
              const st = statusMeta(p.status);
              const bt = p.break_trigger;
              const ns = p.next_support;
              const rz = p.rebound_zone?.zone;
              return (
                <div className="cp-agent-row" key={p.symbol}>
                  <div className="cp-agent-row-head">
                    <span className="cp-agent-sym">{short(p.symbol)}</span>
                    <span className={`cp-agent-badge ${st.cls}`}>{st.text}</span>
                    <span className="cp-agent-price">{p.current_price == null ? '—' : fmt(p.current_price, p.current_price >= 10 ? 2 : 3)}</span>
                  </div>
                  <div className="cp-agent-levels">
                    {bt && (
                      <div className={`cp-agent-level ${bt.state === 'broken' ? 'broken' : 'hold'}`}>
                        <span className="cp-agent-k">破位线</span>
                        <b>{fmt(bt.price, (bt.price || 0) >= 10 ? 2 : 3)}</b>
                        <span className="cp-agent-status">{bt.status_text || bt.state || '—'}</span>
                      </div>
                    )}
                    {ns && (
                      <div className="cp-agent-level">
                        <span className="cp-agent-k">下一支撑</span>
                        <b>{fmt(ns.price, (ns.price || 0) >= 10 ? 2 : 3)}</b>
                      </div>
                    )}
                    {rz && rz.length >= 2 && (
                      <div className="cp-agent-level rebound">
                        <span className="cp-agent-k">反抽带</span>
                        <b>{fmt(rz[0], (rz[0] || 0) >= 10 ? 2 : 3)}–{fmt(rz[1], (rz[1] || 0) >= 10 ? 2 : 3)}</b>
                      </div>
                    )}
                    {!bt && !ns && !rz && (
                      <div className="cp-agent-level muted">暂无 agent 关键位</div>
                    )}
                  </div>
                  <div className="cp-agent-meta">
                    <span>{p.action_mode || '—'}</span>
                    <span>{p.phase || ''}</span>
                    {p.confidence != null && <span>conf {fmt(p.confidence, 2)}</span>}
                    {p.no_confident_level && <span className="warn">低置信</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
