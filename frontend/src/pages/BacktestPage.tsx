import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Play, Square, BarChart3, Activity, AlertTriangle, ChevronRight,
  RefreshCw, Layers, Trash2, Plus, List, X, Terminal, Target, Download,
  FileText, FileSpreadsheet, Briefcase, Copy, Brain,
} from 'lucide-react';
import {
  ComposedChart, Bar, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { backtestApi } from '../api/client';
import '../styles/backtest-page.css';

/* ---- Types ---- */
interface TaskInfo {
  id: string; name: string; start_date: string; end_date: string;
  initial_capital: number; status: string; current_day: string | null;
  total_days: number; completed_days: number; progress: number;
  started_at: string | null; completed_at: string | null;
  error_message: string | null; created_at: string;
}

interface TradeItem {
  id: number; trade_date: string; symbol: string; stock_name: string; direction: string;
  price: number; volume: number; amount: number; commission: number;
  profit: number; profit_pct: number; reason: string;
  phase_time?: string; signal_price?: number; actual_price?: number;
  slippage_pct?: number; stamp_tax?: number; transfer_fee?: number; net_profit?: number;
}

interface EquityPoint {
  date: string; total_asset: number; cost_based_asset: number; available_cash: number;
  position_value: number; cost_value: number; float_pnl: number;
  daily_pct: number; cost_based_return: number;
  daily_return: number; cumulative_return: number; baseline_return: number;
}

interface MonthlyMetric {
  month: string; return_pct: number; trades_count: number;
  win_count: number; win_rate: number; max_drawdown: number;
}

interface PositionItem {
  symbol: string; stock_name: string; trade_date: string;
  volume: number; avg_cost: number; current_price: number;
  cost_value: number; market_value: number;
  float_pnl: number; float_pnl_pct: number;
  entry_date: string | null; holding_days: number;
  t1_status: { locked: boolean; last_buy_date: string | null; unlock_date: string | null; reason: string };
}

interface TaskDetail {
  id: string; name: string; status: string;
  start_date: string; end_date: string;
  initial_capital: number; total_days: number; completed_days: number;
  metrics: Record<string, number> | null;
  equity_curve: EquityPoint[];
  trades: TradeItem[];
  monthly_metrics: MonthlyMetric[];
  final_positions: PositionItem[];
  daily_logs: Array<{ trade_date: string; day_index: number; phase: string; event_type: string; content: string }>;
}

interface LogEntry { message: string; type: 'info' | 'highlight' | 'success' | 'error'; }

function fmtMoney(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return v.toLocaleString('zh-CN');
}
function fmtPct(v: number): string { return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`; }

const STATUS_COLORS: Record<string, string> = {
  pending: '#8a9bb5', running: '#f0b90b', completed: '#2ecc71',
  failed: '#e74c3c', cancelled: '#8a9bb5',
};
const STATUS_LABELS: Record<string, string> = {
  pending: '待启动', running: '运行中', completed: '已完成',
  failed: '失败', cancelled: '已取消',
};

export default function BacktestPage() {
  const { t } = useTranslation();
  const logEndRef = useRef<HTMLDivElement>(null);

  // State
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [showNewForm, setShowNewForm] = useState(false);
  const [formName, setFormName] = useState('');
  const [formStart, setFormStart] = useState('');
  const [formEnd, setFormEnd] = useState('');
  const [formCapital, setFormCapital] = useState(1000000);
  const [formIncludeChiNext, setFormIncludeChiNext] = useState(false);
  const [viewMode, setViewMode] = useState<'progress' | 'results'>('progress');

  // Live progress (SSE)
  const [liveLogs, setLiveLogs] = useState<LogEntry[]>([]);
  const [liveProgress, setLiveProgress] = useState(0);
  const [liveStatus, setLiveStatus] = useState('');
  const [liveEquity, setLiveEquity] = useState<EquityPoint[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // ── 导出菜单项统一样式 ──
  const menuItemStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'flex-start', gap: 10,
    padding: '8px 10px', borderRadius: 4,
    background: 'transparent', border: 'none',
    color: 'var(--agent-text-primary)', cursor: 'pointer',
    textAlign: 'left', width: '100%', fontSize: 13,
    transition: 'background 0.15s',
  };

  // ── Load task list ──
  const loadTasks = useCallback(async () => {
    try {
      const res = await backtestApi.listTasks({ limit: 50 });
      setTasks(res.data.tasks || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  // ── Auto-scroll logs ──
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [liveLogs]);

  // ── Load detail when selectedTaskId changes ──
  useEffect(() => {
    if (!selectedTaskId) { setDetail(null); return; }
    
    // If task is running, switch to progress view
    const task = tasks.find(t => t.id === selectedTaskId);
    if (task?.status === 'running') {
      setViewMode('progress');
    }
    
    setLoading(true);
    backtestApi.getDetail(selectedTaskId)
      .then(res => {
        setDetail(res.data);
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          setViewMode('results');
        }
      })
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [selectedTaskId, tasks]);

  // ── Create task ──
  const handleCreate = async () => {
    if (!formName || !formStart || !formEnd) return;
    try {
      const res = await backtestApi.create({
        name: formName,
        start_date: formStart,
        end_date: formEnd,
        initial_capital: formCapital,
        include_chinext: formIncludeChiNext,
      });
      setShowNewForm(false);
      setFormName(''); setFormStart(''); setFormEnd('');
      setFormCapital(1000000);
      setFormIncludeChiNext(false);
      await loadTasks();
      setSelectedTaskId(res.data.task_id);
    } catch { /* ignore */ }
  };

  // ── Start / Run with SSE ──
  const handleStart = useCallback(async (taskId: string) => {
    setStreaming(true);
    setLiveLogs([]);
    setLiveProgress(0);
    setLiveEquity([]);
    setLiveStatus('running');
    setViewMode('progress');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // Start the task
      await backtestApi.start(taskId);

      // Connect SSE
      const url = backtestApi.getStreamUrl(taskId);
      const response = await fetch(url, { signal: controller.signal });
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6));
            if (data.event === 'done') break;
            processEvent(data);
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setLiveLogs(prev => [...prev, { message: '已取消', type: 'error' }]);
      }
    } finally {
      setStreaming(false);
      await loadTasks();
      // Reload detail after completion
      if (selectedTaskId) {
        backtestApi.getDetail(selectedTaskId).then(res => {
          setDetail(res.data);
          setViewMode('results');
        }).catch(() => {});
      }
    }
  }, [selectedTaskId, loadTasks]);

  const processEvent = useCallback((data: Record<string, unknown>) => {
    const evt = data.event as string;
    const msg = data.message as string || '';
    const prog = data.progress as number || 0;
    const d = data.data as Record<string, unknown> | undefined;

    setLiveProgress(prog);
    if (msg) {
      const type = evt === 'error' ? 'error' : evt === 'complete' ? 'success' :
                   evt === 'status' ? 'highlight' : evt === 'pi_report' ? 'success' : 'info';
      setLiveLogs(prev => [...prev, { message: msg, type }]);
    }
    if (evt === 'equity' && d) {
      setLiveEquity(prev => [...prev, d as unknown as EquityPoint]);
    }
    if (evt === 'complete') setLiveStatus('completed');
    if (evt === 'error') setLiveStatus('failed');
  }, []);

  const handleCancel = useCallback(async () => {
    abortRef.current?.abort();
    if (selectedTaskId) {
      await backtestApi.cancel(selectedTaskId);
      await loadTasks();
    }
  }, [selectedTaskId, loadTasks]);

  const handleDelete = useCallback(async (taskId: string) => {
    try {
      await backtestApi.deleteTask(taskId);
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      await loadTasks();
    } catch { /* ignore */ }
  }, [selectedTaskId, loadTasks]);

  // ── 通用下载 (fetch + blob, 不会跳转页面, 不会阻塞其他接口) ──
  // 关键: 用 fetch 拿响应, 转 Blob, URL.createObjectURL 创建临时 URL, 再用 <a download> 触发
  // 好处: (1) 不会跳转到新页面 (2) Excel/CSV 等二进制都能正确处理
  //      (3) 错误可以 catch 提示用户
  const [downloading, setDownloading] = useState(false);
  const handleDownloadCsv = useCallback(async (url: string) => {
    if (downloading) return;  // 防双击重复
    setDownloading(true);
    try {
      // 从后端响应头 X-Filename 取文件名 (后端暂未返回, fallback 从 URL 推算)
      const resp = await fetch(url, {
        credentials: 'include',
        headers: { 'Accept': '*/*' },
      });
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${errText.slice(0, 200) || resp.statusText}`);
      }
      const blob = await resp.blob();
      // 从 Content-Disposition 解析文件名
      const cd = resp.headers.get('Content-Disposition') || '';
      const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i);
      const asciiMatch = cd.match(/filename="([^"]+)"/i);
      const filename = (utf8Match && decodeURIComponent(utf8Match[1]))
        || (asciiMatch && asciiMatch[1])
        || url.split('/').pop()
        || 'download';
      // 触发下载
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // 释放 blob URL
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (e: any) {
      console.error('[Backtest] 下载失败', e);
      alert(`下载失败: ${e?.message || e}\n\n如果是导出 Excel 大文件,可能需要等待后端处理完成。`);
    } finally {
      setDownloading(false);
    }
  }, [downloading]);

  // ── 下载 Pi 交易报告 (从 Pi Server sessions 读取) ──
  const handleDownloadPiReport = useCallback(async () => {
    if (!selectedTaskId || downloading) return;
    setDownloading(true);
    try {
      const url = `http://localhost:3001/reports/${selectedTaskId}?format=md`;
      const resp = await fetch(url);
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${errText.slice(0, 200)}`);
      }
      const text = await resp.text();
      const blob = new Blob([text], { type: 'text/markdown; charset=utf-8' });
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = `pi_reports_${selectedTaskId.slice(0, 8)}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (e: any) {
      console.error('[Backtest] Pi 报告下载失败', e);
      alert(`Pi 报告下载失败: ${e?.message || e}`);
    } finally {
      setDownloading(false);
    }
  }, [selectedTaskId, downloading]);

  // ── 导出下拉菜单控制 ──
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!exportMenuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setExportMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [exportMenuOpen]);

  // ── 策略报告弹窗 ──
  const [strategyReport, setStrategyReport] = useState<{
    markdown: string; stats: any;
  } | null>(null);
  const [strategyLoading, setStrategyLoading] = useState(false);
  const handleViewStrategy = useCallback(async () => {
    if (!selectedTaskId) return;
    setStrategyLoading(true);
    try {
      const r = await backtestApi.getStrategyReport(selectedTaskId);
      setStrategyReport(r);
    } catch (e) {
      console.error('[Backtest] 策略报告加载失败', e);
      alert('策略报告加载失败,请查看后端日志');
    } finally {
      setStrategyLoading(false);
    }
  }, [selectedTaskId]);


  // ── Monthly cell class ──
  const monthlyCellClass = (ret: number) => {
    if (ret > 8) return 'gain-strong'; if (ret > 0) return 'gain-mild';
    if (ret > -8) return 'loss-mild'; return 'loss-strong';
  };

  // ── Calc metrics from detail ──
  const calcMetrics = (d: TaskDetail | null) => {
    if (!d?.equity_curve || d.equity_curve.length === 0) return null;
    const eq = d.equity_curve;
    const initial = d.initial_capital;
    const final = eq[eq.length - 1].total_asset;
    const totalRet = (final / initial - 1) * 100;
    const totalDays = eq.length;
    const years = totalDays / 252;
    const annualRet = years > 0 ? ((Math.pow(1 + totalRet / 100, 1 / years) - 1) * 100) : 0;

    let peak = 0, maxDd = 0;
    for (const e of eq) {
      if (e.total_asset > peak) peak = e.total_asset;
      const dd = (peak - e.total_asset) / peak * 100;
      if (dd > maxDd) maxDd = dd;
    }

    const buys = d.trades?.filter(t => t.direction === 'buy').length || 0;
    const sells = d.trades?.filter(t => t.direction === 'sell').length || 0;
    const commission = d.trades?.reduce((s, t) => s + (t.commission || 0), 0) || 0;

    // 胜率: 盈利 sell 笔数 / (盈利+亏损) sell 笔数(排除盈亏=0 的 T+0 边界单)
    const profitableSells = d.trades?.filter(t => t.direction === 'sell' && (t.profit || 0) > 0).length || 0;
    const lossSells = d.trades?.filter(t => t.direction === 'sell' && (t.profit || 0) < 0).length || 0;
    const winRate = (profitableSells + lossSells) > 0
      ? (profitableSells / (profitableSells + lossSells)) * 100
      : 0;
    const avgWin = profitableSells > 0
      ? (d.trades?.filter(t => t.direction === 'sell' && (t.profit || 0) > 0)
          .reduce((s, t) => s + (t.profit || 0), 0) || 0) / profitableSells
      : 0;
    const avgLoss = lossSells > 0
      ? Math.abs((d.trades?.filter(t => t.direction === 'sell' && (t.profit || 0) < 0)
          .reduce((s, t) => s + (t.profit || 0), 0) || 0) / lossSells)
      : 0;

    // Sharpe (simplified)
    let sharpe = 0;
    if (eq.length > 1) {
      const rets = eq.map(e => e.daily_return || 0);
      const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
      const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1);
      sharpe = variance > 0 ? (mean / Math.sqrt(variance)) * Math.sqrt(252) : 0;
    }

    return { totalRet, annualRet, maxDd, sharpe, buys, sells, totalTrades: buys + sells,
             finalEquity: final, commission, totalDays,
             winRate, avgWin, avgLoss, profitableSells, lossSells };
  };

  const metrics = calcMetrics(detail);

  return (
    <div className="bt-page">
      {/* ========== Top Bar ========== */}
      <div className="bt-control-bar">
        <BarChart3 size={18} style={{ color: 'var(--agent-gold, #f0b90b)' }} />
        <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--agent-text-primary, #f0f4fa)' }}>
          {t('backtest.title')}
        </span>
        <div style={{ flex: 1 }} />
        <button className="bt-preset-btn" onClick={() => setShowNewForm(true)}>
          <Plus size={14} /> 新建回测
        </button>
      </div>

      {/* ========== Main Layout: Sidebar + Content ========== */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ── Left: Task List Sidebar ── */}
        <div className="bt-sidebar">
          <div className="bt-sidebar-header">
            <List size={14} />
            <span>回测任务</span>
            <span style={{ fontSize: 11, color: 'var(--agent-text-muted)', marginLeft: 'auto' }}>
              {tasks.length}
            </span>
          </div>
          <div className="bt-sidebar-list">
            {tasks.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--agent-text-muted)', fontSize: 13 }}>
                暂无回测任务
              </div>
            ) : (
              tasks.map(task => (
                <div
                  key={task.id}
                  className={`bt-task-item ${selectedTaskId === task.id ? 'active' : ''}`}
                  onClick={() => setSelectedTaskId(task.id)}
                >
                  <div className="bt-task-item-top">
                    <span className="bt-task-name">{task.name}</span>
                    <span className="bt-task-status" style={{ color: STATUS_COLORS[task.status] }}>
                      {STATUS_LABELS[task.status]}
                    </span>
                  </div>
                  <div className="bt-task-item-meta">
                    {task.start_date} ~ {task.end_date}
                  </div>
                  <div className="bt-task-item-meta">
                    初始 ¥{fmtMoney(task.initial_capital)}
                    {task.status === 'running' && ` · ${task.completed_days}/${task.total_days}天`}
                  </div>
                  {task.status === 'running' && (
                    <div className="bt-progress-bar-wrap" style={{ marginTop: 6 }}>
                      <div className="bt-progress-bar-fill" style={{ width: `${task.progress}%` }} />
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Right: Main Content ── */}
        <div className="bt-main-scroll" style={{ flex: 1 }}>
          {/* New Task Form Modal */}
          {showNewForm && (
            <div className="bt-panel" style={{ marginBottom: 16, borderColor: 'var(--agent-gold, #f0b90b)' }}>
              <div className="bt-panel-header">
                <div className="bt-panel-title"><Plus size={14} /> 新建回测任务</div>
                <button onClick={() => setShowNewForm(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--agent-text-muted)' }}>
                  <X size={16} />
                </button>
              </div>
              <div className="bt-panel-body">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div>
                    <label className="bt-label" style={{ display: 'block', marginBottom: 4 }}>任务名称</label>
                    <input className="bt-input" style={{ width: '100%' }} placeholder="如: 2024年AI交易回测"
                      value={formName} onChange={e => setFormName(e.target.value)} />
                  </div>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <div style={{ flex: 1 }}>
                      <label className="bt-label" style={{ display: 'block', marginBottom: 4 }}>起始日期</label>
                      <input type="date" className="bt-input" style={{ width: '100%' }}
                        value={formStart} onChange={e => setFormStart(e.target.value)} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label className="bt-label" style={{ display: 'block', marginBottom: 4 }}>结束日期</label>
                      <input type="date" className="bt-input" style={{ width: '100%' }}
                        value={formEnd} onChange={e => setFormEnd(e.target.value)} />
                    </div>
                  </div>
                  <div>
                    <label className="bt-label" style={{ display: 'block', marginBottom: 4 }}>初始资金</label>
                    <input type="number" className="bt-input" style={{ width: '100%' }}
                      value={formCapital} onChange={e => setFormCapital(Number(e.target.value))}
                      min={10000} max={100000000} step={100000} />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input id="bt-form-chinext" type="checkbox"
                      checked={formIncludeChiNext}
                      onChange={e => setFormIncludeChiNext(e.target.checked)}
                      style={{ width: 16, height: 16, accentColor: '#f0b90b', cursor: 'pointer' }} />
                    <label htmlFor="bt-form-chinext" style={{ fontSize: 13, cursor: 'pointer', color: 'var(--agent-text-primary)' }}>
                      允许交易<span style={{ color: '#f0b90b', fontWeight: 600 }}>创业板</span>股票
                    </label>
                    <span style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                      (代码以 300/301 开头, 深交所)
                    </span>
                  </div>
                  <button className="bt-run-btn" style={{ marginLeft: 0, width: '100%', justifyContent: 'center' }}
                    onClick={handleCreate} disabled={!formName || !formStart || !formEnd}>
                    <Plus size={16} /> 创建任务
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* No task selected */}
          {!selectedTaskId && (
            <div className="bt-empty-state">
              <Terminal size={64} className="bt-empty-icon" />
              <div className="bt-empty-title">AI 交易回测系统</div>
              <div className="bt-empty-desc">
                选择左侧回测任务查看详情，或点击「新建回测」创建新的回测任务。
                系统将模拟真实交易日流程，调用 DeepSeek AI 在每个交易阶段做出决策，完整记录交易细节。
              </div>
            </div>
          )}

          {/* Selected task - show detail */}
          {selectedTaskId && (
            <>
              {/* ── Task Info Header ── */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '12px 16px', background: 'var(--agent-bg-card)', borderRadius: 10,
                border: '1px solid var(--color-border)', marginBottom: 16,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: STATUS_COLORS[detail?.status || 'pending'],
                  }} />
                  <span style={{ fontWeight: 600, color: 'var(--agent-text-primary)' }}>
                    {detail?.name || '加载中...'}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--agent-text-muted)' }}>
                    {detail?.start_date} ~ {detail?.end_date}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {detail?.status === 'completed' && (
                    <button className="bt-preset-btn" onClick={() => setViewMode(viewMode === 'results' ? 'progress' : 'results')}>
                      {viewMode === 'results' ? '查看进度' : '查看结果'}
                    </button>
                  )}
                  {detail?.status === 'completed' && (
                    <div ref={exportMenuRef} style={{ position: 'relative' }}>
                      <button
                        className="bt-preset-btn"
                        onClick={() => setExportMenuOpen(o => !o)}
                        disabled={downloading}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 4,
                          opacity: downloading ? 0.6 : 1,
                          cursor: downloading ? 'wait' : 'pointer',
                        }}
                      >
                        <Download size={14} /> {downloading ? '下载中...' : '导出 ▾'}
                      </button>
                      {exportMenuOpen && (
                        <div
                          style={{
                            position: 'absolute', top: 'calc(100% + 4px)', right: 0,
                            background: 'var(--agent-bg-elevated, #1a1a1a)',
                            border: '1px solid var(--agent-border, #333)',
                            borderRadius: 6, padding: 4, minWidth: 260,
                            boxShadow: '0 4px 16px rgba(0,0,0,0.4)', zIndex: 100,
                            display: 'flex', flexDirection: 'column', gap: 2,
                          }}
                          onClick={e => e.stopPropagation()}
                        >
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              handleDownloadCsv(backtestApi.getEquityCsvUrl(selectedTaskId));
                              setExportMenuOpen(false);
                            }}
                            style={menuItemStyle}
                          >
                            <FileText size={14} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500 }}>权益曲线 CSV</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                daily_pct / cost_based / drawdown 等
                              </div>
                            </div>
                          </button>
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              handleDownloadCsv(backtestApi.getTradesCsvUrl(selectedTaskId));
                              setExportMenuOpen(false);
                            }}
                            style={menuItemStyle}
                          >
                            <FileSpreadsheet size={14} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500, color: 'var(--agent-gold, #f0b90b)' }}>逐笔交易明细 CSV ★</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                时分秒+滑点+印花税+净盈亏(评估真假)
                              </div>
                            </div>
                          </button>
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              handleDownloadCsv(backtestApi.getPositionsCsvUrl(selectedTaskId));
                              setExportMenuOpen(false);
                            }}
                            style={menuItemStyle}
                          >
                            <Briefcase size={14} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500 }}>每日持仓快照 CSV</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                单票集中度+仓位分散度
                              </div>
                            </div>
                          </button>
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              handleDownloadCsv(backtestApi.getIndexCsvUrl(selectedTaskId));
                              setExportMenuOpen(false);
                            }}
                            style={menuItemStyle}
                          >
                            <BarChart3 size={14} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500 }}>基准指数+板块背景 CSV</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                沪深300/创业板/科创50+申万行业 TOP
                              </div>
                            </div>
                          </button>
                          <div style={{ height: 1, background: 'var(--agent-border, #333)', margin: '4px 0' }} />
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              handleDownloadCsv(backtestApi.getExportAllUrl(selectedTaskId));
                              setExportMenuOpen(false);
                            }}
                            style={{
                              ...menuItemStyle,
                              background: 'rgba(240, 185, 11, 0.06)',
                              border: '1px solid rgba(240, 185, 11, 0.25)',
                              borderRadius: 4,
                            }}
                          >
                            <FileSpreadsheet size={14} style={{ color: 'var(--agent-gold, #f0b90b)' }} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 600, color: 'var(--agent-gold, #f0b90b)' }}>
                                ★ 全部导出 (Excel 多 Sheet)
                              </div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                7 个 Sheet: 任务/交易/持仓/权益/月份/指数/策略
                              </div>
                            </div>
                          </button>
                          <div style={{ height: 1, background: 'var(--agent-border, #333)', margin: '4px 0' }} />
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              setExportMenuOpen(false);
                              handleViewStrategy();
                            }}
                            style={menuItemStyle}
                          >
                            <FileText size={14} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500 }}>查看策略报告</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                选股/买卖/风控/统计 (Markdown)
                              </div>
                            </div>
                          </button>
                          <div style={{ height: 1, background: 'var(--agent-border, #333)', margin: '4px 0' }} />
                          <button
                            className="bt-export-menu-item"
                            onClick={() => {
                              setExportMenuOpen(false);
                              handleDownloadPiReport();
                            }}
                            style={menuItemStyle}
                          >
                            <Brain size={14} style={{ color: '#a78bfa' }} />
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 500, color: '#a78bfa' }}>下载 Pi 交易报告</div>
                              <div style={{ fontSize: 11, color: 'var(--agent-text-muted)' }}>
                                每天早盘/午前/午后/尾盘的 AI 决策原文 (Markdown)
                              </div>
                            </div>
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {detail?.status === 'pending' && (
                    <button className="bt-run-btn" style={{ marginLeft: 0, padding: '6px 16px', fontSize: 13 }}
                      onClick={() => handleStart(selectedTaskId)} disabled={streaming}>
                      <Play size={14} /> 启动
                    </button>
                  )}
                  {(detail?.status === 'running' || streaming) && (
                    <button className="bt-run-btn running" style={{ marginLeft: 0, padding: '6px 16px', fontSize: 13 }}
                      onClick={handleCancel}>
                      <Square size={14} /> 停止
                    </button>
                  )}
                  {detail?.status === 'failed' && (
                    <button className="bt-run-btn" style={{ marginLeft: 0, padding: '6px 16px', fontSize: 13 }}
                      onClick={() => handleStart(selectedTaskId)} disabled={streaming}>
                      <RefreshCw size={14} /> 重试
                    </button>
                  )}
                  {detail?.status !== 'running' && (
                    <button className="bt-preset-btn" style={{ color: '#e74c3c', borderColor: 'rgba(231,76,60,0.3)' }}
                      onClick={() => { handleDelete(selectedTaskId); }}>
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>

              {/* ── Progress View ── */}
              {viewMode === 'progress' && (
                <>
                  {/* Progress Bar */}
                  <div className="bt-progress-bar-wrap" style={{ marginBottom: 12 }}>
                    <div className="bt-progress-bar-fill"
                      style={{ width: `${streaming ? liveProgress : detail?.progress || 0}%` }} />
                  </div>

                  {/* Log Panel */}
                  <div className="bt-log-panel">
                    <div className="bt-log-header">
                      <div className="bt-log-title">
                        <div className={`bt-log-status-dot ${streaming ? 'running' : detail?.status}`} />
                        {streaming ? '运行中' : detail?.status === 'completed' ? '已完成' : '日志输出'}
                      </div>
                      <span style={{ fontSize: 12, color: 'var(--agent-text-muted)' }}>
                        {streaming ? `${liveProgress.toFixed(0)}%` : `${detail?.progress || 0}%`}
                      </span>
                    </div>
                    <div className="bt-log-body">
                      {liveLogs.length > 0 ? (
                        liveLogs.map((log, i) => (
                          <div key={i} className={`bt-log-line ${log.type}`}>{log.message}</div>
                        ))
                      ) : detail?.daily_logs ? (
                        detail.daily_logs.slice(-50).map((log, i) => (
                          <div key={i} className="bt-log-line info">
                            [{log.trade_date}] {log.content}
                          </div>
                        ))
                      ) : (
                        <div className="bt-log-line" style={{ opacity: 0.5 }}>等待任务启动...</div>
                      )}
                      <div ref={logEndRef} />
                    </div>
                  </div>

                  {/* Live Equity (柱:当日收益 | 金色面积:资产指数(首日=100) | 蓝虚线:累计收益率%) */}
                  {liveEquity.length > 0 && (
                    <div className="bt-panel" style={{ marginTop: 16 }}>
                      <div className="bt-panel-header">
                        <div className="bt-panel-title"><Activity size={14} /> 实时权益</div>
                        <div className="bt-panel-sub" style={{ fontSize: 11, color: '#8a9bb5', marginLeft: 8 }}>
                          柱:当日收益率% | 金面积:总资产指数(首日=100) | 蓝虚线:累计收益率%(含浮盈)
                        </div>
                      </div>
                      <div className="bt-panel-body">
                        <div className="bt-chart-wrap" style={{ height: 250 }}>
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={liveEquity} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
                              <defs>
                                <linearGradient id="liveGrad" x1="0" y1="0" x2="0" y2="1">
                                  <stop offset="0%" stopColor="#f0b90b" stopOpacity={0.3} />
                                  <stop offset="100%" stopColor="#f0b90b" stopOpacity={0} />
                                </linearGradient>
                              </defs>
                              <CartesianGrid strokeDasharray="3 3" stroke="oklch(27.5% 0 0)" />
                              <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#8a9bb5' }} />
                              {/* 左 Y 轴: 资产指数 (100 = 首日总资产) */}
                              <YAxis yAxisId="left" tick={{ fontSize: 9, fill: '#8a9bb5' }}
                                tickFormatter={v => `${(v as number).toFixed(1)}`} width={45}
                                domain={['auto', 'auto']} label={{ value: '资产指数', angle: -90, position: 'insideLeft', fontSize: 9, fill: '#8a9bb5' }} />
                              {/* 中 Y 轴: 累计收益率% (右内侧) - 独立刻度确保蓝虚线可见 */}
                              <YAxis yAxisId="cum" orientation="right" tick={{ fontSize: 9, fill: '#5ac8fa' }}
                                tickFormatter={v => `${(v as number).toFixed(0)}%`} width={42} />
                              {/* 右 Y 轴: 当日收益率 % */}
                              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 9, fill: '#5ac8fa' }}
                                tickFormatter={v => `${(v as number).toFixed(2)}%`} width={45} />
                              <Tooltip contentStyle={{ background: '#11161e', border: '1px solid oklch(27.5% 0 0)', borderRadius: 8, fontSize: 12, color: '#f0f4fa' }}
                                formatter={(v: number, name: string) => {
                                  if (name === '当日收益率(%)') return [`${(v as number).toFixed(2)}%`, name];
                                  if (name === '累计收益率(%)') return [`${(v as number).toFixed(2)}%`, name];
                                  // 资产指数 → 同步显示对应金额
                                  if (name.includes('资产指数') || name.includes('累计收益率含浮盈')) {
                                    const idx = v as number;
                                    const first = liveEquity[0];
                                    const base = first?.total_asset || 0;
                                    const amount = (idx / 100) * base;
                                    return [`${idx.toFixed(2)} → ¥${amount.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, name];
                                  }
                                  return [v, name];
                                }} />
                              <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} />
                              <ReferenceLine yAxisId="left" y={100} stroke="#8a9bb5" strokeDasharray="4 4" strokeOpacity={0.5} label={{ value: '起点100', fontSize: 9, fill: '#8a9bb5', position: 'insideTopRight' }} />
                              <Bar yAxisId="right" dataKey="daily_pct" name="当日收益率(%)"
                                fill="#5ac8fa" fillOpacity={0.6} />
                              <Area yAxisId="left" type="monotone" dataKey="baseline_return" name="资产指数(含浮盈,首日=100)"
                                stroke="#f0b90b" strokeWidth={2} fill="url(#liveGrad)" />
                              <Line yAxisId="cum" type="monotone" dataKey="cumulative_return" name="累计收益率(%)"
                                stroke="#5ac8fa" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* ── Results View ── */}
              {viewMode === 'results' && detail && metrics && (
                <>
                  {/* Metrics Grid */}
                  <div className="bt-metrics-grid">
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.totalReturn')}</div>
                      <div className={`bt-metric-value ${metrics.totalRet >= 0 ? 'positive' : 'negative'}`}>
                        {fmtPct(metrics.totalRet)}
                      </div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.winRate')}</div>
                      <div className={`bt-metric-value ${metrics.winRate >= 50 ? 'positive' : 'negative'}`}>
                        {metrics.winRate.toFixed(1)}%
                      </div>
                      <div className="bt-metric-sub">
                        胜{metrics.profitableSells} / 负{metrics.lossSells} · 盈亏比 {(metrics.avgWin / (metrics.avgLoss || 1)).toFixed(2)}
                      </div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.sharpeRatio')}</div>
                      <div className={`bt-metric-value ${metrics.sharpe >= 1 ? 'positive' : 'neutral'}`}>
                        {metrics.sharpe.toFixed(3)}
                      </div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.maxDrawdown')}</div>
                      <div className={`bt-metric-value ${metrics.maxDd < 15 ? 'positive' : 'negative'}`}>
                        -{metrics.maxDd.toFixed(2)}%
                      </div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.totalTrades')}</div>
                      <div className="bt-metric-value neutral">{metrics.totalTrades}</div>
                      <div className="bt-metric-sub">买{metrics.buys} / 卖{metrics.sells}</div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">{t('backtest.finalEquity')}</div>
                      <div className="bt-metric-value neutral">{fmtMoney(metrics.finalEquity)}</div>
                      <div className="bt-metric-sub">佣金 ¥{fmtMoney(metrics.commission)}</div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">模拟天数</div>
                      <div className="bt-metric-value neutral">{metrics.totalDays}</div>
                    </div>
                    <div className="bt-metric-card">
                      <div className="bt-metric-label">初始资金</div>
                      <div className="bt-metric-value neutral">{fmtMoney(detail.initial_capital)}</div>
                    </div>
                  </div>

                  {/* Equity Curve */}
                  {detail.equity_curve.length > 0 && (
                    <div className="bt-panel">
                      <div className="bt-panel-header">
                        <div className="bt-panel-title"><Activity size={14} /> {t('backtest.equityCurve')}</div>
                        <div className="bt-panel-sub" style={{ fontSize: 11, color: '#8a9bb5', marginLeft: 8 }}>
                          左轴:资产指数(首日=100,金面积) | 中轴:累计收益率%(蓝虚线) | 右轴:当日收益率%(蓝柱)
                        </div>
                      </div>
                      <div className="bt-panel-body">
                        <div className="bt-chart-wrap">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={detail.equity_curve} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                              <defs>
                                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                                  <stop offset="0%" stopColor="#f0b90b" stopOpacity={0.25} />
                                  <stop offset="100%" stopColor="#f0b90b" stopOpacity={0} />
                                </linearGradient>
                              </defs>
                              <CartesianGrid strokeDasharray="3 3" stroke="oklch(27.5% 0 0)" />
                              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#8a9bb5' }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                              {/* 左 Y 轴: 资产指数 (100 = 首日总资产) */}
                              <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#8a9bb5' }} tickLine={false} axisLine={false}
                                tickFormatter={v => `${(v as number).toFixed(1)}`} width={45}
                                domain={['auto', 'auto']}
                                label={{ value: '资产指数', angle: -90, position: 'insideLeft', fontSize: 10, fill: '#8a9bb5' }} />
                              {/* 中 Y 轴: 累计收益率% (右内侧, 让蓝虚线有独立刻度) */}
                              <YAxis yAxisId="cum" orientation="right" tick={{ fontSize: 10, fill: '#5ac8fa' }} tickLine={false} axisLine={false}
                                tickFormatter={v => `${(v as number).toFixed(0)}%`} width={45}
                                label={{ value: '累计%', angle: 90, position: 'insideRight', fontSize: 10, fill: '#5ac8fa' }} />
                              {/* 右 Y 轴: 当日收益率 % */}
                              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#5ac8fa' }} tickLine={false} axisLine={false}
                                tickFormatter={v => `${(v as number).toFixed(1)}%`} width={45}
                                label={{ value: '当日%', angle: 90, position: 'insideRight', fontSize: 10, fill: '#5ac8fa' }} />
                              <Tooltip contentStyle={{ background: '#11161e', border: '1px solid oklch(27.5% 0 0)', borderRadius: 8, fontSize: 12, color: '#f0f4fa' }}
                                formatter={(v: number, name: string) => {
                                  if (name === '当日收益率(%)') return [`${(v as number).toFixed(2)}%`, name];
                                  if (name === '累计收益率(%)') return [`${(v as number).toFixed(2)}%`, name];
                                  // 资产指数 → 同步显示对应金额
                                  if (name.includes('资产指数')) {
                                    const idx = v as number;
                                    const first = detail.equity_curve[0];
                                    const base = first?.total_asset || 0;
                                    const amount = (idx / 100) * base;
                                    return [`${idx.toFixed(2)} → ¥${fmtMoney(amount)}`, name];
                                  }
                                  return [v, name];
                                }}
                                labelFormatter={(label) => `日期: ${label}`} />
                              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
                              {/* 起点 100 参考线 */}
                              <ReferenceLine yAxisId="left" y={100} stroke="#8a9bb5" strokeDasharray="4 4" strokeOpacity={0.5}
                                label={{ value: '起点100', fontSize: 10, fill: '#8a9bb5', position: 'insideTopRight' }} />
                              {/* 当日收益率柱状图（右 Y 轴） */}
                              <Bar yAxisId="right" dataKey="daily_pct" name="当日收益率(%)"
                                fill="#5ac8fa" fillOpacity={0.6} />
                              {/* 总资产指数（左 Y 轴，金色面积，首日=100） */}
                              <Area yAxisId="left" type="monotone" dataKey="baseline_return" name="资产指数(含浮盈,首日=100)"
                                stroke="#f0b90b" strokeWidth={2} fill="url(#equityGrad)" />
                              {/* 累计收益率% (中 Y 轴, 蓝色虚线) - 独立刻度确保可见 */}
                              <Line yAxisId="cum" type="monotone" dataKey="cumulative_return" name="累计收益率(%)"
                                stroke="#5ac8fa" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Two Column: Monthly Returns + Final Positions */}
                  <div className="bt-two-col">
                    {detail.monthly_metrics.length > 0 && (
                      <div className="bt-panel" style={{ marginTop: 0 }}>
                        <div className="bt-panel-header">
                          <div className="bt-panel-title"><Layers size={14} /> 月度收益</div>
                        </div>
                        <div className="bt-panel-body">
                          <div className="bt-monthly-grid">
                            {detail.monthly_metrics.map(m => (
                              <div key={m.month} className={`bt-monthly-cell ${monthlyCellClass(m.return_pct)}`}>
                                <div className="bt-month-label">{m.month.slice(2)}</div>
                                <div className="bt-month-return" style={{ color: m.return_pct >= 0 ? '#2ecc71' : '#e74c3c' }}>
                                  {fmtPct(m.return_pct)}
                                </div>
                                <div className="bt-month-trades">{m.trades_count}笔</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}

                    {detail.final_positions.length > 0 && (
                      <div className="bt-panel" style={{ marginTop: 0 }}>
                        <div className="bt-panel-header">
                          <div className="bt-panel-title"><Target size={14} /> 最终持仓</div>
                          <div className="bt-panel-sub" style={{ fontSize: 11, color: '#8a9bb5', marginLeft: 8 }}>
                            {detail.final_positions.length}只 · 累计市值 ¥{fmtMoney(detail.final_positions.reduce((s, p) => s + (p.market_value || 0), 0))}
                          </div>
                        </div>
                        <div className="bt-panel-body" style={{ padding: 0 }}>
                          <table className="bt-table" style={{ fontSize: 12 }}>
                            <thead>
                              <tr>
                                <th className="bt-align-left">代码/名称</th>
                                <th>数量</th>
                                <th>成本</th>
                                <th>现价</th>
                                <th>成本市值</th>
                                <th>现市值</th>
                                <th>浮盈额</th>
                                <th>浮盈%</th>
                                <th>持仓天数</th>
                                <th>T+1</th>
                              </tr>
                            </thead>
                            <tbody>
                              {detail.final_positions.map((p, i) => {
                                const t1 = p.t1_status || {};
                                let t1Str = '🟢';
                                if (t1.locked) t1Str = `🔒${t1.unlock_date?.slice(5) || ''}`;
                                else if (t1.last_buy_date) t1Str = `✅${t1.last_buy_date.slice(5)}`;
                                // 老数据兼容: 所有数值字段可能 undefined
                                const volume = p.volume || 0;
                                const avgCost = p.avg_cost || 0;
                                const curPrice = p.current_price || 0;
                                const costVal = p.cost_value || 0;
                                const mktVal = p.market_value || 0;
                                const fPnl = p.float_pnl || 0;
                                const fPnlPct = p.float_pnl_pct || 0;
                                const holdDays = p.holding_days || 0;
                                return (
                                  <tr key={i}>
                                    <td className="bt-align-left">
                                      <div style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--agent-text-primary)' }}>{p.symbol}</div>
                                      <div style={{ fontSize: 10, color: '#8a9bb5' }}>{p.stock_name || '—'}</div>
                                    </td>
                                    <td className="bt-td-muted">{volume.toLocaleString()}</td>
                                    <td className="bt-td-muted">{avgCost.toFixed(2)}</td>
                                    <td className="bt-td-muted">{curPrice.toFixed(2)}</td>
                                    <td className="bt-td-muted">¥{fmtMoney(costVal)}</td>
                                    <td className="bt-td-muted">¥{fmtMoney(mktVal)}</td>
                                    <td className="bt-td-muted" style={{ color: fPnl >= 0 ? '#2ecc71' : '#e74c3c', fontWeight: 600 }}>
                                      {fPnl >= 0 ? '+' : ''}¥{fmtMoney(fPnl)}
                                    </td>
                                    <td className="bt-td-muted" style={{ color: fPnlPct >= 0 ? '#2ecc71' : '#e74c3c', fontWeight: 600 }}>
                                      {fPnlPct >= 0 ? '+' : ''}{fPnlPct.toFixed(2)}%
                                    </td>
                                    <td className="bt-td-muted" style={{ fontSize: 11 }}>
                                      {holdDays}天
                                      <div style={{ fontSize: 9, color: '#8a9bb5' }}>{p.entry_date?.slice(5) || '—'}</div>
                                    </td>
                                    <td style={{ fontSize: 11 }}>{t1Str}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Trade Log */}
                  {detail.trades.length > 0 && (
                    <div className="bt-panel">
                      <div className="bt-panel-header">
                        <div className="bt-panel-title"><ChevronRight size={14} /> 交易明细 ({detail.trades.length}笔)</div>
                      </div>
                      <div className="bt-table-wrap">
                        <table className="bt-table">
                          <thead>
                            <tr>
                              <th className="bt-align-left">日期</th>
                              <th className="bt-align-left">代码</th>
                              <th className="bt-align-left">名称</th>
                              <th>方向</th>
                              <th>价格</th>
                              <th>数量</th>
                              <th>金额</th>
                              <th>佣金</th>
                              <th>滑点%</th>
                              <th>收益</th>
                              <th>收益率</th>
                              <th className="bt-align-left">理由</th>
                            </tr>
                          </thead>
                          <tbody>
                            {detail.trades.slice(0, 100).map(t => {
                              const hasProfit = t.direction === 'sell' && t.profit !== 0;
                              const profitColor = t.profit > 0 ? '#2ecc71' : (t.profit < 0 ? '#e74c3c' : '#8a9bb5');
                              const slipVal = (t.slippage_pct ?? 0);
                              const slipColor = slipVal === 0 ? '#8a9bb5' : (t.direction === 'buy' ? (slipVal > 0.05 ? '#e67e22' : '#8a9bb5') : (slipVal < -0.05 ? '#e74c3c' : '#8a9bb5'));
                              return (
                                <tr key={t.id}>
                                  <td className="bt-td-muted">{t.trade_date}</td>
                                  <td className="bt-td-symbol">{t.symbol}</td>
                                  <td className="bt-td-muted" style={{ maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {t.stock_name || '—'}
                                  </td>
                                  <td style={{ color: t.direction === 'buy' ? '#2ecc71' : '#e74c3c', fontWeight: 600, fontSize: 12 }}>
                                    {t.direction === 'buy' ? '买入' : '卖出'}
                                  </td>
                                  <td className="bt-td-muted">{t.price.toFixed(2)}</td>
                                  <td className="bt-td-muted">{t.volume.toLocaleString()}</td>
                                  <td className="bt-td-muted">{fmtMoney(t.amount)}</td>
                                  <td className="bt-td-muted">{t.commission.toFixed(2)}</td>
                                  <td className="bt-td-muted" style={{ color: slipColor, fontWeight: slipVal !== 0 ? 600 : 400 }}>
                                    {slipVal !== 0 ? `${slipVal > 0 ? '+' : ''}${slipVal.toFixed(2)}%` : '—'}
                                  </td>
                                  <td className="bt-td-muted" style={{ color: hasProfit ? profitColor : '#8a9bb5', fontWeight: hasProfit ? 600 : 400 }}>
                                    {hasProfit ? `${t.profit > 0 ? '+' : ''}${fmtMoney(t.profit)}` : '—'}
                                  </td>
                                  <td className="bt-td-muted" style={{ color: hasProfit ? profitColor : '#8a9bb5', fontWeight: hasProfit ? 600 : 400 }}>
                                    {hasProfit ? `${t.profit_pct > 0 ? '+' : ''}${t.profit_pct.toFixed(2)}%` : '—'}
                                  </td>
                                  <td className="bt-td-muted" style={{ maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {t.reason}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Loading state */}
              {loading && !detail && (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--agent-text-muted)' }}>加载中...</div>
              )}

              {/* Error / Fallback */}
              {!loading && detail?.status === 'failed' && viewMode === 'results' && (
                <div style={{
                  padding: 16, borderRadius: 10, background: 'rgba(231,76,60,0.1)',
                  border: '1px solid rgba(231,76,60,0.3)', color: '#e74c3c', fontSize: 13,
                }}>
                  <AlertTriangle size={16} style={{ display: 'inline', marginRight: 8, verticalAlign: -3 }} />
                  {detail.error_message || '回测执行失败'}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── 策略报告 Modal ── */}
      {(strategyReport || strategyLoading) && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 200,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 24,
          }}
          onClick={() => setStrategyReport(null)}
        >
          <div
            style={{
              background: 'var(--agent-bg-elevated, #1a1a1a)',
              border: '1px solid var(--agent-border, #333)',
              borderRadius: 8, padding: 24,
              maxWidth: 880, width: '100%', maxHeight: '85vh',
              display: 'flex', flexDirection: 'column',
              boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 18, color: 'var(--agent-text-primary)' }}>
                <FileText size={18} style={{ display: 'inline', marginRight: 8, verticalAlign: -3, color: 'var(--agent-gold, #f0b90b)' }} />
                策略逻辑与参数报告
              </h2>
              <div style={{ display: 'flex', gap: 8 }}>
                {strategyReport && (
                  <button
                    className="bt-preset-btn"
                    onClick={() => {
                      navigator.clipboard.writeText(strategyReport.markdown)
                        .then(() => alert('已复制到剪贴板'))
                        .catch(() => alert('复制失败,请手动选择文本'));
                    }}
                    title="复制 Markdown 全文"
                  >
                    <Copy size={14} /> 复制全文
                  </button>
                )}
                <button
                  className="bt-preset-btn"
                  onClick={() => setStrategyReport(null)}
                >
                  <X size={14} /> 关闭
                </button>
              </div>
            </div>
            <div
              style={{
                flex: 1, overflowY: 'auto',
                background: 'var(--agent-bg-base, #0e0e0e)',
                border: '1px solid var(--agent-border, #2a2a2a)',
                borderRadius: 4, padding: 20,
                fontSize: 13, lineHeight: 1.7,
                color: 'var(--agent-text-primary)', fontFamily: 'Consolas, "Microsoft YaHei", monospace',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}
            >
              {strategyLoading ? '加载中...' : strategyReport?.markdown || ''}
            </div>
            {strategyReport?.stats && (
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--agent-text-muted)' }}>
                总交易 {strategyReport.stats.total_trades} 笔
                (买 {strategyReport.stats.buy_count} / 卖 {strategyReport.stats.sell_count}) ·
                单笔胜率 {strategyReport.stats.win_rate}%
                (胜 {strategyReport.stats.wins} / 负 {strategyReport.stats.losses}) ·
                累计手续费 ¥{strategyReport.stats.total_commission?.toLocaleString()} ·
                累计印花税 ¥{strategyReport.stats.total_stamp_tax?.toLocaleString()} ·
                累计过户费 ¥{strategyReport.stats.total_transfer_fee?.toLocaleString()}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
