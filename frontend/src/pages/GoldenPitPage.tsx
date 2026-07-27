import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, AlertTriangle, TrendingDown, Gem } from 'lucide-react';
import { goldenPitApi } from '../api/client';
import '../styles/golden-pit-page.css';

interface Factor {
  key: string;
  name: string;
  weight: number;
  description: string;
  raw: number | string | null;
  raw_label: string;
  score: number;
  weighted: number;
  error?: boolean;
}

interface ScoreData {
  score: number;
  level: string;
  level_label: string;
  level_color: string;
  as_of: string;
  factors: Factor[];
  summary: string;
  errors?: string[] | null;
}

interface ApiResponse {
  code: number;
  data: ScoreData | null;
  msg?: string;
}

function CircularGauge({ score, color, label }: { score: number; color: string; label: string }) {
  const radius = 90;
  const stroke = 12;
  const normalizedRadius = radius - stroke / 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const progress = Math.min(score / 100, 1);
  const strokeDashoffset = circumference - progress * circumference;

  return (
    <div className="gp-gauge-container">
      <svg height={radius * 2} width={radius * 2} className="gp-gauge-svg">
        <circle
          stroke="var(--gp-track, #1e2633)"
          fill="transparent"
          strokeWidth={stroke}
          r={normalizedRadius}
          cx={radius}
          cy={radius}
        />
        <circle
          stroke={color}
          fill="transparent"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={strokeDashoffset}
          r={normalizedRadius}
          cx={radius}
          cy={radius}
          className="gp-gauge-arc"
        />
        <text
          x={radius}
          y={radius - 8}
          textAnchor="middle"
          className="gp-gauge-score"
        >
          {score}
        </text>
        <text
          x={radius}
          y={radius + 20}
          textAnchor="middle"
          className="gp-gauge-label"
        >
          {label}
        </text>
      </svg>
    </div>
  );
}

function FactorBar({ factor }: { factor: Factor }) {
  const pct = Math.round(factor.score);
  const barColor = factor.score >= 60 ? 'var(--gp-danger, #ef4444)'
    : factor.score >= 40 ? 'var(--gp-warn, #f97316)'
    : 'var(--gp-safe, #22c55e)';

  return (
    <div className={`gp-factor-item ${factor.error ? 'error' : ''}`}>
      <div className="gp-factor-header">
        <span className="gp-factor-name">{factor.name}</span>
        <span className="gp-factor-weight">×{factor.weight}</span>
        <span className="gp-factor-score" style={{ color: barColor }}>
          {factor.error ? '--' : factor.score}
        </span>
      </div>
      <div className="gp-factor-bar-track">
        <div
          className="gp-factor-bar-fill"
          style={{ width: `${factor.error ? 0 : pct}%`, backgroundColor: barColor }}
        />
      </div>
      <div className="gp-factor-raw">{factor.raw_label}</div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="gp-page">
      <div className="gp-header">
        <div className="gp-header-left">
          <div className="gp-header-icon skeleton" />
          <div className="skeleton-text" style={{ width: 180, height: 24 }} />
        </div>
      </div>
      <div className="gp-content">
        <div className="gp-left">
          <div className="skeleton-box" style={{ width: 200, height: 200, borderRadius: '50%', margin: '0 auto' }} />
        </div>
        <div className="gp-right">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="skeleton-box" style={{ height: 52, marginBottom: 8 }} />
          ))}
        </div>
      </div>
    </div>
  );
}

export default function GoldenPitPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<ScoreData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await goldenPitApi.getScore();
      const body = resp.data as ApiResponse;
      if (body.code !== 0 || !body.data) {
        setError(body.msg || '获取数据失败');
      } else {
        setData(body.data);
      }
    } catch (err: any) {
      const msg = err?.response?.data?.msg || err?.message || '网络请求失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) return <Skeleton />;

  if (error) {
    return (
      <div className="gp-page">
        <div className="gp-header">
          <div className="gp-header-left">
            <div className="gp-header-icon">
              <Gem size={18} />
            </div>
            <div>
              <h2 className="gp-title">{t('goldenPit.title', '指数黄金坑预测')}</h2>
              <p className="gp-subtitle">{t('goldenPit.subtitle', '多因子情绪共振底部检测')}</p>
            </div>
          </div>
        </div>
        <div className="gp-error-state">
          <AlertTriangle size={40} />
          <p className="gp-error-msg">{error}</p>
          <p className="gp-error-hint">
            {t('goldenPit.errorHint', '请确认已配置 ArkVol API Key（环境变量 ARKVOL_API_KEY 或 ~/.arkvol/arkvol-entry.json）')}
          </p>
          <button className="gp-btn" onClick={fetchData}>
            <RefreshCw size={14} /> {t('common.refresh', '重试')}
          </button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="gp-page">
        <div className="gp-error-state">
          <TrendingDown size={40} />
          <p>{t('common.noData', '暂无数据')}</p>
          <button className="gp-btn" onClick={fetchData}>
            <RefreshCw size={14} /> {t('common.refresh', '刷新')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="gp-page">
      {/* Header */}
      <div className="gp-header">
        <div className="gp-header-left">
          <div className="gp-header-icon">
            <Gem size={18} />
          </div>
          <div>
            <h2 className="gp-title">{t('goldenPit.title', '指数黄金坑预测')}</h2>
            <p className="gp-subtitle">
              {t('goldenPit.subtitle', '多因子情绪共振底部检测')}
              {data.as_of && ` · ${data.as_of}`}
            </p>
          </div>
        </div>
        <button className="gp-btn-icon" onClick={fetchData} title={t('common.refresh', '刷新')}>
          <RefreshCw size={16} />
        </button>
      </div>

      {/* Main Content */}
      <div className="gp-content">
        {/* Left: Gauge */}
        <div className="gp-left">
          <CircularGauge score={data.score} color={data.level_color} label={data.level_label} />
          <div className="gp-level-tag" style={{ backgroundColor: data.level_color }}>
            {data.level_label}
          </div>
          {data.errors && data.errors.length > 0 && (
            <div className="gp-partial-errors">
              <AlertTriangle size={12} />
              <span>部分数据源不可用</span>
            </div>
          )}
        </div>

        {/* Right: Factors + Summary */}
        <div className="gp-right">
          <div className="gp-factors-grid">
            {data.factors.map((factor) => (
              <FactorBar key={factor.key} factor={factor} />
            ))}
          </div>
          <div className="gp-summary-card">
            <p>{data.summary}</p>
          </div>
          {data.errors && data.errors.length > 0 && (
            <div className="gp-errors-detail">
              {data.errors.map((e, i) => (
                <div key={i} className="gp-error-item">{e}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
