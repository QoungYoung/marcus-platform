/* Hallmark · macrostructure: Split-Tool Console · tone: utilitarian-academy · anchor hue: blue (#2f7cd3) */
import { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { tBacktestApi } from '../api/client';
import '../styles/t-backtest-page.css';

interface BtTask {
  id: number;
  symbol: string;
  symbols_json?: any[];
  build_mode?: boolean;
  start_date?: string;
  end_date?: string;
  status: string;
  review_mode?: string;
  error_message?: string;
  created_at?: string;
  conditions_json?: any[];
  progress?: number;
}

interface BtEvent {
  event_type: string;
  trade_day: string;
  bar_time: string;
  data?: any;
}

interface BtMetrics {
  metrics: Record<string, any>;
  caliber_notes: string[];
  equity_curve?: { trade_date: string; total_asset: number }[];
}

interface Candidate {
  symbol: string;
  score: number;
  pass_gate: boolean;
  reasons?: string[];
  trend?: string;
}

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中', running: '回测中', completed: '已完成', failed: '失败', cancelled: '已取消',
};
const STATUS_CLASS: Record<string, string> = {
  pending: 'is-pending', running: 'is-running', completed: 'is-done', failed: 'is-failed', cancelled: 'is-cancel',
};

function fmtPct(v?: number) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(2)}%`;
}
function fmtMoney(v?: number) {
  if (v == null || Number.isNaN(v)) return '—';
  return `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}
function fmtDay(d?: string) {
  if (!d) return '';
  return d.slice(0, 10);
}

