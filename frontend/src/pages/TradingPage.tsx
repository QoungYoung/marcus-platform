import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { tradesApi } from '../api/client';
import type { AxiosError } from 'axios';

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
  const [history, setHistory] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  useEffect(() => {
    fetchHistory();
  }, []);

  const fetchHistory = async () => {
    try {
      const res = await tradesApi.getHistory({ limit: 50 });
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
    } catch (err) {
      const axiosError = err as AxiosError;
      const detail = (axiosError.response?.data as any)?.detail;
      setError(detail || axiosError.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-6 h-full overflow-auto">
      <h1 className="text-2xl font-bold">{t('trading.title')}</h1>

      {/* New Trade Form */}
      <div className="bg-dark-200 rounded-lg border border-gray-800 p-6">
        <h2 className="text-lg font-semibold mb-4">{t('trading.execute')}</h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">{t('trading.symbol')}</label>
              <input
                type="text"
                value={symbol}
                onChange={e => setSymbol(e.target.value.toUpperCase())}
                placeholder="e.g. SH600519"
                className="w-full bg-dark-100 border border-gray-700 rounded px-3 py-2 text-white"
                required
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">{t('trading.direction')}</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="side"
                    value="buy"
                    checked={side === 'buy'}
                    onChange={() => setSide('buy')}
                    className="accent-green-500"
                  />
                  <span className={side === 'buy' ? 'text-green-400' : 'text-gray-400'}>{t('trading.buy')}</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="side"
                    value="sell"
                    checked={side === 'sell'}
                    onChange={() => setSide('sell')}
                    className="accent-red-500"
                  />
                  <span className={side === 'sell' ? 'text-red-400' : 'text-gray-400'}>{t('trading.sell')}</span>
                </label>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">{t('trading.price')} (CNY)</label>
              <input
                type="number"
                step="0.01"
                value={price}
                onChange={e => setPrice(e.target.value)}
                placeholder="0.00"
                className="w-full bg-dark-100 border border-gray-700 rounded px-3 py-2 text-white"
                required
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">{t('trading.volume')}</label>
              <input
                type="number"
                value={volume}
                onChange={e => setVolume(e.target.value)}
                placeholder="100"
                min="100"
                step="100"
                className="w-full bg-dark-100 border border-gray-700 rounded px-3 py-2 text-white"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-1">{t('trading.reason')} ({t('common.cancel')})</label>
            <input
              type="text"
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder={t('trading.reason')}
              className="w-full bg-dark-100 border border-gray-700 rounded px-3 py-2 text-white"
            />
          </div>

          {/* Estimated Cost */}
          {price && volume && (
            <div className="bg-dark-100 rounded p-3 text-sm">
              <span className="text-gray-400">{t('trading.amount')}: </span>
              <span className="font-semibold">
                ¥{(parseFloat(price) * parseInt(volume)).toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {error && (
            <div className="bg-red-900/20 border border-red-800 rounded p-3 text-red-400 text-sm">
              {error}
            </div>
          )}

          {result && (
            <div className="bg-green-900/20 border border-green-800 rounded p-3 text-green-400 text-sm">
              {t('trading.orderId')}: {result.order_id} - {t('common.success')}!
            </div>
          )}

          <div className="flex gap-3">
            <button
              type="submit"
              disabled={loading}
              className={`px-6 py-2 rounded font-semibold transition-colors ${
                side === 'buy'
                  ? 'bg-green-600 hover:bg-green-700 text-white'
                  : 'bg-red-600 hover:bg-red-700 text-white'
              } disabled:opacity-50`}
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
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 rounded text-white"
            >
              {t('common.cancel')}
            </button>
          </div>
        </form>
      </div>

      {/* Recent Trades */}
      <div className="bg-dark-200 rounded-lg border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">{t('trading.orderHistory')}</h2>
        </div>
        {historyLoading ? (
          <div className="p-6 text-gray-500 text-sm">{t('common.loading')}</div>
        ) : history.length === 0 ? (
          <div className="p-6 text-gray-500 text-sm">{t('common.noData')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-gray-400 border-b border-gray-800">
                  <th className="px-6 py-3">{t('trading.orderId')}</th>
                  <th className="px-6 py-3">{t('trading.symbol')}</th>
                  <th className="px-6 py-3">{t('trading.direction')}</th>
                  <th className="px-6 py-3">{t('trading.price')}</th>
                  <th className="px-6 py-3">{t('trading.volume')}</th>
                  <th className="px-6 py-3">{t('trading.status')}</th>
                  <th className="px-6 py-3">Time</th>
                </tr>
              </thead>
              <tbody>
                {history.map((trade: any, idx: number) => (
                  <tr key={idx} className="border-b border-gray-800 hover:bg-dark-100">
                    <td className="px-6 py-4 font-mono text-xs">{trade.order_id}</td>
                    <td className="px-6 py-4 font-mono text-sm">{trade.symbol}</td>
                    <td className={`px-6 py-4 ${trade.direction === '买入' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.direction}
                    </td>
                    <td className="px-6 py-4">¥{trade.price.toFixed(2)}</td>
                    <td className="px-6 py-4">{trade.volume}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 rounded text-xs ${
                        trade.status === 'completed' ? 'bg-green-900 text-green-400' :
                        trade.status === 'pending' ? 'bg-yellow-900 text-yellow-400' :
                        'bg-gray-700 text-gray-400'
                      }`}>
                        {trade.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400">
                      {new Date(trade.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}