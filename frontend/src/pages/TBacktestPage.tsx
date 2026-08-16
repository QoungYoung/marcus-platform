/* Hallmark · macrostructure: Session Ledger · tone: calm-precise · anchor hue: blue (#2f7cd3)
 * theme: Blue Archive (brand preserved) · designed-as-app
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { tAiApi, tBacktestApi } from '../api/client';
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
  symbol?: string;
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
const LIVE_STATUS = new Set(['pending', 'running']);
const TERMINAL_STATUS = new Set(['completed', 'failed', 'cancelled']);

function fmtPct(v?: number) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(2)}%`;
}
function fmtSignedPct(v?: number) {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
}
function fmtMoney(v?: number) {
  if (v == null || Number.isNaN(v)) return '—';
  return `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}
function fmtDay(d?: string) {
  if (!d) return '';
  return d.slice(0, 10);
}

// 事件明细表：统一渲染（运行中实时 + 完成后全量），最新在底部 + 「跟随最新」开关
function EventDetailTable({ events, title }: { events: BtEvent[]; title?: string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const rows = events.slice(-120); // 时间正序，保留最近 120 条

  // 跟随最新：新事件到达时钉在底部；用户上翻则自动松开
  useEffect(() => {
    if (follow && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [follow, rows.length]);

  const handleScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (nearBottom !== follow) setFollow(nearBottom);
  };

  return (
    <div className="tbt-table-wrap">
      <div className="tbt-table-head">
        {title && <h4>{title}（{events.length}）</h4>}
        <button
          type="button"
          className="tbt-follow"
          aria-pressed={follow}
          onClick={() => setFollow((v) => !v)}
        >
          {follow ? '跟随最新 ✓' : '跟随最新'}
        </button>
      </div>
      <div className="tbt-events-scroll" ref={boxRef} onScroll={handleScroll}>
        <table className="tbt-table">
          <thead><tr><th>类型</th><th>交易日</th><th>时间</th><th>标的</th><th>价格</th><th>决策/内容</th></tr></thead>
          <tbody>
            {rows.map((ev, i) => {
              const d = ev.data || {};
              // API 返回 {data: {实际内容}, type: ...}，实际内容在 d.data；兼容平铺结构
              const inner = (d.data && typeof d.data === 'object') ? d.data : d;
              const trig = inner.trigger || {};
              const sym = inner.symbol || trig.symbol || ev.symbol || '';
              // 价格：trigger/交易/复核事件的内容顶层有 quote_price/exec_price/trigger_price
              const price = inner.quote_price ?? inner.exec_price ?? trig.quote_price ?? trig.trigger_price ?? inner.trigger_price ?? '';
              // 内容：reason/decision/action/触发摘要
              let detail = '';
              const t = ev.event_type;
              if (t === 'review') {
                const act = inner.action || '—';
                detail = `${act === 'exec' ? '✅执行' : act === 'wait' ? '⏳等待' : act === 'abandon' ? '⛔放弃' : act}：${inner.reason || ''}`;
              } else if (t === 'escalated') {
                detail = `升级人工：${inner.reason || ''}`;
              } else if (t === 'ai_wait') {
                detail = `AI等待：${inner.reason || ''}`;
              } else if (t === 'blocked') {
                detail = `拦截：${inner.reason || ''}`;
              } else if (t === 'trade') {
                const tr = inner.trade || {};
                detail = `${tr.side === 'buy' ? '买入' : '卖出'} ${tr.volume ?? ''}股 @ ${tr.price ?? ''}${tr.realized_pnl != null ? ` 盈亏${Number(tr.realized_pnl).toFixed(2)}` : ''}`;
              } else if (t === 'trigger') {
                detail = `${inner.event_type || trig.event_type || ''} 触发价=${trig.trigger_price ?? inner.trigger_price ?? ''}`;
              } else {
                detail = inner.reason || inner.decision || '';
              }
              return (
                <tr key={`${ev.event_type}-${i}`}>
                  <td><span className={`tbt-ev tbt-ev-${ev.event_type}`}>{ev.event_type}</span></td>
                  <td>{ev.trade_day || inner.trade_day || ''}</td>
                  <td>{ev.bar_time || inner.bar_time || ''}</td>
                  <td>{sym}</td>
                  <td>{price}</td>
                  <td className="tbt-cell-reason" title={String(detail)}>{String(detail).slice(0, 160)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function TBacktestPage() {
  const [tasks, setTasks] = useState<BtTask[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [report, setReport] = useState<BtMetrics | null>(null);
  const [liveEvents, setLiveEvents] = useState<BtEvent[]>([]);
  const [liveProgress, setLiveProgress] = useState<number | null>(null);
  const [aiActions, setAiActions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [formOpen, setFormOpen] = useState(true);
  const [railOpen, setRailOpen] = useState(true);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const [dupHint, setDupHint] = useState('');

  // 创建表单
  const [symbolInput, setSymbolInput] = useState('');
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectSource, setSelectSource] = useState<'manual' | 'pool' | 'scan'>('manual');
  const [selectLimit, setSelectLimit] = useState('10');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [condTemplate, setCondTemplate] = useState('auto');
  const [buildMode, setBuildMode] = useState(true);
  const [rollingBuild, setRollingBuild] = useState(false);
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

  // AI 决策审计：独立 15s 轮询（不再跟随任务列表节奏，避免列表刷新时重复拉取）
  const loadAiActions = useCallback(async () => {
    try {
      const r = await tAiApi.actions({ limit: 20 });
      setAiActions((r.data as any)?.actions || []);
    } catch { /* 面板静默失败 */ }
  }, []);
  useEffect(() => {
    loadAiActions();
    const timer = window.setInterval(() => {
      if (!document.hidden) loadAiActions();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [loadAiActions]);

  // 实时轮询：有 pending/running 任务时每 3s 刷新任务列表（后台标签页暂停）
  useEffect(() => {
    const hasLive = tasks.some((t) => LIVE_STATUS.has(t.status));
    if (!hasLive) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) loadTasks();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [tasks, loadTasks]);

  // 选中任务：running → 轮询详情（进度）+ 事件流；完成 → 加载报告（失败自动重试 2 次）
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let retryTimer: number | undefined;
    let retryLeft = 2;
    if (selectedId == null) { setReport(null); setLiveEvents([]); setLiveProgress(null); return; }
    const sel = tasks.find((t) => t.id === selectedId);
    const refresh = () => {
      tBacktestApi.detail(selectedId)
        .then((r: any) => {
          if (!alive) return;
          const d = r.data;
          const p = Number(d?.task?.progress ?? 0);
          const st = d?.task?.status;
          setLiveProgress(st === 'running' || st === 'pending' ? p : null);
          if (st === 'running' || st === 'pending') {
            // 运行中：只拉事件流，不请求 report（未完成 report 返回 409）
            tBacktestApi.events(selectedId, 300)
              .then((er: any) => { if (alive) setLiveEvents((er.data as any)?.events || []); })
              .catch(() => {});
          } else if (st === 'completed') {
            // 完成：拉报告 + 事件流
            tBacktestApi.report(selectedId)
              .then((rr) => { if (alive) setReport(rr.data as BtMetrics); })
              .catch(() => {
                if (!alive) return;
                setReport(null);
                if (retryLeft > 0) {
                  retryLeft -= 1;
                  retryTimer = window.setTimeout(refresh, 2500);
                }
              });
            tBacktestApi.events(selectedId, 300)
              .then((er: any) => { if (alive) setLiveEvents((er.data as any)?.events || []); })
              .catch(() => {});
          } else {
            // failed/cancelled：清报告
            setReport(null);
          }
        })
        .catch(() => {});
    };
    refresh();
    if (sel && LIVE_STATUS.has(sel.status)) {
      timer = window.setInterval(() => {
        if (!document.hidden) refresh();
      }, 3000);
    }
    return () => { alive = false; if (timer) window.clearInterval(timer); if (retryTimer) window.clearTimeout(retryTimer); };
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

  const addSymbol = (raw: string) => {
    const s = raw.trim().toUpperCase();
    if (!s) return;
    if (symbols.includes(s)) {
      setDupHint(`「${s}」已在列表中`);
      return;
    }
    setSymbols((p) => [...p, s]);
    setDupHint('');
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
    setError(''); setMsg(''); setDupHint('');
    if (selectSource === 'manual' && symbols.length === 0) { setError('手动模式请至少添加一个标的（或改用自动选股）'); return; }
    if (!startDate || !endDate) { setError('请选择回测日期范围'); return; }
    if (endDate < startDate) { setError('结束日期不能早于开始日期'); return; }
    setLoading(true);
    try {
      const r = await tBacktestApi.create({
        symbol: 'combined',
        symbols: selectSource === 'manual' ? symbols : [],
        select_source: selectSource,
        select_limit: Number(selectLimit) || 10,
        build_mode: buildMode,
        rolling_build: rollingBuild,
        build_limit_ratio: 0.55,
        start_date: startDate,
        end_date: endDate,
        conditions: buildConditions(),
        net_asset: Number(netAsset) || 200000,
        review_mode: reviewMode,
      });
      const taskId = (r.data as any).task_id;
      setMsg(`回测任务已创建 #${taskId}（${(r.data as any).mode || 'single'}）`);
      setSymbols([]); setSymbolInput(''); setCandidates([]);
      setFormOpen(false);           // 提交后收起表单，让位给实时进展
      setSelectedId(taskId);        // 自动选中新任务，直接看实时回放
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

  const restartTask = async (id: number) => {
    try {
      await tBacktestApi.start(id);
      setMsg(`任务 #${id} 已重新排队`);
      setSelectedId(id); setReport(null);
      await loadTasks();
    } catch (e: any) { setError(e?.response?.data?.detail || e?.message || '重跑失败'); }
  };

  const deleteTask = async (id: number) => {
    try {
      await tBacktestApi.deleteTask(id);
      if (selectedId === id) setSelectedId(null);
      await loadTasks();
    } catch (e: any) { setError(e?.message || '删除失败'); }
    finally { setConfirmDel(null); }
  };

  const retryReport = async () => {
    if (selectedId == null) return;
    try {
      const rr = await tBacktestApi.report(selectedId);
      setReport(rr.data as BtMetrics);
    } catch {
      setError(`报告 #${selectedId} 仍未能加载，请稍后重试`);
    }
  };

  const metrics = report?.metrics || {};
  const perSymbol = metrics.per_symbol || [];
  const buildDecisions = metrics.build_decisions || [];
  const equity = report?.equity_curve || [];
  const liveCount = tasks.filter((t) => LIVE_STATUS.has(t.status)).length;
  const sel = tasks.find((t) => t.id === selectedId);

  return (
    <div className="tbt-page">
      <header className="tbt-header">
        <div>
          <h2>做T回测</h2>
          <p className="tbt-sub">多标的多日组合回测 · Agent 选股建仓模拟 · m5 历史回放</p>
        </div>
        <div className="tbt-toolbar">
          {liveCount > 0 && (
            <span className="tbt-live-badge"><span className="tbt-live-dot" />{liveCount} 个任务运行中</span>
          )}
          <button type="button" className="tbt-btn tbt-btn-outline" aria-expanded={railOpen} onClick={() => setRailOpen((v) => !v)}>
            {railOpen ? '收起会话栏' : '展开会话栏'}
          </button>
          <button type="button" className="tbt-btn tbt-btn-outline" onClick={loadTasks} disabled={loading}>刷新任务</button>
        </div>
      </header>

      <div className={`tbt-body ${railOpen ? '' : 'is-rail-closed'}`}>
        {/* ── 会话栏：任务列表 + AI 决策记录 ── */}
        <aside className="tbt-rail" aria-label="回测任务与 AI 决策">
          <div className="tbt-rail-inner">
            <div className="tbt-panel-head">
              <span>回测任务</span>
              <span className="tbt-count">{tasks.length}</span>
            </div>
            <ul className="tbt-task-list">
              {tasks.length === 0 && <li className="tbt-empty">暂无任务 — 在下方创建</li>}
              {tasks.map((t) => (
                <li key={t.id}>
                  <div className={`tbt-task ${selectedId === t.id ? 'is-active' : ''}`}>
                    <button
                      type="button"
                      className="tbt-task-main"
                      aria-current={selectedId === t.id ? 'true' : undefined}
                      onClick={() => { setSelectedId(t.id); setReport(null); setConfirmDel(null); }}
                    >
                      <span className="tbt-task-top">
                        <b>#{t.id}</b>
                        <span className={`tbt-status ${STATUS_CLASS[t.status] || ''}`}>{STATUS_LABEL[t.status] || t.status}</span>
                      </span>
                      <span className="tbt-task-mainline">
                        {t.build_mode ? `${(t.symbols_json || []).length} 标的 · 组合建仓` : t.symbol}
                      </span>
                      <span className="tbt-task-meta">
                        {fmtDay(t.start_date)} → {fmtDay(t.end_date)} · {t.review_mode === 'llm' ? 'LLM复核' : '规则复核'}
                      </span>
                      {LIVE_STATUS.has(t.status) && (
                        <span className="tbt-progress">
                          <span className="tbt-progress-bar">
                            <span
                              className="tbt-progress-fill"
                              style={{ ['--_pct' as any]: `${Math.max(2, Math.min(100, t.progress ?? 0)) / 100}` }}
                            />
                          </span>
                          <span className="tbt-progress-pct">{t.status === 'running' ? `${t.progress ?? 0}%` : '排队中'}</span>
                        </span>
                      )}
                      {t.status === 'failed' && <span className="tbt-task-err">{t.error_message}</span>}
                    </button>
                    <div className="tbt-task-actions">
                      {LIVE_STATUS.has(t.status) && (
                        <button type="button" className="tbt-btn tbt-btn-mini" onClick={() => cancelTask(t.id)}>取消</button>
                      )}
                      {(t.status === 'failed' || t.status === 'cancelled') && (
                        <button type="button" className="tbt-btn tbt-btn-mini" onClick={() => restartTask(t.id)}>重跑</button>
                      )}
                      {TERMINAL_STATUS.has(t.status) && (
                        <button
                          type="button"
                          className={`tbt-btn tbt-btn-mini tbt-btn-del ${confirmDel === t.id ? 'is-confirm' : ''}`}
                          onClick={() => {
                            if (confirmDel === t.id) deleteTask(t.id);
                            else setConfirmDel(t.id);
                          }}
                        >
                          {confirmDel === t.id ? '确认删除？' : '删除'}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {/* ── AI 决策记录（ai_led 审计，独立 15s 轮询） ── */}
          <div className="tbt-rail-inner">
            <div className="tbt-panel-head tbt-panel-head-sub">
              <span>🤖 AI 决策记录</span>
              <span className="tbt-count">{aiActions.length}</span>
            </div>
            <ul className="tbt-ai-list">
              {aiActions.length === 0 && <li className="tbt-empty">暂无 AI 决策 — 做T Agent 唤醒后产生</li>}
              {aiActions.slice(0, 15).map((a) => {
                const out = a.output || {};
                const gw = a.gateway_result || {};
                const oc = a.outcome || {};
                const reason = out.reason || gw.reason || '';
                const gwOk = gw.status === 'success' ? '✅' : gw.status ? '⛔' : '';
                // outcome 摘要：✅+0.85% / ⛔-1.5%
                let ocSum = '';
                let ocClass = '';
                if (oc && oc.pct_change != null) {
                  const pct = Number(oc.pct_change);
                  ocSum = `${pct >= 0 ? '✅' : '⛔'}${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
                  ocClass = pct >= 0 ? 'is-up' : 'is-down';
                }
                return (
                  <li key={a.id} className="tbt-ai-action">
                    <span className="tbt-ai-top">
                      <b>{a.symbol}</b>
                      <span className="tbt-ai-type">{a.action_type}</span>
                      {ocSum && <span className={`tbt-ai-oc ${ocClass}`}>{ocSum}</span>}
                      <span className="tbt-ai-gw">{gwOk}</span>
                    </span>
                    <span className="tbt-ai-meta">{(a.created_at || '').slice(5, 16)}</span>
                    {reason && <span className="tbt-ai-reason">{String(reason).slice(0, 90)}</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        </aside>

        {/* ── 台账工作台：创建 / 实时 / 报告 ── */}
        <main className="tbt-desk">
          {error && <div className="tbt-alert tbt-alert-err" role="alert">{error}</div>}
          {msg && <div className="tbt-alert" role="status">{msg}</div>}

          {/* 创建表单（可折叠） */}
          <section className="tbt-panel">
            <div className="tbt-panel-head">
              <span>新建回测</span>
              <div className="tbt-panel-head-actions">
                <span className="tbt-hint">组合模式：建仓规则选股 → 各自做T → 组合收益</span>
                <button type="button" className="tbt-btn tbt-btn-ghost" aria-expanded={formOpen} onClick={() => setFormOpen((v) => !v)}>
                  {formOpen ? '收起' : '展开'}
                </button>
              </div>
            </div>
            {formOpen && (
              <div className="tbt-form">
                <div className="tbt-field tbt-field-wide">
                  <label>选股方式</label>
                  <div className="tbt-radio-row" role="group" aria-label="选股方式">
                    {([
                      ['manual', '手动输入'],
                      ['pool', '自动 · 做T候选池'],
                      ['scan', '自动 · 全市场扫描'],
                    ] as const).map(([val, label]) => (
                      <button
                        key={val}
                        type="button"
                        className={`tbt-radio ${selectSource === val ? 'is-on' : ''}`}
                        aria-pressed={selectSource === val}
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
                            addSymbol(symbolInput);
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
                    {dupHint && <span className="tbt-dup-hint" role="alert">{dupHint}</span>}
                    {candidates.length > 0 && (
                      <div className="tbt-cand">
                        <span className="tbt-cand-title">候选池（可T质量分）</span>
                        {candidates.map((c) => (
                          <button
                            key={c.symbol}
                            type="button"
                            className={`tbt-cand-item ${c.pass_gate ? 'is-pass' : ''}`}
                            onClick={() => addSymbol(c.symbol)}
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

                {buildMode && (
                  <label className="tbt-switch">
                    <input type="checkbox" checked={rollingBuild} onChange={(e) => setRollingBuild(e.target.checked)} />
                    <span className="tbt-switch-track" />
                    <span className="tbt-switch-label">每日滚动建仓（对齐实盘：每天盘后扫描 → 次日建仓新标的，持续进票做T）</span>
                  </label>
                )}

                <button className="tbt-btn tbt-btn-primary tbt-submit" onClick={submit} disabled={loading}>
                  {loading ? '创建中…' : '开始回测'}
                </button>
              </div>
            )}
          </section>

          {/* 报告 */}
          {selectedId != null && report && (
            <section className="tbt-panel">
              <div className="tbt-panel-head">
                <span>回测报告 #{selectedId}</span>
                {metrics.total_return_pct != null && (
                  <span className={`tbt-return ${Number(metrics.total_return_pct) >= 0 ? 'is-up' : 'is-down'}`}>
                    {fmtSignedPct(metrics.total_return_pct)}
                  </span>
                )}
              </div>

              {/* 头牌指标：组合收益 / 卖出胜率 / 最大回撤 */}
              <div className="tbt-metrics-lead">
                <div className="tbt-lead">
                  <span className="tbt-lead-label">组合收益</span>
                  <b className={Number(metrics.total_return_pct ?? 0) >= 0 ? 'is-up' : 'is-down'}>{fmtSignedPct(metrics.total_return_pct)}</b>
                  <span className="tbt-lead-sub">{metrics.built_count ?? metrics.symbols ?? '—'} 标的 · 触发 {metrics.trigger_count ?? 0} 次</span>
                </div>
                <div className="tbt-lead">
                  <span className="tbt-lead-label">卖出胜率</span>
                  <b>{metrics.total_sell_trades ? fmtPct(metrics.win_rate_pct) : '—'}</b>
                  <span className="tbt-lead-sub">{metrics.total_sell_trades ? `(${metrics.winning_trades ?? metrics.total_sell_trades}/${metrics.total_sell_trades} 笔)` : '无卖出样本'}</span>
                </div>
                <div className="tbt-lead">
                  <span className="tbt-lead-label">最大回撤</span>
                  <b className={Number(metrics.max_drawdown_pct ?? 0) < 0 ? 'is-down' : 'is-up'}>{fmtPct(metrics.max_drawdown_pct)}</b>
                  <span className="tbt-lead-sub">已实现 {fmtMoney(metrics.realized_pnl)}</span>
                </div>
              </div>

              {/* 次级指标 */}
              <div className="tbt-metrics-grid">
                <div className="tbt-metric"><span className="tbt-metric-label">建仓标的</span><b>{metrics.built_count ?? metrics.symbols ?? '—'}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">触发次数</span><b>{metrics.trigger_count ?? 0}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">成交次数</span><b>{metrics.executed_count ?? 0}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">止损次数</span><b>{metrics.stop_loss_count ?? 0}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">AI执行胜率</span><b>{metrics.ai_exec_count != null ? `${fmtPct(metrics.ai_exec_win_rate_pct)} (${metrics.ai_exec_count}笔)` : '—'}</b></div>
                <div className="tbt-metric"><span className="tbt-metric-label">AI等待/放弃</span><b>{metrics.ai_wait_count ?? 0}/{metrics.ai_abandon_count ?? 0}</b></div>
              </div>

              {equity && equity.length > 0 && (
                <div className="tbt-chart" aria-label="组合权益曲线">
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={equity} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--tb-line-soft)" />
                      <XAxis dataKey="trade_date" tick={{ fontSize: 11, fill: 'var(--tb-faint)' }} />
                      <YAxis tick={{ fontSize: 11, fill: 'var(--tb-faint)' }} domain={['auto', 'auto']} width={70} />
                      <Tooltip
                        formatter={(v: any) => [`¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, '组合资产']}
                        contentStyle={{ border: '1px solid var(--tb-line)', borderRadius: 5, fontSize: 12 }}
                      />
                      <Line type="monotone" dataKey="total_asset" stroke="var(--tb-blue)" strokeWidth={2} dot={false} />
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

              {liveEvents.length > 0 && (
                <EventDetailTable events={liveEvents} title="事件明细（触发/决策/成交/拦截全记录）" />
              )}

              {report.caliber_notes && report.caliber_notes.length > 0 && (
                <details className="tbt-notes">
                  <summary>口径差异声明（{report.caliber_notes.length} 条）</summary>
                  <ul>{report.caliber_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                </details>
              )}
            </section>
          )}

          {/* 实时进展 / 报告未就绪 */}
          {selectedId != null && !report && (
            <section className="tbt-panel">
              {sel && LIVE_STATUS.has(sel.status) ? (
                <div className="tbt-live">
                  <div className="tbt-panel-head">
                    <span>实时进展 #{selectedId}</span>
                    {sel.status === 'pending' ? (
                      <span className="tbt-status is-pending">排队中</span>
                    ) : (
                      <span className="tbt-status is-running">回测中 {liveProgress ?? sel.progress ?? 0}%</span>
                    )}
                  </div>
                  {sel.status === 'pending' ? (
                    <div className="tbt-live-queue"><span className="tbt-pulse-dot" />任务已入队，等待 worker 领取执行…</div>
                  ) : (
                    <div className="tbt-progress tbt-progress-lg" aria-live="polite">
                      <span className="tbt-progress-bar">
                        <span
                          className="tbt-progress-fill"
                          style={{ ['--_pct' as any]: `${Math.max(2, Math.min(100, liveProgress ?? sel.progress ?? 0)) / 100}` }}
                        />
                      </span>
                      <span className="tbt-progress-pct">{liveProgress ?? sel.progress ?? 0}%</span>
                    </div>
                  )}
                  {liveEvents.length > 0 && (
                    <EventDetailTable events={liveEvents} title="实时事件流" />
                  )}
                </div>
              ) : (
                <div className="tbt-empty-state">
                  <span>任务 #{selectedId} · {STATUS_LABEL[sel?.status || ''] || '状态未知'}</span>
                  {sel?.status === 'completed' ? (
                    <>
                      <span>报告尚未生成或加载失败</span>
                      <button type="button" className="tbt-btn tbt-btn-primary" onClick={retryReport}>重试加载报告</button>
                    </>
                  ) : (
                    <>
                      <span>{sel?.status === 'failed' ? (sel.error_message || '该任务执行失败') : '该任务暂无报告'}</span>
                      {(sel?.status === 'failed' || sel?.status === 'cancelled') && (
                        <div className="tbt-empty-actions">
                          <button type="button" className="tbt-btn" onClick={() => restartTask(sel.id)}>重跑任务</button>
                          <button type="button" className="tbt-btn tbt-btn-del" onClick={() => deleteTask(sel.id)}>删除任务</button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </section>
          )}
        </main>
      </div>
    </div>
  );
}