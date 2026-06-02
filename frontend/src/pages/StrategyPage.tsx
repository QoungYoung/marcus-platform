import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { strategyApi } from '../api/client';
import { Activity, AlertTriangle } from 'lucide-react';
import type { AxiosError } from 'axios';

interface StrategyState {
  stance: string;
  stance_code: string;
  position_limit: number;
  stop_loss: number;
  take_profit: number;
  trailing_stop: number;
  sentiment_score: number;
  sentiment_label: string;
  gap_risk: any;
  fund_flow: any;
  watchlist: any[];
}

interface ScanHistory {
  scan_time: string;
  stance: string;
  stance_code: string;
  position_limit: number;
  sentiment_score: number;
  hot_concepts: string[];
}

export default function StrategyPage() {
  const { t } = useTranslation();
  const [strategy, setStrategy] = useState<StrategyState | null>(null);
  const [scans, setScans] = useState<ScanHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      strategyApi.getCurrent().catch(() => null),
      strategyApi.getScanHistory({ limit: 10 }).catch(() => null),
    ]).then(([strategyRes, scansRes]) => {
      if (strategyRes?.data) setStrategy(strategyRes.data);
      if (scansRes?.data?.scans) setScans(scansRes.data.scans);
      setLoading(false);
    }).catch((err: AxiosError) => {
      setError(err.message);
      setLoading(false);
    });
  }, []);

  const getStanceColor = (stance: string) => {
    if (stance.includes('BUY') || stance.includes('AGGRESSIVE')) return 'text-green-400';
    if (stance.includes('SELL') || stance.includes('DEFENSIVE')) return 'text-red-400';
    return 'text-yellow-400';
  };

  const getStanceBg = (stance: string) => {
    if (stance.includes('BUY') || stance.includes('AGGRESSIVE')) return 'bg-green-900/20 border-green-800';
    if (stance.includes('SELL') || stance.includes('DEFENSIVE')) return 'bg-red-900/20 border-red-800';
    return 'bg-yellow-900/20 border-yellow-800';
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">{t('common.loading')}</div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 h-full overflow-auto">
      <h1 className="text-2xl font-bold">{t('strategy.title')}</h1>

      {/* Current Strategy */}
      {strategy && (
        <div className={`rounded-lg border p-6 ${getStanceBg(strategy.stance)}`}>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <Activity className={getStanceColor(strategy.stance)} size={24} />
              <span className="text-xl font-bold">{t('strategy.stance')}: </span>
              <span className={`text-xl font-bold ${getStanceColor(strategy.stance)}`}>
                {strategy.stance}
              </span>
            </div>
            <div className="text-sm text-gray-400">
              {t('strategy.positionLimit')}: {strategy.position_limit}%
            </div>
          </div>

          {/* Risk Parameters */}
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="bg-dark-300 rounded p-3">
              <div className="text-sm text-gray-400">{t('strategy.stopLoss')}</div>
              <div className="text-lg font-semibold text-red-400">{strategy.stop_loss}%</div>
            </div>
            <div className="bg-dark-300 rounded p-3">
              <div className="text-sm text-gray-400">{t('strategy.takeProfit')}</div>
              <div className="text-lg font-semibold text-green-400">+{strategy.take_profit}%</div>
            </div>
            <div className="bg-dark-300 rounded p-3">
              <div className="text-sm text-gray-400">{t('strategy.trailingStop')}</div>
              <div className="text-lg font-semibold text-yellow-400">{strategy.trailing_stop}%</div>
            </div>
          </div>

          {/* Sentiment & Risk */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-dark-300 rounded p-3">
              <div className="text-sm text-gray-400">{t('strategy.sentimentScore')}</div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-semibold">{strategy.sentiment_score}/100</span>
                <span className={`text-sm ${strategy.sentiment_label === 'positive' ? 'text-green-400' : strategy.sentiment_label === 'negative' ? 'text-red-400' : 'text-gray-400'}`}>
                  ({t(`news.${strategy.sentiment_label}`)})
                </span>
              </div>
            </div>
            {strategy.gap_risk && (
              <div className="bg-dark-300 rounded p-3">
                <div className="flex items-center gap-2 text-sm text-gray-400">
                  <AlertTriangle size={16} className="text-yellow-400" />
                  Gap Risk
                </div>
                <div className="text-sm">{strategy.gap_risk.adjustment_reason || 'None'}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Watchlist */}
      <div className="bg-dark-200 rounded-lg border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">{t('strategy.hotIndustries')}</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-gray-400 border-b border-gray-800">
                <th className="px-6 py-3">{t('portfolio.symbol')}</th>
                <th className="px-6 py-3">{t('market.sectors')}</th>
                <th className="px-6 py-3">{t('strategy.sentimentScore')}</th>
                <th className="px-6 py-3">{t('news.category')}</th>
              </tr>
            </thead>
            <tbody>
              {strategy?.watchlist.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-8 text-center text-gray-500">
                    {t('common.noData')}
                  </td>
                </tr>
              ) : (
                strategy?.watchlist.slice(0, 10).map((item: any, i: number) => (
                  <tr key={i} className="border-b border-gray-800 hover:bg-dark-100">
                    <td className="px-6 py-4 font-mono">{item.symbol || item.code}</td>
                    <td className="px-6 py-4">{item.sector || '-'}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                        (item.score || 0) >= 80 ? 'bg-green-900/50 text-green-400' :
                        (item.score || 0) >= 60 ? 'bg-yellow-900/50 text-yellow-400' :
                        'bg-gray-700/50 text-gray-400'
                      }`}>
                        {item.score || 0}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400">{item.reason || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Scan History */}
      <div className="bg-dark-200 rounded-lg border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">{t('strategy.scanHistory')}</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-gray-400 border-b border-gray-800">
                <th className="px-6 py-3">{t('strategy.scanTime')}</th>
                <th className="px-6 py-3">{t('strategy.stance')}</th>
                <th className="px-6 py-3">{t('strategy.positionLimit')}</th>
                <th className="px-6 py-3">{t('strategy.sentimentScore')}</th>
              </tr>
            </thead>
            <tbody>
              {scans.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-8 text-center text-gray-500">
                    {t('common.noData')}
                  </td>
                </tr>
              ) : (
                scans.map((scan, i) => (
                  <tr key={i} className="border-b border-gray-800 hover:bg-dark-100">
                    <td className="px-6 py-4 text-gray-400">
                      {new Date(scan.scan_time).toLocaleString()}
                    </td>
                    <td className={`px-6 py-4 font-semibold ${getStanceColor(scan.stance)}`}>
                      {scan.stance}
                    </td>
                    <td className="px-6 py-4">{scan.position_limit}%</td>
                    <td className="px-6 py-4">{scan.sentiment_score}/100</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}