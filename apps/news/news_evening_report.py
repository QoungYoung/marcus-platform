#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻晚报 - 纯数据库版本
不再重新采集新闻，直接从 DB 读取全天数据生成晚间报告

与 news_collector.py 的区别：
- 不调用 AKShare API 采集新闻
- 不重复 AI 过滤/分级（S/A/B/C 已在采集时完成）
- 仅读取 DB 统计 + DeepSeek 情绪分析 + 更新热点缓存
"""

import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

# workspace 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

# 导入数据库相关模块
from workspace_detector import WORKSPACE

DATA_DIR = WORKSPACE / "data"
DB_FILE = DATA_DIR / "news.db"


def get_category_stats(db_file: str, days: int = 1) -> dict:
    """获取各板块统计"""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()

    cursor.execute('''
        SELECT category, 
               COUNT(*) as total,
               SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
               SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
               SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral
        FROM news 
        WHERE publish_time >= ? AND publish_time != ''
        GROUP BY category
        ORDER BY total DESC
    ''', (cutoff_time,))

    rows = cursor.fetchall()
    conn.close()

    stats = {}
    for row in rows:
        category, total, positive, negative, neutral = row
        stats[category] = {
            'total': total,
            'positive': positive,
            'negative': negative,
            'neutral': neutral,
            'score': 50 + (positive - negative) / max(total, 1) * 50
        }

    return stats


def show_stats(stats: dict, days: int = 1):
    """显示板块统计"""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"📊 新闻板块统计 (最近{days}天)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    for category, data in sorted(stats.items(), key=lambda x: x[1]['total'], reverse=True)[:10]:
        score = data['score']
        emoji = '🟢' if score > 60 else '🔴' if score < 40 else '🟡'
        print(f"{emoji} {category}: {data['total']}条 (正:{data['positive']}, 负:{data['negative']}, 中:{data['neutral']}) 情绪分:{score:.1f}", file=sys.stderr)

    print(f"{'='*60}\n", file=sys.stderr)


def get_today_news_count(db_file: str) -> dict:
    """获取今日新闻统计"""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-%d')

    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as positive,
            SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as negative,
            SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neutral
        FROM news 
        WHERE publish_time LIKE ?
    ''', (f'{today}%',))

    row = cursor.fetchone()
    conn.close()

    return {
        'total': row[0] or 0,
        'positive': row[1] or 0,
        'negative': row[2] or 0,
        'neutral': row[3] or 0,
        'total_new': 0  # DB 模式无新增
    }


def main():
    """主函数 - 仅读数据库，不采集"""
    start_time = datetime.now()
    print(f"[新闻晚报] ===== 开始生成晚间报告 {start_time.strftime('%Y-%m-%d %H:%M')} (纯DB模式) =====", file=sys.stderr)

    # 1. 数据库统计
    print(f"[新闻晚报] 数据库: {DB_FILE}", file=sys.stderr)
    if not DB_FILE.exists():
        print(f"[新闻晚报] ❌ 数据库不存在，退出", file=sys.stderr)
        sys.exit(1)

    today_stats = get_today_news_count(str(DB_FILE))
    print(f"[新闻晚报] 今日累计: {today_stats['total']} 条 (正:{today_stats['positive']}, 负:{today_stats['negative']}, 中:{today_stats['neutral']})", file=sys.stderr)

    # 2. 板块统计
    stats = get_category_stats(str(DB_FILE), days=1)
    show_stats(stats, days=1)

    # 3. 输出采集结果（保持兼容格式）
    result = {
        'timestamp': datetime.now().isoformat(),
        'finance_new': 0,
        'total_new': 0,
        'elapsed_seconds': 0,
        'ai_mode': True,
        'db_mode': True,
        'db_total_today': today_stats['total']
    }

    # 4. 热点分析（读 DB + DeepSeek）
    print("[热点缓存] ⏳ 冷却 8s 避免 API 限流...", file=sys.stderr)
    time.sleep(8)

    try:
        from news_analyzer import get_news_analysis

        analysis = get_news_analysis(news_limit=30, use_ai=True)
        sentiment = analysis.get('sentiment', {})
        hot_concepts = sentiment.get('hot_concepts', [])[:8]
        concept_scores = analysis.get('concept_scores', {})
        overall_sentiment = sentiment.get('score', 50)

        cache_data = {
            'generated_at': datetime.now().isoformat(),
            'news_count': today_stats['total'],
            'source': 'deepseek_concept'
        }

        if hot_concepts:
            cache_data.update({
                'sentiment_score': overall_sentiment,
                'hot_concepts': hot_concepts,
                'concept_scores': concept_scores,
                'summary': analysis.get('summary', {}),
                'impact_analysis': analysis.get('impact_analysis', []),
                'source': 'deepseek_concept'
            })
            print(f"[热点缓存] ✅ DeepSeek 概念: {hot_concepts}", file=sys.stderr)

            # 概念回写到最近新闻
            try:
                conn = sqlite3.connect(str(DB_FILE))
                cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
                rows = conn.execute(
                    "SELECT id, title, content FROM news WHERE created_at >= ? AND (concepts IS NULL OR concepts = '')",
                    (cutoff,)
                ).fetchall()
                updated = 0
                for news_id, title, content in rows:
                    text = (title + ' ' + (content or '')).lower()
                    matched = [c for c in hot_concepts if c.lower() in text]
                    if matched:
                        conn.execute(
                            "UPDATE news SET concepts = ? WHERE id = ?",
                            (','.join(matched), news_id)
                        )
                        updated += 1
                conn.commit()
                conn.close()
                if updated:
                    print(f"[概念回写] ✅ {updated} 条新闻已标记概念", file=sys.stderr)
            except Exception as e:
                print(f"[概念回写] ⚠️ 失败: {e}", file=sys.stderr)
        else:
            # 回退：行业统计
            print(f"[热点缓存] ⚠️ DeepSeek 无概念输出，回退到行业统计", file=sys.stderr)
            sorted_sectors = sorted(stats.items(), key=lambda x: x[1]['score'], reverse=True)[:8]
            hot_concepts = [cat for cat, _ in sorted_sectors if stats[cat]['total'] > 0]
            concept_scores = {cat: round(stats[cat]['score'], 1) for cat, _ in sorted_sectors if stats[cat]['total'] > 0}
            total_news = sum(d['total'] for d in stats.values())
            overall_sentiment = round(sum(d['score'] * d['total'] for d in stats.values()) / total_news, 1) if total_news > 0 else 50
            cache_data.update({
                'sentiment_score': overall_sentiment,
                'hot_concepts': hot_concepts,
                'concept_scores': concept_scores,
                'summary': {'s_level_count': 0, 'a_level_count': 0},
                'impact_analysis': [],
                'source': 'industry_stats'
            })

        # 写入缓存文件
        cache_file = WORKSPACE / "data" / "latest_hot_sectors.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"[热点缓存] ✅ 已写入 {cache_file} (来源:{cache_data.get('source')})", file=sys.stderr)

    except Exception as e:
        print(f"[热点缓存] ⚠️ 失败: {e}", file=sys.stderr)

    elapsed = (datetime.now() - start_time).total_seconds()
    result['elapsed_seconds'] = elapsed
    print(f"[新闻晚报] ===== 报告生成完成 / 耗时:{elapsed:.1f}s =====", file=sys.stderr)

    # 输出 JSON 结果
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
