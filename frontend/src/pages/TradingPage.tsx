import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { tradesApi, portfolioApi, strategyApi, marketApi } from '../api/client';
import type { AxiosError } from 'axios';

interface TradeRecord {
  order_id: string;
  symbol: string;
  name: string;
  direction: string;
  price: number;
  volume: number;
  status: string;
  created_at: string;
  reason?: string;
  id?: number;
}

interface AccountSnapshot {
  total_asset: number;
  available_cash: number;
  position_ratio: number;
  total_pnl: number;
  realized_pnl: number;
  float_pnl: number;
}

interface PiStatus {
  stance: string;
  stance_code: string;
  position_limit: number;
}

interface QuoteData {
  price: number;
  change_pct: number;
  name: string;
}

export default function TradingPage() {
  const { t } = useTranslation();
  const [symbol, setSymbol] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [price, setPrice] = useState('');
  const [volume, setVolume] = useState('');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<TradeRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [filter, setFilter] = useState<'all' | '买入' | '卖出'>('all');
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState('');
  const [showVoidDialog, setShowVoidDialog] = useState(false);
  const [voidTarget, setVoidTarget] = useState<{ id: number; symbol: string; direction: string } | null>(null);
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  // ── 右侧辅助面板状态 ──
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [piStatus, setPiStatus] = useState<PiStatus | null>(null);
  const [quoteSymbol, setQuoteSymbol] = useState('');
  const [quoteData, setQuoteData] = useState<QuoteData | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);

  useEffect(() => {
    fetchHistory();
    fetchAccount();
    fetchPiStatus();
  }, []);

  const fetchAccount = async () => {
    try {
      const res = await portfolioApi.getSummary();
      const a = res.data.account;
      if (!a) return;
      setAccount({
        total_asset: a.total_asset,
        available_cash: a.available_cash,
        position_ratio: a.position_ratio,
        total_pnl: a.total_pnl,
        realized_pnl: a.realized_pnl,
        float_pnl: a.float_pnl,
      });
    } catch {
      // silent
    }
  };

  const fetchPiStatus = async () => {
    try {
      const res = await strategyApi.getCurrent();
      setPiStatus({
        stance: res.data.stance,
        stance_code: res.data.stance_code,
        position_limit: res.data.position_limit,
      });
    } catch {
      // silent
    }
  };

  const handleQuoteLookup = async () => {
    if (!quoteSymbol.trim()) return;
    setQuoteLoading(true);
    setQuoteError(null);
    setQuoteData(null);
    try {
      const res = await marketApi.getQuote(quoteSymbol.trim().toUpperCase());
      const d = res.data;
      setQuoteData({
        price: d.current ?? d.price ?? 0,
        change_pct: d.percent ?? d.change_pct ?? 0,
        name: d.name ?? '',
      });
    } catch {
      setQuoteError('查询失败');
    } finally {
      setQuoteLoading(false);
    }
  };

  const showToast = (type: 'success' | 'error', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const fetchHistory = async () => {
    try {
      setHistoryLoading(true);
      const res = await tradesApi.getHistory({ limit: 100 });
      setHistory(res.data.trades || []);
    } catch (err) {
      console.error('Failed to fetch history:', err);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await tradesApi.execute({
        symbol,
        side,
        price: parseFloat(price),
        volume: parseInt(volume),
        reason,
      });
      setResult(res.data);
      if (res.data.status === 'executed') {
        setSymbol('');
        setPrice('');
        setVolume('');
        setReason('');
        fetchHistory();
      }
    } catch (err) {
      const axiosError = err as AxiosError;
      const detail = (axiosError.response?.data as any)?.detail;
      setError(detail || axiosError.message);
    } finally {
      setLoading(false);
    }
  };

  const handleVoid = async () => {
    if (!voidTarget || !voidReason.trim()) return;
    setVoidingId(voidTarget.id);
    try {
      await tradesApi.voidTrade(voidTarget.id, voidReason.trim());
      showToast('success', `${voidTarget.symbol} ${voidTarget.direction} 已撤回`);
      setShowVoidDialog(false);
      setVoidTarget(null);
      setVoidReason('');
      fetchHistory();
    } catch (err: any) {
      showToast('error', err.response?.data?.detail || '撤回失败');
    } finally {
      setVoidingId(null);
    }
  };

  const handleUnvoid = async (tradeId: number) => {
    try {
      await tradesApi.unvoidTrade(tradeId);
      showToast('success', `交易 #${tradeId} 已恢复`);
      fetchHistory();
    } catch (err: any) {
      showToast('error', err.response?.data?.detail || '恢复失败');
    }
  };

  const copyOrderId = (orderId: string) => {
    navigator.clipboard.writeText(orderId);
    showToast('success', '已复制订单号');
  };

  const filteredHistory = filter === 'all'
    ? history
    : history.filter(t => t.direction === filter);

  const amount = price && volume ? parseFloat(price) * parseInt(volume) : 0;

  return (
    <div className="p-6 h-full overflow-auto">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium animate-slide-in ${
          toast.type === 'success'
            ? 'bg-emerald-600 text-white'
            : 'bg-red-600 text-white'
        }`}>
          {toast.msg}
        </div>
      )}

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold tracking-tight">{t('trading.title')}</h1>
        <button
          onClick={() => { fetchHistory(); fetchAccount(); fetchPiStatus(); }}
          className="px-3 py-1.5 text-xs rounded-lg bg-dark-100 border border-gray-700 text-gray-400 hover:text-white hover:border-gray-600 transition-colors"
        >
          🔄 刷新
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── 左侧: 执行交易 + 历史记录 ── */}
        <div className="lg:col-span-2 space-y-6">
          {/* New Trade Form */}
          <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-800 bg-dark-100/50">
              <h2 className="text-base font-semibold">{t('trading.execute')}</h2>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-5">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">{t('trading.symbol')}</label>
                  <input
                    type="text"
                    value={symbol}
                    onChange={e => setSymbol(e.target.value.toUpperCase())}
                    placeholder="SH600519"
                    className="w-full bg-dark-100 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none transition-colors"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">{t('trading.direction')}</label>
                  <div className="flex rounded-lg overflow-hidden border border-gray-700">
                    <button
                      type="button"
                      onClick={() => setSide('buy')}
                      className={`flex-1 py-2.5 text-sm font-medium transition-colors ${
                        side === 'buy'
                          ? 'bg-emerald-600 text-white'
                          : 'bg-dark-100 text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      {t('trading.buy')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setSide('sell')}
                      className={`flex-1 py-2.5 text-sm font-medium transition-colors ${
                        side === 'sell'
                          ? 'bg-red-600 text-white'
                          : 'bg-dark-100 text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      {t('trading.sell')}
                    </button>
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">{t('trading.price')}</label>
                  <input
                    type="number"
                    step="0.01"
                    value={price}
                    onChange={e => setPrice(e.target.value)}
                    placeholder="0.00"
                    className="w-full bg-dark-100 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none transition-colors"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">{t('trading.volume')}</label>
                  <input
                    type="number"
                    value={volume}
                    onChange={e => setVolume(e.target.value)}
                    placeholder="100"
                    min="100"
                    step="100"
                    className="w-full bg-dark-100 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none transition-colors"
                    required
                  />
                </div>
              </div>

          {/* Reason + Amount */}
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">{t('trading.reason')}</label>
              <input
                type="text"
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="操作原因（可选）"
                className="w-full bg-dark-100 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none transition-colors"
              />
            </div>
            {amount > 0 && (
              <div className="flex-shrink-0 px-4 py-2.5 rounded-lg bg-dark-100 border border-gray-700">
                <span className="text-xs text-gray-500 block">预估金额</span>
                <span className={`text-lg font-bold font-mono ${side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                  ¥{amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </span>
              </div>
            )}
          </div>

          {/* Result & Error */}
          {error && (
            <div className="bg-red-950/50 border border-red-800/50 rounded-lg p-3 text-red-400 text-sm flex items-start gap-2">
              <span className="flex-shrink-0 mt-0.5">⚠</span>
              <span>{error}</span>
            </div>
          )}
          {result && (
            <div className={`rounded-lg p-3 text-sm flex items-start gap-2 ${
              result.status === 'rejected' || result.status === 'failed'
                ? 'bg-red-950/50 border border-red-800/50 text-red-400'
                : 'bg-emerald-950/50 border border-emerald-800/50 text-emerald-400'
            }`}>
              <span className="flex-shrink-0 mt-0.5">
                {result.status === 'executed' ? '✓' : '✗'}
              </span>
              <span>
                {result.status === 'executed' ? (
                  <>{t('trading.orderId')}: <code className="font-mono bg-dark-100 px-1.5 py-0.5 rounded">{result.order_id}</code> — {t('common.success')}</>
                ) : (
                  <>{result.status === 'rejected' ? t('trading.rejected') : t('trading.failed')}: {result.reason || result.message || ''}</>
                )}
              </span>
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-3 pt-1">
            <button
              type="submit"
              disabled={loading}
              className={`px-5 py-2.5 rounded-lg font-semibold text-sm transition-all ${
                side === 'buy'
                  ? 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-lg shadow-emerald-900/20'
                  : 'bg-red-600 hover:bg-red-700 text-white shadow-lg shadow-red-900/20'
              } disabled:opacity-50 disabled:shadow-none`}
            >
              {loading ? t('common.loading') : t('trading.execute')}
            </button>
            <button
              type="button"
              onClick={() => {
                setSymbol('');
                setPrice('');
                setVolume('');
                setReason('');
                setResult(null);
                setError(null);
              }}
              className="px-5 py-2.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
            >
              {t('common.cancel')}
            </button>
          </div>
        </form>
      </div>

      {/* Order History */}
      <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between bg-dark-100/50">
          <h2 className="text-base font-semibold">{t('trading.orderHistory')}</h2>
          <div className="flex gap-1 bg-dark-100 rounded-lg p-0.5 border border-gray-700">
            {(['all', '买入', '卖出'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${
                  filter === f
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {f === 'all' ? '全部' : f}
              </button>
            ))}
          </div>
        </div>

        {historyLoading ? (
          <div className="p-8 space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="animate-pulse flex gap-4">
                <div className="h-4 bg-gray-800 rounded w-24" />
                <div className="h-4 bg-gray-800 rounded w-16" />
                <div className="h-4 bg-gray-800 rounded w-12" />
                <div className="h-4 bg-gray-800 rounded w-20" />
                <div className="h-4 bg-gray-800 rounded w-16" />
                <div className="h-4 bg-gray-800 rounded w-20" />
                <div className="h-4 bg-gray-800 rounded w-32" />
                <div className="h-4 bg-gray-800 rounded w-12 ml-auto" />
              </div>
            ))}
          </div>
        ) : filteredHistory.length === 0 ? (
          <div className="p-12 text-center">
            <div className="text-4xl mb-3 opacity-30">📋</div>
            <p className="text-gray-500 text-sm">
              {filter !== 'all' ? `无「${filter}」方向的交易记录` : '暂无交易记录'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-800 bg-dark-100/30">
                  <th className="px-4 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">标的</th>
                  <th className="px-4 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">名称</th>
                  <th className="px-4 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">方向</th>
                  <th className="px-4 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">价格</th>
                  <th className="px-4 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">数量</th>
                  <th className="px-4 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">金额</th>
                  <th className="px-4 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">原因</th>
                  <th className="px-4 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">时间</th>
                  <th className="px-4 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {filteredHistory.map((trade: TradeRecord, idx: number) => {
                  const tradeAmount = trade.price * trade.volume;
                  return (
                    <tr
                      key={idx}
                      className="hover:bg-dark-100/50 transition-colors group"
                    >
                      <td className="px-5 py-3.5">
                        <button
                          onClick={() => { navigator.clipboard.writeText(trade.symbol); showToast('success', '已复制股票代码'); }}
                          className="font-mono text-sm font-medium text-gray-300 hover:text-blue-400 transition-colors cursor-pointer"
                          title="点击复制股票代码"
                        >
                          {trade.symbol}
                        </button>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="text-sm text-gray-400">
                          {trade.name && trade.name !== trade.symbol ? trade.name : '-'}
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                          trade.direction === '买入'
                            ? 'bg-emerald-950/50 text-emerald-400'
                            : 'bg-red-950/50 text-red-400'
                        }`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${
                            trade.direction === '买入' ? 'bg-emerald-400' : 'bg-red-400'
                          }`} />
                          {trade.direction}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 font-mono text-sm text-right">
                        ¥{trade.price.toFixed(2)}
                      </td>
                      <td className="px-5 py-3.5 text-sm text-right">
                        {trade.volume.toLocaleString()}
                      </td>
                      <td className="px-5 py-3.5 font-mono text-sm text-right text-gray-400">
                        ¥{tradeAmount.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-5 py-3.5 text-xs text-gray-500 max-w-[120px] truncate" title={trade.reason || ''}>
                        {trade.reason || '-'}
                      </td>
                      <td className="px-5 py-3.5 text-xs text-gray-500 whitespace-nowrap">
                        {new Date(trade.created_at).toLocaleString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                        })}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <button
                          onClick={() => {
                            setVoidTarget({
                              id: trade.id || 0,
                              symbol: trade.symbol,
                              direction: trade.direction,
                            });
                            setVoidReason('');
                            setShowVoidDialog(true);
                          }}
                          className="opacity-0 group-hover:opacity-100 px-2 py-1 text-xs text-red-400 hover:bg-red-950/50 rounded transition-all"
                          title="撤回此交易"
                        >
                          撤回
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

        </div>

        {/* ── 右侧: 交易辅助面板 ── */}
        <div className="space-y-4">
          {/* Pi 立场卡片 */}
          <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800 bg-dark-100/50">
              <h3 className="text-sm font-semibold">Pi 立场</h3>
            </div>
            <div className="p-4">
              {piStatus ? (
                <div className="flex items-center gap-3">
                  <div className={`w-4 h-4 rounded-full shadow-lg ${
                    piStatus.stance === 'green' ? 'bg-emerald-400 shadow-emerald-400/30' :
                    piStatus.stance === 'red' ? 'bg-red-400 shadow-red-400/30' :
                    'bg-yellow-400 shadow-yellow-400/30'
                  }`} />
                  <div>
                    <span className={`text-lg font-bold uppercase ${
                      piStatus.stance === 'green' ? 'text-emerald-400' :
                      piStatus.stance === 'red' ? 'text-red-400' :
                      'text-yellow-400'
                    }`}>
                      {piStatus.stance === 'green' ? 'GREEN 积极' :
                       piStatus.stance === 'red' ? 'RED 防守' :
                       'YELLOW 中性'}
                    </span>
                    <div className="text-xs text-gray-500 mt-0.5">
                      仓位限制: {piStatus.position_limit}%
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <div className="w-4 h-4 rounded-full bg-gray-600 animate-pulse" />
                  <span className="text-sm text-gray-500">加载中...</span>
                </div>
              )}
            </div>
          </div>

          {/* 账户快照卡片 */}
          <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800 bg-dark-100/50">
              <h3 className="text-sm font-semibold">账户快照</h3>
            </div>
            <div className="p-4 space-y-3">
              {account ? (
                <>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">总资产</span>
                    <span className="text-sm font-mono font-semibold text-white">
                      ¥{account.total_asset.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">可用现金</span>
                    <span className="text-sm font-mono text-emerald-400">
                      ¥{account.available_cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">仓位占比</span>
                    <div className="flex items-center gap-2">
                      <div className="w-20 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            (account.position_ratio ?? 0) > 80 ? 'bg-red-500' :
                            (account.position_ratio ?? 0) > 50 ? 'bg-yellow-500' :
                            'bg-emerald-500'
                          }`}
                          style={{ width: `${Math.min(account.position_ratio ?? 0, 100)}%` }}
                        />
                      </div>
                      <span className="text-sm font-mono text-gray-300">
                        {(account.position_ratio ?? 0).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                  <hr className="border-gray-800" />
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">累计盈亏</span>
                    <span className={`text-sm font-mono font-semibold ${(account.total_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {(account.total_pnl ?? 0) >= 0 ? '+' : ''}¥{(account.total_pnl ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">已实现</span>
                    <span className={`text-xs font-mono ${(account.realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {(account.realized_pnl ?? 0) >= 0 ? '+' : ''}¥{(account.realized_pnl ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-500">浮动盈亏</span>
                    <span className={`text-xs font-mono ${(account.float_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {(account.float_pnl ?? 0) >= 0 ? '+' : ''}¥{(account.float_pnl ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                </>
              ) : (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="flex justify-between animate-pulse">
                      <div className="h-3 bg-gray-800 rounded w-16" />
                      <div className="h-3 bg-gray-800 rounded w-24" />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 快速报价卡片 */}
          <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800 bg-dark-100/50">
              <h3 className="text-sm font-semibold">快速报价</h3>
            </div>
            <div className="p-4 space-y-3">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={quoteSymbol}
                  onChange={e => setQuoteSymbol(e.target.value.toUpperCase())}
                  onKeyDown={e => e.key === 'Enter' && handleQuoteLookup()}
                  placeholder="SH600519"
                  className="flex-1 bg-dark-100 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none transition-colors"
                />
                <button
                  onClick={handleQuoteLookup}
                  disabled={quoteLoading || !quoteSymbol.trim()}
                  className="px-3 py-2 text-xs font-medium bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg transition-colors"
                >
                  {quoteLoading ? '...' : '查询'}
                </button>
              </div>

              {quoteError && (
                <div className="text-xs text-red-400">{quoteError}</div>
              )}

              {quoteData && (
                <div className="bg-dark-100 rounded-lg p-3 space-y-2">
                  {quoteData.name && (
                    <div className="text-xs text-gray-400">{quoteData.name}</div>
                  )}
                  <div className="flex items-baseline justify-between">
                    <span className="text-xl font-mono font-bold text-white">
                      ¥{quoteData.price.toFixed(2)}
                    </span>
                    <span className={`text-sm font-mono font-semibold px-2 py-0.5 rounded ${
                      quoteData.change_pct >= 0
                        ? 'bg-emerald-950/50 text-emerald-400'
                        : 'bg-red-950/50 text-red-400'
                    }`}>
                      {quoteData.change_pct >= 0 ? '+' : ''}{quoteData.change_pct.toFixed(2)}%
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Void Confirmation Dialog */}
      {showVoidDialog && voidTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowVoidDialog(false)} />
          <div className="relative bg-dark-200 border border-gray-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h3 className="text-lg font-semibold mb-2">撤回交易</h3>
            <p className="text-sm text-gray-400 mb-4">
              将撤回 <span className="font-mono text-white">{voidTarget.symbol}</span> 的
              <span className={voidTarget.direction === '买入' ? 'text-emerald-400' : 'text-red-400'}>
                {' '}{voidTarget.direction}
              </span> 交易。
              撤回后该交易将不计入持仓计算，但记录保留可恢复。
            </p>
            <label className="block text-xs text-gray-500 mb-1.5 uppercase tracking-wider">撤回原因</label>
            <textarea
              value={voidReason}
              onChange={e => setVoidReason(e.target.value)}
              placeholder="例如：重复止盈、超仓加仓等 bug 导致"
              rows={2}
              className="w-full bg-dark-100 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:border-blue-500 focus:outline-none resize-none transition-colors"
              autoFocus
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => setShowVoidDialog(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleVoid}
                disabled={voidingId !== null || !voidReason.trim()}
                className="px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-700 text-white rounded-lg disabled:opacity-50 transition-colors"
              >
                {voidingId !== null ? '撤回中...' : '确认撤回'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
