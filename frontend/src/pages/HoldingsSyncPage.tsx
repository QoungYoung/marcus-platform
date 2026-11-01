import { useState } from 'react';
import '../styles/t-account-page.css';

const API = '/api/v1/t';
const ACCOUNT = 't';

interface ParsedRow {
  name?: string | null;
  code?: string | null;
  volume?: number | null;
  avg_price?: number | null;
  symbol?: string | null;
  status?: string;
}

export default function HoldingsSyncPage() {
  const [text, setText] = useState('');
  const [rows, setRows] = useState<ParsedRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');

  const validRows = (rows || []).filter((r) => r.status === 'ok' && r.symbol && (r.volume || 0) > 0);
  const unresolved = (rows || []).filter((r) => r.status !== 'ok');

  const parse = async () => {
    if (!text.trim()) { setError('请先粘贴持仓文本'); return; }
    setBusy(true); setError(''); setMsg('');
    try {
      const res = await fetch(`${API}/holdings/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, account: ACCOUNT }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || res.statusText);
      setRows(j.parsed || []);
      setMsg(`识别完成：共 ${j.count ?? 0} 条，请核对预览后确认同步`);
    } catch (e) {
      setError(`解析失败: ${e instanceof Error ? e.message : String(e)}`);
      setRows(null);
    } finally {
      setBusy(false);
    }
  };

  const sync = async () => {
    if (!validRows.length) { setError('没有可同步的有效持仓（请先解析并核对代码）'); return; }
    if (!window.confirm(`确认用这 ${validRows.length} 条解析结果整体替换 ${ACCOUNT} 账户系统持仓？原持仓会先备份到审计文件。`)) return;
    setBusy(true); setError(''); setMsg('');
    try {
      const res = await fetch(`${API}/holdings/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account: ACCOUNT,
          positions: validRows.map((r) => ({ symbol: r.symbol, volume: r.volume, avg_price: r.avg_price || 0 })),
        }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || res.statusText);
      setMsg(`已同步：替换 ${j.replaced} 条，写入 ${j.inserted} 条（原持仓已审计备份）`);
    } catch (e) {
      setError(`同步失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const clearAll = () => { setText(''); setRows(null); setError(''); setMsg(''); };

  return (
    <div className="tac-page">
      <header className="tac-header">
        <div className="tac-header-title">
          <h2>持仓同步 · 实际 vs 系统</h2>
          <span className="tac-sign">Marcus · Holdings Sync</span>
        </div>
        <div className="tac-header-right">
          <span className="tac-risk-flag tac-risk-soft">目标账户：{ACCOUNT}（做T账户）</span>
        </div>
      </header>

      {error && <div className="tac-error">{error}</div>}
      {msg && <div className="tac-msg">✅ {msg}</div>}
      {busy && <div className="tac-loading">处理中…（AI 识别约 10-60 秒）</div>}

      <div className="tac-canvas">
        <div className="tac-panels">
          <section className="tac-panel">
            <div className="tac-panel-head">
              <span className="tac-panel-title">① 粘贴实际持仓</span>
              <div className="tac-toolbar">
                <button className="tac-btn" onClick={parse} disabled={busy}>AI 识别</button>
                <button className="tac-btn" onClick={sync} disabled={busy || !validRows.length}>② 确认同步到库</button>
                <button className="tac-btn" onClick={clearAll} disabled={busy}>清空</button>
              </div>
            </div>
            <div className="tac-panel-body">
              <textarea
                className="tac-input hs-textarea"
                rows={8}
                aria-label="实际持仓文本"
                placeholder={'粘贴实际持仓，例如：\n科创50ETF 588000 20000份 成本1.80\n火炬电子 100股 成本48.51'}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <span className="tac-hint">每行一条，名称 / 6 位代码 / 数量 / 成本均可；AI 识别后先出预览，不直接落库。</span>
            </div>
          </section>

          {rows && (
            <section className="tac-panel">
              <div className="tac-panel-head">
                <span className="tac-panel-title">② 识别预览</span>
                <span className="tac-panel-hint">
                  识别 {rows.length} 条 · 可同步 {validRows.length} 条 · 未匹配 {unresolved.length} 条
                </span>
              </div>
              <div className="tac-panel-body">
                <div className="tac-cards">
                  <div className="tac-card">
                    <div className="tac-card-label">识别条数</div>
                    <div className="tac-card-value">{rows.length}</div>
                  </div>
                  <div className="tac-card">
                    <div className="tac-card-label">可同步</div>
                    <div className="tac-card-value">{validRows.length}</div>
                  </div>
                  <div className="tac-card">
                    <div className="tac-card-label">未匹配（不写入）</div>
                    <div className="tac-card-value">{unresolved.length}</div>
                  </div>
                </div>
                <table className="tac-table tac-table-tight">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>数量</th>
                      <th>成本</th>
                      <th>系统代码</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        <td>{r.name || '-'}</td>
                        <td>{(r.volume || 0).toLocaleString()}</td>
                        <td>{r.avg_price != null ? Number(r.avg_price).toFixed(3) : '-'}</td>
                        <td className="tac-sym">{r.symbol || '-'}</td>
                        <td>
                          {r.status === 'ok'
                            ? <span className="hs-rule-ok">✅ 已识别</span>
                            : <span className="hs-rule-warn">⚠️ 未匹配</span>}
                        </td>
                      </tr>
                    ))}
                    {rows.length === 0 && <tr><td colSpan={5} className="tac-empty">未识别到持仓，请检查粘贴内容</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="tac-panel">
            <div className="tac-panel-head">
              <span className="tac-panel-title">③ 同步规则</span>
            </div>
            <div className="tac-panel-body">
              <ol className="hs-steps">
                <li><b>整体替换</b>：确认后用本次解析结果替换 {ACCOUNT} 账户全部系统持仓，不是增量追加。</li>
                <li><b>先备份再写库</b>：原持仓写入 <code>data/holdings_sync_history.jsonl</code>，可追溯。</li>
                <li><b>未匹配不写入</b>：只有状态为「已识别」且有系统代码的行会落库，请先核对代码与成本。</li>
                <li><b>AI 只是辅助</b>：识别结果可能出错，确认前请逐行核对数量与成本。</li>
              </ol>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
