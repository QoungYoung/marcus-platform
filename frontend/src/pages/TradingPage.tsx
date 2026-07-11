import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { tradesApi } from '../api/client';
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

  useEffect(() => {
    fetchHistory();
  }, []);

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
    <div className="p-6 space-y-6 h-full overflow-auto">
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

      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">{t('trading.title')}</h1>
        <button
          onClick={fetchHistory}
          className="px-3 py-1.5 text-xs rounded-lg bg-dark-100 border border-gray-700 text-gray-400 hover:text-white hover:border-gray-600 transition-colors"
        >
          🔄 刷新
        </button>
      </div>

      {/* New Trade Form */}
      <div className="bg-dark-200 rounded-xl border border-gray-800 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-800 bg-dark-100/50">
          <h2 className="text-base font-semibold">{t('trading.execute')}</h2>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-5">
          <div className="grid grid-cols-4 gap-4">
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
                  <th className="px-5 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">订单号</th>
                  <th className="px-5 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">标的</th>
                  <th className="px-5 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">方向</th>
                  <th className="px-5 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">价格</th>
                  <th className="px-5 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">数量</th>
                  <th className="px-5 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">金额</th>
                  <th className="px-5 py-3 text-left text-xs text-gray-500 font-medium uppercase tracking-wider">时间</th>
                  <th className="px-5 py-3 text-right text-xs text-gray-500 font-medium uppercase tracking-wider">操作</th>
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
                          onClick={() => copyOrderId(trade.order_id)}
                          className="font-mono text-xs text-gray-400 hover:text-blue-400 transition-colors cursor-pointer"
                          title="点击复制"
                        >
                          {trade.order_id.length > 16
                            ? trade.order_id.slice(0, 8) + '...' + trade.order_id.slice(-4)
                            : trade.order_id}
                        </button>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="font-mono text-sm font-medium">{trade.symbol}</span>
                        {trade.name && trade.name !== trade.symbol && (
                          <span className="text-xs text-gray-600 ml-1.5">{trade.name}</span>
                        )}
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
