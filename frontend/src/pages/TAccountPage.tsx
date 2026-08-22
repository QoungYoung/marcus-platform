import { useCallback, useEffect, useState, useRef } from 'react';
import gsap from 'gsap';
import '../styles/t-account-page.css';

const API = '/api/v1/t';

// ── 类型 ──
interface Capital {
  account_id: string;
  initial_capital: number;
  available_cash: number;
  frozen_cash: number;
}

interface SellableItem {
  symbol: string;
  volume: number;
  today_buy: number;
  sellable: number;
  avg_price: number;
}

interface Regime {
  regime: string;
  regime_day?: string;
  intraday_warn?: boolean;
  hard_fuse?: boolean;
  index_drop?: number;
  gate_low_buy?: string;
  gate_high_sell?: string;
  interpret_sign?: number;
}

interface PoolEntry {
  symbol: string;
  score?: number;
  spread?: number;
  oc?: number;
  round_trip?: number;
  tier?: string;
  reason?: string[];
  volume?: number;
  avg_price?: number;
}

interface PoolResp {
  regime: string;
  pool: { candidate: Record<string, PoolEntry>; live: Record<string, PoolEntry>; watch: Record<string, PoolEntry> };
}

interface Condition {
  id: number;
  symbol: string;
  trigger_kind: string;
  target_price: number;
  reinform_price: number;
  sell_target_price: number;
  stop_loss_price: number;
  armed: number;
  status: string;
  regime_gate: string;
  trigger_count_today: number;
}

interface Trigger {
  id: number;
  symbol: string;
  event_type: string;
  trigger_price: number;
  quote_price: number;
  suggest_bid_price: number;
  suggest_ask_price: number;
  status: string;
  mode: string;
  reason?: string;
  created_at?: string;
  condition_id?: number;
}

interface AuditResp {
  triggers: Trigger[];
  daily_state: Record<string, any> | null;
  risk_state: Record<string, any> | null;
}

// ── 底仓建仓（t-position-building）──
interface BuildOverview {
  account_id: string;
  regime: string;
  tier: string;
  net_asset: number;
  total_floor_value: number;
  total_floor_cap: number;
  per_symbol_cap: number;
  single_order_cap: number;
  max_floor_symbols: number;
  service?: { running?: boolean; last_result?: string };
}

interface BuildCandidate {
  symbol: string;
  score: number;
  pass_gate: boolean;
  source?: string;
  quality?: { score?: number };
  trend?: { note?: string };
  reasons?: string[];
}

interface BuildEvent {
  id: number;
  symbol: string;
  event_type: string;
  price: number;
  volume: number;
  amount: number;
  executed_price?: number;
  decision_source: string;
  reason?: string;
  regime?: string;
  status: string;
  created_at?: string;
}

function fmtMoney(v?: number) {
  if (v == null) return '-';
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

function fmtTime(ts?: string) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', { hour12: false });
}

function regimeLabel(r?: string) {
  return r === 'HALT' ? '🔴 关停' : r === 'CAUTIOUS' ? '🟡 谨慎' : '🟢 活跃';
}

const TRIGGER_KIND_LABEL: Record<string, string> = {
  low_buy: '低吸', high_sell_then_buy_back: '高抛接回',
  panic_vibrate: '恐慌震荡', high_only: '只高抛',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理', claimed: '已认领', auto_ready: '待执行',
  human_confirm: '待人工', executed: '已执行', blocked: '已拦截', cancelled: '已取消',
  expired: '已过期',
};

interface VrebStatus {
  enabled?: boolean;
  running?: boolean;
  account?: string;
  last_scan?: string;
}

interface VrebCandidate {
  symbol: string;
  score?: number;
  reasons?: any;
  trend?: string;
  status?: string;
  created_at?: string;
}

interface VrebEvent {
  id: number;
  symbol: string;
  event_type: string;
  side?: string;
  price?: number;
  volume?: number;
  amount?: number;
  executed_price?: number;
  status?: string;
  reason?: string;
  created_at?: string;
}

