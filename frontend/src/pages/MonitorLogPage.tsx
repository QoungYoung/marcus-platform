import { useCallback, useEffect, useState } from 'react';
import '../styles/monitor-log-page.css';

const API = '/api/v1';

interface LogItem {
  id: number;
  timestamp: string;
  symbol: string;
  current_tier: string;
  target_tier: string;
  action: string;
  result: string;
  float_pnl_pct: number;
  current_price: number;
  add_shares: number;
  block_reason: string;
}

interface GateCheck {
  gate: string;
  status: string;
  detail: string;
}

interface TrendCheck {
  ma_align?: { passed: boolean; value: string; threshold: string; detail: string };
  ma5_slope?: { passed: boolean; value: string; threshold: string; detail: string };
  volume_ratio?: { passed: boolean; value: string; threshold: string; detail: string };
  sector_flow?: { passed: boolean; value: string; threshold: string; detail: string };
  moneyflow?: { passed: boolean; value: string; threshold: string; detail: string };
}

interface LogDetail {
  gate_details: GateCheck[];
  trend_details: {
    core_passed: boolean;
    aux_passed: number;
    aux_total: number;
    checks: TrendCheck;
  };
}

const RESULT_OPTIONS = ['', 'HOLD', 'BLOCKED', 'EXECUTED', 'SKIPPED', 'OUTFLOW'];

function resultLabel(r: string) {
  const map: Record<string, string> = {
    HOLD: '持有', BLOCKED: '拦截', EXECUTED: '已执行',
    SKIPPED: '跳过', OUTFLOW: '流出',
  };
  return map[r] || r;
}

function fmtPct(v: number) {
  if (v == null) return '-';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtTime(ts: string) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', { hour12: false });
}

