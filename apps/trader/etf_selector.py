#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 专用选股器
基于板块新闻催化 + momentum 右侧确认 + 流动性过滤的三重确认选股
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Optional

# Cross-platform workspace detection
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.workspace_detector import WORKSPACE, XUEQIU_DIR, AKSHARE_DIR, DATA_DIR
from core.xueqiu_engine import XueqiuEngine

import sqlite3


def load_etf_pool() -> dict:
    """读取 etf_pool.json，返回完整配置"""
    pool_file = WORKSPACE / "data" / "etf_pool.json"
    if not pool_file.exists():
        print(f"[ETF Selector] ⚠️ etf_pool.json 缺失")
        return {}
    with open(pool_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_etf_quotes(etf_list: List[dict]) -> Dict[str, dict]:
    """调用雪球批量获取 ETF 行情"""
    xq = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
    symbols = [etf['symbol'] for etf in etf_list]
    return xq.batch_get_etf_quotes(symbols)


def get_sector_news_score(sector: str) -> float:
    """
    从 news.db 读取板块新闻情绪（归一化 0~50）
    板块新闻 = 该板块内所有成分股近期新闻的聚合情绪
    """
    news_db = DATA_DIR / "news.db"
    if not news_db.exists():
        return 25.0  # 无数据返回默认值

    # 读取板块内股票（从 stock_pool.db 获取板块成分）
    pool_db = WORKSPACE / "data" / "stock_pool.db"
    codes = []
    if pool_db.exists():
        conn = sqlite3.connect(str(pool_db))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT symbol FROM stock_pool WHERE industry = ? LIMIT 50",
            (sector,)
        )
        codes = [row['symbol'] for row in cur.fetchall()]
        conn.close()

    if not codes:
        # 无成分股数据时，用板块名直接查 news.db
        codes = [sector]

    cutoff_time = "2026-05-01 00:00"  # 简化为固定时间窗口
    conn_n = sqlite3.connect(str(news_db))
    conn_n.row_factory = sqlite3.Row
    c_n = conn_n.cursor()

    placeholders = ','.join('?' * len(codes))
    c_n.execute(
        f"SELECT sentiment, title FROM news WHERE keyword IN ({placeholders}) AND publish_time >= ?",
        codes + [cutoff_time]
    )
    rows = c_n.fetchall()
    conn_n.close()

    if not rows:
        return 25.0

    sentiment_map = {'positive': 1, 'neutral': 0, 'negative': -1}
    total_score = 0
    catalyst_kw = ["增长", "超预期", "业绩", "中标", "合作", "突破", "创新", "政策", "获批"]
    risk_kw = ["亏损", "处罚", "调查", "诉讼", "跌停", "减持", "ST", "下滑"]

    for row in rows:
        s_val = sentiment_map.get(row['sentiment'], 0)
        total_score += s_val
        title = row['title'] or ''
        for kw in catalyst_kw:
            if kw in title:
                total_score += 0.5
                break
        for kw in risk_kw:
            if kw in title:
                total_score -= 0.5
                break

    avg = total_score / len(rows)
    # 归一化到 0~50
    normalized = max(0, min(50, 25 + avg * 15))
    return round(normalized, 1)


def score_etf(etf: dict, quote: dict, sector_news_score: float) -> dict:
    """
    综合评分：Momentum(40%) + 板块催化(60%)

    Args:
        etf: ETF 配置 dict
        quote: ETF 行情 dict
        sector_news_score: 板块新闻分 (0~50)

    Returns:
        包含 catalyst_score, catalyst_tag 等字段的 dict
    """
    pct_1d = quote.get('percent', 0) or 0

    # Momentum 评分 (0~50)
    momentum_score = max(0, min(50, (pct_1d + 2) * 12.5))

    # 板块催化评分 (0~50)
    catalyst_score = sector_news_score

    # 综合评分（板块催化 60% + momentum 40%）
    total_score = catalyst_score * 0.6 + momentum_score * 0.4

    # 标签
    if total_score >= 70:
        tag = '🟢'
    elif total_score >= 55:
        tag = '🟡'
    else:
        tag = '🔴'

    return {
        **etf,
        'quote': quote,
        'pct_1d': pct_1d,
        'sector_news_score': sector_news_score,
        'momentum_score': round(momentum_score, 1),
        'catalyst_score': round(total_score, 1),
        'catalyst_tag': tag,
    }