export default function TAccountPage() {
  const pageRef = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState<'dashboard' | 'trade' | 'signal'>('dashboard');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');

  const [capital, setCapital] = useState<Capital | null>(null);
  const [ledger, setLedger] = useState<Record<string, SellableItem>>({});
  const [regime, setRegime] = useState<Regime | null>(null);
  const [breaker, setBreaker] = useState<{ triggered: boolean; reason: string } | null>(null);

  const [pool, setPool] = useState<PoolResp | null>(null);
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [audit, setAudit] = useState<AuditResp | null>(null);

  // 底仓建仓状态
  const [buildOverview, setBuildOverview] = useState<BuildOverview | null>(null);
  const [candidates, setCandidates] = useState<BuildCandidate[]>([]);
  const [buildEvents, setBuildEvents] = useState<BuildEvent[]>([]);
  const [buildForm, setBuildForm] = useState({ symbol: '', price: '', volume: '', reason: '' });
  const [buildSource, setBuildSource] = useState<'pool' | 'scan'>('pool');

  // V反 短线
  const [vrebStatus, setVrebStatus] = useState<VrebStatus | null>(null);
  const [vrebCands, setVrebCands] = useState<VrebCandidate[]>([]);
  const [vrebEvents, setVrebEvents] = useState<VrebEvent[]>([]);

  // 科技ETF V反 / 动量趋势
  const [veStatus, setVeStatus] = useState<VrebStatus | null>(null);
  const [veCands, setVeCands] = useState<VrebCandidate[]>([]);
  const [meStatus, setMeStatus] = useState<VrebStatus | null>(null);
  const [meCands, setMeCands] = useState<VrebCandidate[]>([]);

  const fetchJson = async (url: string, opts?: RequestInit) => {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  };

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchJson(`${API}/overview`);
      setCapital(data.capital);
      setLedger(data.sellable_ledger || {});
      setRegime(data.regime);
      setBreaker(data.breaker);
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPool = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPool(await fetchJson(`${API}/pool`));
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadConditions = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchJson(`${API}/conditions`);
      setConditions(data.conditions || []);
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTriggers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchJson(`${API}/triggers?limit=50`);
      setTriggers(data.triggers || []);
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAudit = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setAudit(await fetchJson(`${API}/audit`));
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadBuild = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [ov, evs] = await Promise.all([
        fetchJson(`${API}/build/overview`),
        fetchJson(`${API}/build/events?limit=30`),
      ]);
      setBuildOverview(ov);
      setBuildEvents(evs.events || []);
    } catch (e: any) {
      setError(`加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchJson(`${API}/build/candidates?source=${buildSource}&limit=20`);
      setCandidates(data.candidates || []);
    } catch (e: any) {
      setError(`候选扫描失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [buildSource]);

  const loadVreb = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [st, cd, ev] = await Promise.all([
        fetchJson(API + '/vrebounce/status'),
        fetchJson(API + '/vrebounce/candidates?limit=20'),
        fetchJson(API + '/vrebounce/events?limit=20'),
      ]);
      setVrebStatus(st);
      setVrebCands(cd.candidates || []);
      setVrebEvents(ev.events || []);
    } catch (e: any) {
      setError('V反加载失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadVrebEtf = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [st, cd] = await Promise.all([
        fetchJson(API + '/vreb-etf/status'),
        fetchJson(API + '/vreb-etf/candidates?limit=20'),
      ]);
      setVeStatus(st); setVeCands(cd.candidates || []);
    } catch (e: any) { setError('科技ETF V反加载失败: ' + e.message); }
    finally { setLoading(false); }
  }, []);

  const loadMomEtf = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [st, cd] = await Promise.all([
        fetchJson(API + '/mom-etf/status'),
        fetchJson(API + '/mom-etf/candidates?limit=20'),
      ]);
      setMeStatus(st); setMeCands(cd.candidates || []);
    } catch (e: any) { setError('动量趋势加载失败: ' + e.message); }
    finally { setLoading(false); }
  }, []);

  // 常驻加载账户总览 → 供左侧身份/风控 rail 在任何 tab 都可用
  useEffect(() => { loadOverview(); }, [loadOverview]);

  useEffect(() => {
    if (tab === 'dashboard') { loadAudit(); }
    if (tab === 'trade') { loadPool(); loadConditions(); loadTriggers(); }
    if (tab === 'signal') { loadBuild(); loadVreb(); loadVrebEtf(); loadMomEtf(); }
  }, [tab, loadPool, loadConditions, loadTriggers, loadAudit, loadBuild, loadVreb, loadVrebEtf, loadMomEtf]);

  // 角色档案式入场动画（GSAP）：Header 下拉 + rail/canvas 内容 stagger 浮现（切 tab 重放）
  useEffect(() => {
    if (!pageRef.current) return;
    const ctx = gsap.context(() => {
      gsap.fromTo('.tac-header', { opacity: 0, y: -12 },
        { opacity: 1, y: 0, duration: 0.45, ease: 'power2.out' });
      gsap.fromTo(
        '.tac-rail-block, .tac-panel, .tac-kpi',
        { opacity: 0, y: 16 },
        { opacity: 1, y: 0, stagger: 0.05, duration: 0.4, ease: 'power2.out', clearProps: 'transform,opacity' });
    }, pageRef);
    return () => ctx.revert();
  }, [tab]);

  // 操作
  const doPost = async (path: string, body?: any, okMsg?: string) => {
    setMsg('');
    try {
      await fetchJson(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (okMsg) setMsg(okMsg);
      // 刷新当前 tab
      if (tab === 'dashboard') { loadAudit(); }
      if (tab === 'trade') { loadPool(); loadConditions(); loadTriggers(); }
      if (tab === 'signal') { loadBuild(); loadVreb(); loadVrebEtf(); loadMomEtf(); }
      loadOverview();
    } catch (e: any) {
      setError('操作失败: ' + e.message);
    }
  };

  const confirmTrigger = async (id: number, action: 'execute' | 'cancel') => {
    await doPost(`/triggers/${id}/confirm?action=${action}`, null,
      action === 'execute' ? `已放行 #${id}` : `已取消 #${id}`);
  };

  // ── 底仓建仓操作 ──
  const submitBuild = async () => {
    if (!buildForm.symbol || !buildForm.price) { setError('请填写代码与价格'); return; }
    setMsg('');
    try {
      const body: any = {
        symbol: buildForm.symbol,
        price: Number(buildForm.price),
        reason: buildForm.reason || '前端建仓',
        decision_source: 'agent',
      };
      if (buildForm.volume) body.volume = Number(buildForm.volume);
      const data = await fetchJson(`${API}/build/position`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (data.status === 'human_confirm') {
        setMsg(`👤 建仓已升级人工确认（事件 #${data.event_id}）: ${data.reason || ''}`);
      } else if (data.status === 'success') {
        setMsg(`✅ 建仓成交 #${data.event_id}（次日自动生成做T条件）`);
      } else {
        setError(`建仓被拒: ${data.reason || '未知原因'}`);
      }
      loadBuild();
      loadOverview();
    } catch (e: any) {
      setError(`建仓失败: ${e.message}`);
    }
  };

  const confirmBuildEvent = async (id: number, action: 'execute' | 'cancel') => {
    try {
      const data = await fetchJson(`${API}/build/events/${id}/confirm?action=${action}`, { method: 'POST' });
      if (data.status === 'success') setMsg(`✅ 建仓放行成交 #${id}`);
      else if (data.status === 'rejected') setError(`建仓执行被拒: ${data.reason || ''}`);
      else setMsg(`已处理建仓事件 #${id}`);
      loadBuild();
    } catch (e: any) {
      setError(`操作失败: ${e.message}`);
    }
  };

  const TABS: { key: typeof tab; label: string }[] = [
    { key: 'dashboard', label: '总览看板' },
    { key: 'trade', label: '做T交易' },
    { key: 'signal', label: '信号与建仓' },
  ];

  const daily = audit?.daily_state || {};
  const risk = audit?.risk_state || {};

  return (
    <div className="tac-page" ref={pageRef}>
      <header className="tac-header">
        <div className="tac-header-title">
          <h2>做T账户 · T+0 回转</h2>
          <span className="tac-sign">Marcus · T-Account</span>
        </div>
        <div className="tac-header-right">
          {regime && <span className={`tac-regime tac-regime-${regime.regime?.toLowerCase()}`}>{regimeLabel(regime.regime)}</span>}
          {breaker?.triggered && <span className="tac-breaker">⚠️ {breaker.reason}</span>}
          <div className="tac-tabs">
            {TABS.map((t) => (
              <button key={t.key} className={`tac-tab ${tab === t.key ? 'active' : ''}`}
                onClick={() => setTab(t.key)}>{t.label}</button>
            ))}
          </div>
        </div>
      </header>

      {error && <div className="tac-error">{error}</div>}
      {msg && <div className="tac-msg">✅ {msg}</div>}
      {loading && <div className="tac-loading">加载中...</div>}

      <div className="tac-layout">
        {/* ── 左侧身份 / 风控 rail ── */}
        <aside className="tac-rail">
          <div className="tac-rail-block tac-rail-id">
            <span className="tac-id-eyebrow">短线 · T+0 回转</span>
            <h3 className="tac-id-title">做T账户</h3>
            <span className="tac-id-sub">Marcus strategy workbench</span>
            <div className="tac-id-flags">
              {regime && <span className={`tac-regime tac-regime-${regime.regime?.toLowerCase()}`}>{regimeLabel(regime.regime)}</span>}
              {breaker?.triggered && <span className="tac-risk-flag tac-risk-err">⚠️ {breaker.reason}</span>}
            </div>
          </div>

          <div className="tac-rail-block">
            <div className="tac-rail-label">资金盘面</div>
            <div className="tac-rail-rows">
              <div className="tac-rail-row"><span>可用资金</span><b>{fmtMoney(capital?.available_cash)}</b></div>
              <div className="tac-rail-row"><span>初始资金</span><b>{fmtMoney(capital?.initial_capital)}</b></div>
              <div className="tac-rail-row"><span>冻结资金</span><b>{fmtMoney(capital?.frozen_cash)}</b></div>
              <div className="tac-rail-row"><span>持仓数</span><b>{Object.keys(ledger).length}</b></div>
            </div>
          </div>

          <div className="tac-rail-block">
            <div className="tac-rail-label">风险姿态</div>
            <div className="tac-rail-chips">
              {risk.stop_all
                ? <span className="tac-risk-flag tac-risk-err">🔴 STOP_ALL 熔断</span>
                : <span className="tac-risk-flag tac-risk-ok">🟢 风控正常</span>}
              <span className="tac-risk-flag tac-risk-soft">
                {daily.risk_breaker ? '🔴 日亏熔断' : '⚪ 未触发'}
              </span>
            </div>
          </div>
        </aside>

        {/* ── 右侧 12 列内容画布 ── */}
        <main className="tac-canvas">
          <div className="tac-panels">
            {/* ── 账户总览 ── */}
            {tab === 'dashboard' && (
              <>
                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">今日回转绩效</span>
                    <span className="tac-panel-hint">核算口径：当日回购成本 + 已实现盈亏</span>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-kpi-grid">
                      <div className="tac-kpi"><span>当日回转额</span><b>{fmtMoney(daily.daily_turnover_amount)}</b></div>
                      <div className="tac-kpi"><span>已实现盈亏</span><b>{fmtMoney(daily.realized_pnl)}</b></div>
                      <div className="tac-kpi"><span>买卖次数</span><b>{daily.buy_count ?? 0} 买 / {daily.sell_count ?? 0} 卖</b></div>
                      <div className="tac-kpi"><span>账户状态</span><b className={`tac-kpi-state ${risk.stop_all ? 'tac-kpi-state-err' : 'tac-kpi-state-ok'}`}>{risk.stop_all ? '🔴 STOP_ALL' : '🟢 正常'}</b></div>
                    </div>
                  </div>
                </section>

                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">当日可卖额度账本（T+1）</span>
                    <span className="tac-panel-hint">持仓数 {Object.keys(ledger).length}</span>
                  </div>
                  <div className="tac-panel-body">
                    <table className="tac-table">
                      <thead><tr><th>代码</th><th>持仓</th><th>今日买入(锁定)</th><th>可卖</th><th>成本</th></tr></thead>
                      <tbody>
                        {Object.values(ledger).map((it) => (
                          <tr key={it.symbol}>
                            <td className="tac-sym">{it.symbol}</td>
                            <td>{it.volume}</td>
                            <td>{it.today_buy}</td>
                            <td><b className="tac-sellable">{it.sellable}</b></td>
                            <td>{it.avg_price?.toFixed(2)}</td>
                          </tr>
                        ))}
                        {Object.keys(ledger).length === 0 && <tr><td colSpan={5} className="tac-empty">暂无持仓</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">事件审计链（条件单 → 状态流转）</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={loadAudit}>刷新</button>
                      <button className="tac-btn tac-btn-danger" onClick={() => doPost('/stop-all', { flag: true, reason: 'manual' }, 'STOP_ALL 已开启')}>开启 STOP_ALL</button>
                      <button className="tac-btn" onClick={() => doPost('/stop-all', { flag: false }, 'STOP_ALL 已关闭')}>关闭 STOP_ALL</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <table className="tac-table">
                      <thead><tr>
                        <th>#</th><th>代码</th><th>类型</th><th>状态</th><th>模式</th><th>条件#</th><th>原因</th><th>创建时间</th>
                      </tr></thead>
                      <tbody>
                        {(audit?.triggers || []).map((t) => (
                          <tr key={t.id}>
                            <td>{t.id}</td>
                            <td className="tac-sym">{t.symbol}</td>
                            <td>{TRIGGER_KIND_LABEL[t.event_type] || t.event_type}</td>
                            <td><span className={`tac-status tac-status-${t.status}`}>{STATUS_LABEL[t.status] || t.status}</span></td>
                            <td>{t.mode === 'auto' ? '自动' : '人工'}</td>
                            <td>{t.condition_id ?? '-'}</td>
                            <td className="tac-muted">{t.reason || ''}</td>
                            <td className="tac-muted">{fmtTime(t.created_at)}</td>
                          </tr>
                        ))}
                        {(audit?.triggers || []).length === 0 && <tr><td colSpan={8} className="tac-empty">暂无审计记录</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}

            {/* ── 做T交易 ── */}
            {tab === 'trade' && (
              <>
                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">三层股票池</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={() => doPost('/conditions/generate', null, '已为实盘池生成条件')}>生成监控条件</button>
                      <button className="tac-btn" onClick={loadPool}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-tier-grid">
                      {(['candidate', 'live', 'watch'] as const).map((tier) => (
                        <div className={`tac-tier tac-tier-${tier}`} key={tier}>
                          <div className="tac-tier-head">
                            {tier === 'candidate' ? '底仓候选池（仅建仓）' : tier === 'live' ? '做T实盘池（唯一可触发）' : '观察池（缓冲）'}
                            <span className="tac-tier-count">{Object.keys(pool?.pool?.[tier] || {}).length}</span>
                          </div>
                          <table className="tac-table tac-table-tight">
                            <thead><tr><th>代码</th><th>得分</th><th>价差</th><th>O-C</th><th>往返</th></tr></thead>
                            <tbody>
                              {Object.values(pool?.pool?.[tier] || {}).map((e) => (
                                <tr key={e.symbol}>
                                  <td className="tac-sym">{e.symbol}</td>
                                  <td>{e.score?.toFixed(2) ?? '-'}</td>
                                  <td>{e.spread?.toFixed(2) ?? '-'}</td>
                                  <td>{e.oc?.toFixed(2) ?? '-'}</td>
                                  <td>{e.round_trip ?? '-'}</td>
                                </tr>
                              ))}
                              {Object.keys(pool?.pool?.[tier] || {}).length === 0 && (
                                <tr><td colSpan={5} className="tac-empty">暂无标的</td></tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                      ))}
                    </div>
                  </div>
                </section>

                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">监控条件</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={() => doPost('/conditions/generate', null, '已生成条件')}>生成监控条件</button>
                      <button className="tac-btn" onClick={loadConditions}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <table className="tac-table">
                      <thead><tr>
                        <th>代码</th><th>类型</th><th>触发价</th><th>复归价</th><th>高抛目标</th><th>止损</th>
                        <th>armed</th><th>regime门</th><th>今日触发</th><th>操作</th>
                      </tr></thead>
                      <tbody>
                        {conditions.map((c) => (
                          <tr key={c.id}>
                            <td className="tac-sym">{c.symbol}</td>
                            <td>{TRIGGER_KIND_LABEL[c.trigger_kind] || c.trigger_kind}</td>
                            <td>{c.target_price ?? '-'}</td>
                            <td>{c.reinform_price ?? '-'}</td>
                            <td>{c.sell_target_price ?? '-'}</td>
                            <td>{c.stop_loss_price ?? '-'}</td>
                            <td>{c.armed === 1 ? '✅' : '⛔'}</td>
                            <td>{c.regime_gate}</td>
                            <td>{c.trigger_count_today}</td>
                            <td>
                              {c.armed !== 1 && (
                                <button className="tac-btn tac-btn-sm" onClick={() => doPost(`/conditions/${c.id}/rearm`, null, '已重新武装')}>重新武装</button>
                              )}
                            </td>
                          </tr>
                        ))}
                        {conditions.length === 0 && <tr><td colSpan={10} className="tac-empty">暂无条件，点击"生成监控条件"</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">触发事件</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={loadTriggers}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <table className="tac-table">
                      <thead><tr>
                        <th>#</th><th>代码</th><th>类型</th><th>触发价</th><th>建议买</th><th>建议卖</th>
                        <th>状态</th><th>模式</th><th>原因</th><th>时间</th><th>操作</th>
                      </tr></thead>
                      <tbody>
                        {triggers.map((t) => (
                          <tr key={t.id}>
                            <td>{t.id}</td>
                            <td className="tac-sym">{t.symbol}</td>
                            <td>{TRIGGER_KIND_LABEL[t.event_type] || t.event_type}</td>
                            <td>{t.trigger_price ?? '-'}</td>
                            <td>{t.suggest_bid_price?.toFixed(2) ?? '-'}</td>
                            <td>{t.suggest_ask_price?.toFixed(2) ?? '-'}</td>
                            <td><span className={`tac-status tac-status-${t.status}`}>{STATUS_LABEL[t.status] || t.status}</span></td>
                            <td>{t.mode === 'auto' ? '自动' : '人工'}</td>
                            <td className="tac-muted">{t.reason || ''}</td>
                            <td className="tac-muted">{fmtTime(t.created_at)}</td>
                            <td>
                              {(t.status === 'human_confirm') && (
                                <>
                                  <button className="tac-btn tac-btn-sm" onClick={() => confirmTrigger(t.id, 'execute')}>执行</button>
                                  <button className="tac-btn tac-btn-sm tac-btn-danger" onClick={() => confirmTrigger(t.id, 'cancel')}>取消</button>
                                </>
                              )}
                            </td>
                          </tr>
                        ))}
                        {triggers.length === 0 && <tr><td colSpan={11} className="tac-empty">暂无触发事件</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}

            {/* ── 信号与建仓 ── */}
            {tab === 'signal' && (
              <>
                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">底仓建仓（t-position-building）</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={loadBuild}>刷新</button>
                      <button className="tac-btn" onClick={() => doPost('/build/auto-gen', null, '已补生成次日条件')}>补生成次日条件</button>
                      <button className="tac-btn" onClick={() => doPost('/build/rebalance', null, '再平衡评估完成')}>底仓再平衡</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-cards">
                      <div className="tac-card"><div className="tac-card-label">t 净值</div><div className="tac-card-value">{fmtMoney(buildOverview?.net_asset)}</div></div>
                      <div className="tac-card"><div className="tac-card-label">底仓市值</div><div className="tac-card-value">{fmtMoney(buildOverview?.total_floor_value)}</div></div>
                      <div className="tac-card"><div className="tac-card-label">总底仓上限</div><div className="tac-card-value">{fmtMoney(buildOverview?.total_floor_cap)}</div></div>
                      <div className="tac-card"><div className="tac-card-label">单笔/单标上限</div><div className="tac-card-value tac-card-sm-value">{fmtMoney(buildOverview?.single_order_cap)} / {fmtMoney(buildOverview?.per_symbol_cap)}</div></div>
                    </div>
                    <div className="tac-hint">
                      regime: {buildOverview?.regime ?? '-'}（档位 {buildOverview?.tier ?? '-'}）| 组合标的上限: {buildOverview?.max_floor_symbols ?? '-'}
                      {buildOverview?.service?.running ? ' | 建仓服务 🟢 运行中' : ' | 建仓服务 ⚪'}
                      {buildOverview?.service?.last_result ? ` | ${buildOverview.service.last_result}` : ''}
                    </div>

                    <div className="tac-subtitle">发起底仓建仓</div>
                    <div className="tac-form-row">
                      <input className="tac-input" placeholder="代码，如 SH600519" value={buildForm.symbol}
                        onChange={(e) => setBuildForm({ ...buildForm, symbol: e.target.value })} />
                      <input className="tac-input" placeholder="委托价" value={buildForm.price}
                        onChange={(e) => setBuildForm({ ...buildForm, price: e.target.value })} />
                      <input className="tac-input" placeholder="股数（留空=按上限自动）" value={buildForm.volume}
                        onChange={(e) => setBuildForm({ ...buildForm, volume: e.target.value })} />
                      <input className="tac-input tac-input-wide" placeholder="建仓理由（≥10字，说明选股依据）" value={buildForm.reason}
                        onChange={(e) => setBuildForm({ ...buildForm, reason: e.target.value })} />
                      <button className="tac-btn" onClick={submitBuild}>建仓</button>
                    </div>
                    <div className="tac-hint">规则：首开新标的升级人工确认；单笔≤净值5%；冷静期 9:45 前/午后 13:00 后不自动建；建仓当日不可卖（T+1），次日自动生成做T条件。</div>

                    <div className="tac-subtitle">建仓候选短名单（可T质量 + 趋势闸门 + 风险惩罚）</div>
                    <div className="tac-toolbar">
                      <select className="tac-input" value={buildSource} onChange={(e) => setBuildSource(e.target.value as any)}>
                        <option value="pool">既有候选池</option>
                        <option value="scan">全市场粗筛</option>
                      </select>
                      <button className="tac-btn" onClick={loadCandidates}>扫描候选</button>
                    </div>
                    <table className="tac-table">
                      <thead><tr><th>代码</th><th>build_score</th><th>可T质量</th><th>趋势</th><th>说明</th></tr></thead>
                      <tbody>
                        {candidates.map((c) => (
                          <tr key={c.symbol}>
                            <td className="tac-sym">{c.symbol}</td>
                            <td>{c.score?.toFixed(2) ?? '-'} {c.pass_gate ? '✅' : '⛔'}</td>
                            <td>{c.quality?.score?.toFixed(2) ?? '-'}</td>
                            <td className="tac-muted">{c.trend?.note ?? '-'}</td>
                            <td className="tac-muted">{(c.reasons || []).join('；')}</td>
                          </tr>
                        ))}
                        {candidates.length === 0 && <tr><td colSpan={5} className="tac-empty">暂无候选（点击「扫描候选」）</td></tr>}
                      </tbody>
                    </table>

                    <div className="tac-subtitle">建仓审计（独立于做T触发事件流）</div>
                    <table className="tac-table">
                      <thead><tr>
                        <th>#</th><th>代码</th><th>类型</th><th>价格</th><th>数量</th><th>金额</th>
                        <th>决策</th><th>状态</th><th>原因</th><th>操作</th>
                      </tr></thead>
                      <tbody>
                        {buildEvents.map((ev) => (
                          <tr key={ev.id}>
                            <td>{ev.id}</td>
                            <td className="tac-sym">{ev.symbol}</td>
                            <td>{ev.event_type === 'capital_adjust' ? '调额' : ev.event_type === 'build_position' ? '建仓' : ev.event_type}</td>
                            <td>{ev.price ?? '-'}</td>
                            <td>{ev.volume ?? '-'}</td>
                            <td>{fmtMoney(ev.amount)}</td>
                            <td>{ev.decision_source === 'human' ? '人工' : 'Agent'}</td>
                            <td><span className={`tac-status tac-status-${ev.status}`}>{STATUS_LABEL[ev.status] || ev.status}</span></td>
                            <td className="tac-muted">{ev.reason || ''}</td>
                            <td>
                              {ev.status === 'human_confirm' && (
                                <>
                                  <button className="tac-btn tac-btn-sm" onClick={() => confirmBuildEvent(ev.id, 'execute')}>放行</button>
                                  <button className="tac-btn tac-btn-sm tac-btn-danger" onClick={() => confirmBuildEvent(ev.id, 'cancel')}>取消</button>
                                </>
                              )}
                            </td>
                          </tr>
                        ))}
                        {buildEvents.length === 0 && <tr><td colSpan={10} className="tac-empty">暂无建仓记录</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="tac-panel tac-panel-half">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">股票 V反</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={() => doPost('/vrebounce/scan', null, '已触发全市场扫描')}>扫描</button>
                      <button className="tac-btn" onClick={() => doPost('/vrebounce/build', null, '已触发实时复核+建仓')}>建仓</button>
                      <button className="tac-btn" onClick={() => doPost('/vrebounce/exit-check', null, '已触发出场检查')}>出场</button>
                      <button className="tac-btn" onClick={loadVreb}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-cards tac-cards-sm">
                      <div className="tac-card"><div className="tac-card-label">监控</div><div className="tac-card-value">{vrebStatus?.enabled ? '🟢 启用' : '⚪ 关闭'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">运行</div><div className="tac-card-value">{vrebStatus?.running ? '运行中' : '未运行'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">账户</div><div className="tac-card-value">{vrebStatus?.account ?? '-'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">上次</div><div className="tac-card-value tac-card-sm-value">{fmtTime(vrebStatus?.last_scan)}</div></div>
                    </div>
                    <div className="tac-subtitle">当日候选（t 账户 · 盘后全市场扫描）</div>
                    <table className="tac-table tac-table-tight">
                      <thead><tr><th>代码</th><th>得分</th><th>状态</th><th>条件</th></tr></thead>
                      <tbody>
                        {vrebCands.map((c) => (
                          <tr key={c.symbol + (c.created_at || '')}>
                            <td className="tac-sym">{c.symbol}</td>
                            <td>{c.score?.toFixed(2) ?? '-'}</td>
                            <td>{c.status ?? '-'}</td>
                            <td className="tac-muted">{c.trend ?? ''}</td>
                          </tr>
                        ))}
                        {vrebCands.length === 0 && <tr><td colSpan={4} className="tac-empty">暂无候选</td></tr>}
                      </tbody>
                    </table>
                    <div className="tac-subtitle">建仓/平仓事件</div>
                    <table className="tac-table tac-table-tight">
                      <thead><tr><th>代码</th><th>类型</th><th>价格</th><th>状态</th><th>时间</th></tr></thead>
                      <tbody>
                        {vrebEvents.map((ev) => (
                          <tr key={ev.id}>
                            <td className="tac-sym">{ev.symbol}</td>
                            <td>{ev.event_type === 'build_position' ? '建仓' : ev.side}</td>
                            <td>{ev.executed_price ?? ev.price ?? '-'}</td>
                            <td>{ev.status ?? '-'}</td>
                            <td className="tac-muted">{fmtTime(ev.created_at)}</td>
                          </tr>
                        ))}
                        {vrebEvents.length === 0 && <tr><td colSpan={5} className="tac-empty">暂无事件</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="tac-panel tac-panel-half">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">科技ETF V反</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={() => doPost('/vreb-etf/scan', null, '已触发科技ETF扫描')}>扫描</button>
                      <button className="tac-btn" onClick={() => doPost('/vreb-etf/build', null, '已触发复核+建仓')}>建仓</button>
                      <button className="tac-btn" onClick={() => doPost('/vreb-etf/exit-check', null, '已触发出场检查')}>出场</button>
                      <button className="tac-btn" onClick={loadVrebEtf}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-cards tac-cards-sm">
                      <div className="tac-card"><div className="tac-card-label">监控</div><div className="tac-card-value">{veStatus?.enabled ? '🟢 启用' : '⚪ 关闭'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">运行</div><div className="tac-card-value">{veStatus?.running ? '运行中' : '未运行'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">账户</div><div className="tac-card-value">{veStatus?.account ?? '-'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">上次</div><div className="tac-card-value tac-card-sm-value">{fmtTime(veStatus?.last_scan)}</div></div>
                    </div>
                    <div className="tac-subtitle">当日候选（tech7 池 · 暴跌反弹）</div>
                    <table className="tac-table tac-table-tight">
                      <thead><tr><th>代码</th><th>得分</th><th>状态</th><th>条件</th></tr></thead>
                      <tbody>
                        {veCands.map((c) => (
                          <tr key={c.symbol + (c.created_at || '')}>
                            <td className="tac-sym">{c.symbol}</td>
                            <td>{c.score?.toFixed(2) ?? '-'}</td>
                            <td>{c.status ?? '-'}</td>
                            <td className="tac-muted">{c.trend ?? ''}</td>
                          </tr>
                        ))}
                        {veCands.length === 0 && <tr><td colSpan={4} className="tac-empty">暂无候选</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="tac-panel">
                  <div className="tac-panel-head">
                    <span className="tac-panel-title">科技ETF动量趋势（t-mom-etf）</span>
                    <div className="tac-toolbar">
                      <button className="tac-btn" onClick={() => doPost('/mom-etf/scan', null, '已触发动量扫描')}>扫描</button>
                      <button className="tac-btn" onClick={() => doPost('/mom-etf/rebalance', null, '已触发动量调仓')}>调仓</button>
                      <button className="tac-btn" onClick={() => doPost('/mom-etf/exit-check', null, '已触发出场合查')}>出场</button>
                      <button className="tac-btn" onClick={loadMomEtf}>刷新</button>
                    </div>
                  </div>
                  <div className="tac-panel-body">
                    <div className="tac-cards tac-cards-sm">
                      <div className="tac-card"><div className="tac-card-label">动量</div><div className="tac-card-value">{meStatus?.enabled ? '🟢 启用' : '⚪ 关闭'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">双周轮动</div><div className="tac-card-value">{meStatus?.running ? '运行中' : '未运行'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">账户</div><div className="tac-card-value">{meStatus?.account ?? '-'}</div></div>
                      <div className="tac-card"><div className="tac-card-label">上次</div><div className="tac-card-value tac-card-sm-value">{fmtTime(meStatus?.last_scan)}</div></div>
                    </div>
                    <div className="tac-subtitle">目标组合（20日动量 TOP3 · 贪婪门控）</div>
                    <table className="tac-table">
                      <thead><tr><th>代码</th><th>得分</th><th>状态</th><th>条件</th><th>时间</th></tr></thead>
                      <tbody>
                        {meCands.map((c) => (
                          <tr key={c.symbol + (c.created_at || '')}>
                            <td className="tac-sym">{c.symbol}</td>
                            <td>{c.score?.toFixed(2) ?? '-'}</td>
                            <td>{c.status ?? '-'}</td>
                            <td className="tac-muted">{c.trend ?? ''}</td>
                            <td className="tac-muted">{fmtTime(c.created_at)}</td>
                          </tr>
                        ))}
                        {meCands.length === 0 && <tr><td colSpan={5} className="tac-empty">暂无目标组合</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
