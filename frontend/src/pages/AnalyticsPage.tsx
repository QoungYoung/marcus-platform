import { useTranslation } from 'react-i18next';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';
import { TrendingUp, TrendingDown, Target, Award } from 'lucide-react';

// Mock data for demonstration
const mockReturnsData = [
  { date: '2026-04-06', marcus: 0, sh000001: 0, sz399006: 0 },
  { date: '2026-04-07', marcus: 1.2, sh000001: 0.5, sz399006: 0.8 },
  { date: '2026-04-08', marcus: 0.8, sh000001: -0.3, sz399006: 0.2 },
  { date: '2026-04-09', marcus: 2.1, sh000001: 1.2, sz399006: 1.5 },
  { date: '2026-04-10', marcus: 1.5, sh000001: 0.8, sz399006: 1.1 },
  { date: '2026-04-11', marcus: -0.5, sh000001: -0.4, sz399006: -0.3 },
  { date: '2026-04-12', marcus: 0.3, sh000001: 0.1, sz399006: 0.2 },
  { date: '2026-04-13', marcus: 2.8, sh000001: 1.5, sz399006: 2.0 },
  { date: '2026-04-14', marcus: 2.5, sh000001: 1.3, sz399006: 1.8 },
  { date: '2026-04-15', marcus: 3.0, sh000001: 1.8, sz399006: 2.2 },
];

const mockDailyReturns = [
  { date: '04-06', return: 0 },
  { date: '04-07', return: 1.2 },
  { date: '04-08', return: -0.4 },
  { date: '04-09', return: 1.3 },
  { date: '04-10', return: -0.6 },
  { date: '04-11', return: 0.8 },
  { date: '04-12', return: 2.5 },
  { date: '04-13', return: -0.3 },
  { date: '04-14', return: 0.5 },
  { date: '04-15', return: 0.5 },
];

export default function AnalyticsPage() {
  const { t } = useTranslation();

  return (
    <div className="p-6 space-y-6 h-full overflow-auto">
      <h1 className="text-2xl font-bold">{t('analytics.title')}</h1>

      {/* KPI Cards */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard
          title={t('analytics.dailyPnL')}
          value="+3.00%"
          icon={TrendingUp}
          trend="up"
          trendValue="+¥30,000"
        />
        <KpiCard
          title={t('analytics.winRate')}
          value="68.5%"
          icon={Target}
        />
        <KpiCard
          title={t('market.sentiment')}
          value="+1.2%"
          icon={Award}
          trend="up"
          trendValue="Outperforming"
        />
        <KpiCard
          title="Max Drawdown"
          value="-2.1%"
          icon={TrendingDown}
          trend="down"
        />
      </div>

      {/* Cumulative Returns Chart */}
      <div className="bg-dark-200 rounded-lg border border-gray-800 p-6">
        <h2 className="text-lg font-semibold mb-4">{t('analytics.monthlyPnL')} (30 Days)</h2>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={mockReturnsData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--agent-chart-grid)" />
              <XAxis dataKey="date" stroke="var(--agent-chart-axis)" fontSize={12} />
              <YAxis stroke="var(--agent-chart-axis)" fontSize={12} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--agent-chart-tooltip-bg)',
                  border: 'var(--agent-chart-tooltip-border)',
                  borderRadius: '8px',
                  color: 'var(--agent-text-primary)',
                }}
                labelStyle={{ color: 'var(--agent-text-primary)', fontWeight: 600 }}
                itemStyle={{ color: 'var(--agent-text-secondary)' }}
              />
              <Line
                type="monotone"
                dataKey="marcus"
                stroke="#0ea5e9"
                strokeWidth={2}
                dot={false}
                name="Marcus"
              />
              <Line
                type="monotone"
                dataKey="sh000001"
                stroke="#6b7280"
                strokeWidth={1}
                dot={false}
                name="Shanghai"
              />
              <Line
                type="monotone"
                dataKey="sz399006"
                stroke="#8b5cf6"
                strokeWidth={1}
                dot={false}
                name="ChiNext"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Daily Returns Bar Chart */}
      <div className="bg-dark-200 rounded-lg border border-gray-800 p-6">
        <h2 className="text-lg font-semibold mb-4">{t('analytics.dailyPnL')}</h2>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={mockDailyReturns}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--agent-chart-grid)" />
              <XAxis dataKey="date" stroke="var(--agent-chart-axis)" fontSize={12} />
              <YAxis stroke="var(--agent-chart-axis)" fontSize={12} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--agent-chart-tooltip-bg)',
                  border: 'var(--agent-chart-tooltip-border)',
                  borderRadius: '8px',
                  color: 'var(--agent-text-primary)',
                }}
                labelStyle={{ color: 'var(--agent-text-primary)', fontWeight: 600 }}
                itemStyle={{ color: 'var(--agent-text-secondary)' }}
              />
              <Bar
                dataKey="return"
                radius={[4, 4, 0, 0]}
              >
                {mockDailyReturns.map((entry, index) => (
                  <rect
                    key={index}
                    fill={entry.return >= 0 ? '#22c55e' : '#ef4444'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Statistics Table */}
      <div className="bg-dark-200 rounded-lg border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">Performance Statistics</h2>
        </div>
        <div className="p-6">
          <div className="grid grid-cols-4 gap-6">
            <div>
              <div className="text-sm text-gray-400 mb-1">{t('analytics.totalTrades')}</div>
              <div className="text-2xl font-bold">247</div>
            </div>
            <div>
              <div className="text-sm text-gray-400 mb-1">{t('analytics.profitableTrades')}</div>
              <div className="text-2xl font-bold text-green-400">169</div>
            </div>
            <div>
              <div className="text-sm text-gray-400 mb-1">{t('analytics.losingTrades')}</div>
              <div className="text-2xl font-bold text-red-400">78</div>
            </div>
            <div>
              <div className="text-sm text-gray-400 mb-1">{t('analytics.avgProfit')}/{t('analytics.avgLoss')}</div>
              <div className="text-2xl font-bold">+¥121.5</div>
            </div>
          </div>
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
}: {
  title: string;
  value: string;
  icon: any;
  trend?: 'up' | 'down';
  trendValue?: string;
}) {
  return (
    <div className="bg-dark-200 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-gray-400">{title}</span>
        <Icon size={18} className={trend === 'up' ? 'text-green-400' : trend === 'down' ? 'text-red-400' : 'text-gray-500'} />
      </div>
      <div className="text-2xl font-bold">{value}</div>
      {trendValue && (
        <div className={`text-sm mt-1 ${trend === 'up' ? 'text-green-400' : 'text-red-400'}`}>
          {trendValue}
        </div>
      )}
    </div>
  );
}