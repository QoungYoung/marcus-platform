import { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { newsApi } from '../api/client';
import { ExternalLink, TrendingUp, TrendingDown, Minus, Tag, Building2, Lightbulb, Hash, Loader2 } from 'lucide-react';
import type { AxiosError } from 'axios';

interface NewsItem {
  id: number;
  title: string;
  content: string;
  source: string;
  publish_time: string;
  sentiment: string;
  sentiment_score: number;
  category: string | null;
  industry: string | null;
  keyword: string | null;
  concepts: string[];
  url: string;
}

type SentimentFilter = 'all' | 'positive' | 'neutral' | 'negative';

export default function NewsPage() {
  const { t } = useTranslation();
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<SentimentFilter>('all');
  const [, setError] = useState<string | null>(null);

  useEffect(() => {
    newsApi.getNews({ limit: 50 })
      .then(res => {
        setNews(res.data.news || []);
        setLoading(false);
      })
      .catch((err: AxiosError) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  const filteredNews = useMemo(() => {
    if (filter === 'all') return news;
    return news.filter(n => n.sentiment === filter);
  }, [news, filter]);

  const counts = useMemo(() => ({
    all: news.length,
    positive: news.filter(n => n.sentiment === 'positive').length,
    neutral: news.filter(n => n.sentiment === 'neutral').length,
    negative: news.filter(n => n.sentiment === 'negative').length,
  }), [news]);

  const formatTime = (time: string) => {
    try {
      const d = new Date(time);
      const now = new Date();
      const diffMs = now.getTime() - d.getTime();
      const diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return 'just now';
      if (diffMin < 60) return `${diffMin}m ago`;
      const diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return `${diffHr}h ago`;
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return time;
    }
  };

  const getSentimentBadge = (sentiment: string) => {
    switch (sentiment) {
      case 'positive': return { icon: <TrendingUp size={12} />, bg: 'bg-emerald-500/15', border: 'border-emerald-500/30', text: 'text-emerald-400', label: '看多' };
      case 'negative': return { icon: <TrendingDown size={12} />, bg: 'bg-red-500/15', border: 'border-red-500/30', text: 'text-red-400', label: '看空' };
      default:         return { icon: <Minus size={12} />, bg: 'bg-gray-500/10', border: 'border-gray-500/30', text: 'text-gray-400', label: '中性' };
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={32} className="animate-spin text-blue-400" />
          <span className="text-sm text-gray-500">{t('common.loading')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto bg-dark-300/30">
      {/* ====== Top Header ====== */}
      <div className="sticky top-0 z-10 backdrop-blur-md bg-dark-300/80 border-b border-gray-800/60 px-8 py-4">
        <div className="max-w-[1920px] mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-1.5 rounded-lg bg-blue-500/10">
              <Hash size={18} className="text-blue-400" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-white">{t('news.title')}</h1>
            <span className="text-xs text-gray-500">{counts.all} articles</span>
          </div>

          {/* Sentiment Filter Tabs */}
          <div className="flex items-center gap-1 bg-dark-200/80 rounded-lg border border-gray-700/50 p-1">
            {([
              { key: 'all' as const, label: t('news.all'), count: counts.all },
              { key: 'positive' as const, label: t('news.positive'), count: counts.positive },
              { key: 'neutral' as const, label: t('news.neutral'), count: counts.neutral },
              { key: 'negative' as const, label: t('news.negative'), count: counts.negative },
            ]).map(tab => (
              <button
                key={tab.key}
                onClick={() => setFilter(tab.key)}
                className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-all flex items-center gap-1.5 ${
                  filter === tab.key
                    ? 'bg-dark-100 text-white shadow-sm'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {tab.label}
                <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                  filter === tab.key ? 'bg-gray-700 text-gray-300' : 'bg-dark-300 text-gray-500'
                }`}>{tab.count}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="max-w-[1920px] mx-auto p-8">
        {/* ====== Two-Column Widescreen Layout ====== */}
        <div className="flex flex-col xl:flex-row gap-6">
          {/* ---- Left Sidebar: Stats & Hot Concepts ---- */}
          <div className="xl:w-72 flex-shrink-0 space-y-4">
            {/* Market Sentiment Gauge */}
            <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 p-5 backdrop-blur-sm">
              <div className="flex items-center gap-2 mb-4">
                <div className="p-1.5 rounded-lg bg-blue-500/10">
                  <TrendingUp size={15} className="text-blue-400" />
                </div>
                <h3 className="text-xs font-semibold text-white uppercase tracking-wider">Market Mood</h3>
              </div>

              {/* Sentiment bar */}
              <div className="flex h-2 rounded-full overflow-hidden bg-dark-100 mb-3">
                <div
                  className="bg-emerald-500 transition-all duration-500"
                  style={{ width: counts.all ? `${(counts.positive / counts.all) * 100}%` : '0%' }}
                />
                <div
                  className="bg-gray-500 transition-all duration-500"
                  style={{ width: counts.all ? `${(counts.neutral / counts.all) * 100}%` : '0%' }}
                />
                <div
                  className="bg-red-500 transition-all duration-500"
                  style={{ width: counts.all ? `${(counts.negative / counts.all) * 100}%` : '0%' }}
                />
              </div>

              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="bg-emerald-500/10 rounded-lg p-2">
                  <div className="text-lg font-bold text-emerald-400">{counts.positive}</div>
                  <div className="text-[10px] text-emerald-400/60">Bullish</div>
                </div>
                <div className="bg-gray-500/10 rounded-lg p-2">
                  <div className="text-lg font-bold text-gray-400">{counts.neutral}</div>
                  <div className="text-[10px] text-gray-500">Neutral</div>
                </div>
                <div className="bg-red-500/10 rounded-lg p-2">
                  <div className="text-lg font-bold text-red-400">{counts.negative}</div>
                  <div className="text-[10px] text-red-400/60">Bearish</div>
                </div>
              </div>
            </div>

            {/* Hot Industries */}
            <IndustryCloud news={news.slice(0, 50)} />

            {/* Hot Concepts */}
            <ConceptCloud news={news.slice(0, 50)} />
          </div>

          {/* ---- Main Area: News List ---- */}
          <div className="flex-1 min-w-0 space-y-3">
            {filteredNews.length === 0 ? (
              <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 py-16 text-center backdrop-blur-sm">
                <Hash size={40} className="mx-auto mb-3 opacity-30 text-gray-500" />
                <div className="text-gray-500">{t('common.noData')}</div>
              </div>
            ) : (
              filteredNews.map(item => {
                const badge = getSentimentBadge(item.sentiment);
                return (
                  <div
                    key={item.id}
                    className="group rounded-xl bg-dark-200/80 border border-gray-800/60 hover:border-gray-700/70 hover:bg-dark-200/90 transition-all duration-200 backdrop-blur-sm overflow-hidden"
                  >
                    <div className="p-5">
                      <div className="flex items-start gap-4">
                        {/* Sentiment indicator bar */}
                        <div className={`w-1 self-stretch rounded-full flex-shrink-0 ${
                          item.sentiment === 'positive' ? 'bg-emerald-500' :
                          item.sentiment === 'negative' ? 'bg-red-500' : 'bg-gray-600'
                        }`} />

                        <div className="flex-1 min-w-0">
                          {/* Top row: source + time + sentiment */}
                          <div className="flex items-center gap-2 mb-2 flex-wrap">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium ${badge.bg} ${badge.border} border ${badge.text}`}>
                              {badge.icon}
                              {badge.label}
                            </span>
                            <span className="text-xs text-gray-500">{item.source}</span>
                            <span className="text-gray-600">·</span>
                            <span className="text-xs text-gray-500">{formatTime(item.publish_time)}</span>
                          </div>

                          {/* Title */}
                          <h3 className="text-base font-semibold text-white mb-2 group-hover:text-blue-300 transition-colors line-clamp-2 leading-snug">
                            {item.title}
                          </h3>

                          {/* Content preview */}
                          {item.content && (
                            <p className="text-sm text-gray-400 line-clamp-2 mb-3 leading-relaxed">
                              {item.content}
                            </p>
                          )}

                          {/* Tags: industry + keyword + concepts */}
                          <div className="flex flex-wrap items-center gap-1.5">
                            {/* Industry tag */}
                            {item.industry && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] bg-blue-500/10 border border-blue-500/20 text-blue-300">
                                <Building2 size={10} />
                                {item.industry}
                              </span>
                            )}
                            {/* Keyword / Event type */}
                            {item.keyword && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] bg-violet-500/10 border border-violet-500/20 text-violet-300">
                                <Tag size={10} />
                                {item.keyword}
                              </span>
                            )}
                            {/* Concepts */}
                            {item.concepts && item.concepts.length > 0 && item.concepts.slice(0, 4).map((concept, idx) => (
                              <span
                                key={idx}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] bg-yellow-400/15 border border-yellow-400/30 text-white"
                              >
                                <Lightbulb size={10} />
                                {concept}
                              </span>
                            ))}
                          </div>
                        </div>

                        {/* External link */}
                        {item.url && (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="p-2 rounded-lg text-gray-500 hover:text-blue-400 hover:bg-blue-500/10 transition-all flex-shrink-0 mt-1"
                          >
                            <ExternalLink size={16} />
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ====== Helper Components ======

function IndustryCloud({ news }: { news: NewsItem[] }) {
  const industries = useMemo(() => {
    const map = new Map<string, number>();
    news.forEach(item => {
      if (item.industry) {
        map.set(item.industry, (map.get(item.industry) || 0) + 1);
      }
    });
    return Array.from(map.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8);
  }, [news]);

  if (industries.length === 0) return null;

  return (
    <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 p-5 backdrop-blur-sm">
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1 rounded-lg bg-blue-500/10">
          <Building2 size={14} className="text-blue-400" />
        </div>
        <h3 className="text-xs font-semibold text-white uppercase tracking-wider">Hot Industries</h3>
      </div>
      <div className="space-y-1.5">
        {industries.map(([name, count]) => (
          <div key={name} className="flex items-center justify-between text-xs">
            <span className="text-gray-400 truncate flex-1 mr-2">{name}</span>
            <span className="text-gray-500 bg-dark-100 px-1.5 py-0.5 rounded font-mono">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ConceptCloud({ news }: { news: NewsItem[] }) {
  const concepts = useMemo(() => {
    const map = new Map<string, number>();
    news.forEach(item => {
      if (item.concepts) {
        item.concepts.forEach(c => {
          map.set(c, (map.get(c) || 0) + 1);
        });
      }
    });
    return Array.from(map.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [news]);

  if (concepts.length === 0) return null;

  return (
    <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 p-5 backdrop-blur-sm">
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1 rounded-lg bg-yellow-400/15">
          <Lightbulb size={14} className="text-white" />
        </div>
        <h3 className="text-xs font-semibold text-white uppercase tracking-wider">Hot Concepts</h3>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {concepts.map(([name, count]) => (
          <span
            key={name}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-yellow-400/15 border border-yellow-400/30 text-white"
          >
            {name}
            <span className="text-white/60 font-mono">{count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
