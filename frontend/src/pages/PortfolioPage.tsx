import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TrendingUp, TrendingDown, Wallet, PieChart } from 'lucide-react';
import { portfolioApi } from '../api/client';
import type { AxiosError } from 'axios';

interface Position {
  symbol: string;
  name: string;
  volume: number;
  avg_price: number;
  current_price: number;
  market_value: number;
  floating_pnl: number;
  floating_pnl_pct: number;
}

interface Account {
  initial_capital: number;
  available_cash: number;
  position_value: number;
  total_asset: number;
  realized_pnl: number;
  float_pnl: number;
  total_pnl: number;
  position_ratio: number;
  positions: Position[];
}

interface PortfolioSummary {
  account: Account;
  total_return: number;
  total_return_pct: number;
  win_rate: number;
}

export default function PortfolioPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<PortfolioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    portfolioApi.getSummary()
      .then(res => {
        setData(res.data);
        setLoading(false);
      })
      .catch((err: AxiosError) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">{t('common.loading')}</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 text-red-400">
          {t('common.error')}: {error}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { account, total_return, total_return_pct } = data;
  const positionsList = account?.positions || [];

  return (
    <div className="p-6 space-y-6 h-full overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t('portfolio.title')}</h1>
        <div className="text-sm text-gray-500">
          {t('common.refresh')}: {new Date().toLocaleTimeString()}
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard
          title={t('portfolio.totalAsset')}
          value={`¥${(account?.total_asset || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          icon={Wallet}
          trend={(account?.total_pnl || 0) >= 0 ? 'up' : 'down'}
          trendValue={`${(account?.total_pnl || 0) >= 0 ? '+' : ''}¥${(account?.total_pnl || 0).toFixed(2)}`}
        />
        <KpiCard
          title={t('portfolio.availableCash')}
          value={`¥${(account?.available_cash || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          icon={PieChart}
        />
        <KpiCard
          title={t('portfolio.positionValue')}
          value={`¥${(account?.position_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          icon={TrendingUp}
          subtitle={`${t('portfolio.positionRatio')} ${(account?.position_ratio || 0).toFixed(1)}%`}
        />
        <KpiCard
          title={t('portfolio.totalPnL')}
          value={`${(total_return_pct || 0) >= 0 ? '+' : ''}${(total_return_pct || 0).toFixed(2)}%`}
          icon={(total_return || 0) >= 0 ? TrendingUp : TrendingDown}
          trend={(total_return || 0) >= 0 ? 'up' : 'down'}
          trendValue={`${(total_return || 0) >= 0 ? '+' : ''}¥${(total_return || 0).toFixed(2)}`}
        />
      </div>

      {/* Positions Table */}
      <div className="bg-dark-200 rounded-lg border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">{t('portfolio.positions')}</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-gray-400 border-b border-gray-800">
                <th className="px-6 py-3">{t('portfolio.symbol')}</th>
                <th className="px-6 py-3">{t('portfolio.name')}</th>
                <th className="px-6 py-3">{t('portfolio.volume')}</th>
                <th className="px-6 py-3">{t('portfolio.avgPrice')}</th>
                <th className="px-6 py-3">{t('portfolio.currentPrice')}</th>
                <th className="px-6 py-3">{t('portfolio.marketValue')}</th>
                <th className="px-6 py-3">{t('portfolio.floatingPnL')}</th>
                <th className="px-6 py-3">{t('portfolio.profitRate')}</th>
              </tr>
            </thead>
            <tbody>
              {positionsList.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-6 py-8 text-center text-gray-500">
                    {t('common.noData')}
                  </td>
                </tr>
              ) : (
                positionsList.map((pos: Position) => (
                  <tr key={pos.symbol} className="border-b border-gray-800 hover:bg-dark-100">
                    <td className="px-6 py-4 font-mono text-sm">{pos.symbol}</td>
                    <td className="px-6 py-4">{pos.name?.replace(/^(SH|SZ|BJ)\d+/, '').trim() || pos.symbol}</td>
                    <td className="px-6 py-4">{pos.volume}</td>
                    <td className="px-6 py-4">¥{(pos.avg_price || 0).toFixed(2)}</td>
                    <td className="px-6 py-4">¥{(pos.current_price || 0).toFixed(2)}</td>
                    <td className="px-6 py-4">¥{(pos.market_value || 0).toFixed(2)}</td>
                    <td className={`px-6 py-4 ${(pos.floating_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {(pos.floating_pnl || 0) >= 0 ? '+' : ''}¥{(pos.floating_pnl || 0).toFixed(2)}
                    </td>
                    <td className={`px-6 py-4 ${(pos.floating_pnl_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {(pos.floating_pnl_pct || 0) >= 0 ? '+' : ''}{(pos.floating_pnl_pct || 0).toFixed(2)}%
                    </td>
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

function KpiCard({
  title,
  value,
  icon: Icon,
  trend,
  trendValue,
  subtitle,
}: {
  title: string;
  value: string;
  icon: any;
  trend?: 'up' | 'down';
  trendValue?: string;
  subtitle?: string;
}) {
  return (
    <div className="bg-dark-200 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-gray-400">{title}</span>
        <Icon size={18} className="text-gray-500" />
      </div>
      <div className="text-2xl font-bold">{value}</div>
      {trendValue && (
        <div className={`text-sm mt-1 ${trend === 'up' ? 'text-green-400' : 'text-red-400'}`}>
          {trend === 'up' ? '+' : ''}{trendValue}
        </div>
      )}
      {subtitle && <div className="text-sm text-gray-500 mt-1">{subtitle}</div>}
    </div>
  );
}