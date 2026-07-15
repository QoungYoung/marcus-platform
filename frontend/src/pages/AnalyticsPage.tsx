import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Play, Copy, Check, ChevronDown, ChevronRight, Clock, X, Loader2, Wrench } from 'lucide-react';
import axios from 'axios';
import type { ToolDef, ToolCategory, ParamDef } from '../utils/toolRegistry';
import { ALL_TOOLS, CATEGORY_LABELS, getToolsByCategory, searchTools } from '../utils/toolRegistry';

const API_BASE = '/api/v1';

// ─── 股票代码自动补全 ───

interface StockOption { ts_code: string; symbol: string; name: string }

async function fetchStockSuggestions(query: string): Promise<StockOption[]> {
  if (!query || query.length < 1) return [];
  try {
    const clean = query.replace(/[.\s]/g, '').toUpperCase();
    const numeric = clean.replace(/^(SH|SZ|BJ)/, '');
    const where = `ts_code LIKE '%${numeric}%' OR symbol LIKE '%${numeric}%' OR name LIKE '%${query}%'`;
    const resp = await axios.get(`${API_BASE}/db/query`, {
      params: { db: 'stock_pool.db', table: 'stock_pool', columns: 'ts_code,symbol,name', where, limit: 10 },
    });
    const rows = resp.data?.rows || [];
    return [...new Map(rows.map((r: any) => [r.ts_code || r.symbol, r])).values()] as StockOption[];
  } catch {
    return [];
  }
}

// ─── 股票代码标准化 ───
// 将各种格式统一转换为 SZ000739 格式（Xueqiu 格式，不带点）
function normalizeStockCode(input: string): string {
  const raw = input.trim().toUpperCase();
  if (!raw) return raw;
  // 已经是 SZ000739 / SH600519 格式
  if (/^(SH|SZ|BJ)\d{6}$/.test(raw)) return raw;
  // 000739.SZ → SZ000739
  const dotMatch = raw.match(/^(\d{6})\.(SH|SZ|BJ)$/);
  if (dotMatch) return dotMatch[2] + dotMatch[1];
  // 纯数字
  if (/^\d{6}$/.test(raw)) {
    if (raw.startsWith('6') || raw.startsWith('9')) return 'SH' + raw;
    if (raw.startsWith('0') || raw.startsWith('3')) return 'SZ' + raw;
    if (raw.startsWith('4') || raw.startsWith('8')) return 'BJ' + raw;
  }
  return raw;
}

// ─── StockCodeInput 组件 ───

function StockCodeInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [open, setOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<StockOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const handleInputChange = useCallback(async (text: string) => {
    onChange(text);
    if (text.trim().length >= 1) {
      setLoading(true);
      setOpen(true);
      const results = await fetchStockSuggestions(text);
      setSuggestions(results);
      setHighlightIdx(-1);
      setLoading(false);
    } else {
      setOpen(false);
    }
  }, [onChange]);

  // 失焦时自动标准化代码格式
  const handleBlur = useCallback(() => {
    if (value) {
      const normalized = normalizeStockCode(value);
      if (normalized !== value) onChange(normalized);
    }
  }, [value, onChange]);

  const selectStock = useCallback((opt: StockOption) => {
    onChange(normalizeStockCode(opt.ts_code || opt.symbol || ''));
    setOpen(false);
  }, [onChange]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlightIdx(i => Math.min(i + 1, suggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlightIdx(i => Math.max(i - 1, -1)); }
    else if (e.key === 'Enter' && highlightIdx >= 0) { e.preventDefault(); selectStock(suggestions[highlightIdx]); }
    else if (e.key === 'Escape') setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={e => handleInputChange(e.target.value)}
          onFocus={() => value.trim().length >= 1 && setOpen(true)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder={placeholder || 'SH600519'}
          className="w-full bg-dark-300 border border-gray-700 rounded-lg px-3 py-2 text-sm
                     text-foreground placeholder:text-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
        />
        {loading && <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 animate-spin" />}
        {!loading && value && (
          <button onClick={() => { onChange(''); setOpen(false); }} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
            <X size={14} />
          </button>
        )}
      </div>
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 mt-1 w-full bg-dark-200 border border-gray-700 rounded-lg shadow-xl max-h-48 overflow-y-auto">
          {suggestions.map((s, i) => (
            <button
              key={s.ts_code || s.symbol}
              onClick={() => selectStock(s)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-dark-100 transition-colors flex items-center justify-between
                ${i === highlightIdx ? 'bg-dark-100' : ''}`}
            >
              <span className="text-foreground font-mono">{s.ts_code || s.symbol}</span>
              <span className="text-gray-400 text-xs truncate ml-2">{s.name}</span>
            </button>
          ))}
        </div>
      )}
      {open && !loading && suggestions.length === 0 && value.trim().length >= 1 && (
        <div className="absolute z-50 mt-1 w-full bg-dark-200 border border-gray-700 rounded-lg shadow-xl p-3 text-sm text-gray-500">
          未找到匹配股票
        </div>
      )}
    </div>
  );
}

// ─── 主页面 ───

export default function AnalyticsPage() {
  const { t } = useTranslation();

  // 状态
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedCats, setCollapsedCats] = useState<Set<ToolCategory>>(new Set());
  const [selectedTool, setSelectedTool] = useState<ToolDef | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<any>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const groupedTools = useMemo(() => getToolsByCategory(), []);
  const filteredTools = useMemo(() => searchQuery ? searchTools(searchQuery) : null, [searchQuery]);

  // 切换分类折叠
  const toggleCat = (cat: ToolCategory) => {
    setCollapsedCats(prev => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  // 选择工具
  const selectTool = (tool: ToolDef) => {
    setSelectedTool(tool);
    setResponse(null);
    setError(null);
    setResponseTime(null);
    // 初始化参数默认值
    const defaults: Record<string, any> = {};
    for (const [key, param] of Object.entries(tool.parameters)) {
      if (param.default !== undefined) defaults[key] = param.default;
    }
    setParamValues(defaults);
  };

  // 更新参数值
  const updateParam = (key: string, value: any) => {
    setParamValues(prev => ({ ...prev, [key]: value }));
  };

  // 执行工具调用
  const executeTool = async () => {
    if (!selectedTool) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    const startTime = performance.now();

    try {
      let url: string = selectedTool.endpoint.path;
      const method = selectedTool.endpoint.method;
      const queryParams: Record<string, string> = {};
      let body: any = null;

      // 处理参数
      for (const [key, param] of Object.entries(selectedTool.parameters)) {
        const val = paramValues[key];
        if (val === undefined || val === '' || val === null) continue;

        if (selectedTool.pathParamNames.includes(key)) {
          url = url.replace(`{${key}}`, encodeURIComponent(String(val)));
        } else if (method === 'POST' || method === 'DELETE') {
          if (!body) body = {};
          if (param.type === 'date') {
            body[key] = val.replace(/-/g, ''); // YYYY-MM-DD → YYYYMMDD
          } else if (param.type === 'number') {
            body[key] = Number(val);
          } else {
            body[key] = val;
          }
        } else {
          if (param.type === 'date') {
            queryParams[key] = val.replace(/-/g, '');
          } else {
            queryParams[key] = String(val);
          }
        }
      }

      // 对 GET 请求，未在 path 中的参数作为 query string
      // （以上已处理，URL 中未替换的 {param} 需要处理——移除它们，因为没有值）
      url = url.replace(/\/\{[^}]+\}/g, '');

      const qs = Object.keys(queryParams).length > 0
        ? '?' + new URLSearchParams(queryParams).toString()
        : '';

      const fullUrl = `${API_BASE}${url}${qs}`;

      let result: any;
      switch (method) {
        case 'DELETE':
          result = await axios.delete(fullUrl);
          break;
        case 'POST':
          result = await axios.post(fullUrl, body);
          break;
        case 'PUT':
          result = await axios.put(fullUrl, body);
          break;
        default:
          result = await axios.get(fullUrl);
      }

      const elapsed = performance.now() - startTime;
      setResponseTime(Math.round(elapsed));
      setResponse(result.data);
    } catch (e: any) {
      const elapsed = performance.now() - startTime;
      setResponseTime(Math.round(elapsed));
      const errData = e.response?.data;
      setError(
        errData?.detail || errData?.error || e.message || '请求失败'
      );
      setResponse(errData || { error: e.message });
    } finally {
      setLoading(false);
    }
  };

  // 复制结果
  const copyResult = async () => {
    if (!response) return;
    await navigator.clipboard.writeText(JSON.stringify(response, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // 渲染参数输入
  const renderParamInput = (key: string, param: ParamDef) => {
    const value = paramValues[key] ?? '';
    const required = param.required;

    switch (param.type) {
      case 'stock_code':
        return (
          <StockCodeInput
            value={String(value)}
            onChange={v => updateParam(key, v)}
            placeholder={param.placeholder}
          />
        );

      case 'date':
        return (
          <input
            type="date"
            value={String(value)}
            onChange={e => updateParam(key, e.target.value)}
            className="w-full bg-dark-300 border border-gray-700 rounded-lg px-3 py-2 text-sm
                       text-foreground placeholder:text-gray-500 focus:outline-none focus:border-blue-500 transition-colors
                       [color-scheme:dark]"
          />
        );

      case 'select':
        return (
          <select
            value={String(value)}
            onChange={e => updateParam(key, e.target.value)}
            className="w-full bg-dark-300 border border-gray-700 rounded-lg px-3 py-2 text-sm
                       text-foreground focus:outline-none focus:border-blue-500 transition-colors"
          >
            <option value="">-- 请选择 --</option>
            {param.options?.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        );

      case 'number':
        return (
          <input
            type="number"
            value={value === undefined || value === null ? '' : value}
            onChange={e => updateParam(key, e.target.value === '' ? '' : e.target.value)}
            placeholder={param.placeholder}
            step="any"
            className="w-full bg-dark-300 border border-gray-700 rounded-lg px-3 py-2 text-sm
                       text-foreground placeholder:text-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
        );

      case 'boolean':
        return (
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={!!value}
              onChange={e => updateParam(key, e.target.checked)}
              className="w-4 h-4 rounded border-gray-600 bg-dark-300 text-blue-500 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-400">{value ? 'True' : 'False'}</span>
          </label>
        );

      default:
        return (
          <input
            type="text"
            value={String(value)}
            onChange={e => updateParam(key, e.target.value)}
            placeholder={param.placeholder}
            className="w-full bg-dark-300 border border-gray-700 rounded-lg px-3 py-2 text-sm
                       text-foreground placeholder:text-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
        );
    }
  };

  // ─── Render ───

  return (
    <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
      {/* ── 左侧边栏 ── */}
      <aside className="w-72 flex-shrink-0 border-r border-gray-800 bg-dark-100 flex flex-col">
        {/* 搜索框 */}
        <div className="p-3 border-b border-gray-800">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={t('tools.searchPlaceholder')}
              className="w-full bg-dark-200 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm
                         text-foreground placeholder:text-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>
        </div>

        {/* 工具列表 */}
        <div className="flex-1 overflow-y-auto">
          {filteredTools ? (
            // 搜索模式：平铺结果
            <div className="p-2">
              {filteredTools.length === 0 ? (
                <p className="text-sm text-gray-500 p-3 text-center">{t('tools.noToolsFound')}</p>
              ) : (
                filteredTools.map(tool => (
                  <ToolItem
                    key={tool.name}
                    tool={tool}
                    active={selectedTool?.name === tool.name}
                    onClick={() => selectTool(tool)}
                  />
                ))
              )}
            </div>
          ) : (
            // 分类模式
            (Object.entries(groupedTools) as [ToolCategory, ToolDef[]][]).map(([cat, tools]) => {
              if (tools.length === 0) return null;
              const collapsed = collapsedCats.has(cat);
              return (
                <div key={cat} className="border-b border-gray-800/50 last:border-b-0">
                  <button
                    onClick={() => toggleCat(cat)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold text-gray-400
                               hover:text-gray-200 hover:bg-dark-200 transition-colors uppercase tracking-wide"
                  >
                    {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                    {t(`tools.categoryLabels.${cat}`)}
                    <span className="ml-auto text-gray-600">{tools.length}</span>
                  </button>
                  {!collapsed && (
                    <div className="pb-1">
                      {tools.map(tool => (
                        <ToolItem
                          key={tool.name}
                          tool={tool}
                          active={selectedTool?.name === tool.name}
                          onClick={() => selectTool(tool)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* ── 右侧主面板 ── */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {!selectedTool ? (
          /* 未选择工具时的占位 */
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-4">
              <div className="w-16 h-16 rounded-2xl bg-dark-200 border border-gray-700 flex items-center justify-center mx-auto">
                <Wrench size={28} className="text-gray-500" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">{t('tools.selectTool')}</h2>
                <p className="text-sm text-gray-500 mt-1">{t('tools.selectToolDesc')}</p>
              </div>
            </div>
          </div>
        ) : (
          /* 工具详情 */
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* 工具头部 */}
            <div className="px-6 py-4 border-b border-gray-800 bg-dark-100">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-2 py-0.5 text-[10px] font-mono text-blue-400 bg-blue-400/10 rounded">
                      {selectedTool.name}
                    </span>
                    <span className="text-xs text-gray-500">{selectedTool.endpoint.method}</span>
                    <span className="text-xs text-gray-600 font-mono">{selectedTool.endpoint.path}</span>
                  </div>
                  <p className="text-sm text-gray-400 leading-relaxed">{selectedTool.description}</p>
                </div>
                <button
                  onClick={executeTool}
                  disabled={loading}
                  className="ml-4 flex-shrink-0 flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700
                             disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium
                             transition-colors"
                >
                  {loading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Play size={14} />
                  )}
                  {loading ? t('tools.executing') : t('tools.execute')}
                </button>
              </div>
            </div>

            {/* 内容区：参数 + 响应 */}
            <div className="flex-1 overflow-y-auto">
              <div className="p-6 space-y-6">
                {/* 参数表单 */}
                {Object.keys(selectedTool.parameters).length > 0 && (
                  <div className="bg-dark-200 rounded-lg border border-gray-800 p-5">
                    <h3 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
                      {t('tools.parameters')}
                      <span className="text-[10px] text-gray-500 font-normal">
                        ({Object.values(selectedTool.parameters).filter(p => p.required).length} {t('tools.required')}, {Object.values(selectedTool.parameters).filter(p => !p.required).length} {t('tools.optional')})
                      </span>
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {Object.entries(selectedTool.parameters).map(([key, param]) => (
                        <div key={key} className={param.type === 'string' && param.description.length > 30 ? 'md:col-span-2' : ''}>
                          <label className="block mb-1.5">
                            <span className="text-sm font-medium text-foreground font-mono">{key}</span>
                            {param.required ? (
                              <span className="ml-1.5 text-[10px] text-red-400 font-semibold">*{t('tools.required')}</span>
                            ) : (
                              <span className="ml-1.5 text-[10px] text-gray-600">{t('tools.optional')}</span>
                            )}
                          </label>
                          <p className="text-[11px] text-gray-500 mb-1.5 leading-tight">{param.description}</p>
                          {renderParamInput(key, param)}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 无参数工具提示 */}
                {Object.keys(selectedTool.parameters).length === 0 && (
                  <div className="bg-dark-200 rounded-lg border border-gray-800 p-5">
                    <p className="text-sm text-gray-500 text-center">此工具无需参数</p>
                  </div>
                )}

                {/* 响应结果 */}
                {(response || error || loading) && (
                  <div className="bg-dark-200 rounded-lg border border-gray-800 overflow-hidden">
                    <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
                      <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                        {t('tools.response')}
                        {loading && <Loader2 size={12} className="animate-spin text-blue-400" />}
                        {error && !loading && <span className="text-[10px] text-red-400 font-normal">({error})</span>}
                      </h3>
                      <div className="flex items-center gap-3">
                        {responseTime !== null && (
                          <span className="flex items-center gap-1 text-xs text-gray-500">
                            <Clock size={11} />
                            {responseTime}ms
                          </span>
                        )}
                        {response && (
                          <button
                            onClick={copyResult}
                            className="flex items-center gap-1 text-xs text-gray-400 hover:text-foreground transition-colors"
                          >
                            {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
                            {copied ? t('tools.copied') : t('tools.copyResult')}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="p-5">
                      {loading ? (
                        <div className="flex items-center justify-center py-12">
                          <Loader2 size={24} className="animate-spin text-blue-400" />
                        </div>
                      ) : (
                        <pre className="text-xs text-foreground font-mono whitespace-pre-wrap break-all max-h-96 overflow-y-auto
                                        bg-dark-300 rounded-lg p-4 leading-relaxed">
                          {JSON.stringify(response, null, 2)}
                        </pre>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

// ─── 工具列表项 ───

function ToolItem({ tool, active, onClick }: { tool: ToolDef; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-2.5 transition-colors group ${
        active
          ? 'bg-blue-600/10 border-l-2 border-blue-500'
          : 'border-l-2 border-transparent hover:bg-dark-200'
      }`}
    >
      <div className={`text-sm font-medium truncate ${active ? 'text-blue-400' : 'text-foreground group-hover:text-gray-200'}`}>
        {tool.label}
      </div>
      <div className="text-[11px] text-gray-600 truncate mt-0.5">{tool.name}</div>
    </button>
  );
}