export default function MonitorLogPage() {
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const [symbol, setSymbol] = useState('');
  const [result, setResult] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<LogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchLogs = useCallback(async (p: number) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      params.set('page', String(p));
      params.set('page_size', String(pageSize));
      if (symbol) params.set('symbol', symbol);
      if (result) params.set('result', result);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);

      const res = await fetch(`${API}/monitor/logs?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [symbol, result, dateFrom, dateTo]);

  useEffect(() => { fetchLogs(page); }, [fetchLogs, page]);

  const toggleExpand = async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    setDetailLoading(true);
    try {
      const res = await fetch(`${API}/monitor/logs/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setDetail(data);
    } catch {
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="ml-page">
      {/* Header */}
      <div className="ml-header">
        <div className="ml-header-left">
          <div className="ml-header-icon"><i className="fas fa-clipboard-list" /></div>
          <h1 className="ml-header-title">加仓监控日志</h1>
          <span className="ml-header-count">{total} 条记录</span>
        </div>
        <button className="ml-refresh-btn" onClick={() => fetchLogs(page)} title="刷新">
          <i className={`fas fa-sync-alt ${loading ? 'fa-spin' : ''}`} />
        </button>
      </div>

      {/* Filter bar */}
      <div className="ml-filter-bar">
        <input
          className="ml-filter-input"
          type="text"
          placeholder="标的代码，如 000001"
          value={symbol}
          onChange={e => { setSymbol(e.target.value); setPage(1); }}
        />
        <select
          className="ml-filter-select"
          value={result}
          onChange={e => { setResult(e.target.value); setPage(1); }}
        >
          <option value="">全部结果</option>
          {RESULT_OPTIONS.filter(Boolean).map(r => (
            <option key={r} value={r}>{resultLabel(r)}</option>
          ))}
        </select>
        <input
          className="ml-filter-date"
          type="date"
          value={dateFrom}
          onChange={e => { setDateFrom(e.target.value); setPage(1); }}
          title="起始日期"
        />
        <span className="ml-filter-sep">—</span>
        <input
          className="ml-filter-date"
          type="date"
          value={dateTo}
          onChange={e => { setDateTo(e.target.value); setPage(1); }}
          title="结束日期"
        />
      </div>

      {/* Table */}
      {loading ? (
        <div className="ml-loading">
          <i className="fas fa-spinner fa-spin" />
          <span>加载中...</span>
        </div>
      ) : error ? (
        <div className="ml-error">
          <div className="ml-error-inner">
            <i className="fas fa-exclamation-triangle" /> {error}
          </div>
        </div>
      ) : logs.length === 0 ? (
        <div className="ml-empty">
          <i className="fas fa-inbox" />
          <span>暂无日志记录</span>
        </div>
      ) : (
        <div className="ml-table-wrap">
          <table className="ml-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>标的</th>
                <th>当前层级</th>
                <th>目标层级</th>
                <th>操作</th>
                <th>结果</th>
                <th className="right">盈亏%</th>
                <th className="right">价格</th>
                <th className="right">加仓股数</th>
                <th>拦截原因</th>
              </tr>
            </thead>
            <tbody>
              {logs.map(item => (
                <>
                  <tr
                    key={item.id}
                    className={`ml-row ${expandedId === item.id ? 'ml-row--expanded' : ''}`}
                    onClick={() => toggleExpand(item.id)}
                  >
                    <td className="ml-mono dim">{fmtTime(item.timestamp)}</td>
                    <td className="bold">{item.symbol}</td>
                    <td>{item.current_tier}</td>
                    <td>{item.target_tier || '-'}</td>
                    <td>{item.action}</td>
                    <td>
                      <span className={`ml-badge ml-badge--${item.result.toLowerCase()}`}>
                        {resultLabel(item.result)}
                      </span>
                    </td>
                    <td className={`ml-mono right ${item.float_pnl_pct >= 0 ? 'up' : 'down'}`}>
                      {fmtPct(item.float_pnl_pct)}
                    </td>
                    <td className="ml-mono right">{item.current_price?.toFixed(2) || '-'}</td>
                    <td className="ml-mono right">{item.add_shares || '-'}</td>
                    <td className="dim ml-reason">{item.block_reason || '-'}</td>
                  </tr>
                  {expandedId === item.id && (
                    <tr key={`${item.id}-detail`} className="ml-detail-row">
                      <td colSpan={10}>
                        {detailLoading ? (
                          <div className="ml-detail-loading">
                            <i className="fas fa-spinner fa-spin" /> 加载详情...
                          </div>
                        ) : detail ? (
                          <DetailPanel detail={detail} />
                        ) : (
                          <div className="ml-detail-loading">加载失败</div>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="ml-pager">
          <button
            className="ml-pager-btn"
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
          >
            <i className="fas fa-chevron-left" /> 上一页
          </button>
          <span className="ml-pager-info">{page} / {totalPages}</span>
          <button
            className="ml-pager-btn"
            disabled={page >= totalPages}
            onClick={() => setPage(p => p + 1)}
          >
            下一页 <i className="fas fa-chevron-right" />
          </button>
        </div>
      )}
    </div>
  );
}

function DetailPanel({ detail }: { detail: LogDetail }) {
  const { gate_details, trend_details } = detail;
  return (
    <div className="ml-detail">
      <div className="ml-detail-grid">
        {/* Gate details */}
        <div className="ml-detail-section">
          <h4 className="ml-detail-title">
            <i className="fas fa-shield-halved" /> 门控详情
          </h4>
          <table className="ml-gate-table">
            <thead>
              <tr><th>门控</th><th>结果</th><th>详情</th></tr>
            </thead>
            <tbody>
              {(gate_details || []).map((g, i) => (
                <tr key={i} className={`ml-gate-row ml-gate--${g.status.toLowerCase()}`}>
                  <td className="bold">{g.gate}</td>
                  <td>
                    <span className={`ml-gate-badge ml-gate-badge--${g.status.toLowerCase()}`}>
                      {g.status}
                    </span>
                  </td>
                  <td className="dim">{g.detail}</td>
                </tr>
              ))}
              {(!gate_details || gate_details.length === 0) && (
                <tr><td colSpan={3} className="dim">无门控数据</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Trend details */}
        <div className="ml-detail-section">
          <h4 className="ml-detail-title">
            <i className="fas fa-chart-line" /> 趋势强度
          </h4>
          <div className="ml-trend-summary">
            <span className={trend_details.core_passed ? 'up' : 'down'}>
              核心指标: {trend_details.core_passed ? '通过' : '未通过'}
            </span>
            <span className="dim">
              辅助指标: {trend_details.aux_passed}/{trend_details.aux_total}
            </span>
          </div>
          {trend_details.checks && (
            <table className="ml-gate-table" style={{ marginTop: 8 }}>
              <thead>
                <tr><th>指标</th><th>结果</th><th>数值</th><th>阈值</th><th>详情</th></tr>
              </thead>
              <tbody>
                {Object.entries(trend_details.checks).map(([key, ck]) => {
                  if (!ck) return null;
                  return (
                    <tr key={key} className={ck.passed ? 'ml-gate--passed' : 'ml-gate--blocked'}>
                      <td className="bold">{key}</td>
                      <td>
                        <span className={`ml-gate-badge ml-gate-badge--${ck.passed ? 'passed' : 'blocked'}`}>
                          {ck.passed ? 'PASSED' : 'BLOCKED'}
                        </span>
                      </td>
                      <td className="ml-mono right">{ck.value}</td>
                      <td className="ml-mono right dim">{ck.threshold}</td>
                      <td className="dim">{ck.detail}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