export default function TBacktestPage() {
  const [tasks, setTasks] = useState<BtTask[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [report, setReport] = useState<BtMetrics | null>(null);
  const [liveEvents, setLiveEvents] = useState<BtEvent[]>([]);
  const [liveProgress, setLiveProgress] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);

  // 创建表单
  const [symbolInput, setSymbolInput] = useState('');
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectSource, setSelectSource] = useState<'manual' | 'pool' | 'scan'>('manual');
  const [selectLimit, setSelectLimit] = useState('10');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [condTemplate, setCondTemplate] = useState('auto');
  const [buildMode, setBuildMode] = useState(true);
  const [netAsset, setNetAsset] = useState('200000');
  const [reviewMode, setReviewMode] = useState<'llm' | 'rule'>('rule');

  const loadTasks = useCallback(async () => {
    try {
      const r = await tBacktestApi.list(50);
      setTasks((r.data as any).tasks || []);
    } catch (e: any) {
      setError(e?.message || '任务列表加载失败');
    }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  // 实时轮询：有 pending/running 任务时每 3s 刷新任务列表
  useEffect(() => {
    const hasLive = tasks.some((t) => t.status === 'pending' || t.status === 'running');
    if (!hasLive) return;
    const timer = window.setInterval(() => { loadTasks(); }, 3000);
    return () => window.clearInterval(timer);
  }, [tasks, loadTasks]);

  // 选中任务：running → 轮询详情（进度）+ 事件流；完成 → 加载报告
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    if (selectedId == null) { setReport(null); setLiveEvents([]); setLiveProgress(null); return; }
    const sel = tasks.find((t) => t.id === selectedId);
    const refresh = () => {
      tBacktestApi.detail(selectedId)
        .then((r: any) => {
          if (!alive) return;
          const d = r.data;
          const p = Number(d?.task?.progress ?? 0);
          setLiveProgress(d?.task?.status === 'running' || d?.task?.status === 'pending' ? p : null);
          if (d?.task?.status === 'running' || d?.task?.status === 'pending') {
            tBacktestApi.events(selectedId, 300)
              .then((er: any) => { if (alive) setLiveEvents((er.data as any)?.events || []); })
              .catch(() => {});
          }
        })
        .catch(() => {});
      tBacktestApi.report(selectedId)
        .then((r) => { if (alive) setReport(r.data as BtMetrics); })
        .catch(() => { if (alive) setReport(null); });
    };
    refresh();
    if (sel && (sel.status === 'pending' || sel.status === 'running')) {
      timer = window.setInterval(refresh, 3000);
    }
    return () => { alive = false; if (timer) window.clearInterval(timer); };
  }, [selectedId, tasks]);

  const loadCandidates = async () => {
    setError('');
    try {
      const r = await tBacktestApi.candidates(10);
      const list: Candidate[] = (r.data as any).candidates || [];
      setCandidates(list);
      const ok = list.filter((c) => c.pass_gate).map((c) => c.symbol);
      if (ok.length) setSymbols((prev) => Array.from(new Set([...prev, ...ok])));
      setMsg(`候选池加载 ${list.length} 只（达标 ${ok.length} 只已加入）`);
    } catch (e: any) {
      setError(e?.message || '候选池加载失败');
    }
  };

  const buildConditions = () => {
    if (condTemplate === 'none') return [];
    const base = { armed: 1, vol_ratio_thresh: 1.5, stabilize_level: 'not_new_low' };
    if (condTemplate === 'low_buy') {
      return [{ trigger_kind: 'low_buy', target_price: 0, ...base, expression: {
        and: [
          { field: 'quote.change_pct', op: '<=', value: -1.5 },
          { field: 'vol_ratio', op: '>=', value: 1.5 },
        ] } }];
    }
    if (condTemplate === 'high_sell') {
      return [{ trigger_kind: 'high_sell_then_buy_back', sell_target_price: 0, ...base, expression: {
        and: [
          { field: 'quote.change_pct', op: '>=', value: 1.5 },
          { field: 'vol_ratio', op: '>=', value: 1.5 },
        ] } }];
    }
    return []; // auto：引擎按建仓成本生成
  };

  const submit = async () => {
    setError(''); setMsg('');
    if (selectSource === 'manual' && symbols.length === 0) { setError('手动模式请至少添加一个标的（或改用自动选股）'); return; }
    if (!startDate || !endDate) { setError('请选择回测日期范围'); return; }
    setLoading(true);
    try {
      const r = await tBacktestApi.create({
        symbol: 'combined',
        symbols: selectSource === 'manual' ? symbols : [],
        select_source: selectSource,
        select_limit: Number(selectLimit) || 10,
        build_mode: buildMode,
        build_limit_ratio: 0.55,
        start_date: startDate,
        end_date: endDate,
        conditions: buildConditions(),
        net_asset: Number(netAsset) || 200000,
        review_mode: reviewMode,
      });
      const taskId = (r.data as any).task_id;
      setMsg(`回测任务已创建 #${taskId}（${(r.data as any).mode || 'single'}）`);
      setSymbols([]); setSymbolInput('');
      await loadTasks();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '任务创建失败');
    } finally {
      setLoading(false);
    }
  };

  const cancelTask = async (id: number) => {
    try { await tBacktestApi.cancel(id); await loadTasks(); }
    catch (e: any) { setError(e?.message || '取消失败'); }
  };

  const metrics = report?.metrics || {};
  const portfolio = metrics.per_symbol_return ? metrics : null; // 组合模式
  const perSymbol = metrics.per_symbol || [];
  const buildDecisions = metrics.build_decisions || [];
  const equity = report?.equity_curve || [];

  return (
    <div className="tbt-page">
      <header className="tbt-header">
        <div>
          <h2>做T回测</h2>
          <p className="tbt-sub">多标的多日组合回测 · Agent 选股建仓模拟 · m5 历史回放</p>
        </div>
        <button className="tbt-btn tbt-btn-outline" onClick={loadTasks} disabled={loading}>刷新任务</button>
      </header>

      <div className="tbt-body">
        {/* ── 左：任务列表 ── */}
        <aside className="tbt-list">
          <div className="tbt-panel-head">
            <span>回测任务</span>
            <span className="tbt-count">{tasks.length}</span>
          </div>
          <ul className="tbt-task-list">
            {tasks.length === 0 && <li className="tbt-empty">暂无任务 — 右侧创建</li>}
            {tasks.map((t) => (
              <li key={t.id}>
                <button
                  className={`tbt-task ${selectedId === t.id ? 'is-active' : ''}`}
                  onClick={() => { setSelectedId(t.id); setReport(null); }}
                >
                  <span className="tbt-task-top">
                    <b>#{t.id}</b>
                    <span className={`tbt-status ${STATUS_CLASS[t.status] || ''}`}>{STATUS_LABEL[t.status] || t.status}</span>
                  </span>
                  <span className="tbt-task-main">
                    {t.build_mode ? `${(t.symbols_json || []).length} 标的 · 组合建仓` : t.symbol}
                  </span>
                  <span className="tbt-task-meta">
                    {fmtDay(t.start_date)} → {fmtDay(t.end_date)} · {t.review_mode === 'llm' ? 'LLM复核' : '规则复核'}
                  </span>
                  {(t.status === 'running' || t.status === 'pending') && (
                    <span className="tbt-progress">
                      <span className="tbt-progress-bar">
                        <span className="tbt-progress-fill" style={{ width: `${Math.max(2, Math.min(100, t.progress ?? 0))}%` }} />
                      </span>
                      <span className="tbt-progress-pct">{t.status === 'running' ? `${t.progress ?? 0}%` : '排队中'}</span>
                    </span>
                  )}
                  {t.status === 'failed' && <span className="tbt-task-err">{t.error_message}</span>}
                  {t.status === 'running' || t.status === 'pending' ? (
                    <button className="tbt-btn tbt-btn-mini" onClick={(e) => { e.stopPropagation(); cancelTask(t.id); }}>取消</button>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* ── 右：创建 / 报告 ── */}
        <main className="tbt-main">
          {error && <div className="tbt-alert tbt-alert-err">{error}</div>}
          {msg && <div className="tbt-alert">{msg}</div>}

          {/* 创建表单 */}
          <section className="tbt-panel">
            <div className="tbt-panel-head"><span>新建回测</span><span className="tbt-hint">组合模式：建仓规则选股 → 各自做T → 组合收益</span></div>
            <div className="tbt-form">
              <div className="tbt-field tbt-field-wide">
                <label>选股方式</label>
                <div className="tbt-radio-row">
                  {([
                    ['manual', '手动输入'],
                    ['pool', '自动 · 做T候选池'],
                    ['scan', '自动 · 全市场扫描'],
                  ] as const).map(([val, label]) => (
                    <button
                      key={val}
                      type="button"
                      className={`tbt-radio ${selectSource === val ? 'is-on' : ''}`}
                      onClick={() => setSelectSource(val)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="tbt-symbol-hint">
                  {selectSource === 'manual'
                    ? '手动添加标的（支持"从候选池加载"快速选择）'
                    : selectSource === 'pool'
                      ? '自动从做T候选池选股（可T质量打分达标，精筛用回测期前历史日线防前视）'
                      : '全市场扫描选股（stock_basic 粗筛 → 精筛，首跑约 1-2 分钟；粗筛活跃度用当前数据，精筛打分历史化）'}
                </div>
              </div>

              {selectSource === 'manual' && (
                <div className="tbt-field tbt-field-wide">
                  <label>候选标的</label>
                  <div className="tbt-symbol-row">
                    <input
                      value={symbolInput}
                      placeholder="输入代码如 600519，回车添加"
                      onChange={(e) => setSymbolInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && symbolInput.trim()) {
                          setSymbols((p) => Array.from(new Set([...p, symbolInput.trim().toUpperCase()])));
                          setSymbolInput('');
                        }
                      }}
                    />
                    <button type="button" className="tbt-btn tbt-btn-outline" onClick={loadCandidates}>从候选池加载</button>
                  </div>
                  <div className="tbt-chips">
                    {symbols.map((s) => (
                      <span key={s} className="tbt-chip">
                        {s}
                        <button type="button" aria-label={`移除 ${s}`} onClick={() => setSymbols((p) => p.filter((x) => x !== s))}>×</button>
                      </span>
                    ))}
                    {symbols.length === 0 && <span className="tbt-chip-hint">未添加 — 可使用"从候选池加载"或手动输入</span>}
                  </div>
                  {candidates.length > 0 && (
                    <div className="tbt-cand">
                      <span className="tbt-cand-title">候选池（可T质量分）</span>
                      {candidates.map((c) => (
                        <button
                          key={c.symbol}
                          type="button"
                          className={`tbt-cand-item ${c.pass_gate ? 'is-pass' : ''}`}
                          onClick={() => setSymbols((p) => Array.from(new Set([...p, c.symbol])))}
                        >
                          {c.symbol} · {fmtPct(c.score * 100)} {c.pass_gate ? '✓' : `✗ ${(c.reasons || []).join(',')}`}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {selectSource !== 'manual' && (
                <div className="tbt-field">
                  <label>选股数量</label>
                  <input type="number" value={selectLimit} min="1" max="20" onChange={(e) => setSelectLimit(e.target.value)} />
                </div>
              )}

              <div className="tbt-field">
                <label>开始日期</label>
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div className="tbt-field">
                <label>结束日期</label>
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
              <div className="tbt-field">
                <label>条件模板</label>
                <select value={condTemplate} onChange={(e) => setCondTemplate(e.target.value)}>
                  <option value="auto">自动（按建仓成本生成低吸/高抛）</option>
                  <option value="low_buy">表达式：放量下跌 ≥1.5% 低吸</option>
                  <option value="high_sell">表达式：放量上涨 ≥1.5% 高抛</option>
                  <option value="none">无条件（仅建仓评估）</option>
                </select>
              </div>
              <div className="tbt-field">
                <label>组合净值（元）</label>
                <input type="number" value={netAsset} min="10000" step="10000" onChange={(e) => setNetAsset(e.target.value)} />
              </div>
              <div className="tbt-field">
                <label>复核模式</label>
                <select value={reviewMode} onChange={(e) => setReviewMode(e.target.value as any)}>
                  <option value="rule">规则（快、可复现）</option>
                  <option value="llm">LLM（真实复核，沙盒）</option>
                </select>
              </div>

              <label className="tbt-switch">
                <input type="checkbox" checked={buildMode} onChange={(e) => setBuildMode(e.target.checked)} />
                <span className="tbt-switch-track" />
                <span className="tbt-switch-label">建仓模拟（Agent 选股：build_score + 趋势闸门，资金 ≤ 净值×55%）</span>
              </label>

              <button className="tbt-btn tbt-btn-primary tbt-submit" onClick={submit} disabled={loading}>
                {loading ? '创建中…' : '开始回测'}
              </button>
            </div>
          </section>

          {/* 报告 */}
          {selectedId != null && report && (
            <section className="tbt-panel">
              <div className="tbt-panel-head">
                <span>回测报告 #{selectedId}</span>
                {metrics.total_return_pct != null && (
                  <span className={`tbt-return ${Number(metrics.total_return_pct) >= 0 ? 'is-up' : 'is-down'}`}>
                    {fmtPct(metrics.total_return_pct)}
                  </span>
                )}
              </div>

              <div className="tbt-metrics">
                <div className="tbt-metric"><span className="tbt-metric-label">组合收益</span><b>{fmtPct(metrics.total_return_pct)}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">建仓标的</span><b>{metrics.built_count ?? metrics.symbols ?? '—'}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">触发次数</span><b>{metrics.trigger_count ?? 0}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">成交次数</span><b>{metrics.executed_count ?? 0}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">胜率</span><b>{fmtPct(metrics.win_rate_pct)}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">最大回撤</span><b>{fmtPct(metrics.max_drawdown_pct)}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">已实现盈亏</span><b>{fmtMoney(metrics.realized_pnl)}</b></div>
              </div>

              {equity && equity.length > 0 && (
                <div className="tbt-chart">
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={equity} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--gp-line-soft)" />
                      <XAxis dataKey="trade_date" tick={{ fontSize: 11, fill: 'var(--gp-muted)' }} />
                      <YAxis tick={{ fontSize: 11, fill: 'var(--gp-muted)' }} domain={['auto', 'auto']} width={70} />
                      <Tooltip
                        formatter={(v: any) => [`¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, '组合资产']}
                        contentStyle={{ border: '1px solid var(--gp-line)', borderRadius: 5, fontSize: 12 }}
                      />
                      <Line type="monotone" dataKey="total_asset" stroke="var(--gp-blue)" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}

              {buildDecisions && buildDecisions.length > 0 && (
                <div className="tbt-table-wrap">
                  <h4>建仓决策（规则模拟）</h4>
                  <table className="tbt-table">
                    <thead><tr><th>标的</th><th>决策</th><th>打分</th><th>建仓价</th><th>股数</th><th>原因</th></tr></thead>
                    <tbody>
                      {buildDecisions.map((d: any, i: number) => (
                        <tr key={i}>
                          <td>{d.symbol}</td>
                          <td>{d.decision === 'built' ? '已建仓' : d.decision === 'fixed_hold' ? '固定底仓' : '否决'}</td>
                          <td>{d.score != null ? d.score.toFixed(2) : '—'}</td>
                          <td>{d.price ?? '—'}</td>
                          <td>{d.shares ?? '—'}</td>
                          <td className="tbt-cell-reason">{(d.reasons || []).join('；') || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {perSymbol && perSymbol.length > 0 && (
                <div className="tbt-table-wrap">
                  <h4>标的分项</h4>
                  <table className="tbt-table">
                    <thead><tr><th>标的</th><th>建仓</th><th>收益</th><th>触发</th><th>成交</th><th>拦截</th></tr></thead>
                    <tbody>
                      {perSymbol.map((r: any, i: number) => (
                        <tr key={i}>
                          <td>{r.symbol}</td>
                          <td>{r.build?.source === 'build_rule' ? '规则建仓' : '固定底仓'}</td>
                          <td>{fmtPct(r.metrics?.total_return_pct)}</td>
                          <td>{r.metrics?.trigger_count ?? 0}</td>
                          <td>{r.metrics?.executed_count ?? 0}</td>
                          <td>{r.metrics?.blocked_count ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {report.caliber_notes && report.caliber_notes.length > 0 && (
                <details className="tbt-notes">
                  <summary>口径差异声明（{report.caliber_notes.length} 条）</summary>
                  <ul>{report.caliber_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                </details>
              )}
            </section>
          )}

          {selectedId != null && !report && (
            <section className="tbt-panel">
              {liveProgress != null ? (
                <div className="tbt-live">
                  <div className="tbt-panel-head">
                    <span>实时进展 #{selectedId}</span>
                    <span className="tbt-status is-running">回测中 {liveProgress}%</span>
                  </div>
                  <div className="tbt-progress tbt-progress-lg">
                    <span className="tbt-progress-bar">
                      <span className="tbt-progress-fill" style={{ width: `${Math.max(2, Math.min(100, liveProgress))}%` }} />
                    </span>
                    <span className="tbt-progress-pct">{liveProgress}%</span>
                  </div>
                  {liveEvents.length > 0 && (
                    <div className="tbt-table-wrap">
                      <h4>实时事件流（{liveEvents.length}）</h4>
                      <table className="tbt-table">
                        <thead><tr><th>类型</th><th>交易日</th><th>时间</th><th>内容</th></tr></thead>
                        <tbody>
                          {liveEvents.slice(-50).reverse().map((ev, i) => {
                            const d = ev.data || {};
                            // API 返回完整事件对象（{type, data:{...}}），兼容平铺/嵌套两种结构
                            const inner = d.data && typeof d.data === 'object' ? d.data : d;
                            const trig = inner.trigger || {};
                            const detail = inner.reason || inner.decision || (trig.event_type ? `触发 ${trig.event_type} @ ${trig.trigger_price}` : '') || JSON.stringify(inner).slice(0, 80);
                            return (
                              <tr key={i}>
                                <td><span className={`tbt-ev tbt-ev-${ev.event_type}`}>{ev.event_type}</span></td>
                                <td>{ev.trade_day || inner.trade_day || ''}</td>
                                <td>{ev.bar_time || inner.bar_time || ''}</td>
                                <td className="tbt-cell-reason">{String(detail)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ) : (
                <div className="tbt-empty-report">
                  任务 #{selectedId} 报告尚未生成（{STATUS_LABEL[tasks.find((t) => t.id === selectedId)?.status || ''] || '状态未知'}），稍后刷新
                </div>
              )}
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