def get_etf_candidates(top_n: int = 5, external_hot_sectors: List[str] = None,
                       market_stance: str = 'yellow') -> List[dict]:
    """
    主选股函数，输出候选 ETF 列表

    Args:
        top_n: 返回最多多少只
        external_hot_sectors: 外部传入的热点行业列表（来自 market_scan）
        market_stance: 市场立场 (green/yellow/red)

    Returns:
        候选 ETF 列表，按综合评分降序
    """
    pool_config = load_etf_pool()
    if not pool_config:
        return []

    etf_list = pool_config.get('etf_list', [])
    sector_priority = pool_config.get('sector_priority', {})

    # 1. 获取所有 ETF 行情
    quotes = get_etf_quotes(etf_list)

    # 2. 按板块优先级分组
    p1_sectors = sector_priority.get('1_Priority', [])
    p2_sectors = sector_priority.get('2_Priority', [])
    p3_sectors = sector_priority.get('3_Priority', [])

    # 3. 对每只 ETF 评分
    scored_etfs = []
    for etf in etf_list:
        sym = etf['symbol']
        quote = quotes.get(sym, {})

        # 跳过无效行情
        if not quote or not quote.get('current'):
            continue

        sector = etf.get('sector', '')

        # 流动性过滤：成交额 >= 1亿
        amount = quote.get('amount', 0)
        if amount < 100000000:
            continue

        # Momentum 门槛（根据市场立场）
        pct_1d = quote.get('percent', 0) or 0
        momentum_threshold = {
            'green': -1.0,
            'yellow': 0.5,
            'red': 1.5,
        }.get(market_stance, 0.5)

        if pct_1d < momentum_threshold:
            continue

        # 获取板块新闻分
        sector_news_score = get_sector_news_score(sector)

        # 综合评分
        scored = score_etf(etf, quote, sector_news_score)
        scored_etfs.append(scored)

    # 4. 外部热点行业加权
    if external_hot_sectors:
        hot_set = set(external_hot_sectors)
        for etf in scored_etfs:
            if etf.get('sector') in hot_set:
                etf['catalyst_score'] = min(100, etf['catalyst_score'] + 10)
                etf['hot_sector_boost'] = True

    # 5. 按优先级排序（先按评分，再按优先级）
    priority_map = {s: 1 for s in p1_sectors}
    priority_map.update({s: 2 for s in p2_sectors})
    priority_map.update({s: 3 for s in p3_sectors})

    def sort_key(etf):
        sector_prio = priority_map.get(etf.get('sector', ''), 3)
        return (sector_prio, -etf.get('catalyst_score', 0))

    scored_etfs.sort(key=sort_key)

    # 6. 返回 top_n
    return scored_etfs[:top_n]


def get_sector_hot_sectors(limit: int = 5) -> List[dict]:
    """
    返回最热板块（用于注入 stock_selector）

    Returns:
        [{'sector': str, 'news_score': float, 'etf_count': int}, ...]
    """
    pool_config = load_etf_pool()
    if not pool_config:
        return []

    sector_scores = {}
    for etf in pool_config.get('etf_list', []):
        sector = etf.get('sector', '')
        if sector not in sector_scores:
            sector_scores[sector] = {
                'sector': sector,
                'news_score': get_sector_news_score(sector),
                'etf_count': 0
            }
        sector_scores[sector]['etf_count'] += 1

    # 按新闻分降序
    sorted_sectors = sorted(sector_scores.values(),
                           key=lambda x: -x['news_score'])[:limit]

    return sorted_sectors


if __name__ == "__main__":
    print("=" * 60)
    print("ETF 选股器测试")
    print("=" * 60)

    # 测试加载 ETF 池
    pool = load_etf_pool()
    print(f"\n[1] ETF 池加载: {len(pool.get('etf_list', []))} 只 ETF")

    # 测试获取候选
    candidates = get_etf_candidates(top_n=5, market_stance='yellow')
    print(f"\n[2] 候选 ETF ({len(candidates)} 只):")
    for c in candidates:
        print(f"  {c['catalyst_tag']} {c['symbol']} {c['name']} | "
              f"板块:{c['sector']} | 催化:{c['catalyst_score']:.1f} | "
              f"Momentum:{c['pct_1d']:+.2f}%")

    # 测试热点板块
    hot = get_sector_hot_sectors(limit=5)
    print(f"\n[3] 热点板块 ({len(hot)} 个):")
    for h in hot:
        print(f"  {h['sector']}: 新闻分={h['news_score']:.1f} | ETF={h['etf_count']}只")

    print("\n" + "=" * 60)