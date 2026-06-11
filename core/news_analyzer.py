#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 统一新闻分析模块

封装所有新闻分析逻辑，提供统一接口：
- get_news_analysis() - 完整分析（情绪 + 影响力 + 热点板块）
- get_news_sentiment_simple() - 快速情绪分析（关键词匹配）
- get_stock_news() - 获取个股相关新闻

所有新闻分析任务都应调用此模块，避免重复代码。
"""

import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# Marcus workspace - auto-detect from project root
import platform as _plat
_ws = Path(__file__).parent.parent  # core/ -> marcus-platform/

AKSHARE_DIR = _ws / "apps" / "news"
MARCUS_DIR = _ws / "apps" / "integration"
DEEPSEEK_DIR = _ws / "core" / "deepseek"
DATA_DIR = _ws / "data"

sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(MARCUS_DIR))
sys.path.insert(0, str(DEEPSEEK_DIR))

try:
    from akshare_engine_enhanced import AKShareEnhancedEngine
    from deepseek_analyzer_combined import analyze_news_combined
    DEEPSEEK_AVAILABLE = True
except Exception as e:
    print(f"[news_analyzer] ⚠️ 模块加载失败：{e}", file=sys.stderr)
    AKShareEnhancedEngine = None
    analyze_news_combined = None
    DEEPSEEK_AVAILABLE = False


# ============= 配置 =============
DEFAULT_NEWS_LIMIT = 30
INDUSTRY_LIST = ['半导体', '人工智能', '新能源', '消费电子', '机器人', '金融科技', '医药生物', '汽车']
FOCUS_INDUSTRIES = ['半导体', '人工智能', '新能源', '消费电子', '机器人']


# ============= 内部辅助函数 =============

def _calc_sentiment_by_level(impact_analysis: List[dict], news_list: List[dict]) -> dict:
    """
    根据原始新闻的 DB sentiment 字段，统计各影响等级的看多/看空数量。

    Args:
        impact_analysis: AI 返回的影响力分析列表，每项含 news_id + impact_level
        news_list: 原始新闻列表（与 AI 分析的顺序一致，news_id 为 1-indexed）

    Returns:
        dict: {s_bullish_count, s_bearish_count, a_bullish_count, a_bearish_count, ...}
    """
    counts = {
        's_bullish_count': 0, 's_bearish_count': 0,
        'a_bullish_count': 0, 'a_bearish_count': 0,
        'b_bullish_count': 0, 'b_bearish_count': 0,
        'c_bullish_count': 0, 'c_bearish_count': 0,
    }

    for item in impact_analysis:
        news_id = item.get('news_id', -1)
        if not (1 <= news_id <= len(news_list)):
            continue

        db_sentiment = (news_list[news_id - 1].get('sentiment') or 'neutral').lower()
        level = (item.get('impact_level') or '').upper()

        is_bullish = db_sentiment == 'positive'
        is_bearish = db_sentiment == 'negative'

        if level == 'S':
            if is_bullish: counts['s_bullish_count'] += 1
            elif is_bearish: counts['s_bearish_count'] += 1
        elif level == 'A':
            if is_bullish: counts['a_bullish_count'] += 1
            elif is_bearish: counts['a_bearish_count'] += 1
        elif level == 'B':
            if is_bullish: counts['b_bullish_count'] += 1
            elif is_bearish: counts['b_bearish_count'] += 1
        elif level == 'C':
            if is_bullish: counts['c_bullish_count'] += 1
            elif is_bearish: counts['c_bearish_count'] += 1

    return counts


# ============= 核心接口 =============

def get_news_analysis(news_limit: int = DEFAULT_NEWS_LIMIT, use_ai: bool = True) -> dict:
    """
    获取新闻完整分析（情绪 + 影响力 + 热点板块）
    
    Args:
        news_limit: 获取新闻数量（默认 30）
        use_ai: 是否使用 DeepSeek AI 分析（默认 True）
    
    Returns:
        {
            'sentiment': {
                'score': 65.3,          # 情绪分数 0-100
                'positive': 5,          # 正面新闻数
                'negative': 2,          # 负面新闻数
                'neutral': 23,          # 中性新闻数
                'hot_concepts': [...],   # 热点概念
                'catalysts': [...],      # 重大催化剂
                'risks': [...]           # 风险因素
            },
            'impact_analysis': [         # 新闻影响力分析
                {
                    'title': '...',
                    'level': 'A',        # S/A/B/C
                    'sector': '半导体',
                    'impact': '正面'
                }
            ],
            'summary': {
                's_level_count': 2,
                'a_level_count': 5,
                'b_level_count': 10,
                'c_level_count': 13,
                'top_sectors': ['半导体', '人工智能']
            }
        }
    """
    default_result = {
        'sentiment': {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0, 'hot_concepts': [], 'catalysts': [], 'risks': []},
        'impact_analysis': [],
        'summary': {
            's_level_count': 0, 'a_level_count': 0, 'b_level_count': 0, 'c_level_count': 0,
            's_bullish_count': 0, 's_bearish_count': 0,
            'a_bullish_count': 0, 'a_bearish_count': 0,
            'b_bullish_count': 0, 'b_bearish_count': 0,
            'c_bullish_count': 0, 'c_bearish_count': 0,
            'top_sectors': []
        }
    }
    
    if AKShareEnhancedEngine is None:
        print(f"[news_analyzer] ❌ AKShareEnhancedEngine 未加载", file=sys.stderr)
        return default_result
    
    try:
        # 1. 从数据库获取过去 12 小时的新闻
        ak_enhanced = AKShareEnhancedEngine(data_dir=str(DATA_DIR))
        all_news = ak_enhanced.get_recent_news_from_db(hours=12, limit=news_limit)
        
        if not all_news:
            print(f"[news_analyzer] ⚠️ 过去 12 小时无新闻，使用 API 获取", file=sys.stderr)
            # 降级：从 API 获取
            finance_news = ak_enhanced.get_finance_news(limit=news_limit)
            industry_news = []
            for ind in FOCUS_INDUSTRIES:
                ind_news = ak_enhanced.get_industry_news(ind, limit=5)
                industry_news.extend(ind_news)
            all_news = finance_news + industry_news
        
        if not all_news:
            print(f"[news_analyzer] ⚠️ 未获取到新闻", file=sys.stderr)
            return default_result
        
        # 4. AI 分析（直接调用，不设超时，不做 fallback）
        if use_ai and DEEPSEEK_AVAILABLE and analyze_news_combined:
            print(f"[news_analyzer] ✓ 使用 DeepSeek AI 分析 {len(all_news)} 条新闻", file=sys.stderr)
            combined_result = analyze_news_combined(all_news[:20])
            sentiment_result = combined_result.get('sentiment', default_result['sentiment'])
            impact_summary = dict(combined_result.get('summary', default_result['summary']))
            impact_analysis = combined_result.get('impact_analysis', [])

            # 根据 DB 中的 sentiment 字段，统计各影响等级的看多/看空数量
            sentiment_by_level = _calc_sentiment_by_level(impact_analysis, all_news[:20])
            impact_summary.update(sentiment_by_level)

            return {
                'sentiment': sentiment_result,
                'impact_analysis': impact_analysis,
                'summary': impact_summary
            }
        else:
            print(f"[news_analyzer] ⚠️ DeepSeek 不可用，返回空结果", file=sys.stderr)
            return default_result
    
    except Exception as e:
        print(f"[news_analyzer] ❌ 分析失败：{e}", file=sys.stderr)
        return default_result


def get_news_sentiment_simple(news_list: List[dict]) -> dict:
    """
    快速情绪分析（关键词匹配，不使用 AI）
    
    Args:
        news_list: 新闻列表
    
    Returns:
        {'score': 50-100, 'positive': int, 'negative': int, 'neutral': int, 'total': int}
    """
    # 高权重关键词（1 条顶 3 条）
    high_positive = ['涨停', '业绩超预期', '重大中标', '创新高', '暴增', '重组成功', '获批', '突破']
    high_negative = ['跌停', '被立案', '退市', '暴雷', '亏损', '调查', '处罚', '违规']
    
    # 普通关键词
    positive = ['增长', '利好', '突破', '超预期', '业绩', '重组', '并购', '分红', '政策扶持', '行业景气', '订单', '合作', '放量', '中标']
    negative = ['下跌', '风险', '减持', '监管', '警告', '下滑', '萎缩', '缩量']
    
    if not news_list:
        return {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0, 'total': 0}
    
    pos_count = 0
    neg_count = 0
    neutral_count = 0
    
    for news in news_list:
        title = news.get('title', '') + ' ' + str(news.get('content', ''))
        title_lower = title.lower()
        
        # 检查高权重词
        has_high_pos = any(kw in title for kw in high_positive)
        has_high_neg = any(kw in title for kw in high_negative)
        
        # 检查普通词
        has_pos = any(kw in title for kw in positive)
        has_neg = any(kw in title for kw in negative)
        
        if has_high_pos:
            pos_count += 3  # 高权重正面
        elif has_high_neg:
            neg_count += 3  # 高权重负面
        elif has_pos and not has_neg:
            pos_count += 1
        elif has_neg and not has_pos:
            neg_count += 1
        else:
            neutral_count += 1
    
    total = len(news_list)
    # 计算分数（高权重词影响更大）
    score = 50 + (pos_count - neg_count) / total * 20 if total > 0 else 50
    score = max(0, min(100, score))
    
    return {
        'score': score,
        'positive': pos_count,
        'negative': neg_count,
        'neutral': neutral_count,
        'total': total
    }


def get_stock_news(stock_codes: List[str], limit: int = 5) -> dict:
    """
    获取指定股票的相关新闻
    
    Args:
        stock_codes: 股票代码列表 ['002230', '600519']
        limit: 每只股票返回新闻数量
    
    Returns:
        {
            '601225': [{'title': '...', 'publish_time': '...'}, ...],
            '002384': [...],
            ...
        }
    """
    if AKShareEnhancedEngine is None:
        return {}
    
    try:
        ak_enhanced = AKShareEnhancedEngine(data_dir=str(DATA_DIR))
        # 使用批量获取方法
        result = ak_enhanced.get_stock_news_batch(stock_codes, limit_per_stock=limit)
        return result
    except Exception as e:
        print(f"[news_analyzer] ❌ 获取个股新闻失败：{e}", file=sys.stderr)
        return {}


def get_focused_news(sentiment_result: dict) -> dict:
    """
    从完整分析结果中提取关键信息（用于快速决策）
    
    Args:
        sentiment_result: get_news_analysis() 的返回结果
    
    Returns:
        简化版结果（只包含关键字段）
    """
    return {
        'score': sentiment_result.get('sentiment', {}).get('score', 50),
        'positive': sentiment_result.get('sentiment', {}).get('positive', 0),
        'negative': sentiment_result.get('sentiment', {}).get('negative', 0),
        's_level': sentiment_result.get('summary', {}).get('s_level_count', 0),
        'a_level': sentiment_result.get('summary', {}).get('a_level_count', 0),
        'top_sectors': sentiment_result.get('summary', {}).get('top_sectors', [])[:5],
        'hot_concepts': sentiment_result.get('sentiment', {}).get('hot_concepts',
                            sentiment_result.get('sentiment', {}).get('hot_industries', []))[:5]
    }


# ============= 内部函数 =============

def _analyze_news_simple(news_list: List[dict]) -> dict:
    """
    简单关键词分析（AI 不可用时的降级方案）
    """
    sentiment = get_news_sentiment_simple(news_list)
    
    # 简化版影响力分析（按标题长度和关键词判断）
    impact_analysis = []
    s_count = 0
    a_count = 0
    b_count = 0
    c_count = 0
    
    for news in news_list[:20]:
        title = news.get('title', '')
        # 简化分级逻辑
        if any(kw in title for kw in ['涨停', '业绩超预期', '重大']):
            level = 'S'
            s_count += 1
        elif any(kw in title for kw in ['中标', '增长', '突破']):
            level = 'A'
            a_count += 1
        elif any(kw in title for kw in ['下跌', '风险']):
            level = 'B'
            b_count += 1
        else:
            level = 'C'
            c_count += 1
        
        impact_analysis.append({
            'title': title,
            'level': level,
            'sector': '综合',
            'impact': '正面' if any(kw in title for kw in ['涨', '增', '突破']) else '负面' if any(kw in title for kw in ['跌', '亏', '风险']) else '中性'
        })
    
    return {
        'sentiment': sentiment,
        'impact_analysis': impact_analysis,
        'summary': {
            's_level_count': s_count,
            'a_level_count': a_count,
            'b_level_count': b_count,
            'c_level_count': c_count,
            'top_sectors': []
        }
    }


def get_hot_sectors_from_cache(max_age_minutes: int = 1440) -> dict:
    """
    从缓存文件读取热点概念分析结果（由 news_collector 生成）
    如果缓存不存在或过期，返回空 dict

    Returns:
        dict: {
            'available': bool,
            'generated_at': str,
            'sentiment_score': float,
            'hot_concepts': list,
            'concept_scores': dict,
            'summary': dict,
        }
    """
    cache_file = _ws / "data" / "latest_hot_sectors.json"

    default_result = {
        'available': False,
        'generated_at': '',
        'sentiment_score': 50,
        'hot_concepts': [],
        'concept_scores': {},
        'summary': {'s_level_count': 0, 'a_level_count': 0, 'top_sectors': []},
        'impact_analysis': []
    }

    if not cache_file.exists():
        return default_result

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 检查是否过期
        generated_at = data.get('generated_at', '')
        if generated_at:
            try:
                gen_time = datetime.fromisoformat(generated_at)
                age_minutes = (datetime.now() - gen_time).total_seconds() / 60
                if age_minutes > max_age_minutes:
                    print(f"[热点缓存] ⚠️ 缓存已过期（{age_minutes:.0f} 分钟），需要重新分析", file=sys.stderr)
                    return default_result
            except Exception:
                pass

        return {
            'available': True,
            'generated_at': generated_at,
            'sentiment_score': data.get('sentiment_score', 50),
            'hot_concepts': data.get('hot_concepts', data.get('hot_industries', [])),  # 兼容旧缓存
            'concept_scores': data.get('concept_scores', data.get('industry_scores', {})),  # 兼容旧缓存
            'summary': data.get('summary', {}),
            'impact_analysis': data.get('impact_analysis', []),
        }
    except Exception as e:
        print(f"[热点缓存] ⚠️ 读取缓存失败：{e}", file=sys.stderr)
        return default_result


# ============= 概念名标准化（解决 AI 输出概念名与数据源不匹配的问题） =============

# 全局概念词汇表，首次使用时从 Tushare concept-fund-flow 拉取，不硬编码
_concept_vocabulary: List[str] = []
_vocabulary_loaded = False


def _init_concept_vocabulary():
    """
    首次使用时从 Tushare dc_daily 拉取当日涨幅 Top 50 概念板块作为标准词汇表。
    
    数据源: dc_daily (东方财富概念板块日线) ← 概念名与 stock_concept_map 完全一致。
    排序: 按 pct_change 降序，取量价表现最强的 50 个概念。
    """
    global _concept_vocabulary, _vocabulary_loaded
    if _vocabulary_loaded:
        return

    concepts = []
    try:
        import pandas as pd
        from _api_config import get_tushare_pro
        from datetime import datetime as dt, timedelta
        pro = get_tushare_pro()

        for offset in range(3):
            attempt_date = (dt.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                # 获取概念板块日线（量价数据）
                df_daily = pro.dc_daily(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,pct_change,vol,amount,turnover_rate'
                )
                # 获取概念名称（ts_code → name 映射）
                df_index = pro.dc_index(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,name'
                )
                if df_daily is not None and len(df_daily) > 0 and df_index is not None and len(df_index) > 0:
                    # 合并：按 ts_code 关联，得到 name + 量价数据
                    df = pd.merge(df_index, df_daily, on='ts_code', how='inner')
                    if len(df) == 0:
                        continue
                    # 过滤：量比 > 0 且换手率 > 0.5%（排除僵尸概念）
                    avg_vol = df['vol'].median()
                    df = df[(df['vol'] > avg_vol * 0.1) | (df['turnover_rate'] > 0.5)]
                    # 按涨幅降序取 Top 50
                    df = df.sort_values('pct_change', ascending=False).head(50)
                    concepts = df['name'].tolist()
                    top_pct = df.iloc[0]['pct_change'] if len(df) > 0 else 0
                    print(f"[概念词汇] ✅ 从 dc_daily 加载 Top {len(concepts)} 个概念 "
                          f"(领涨: {concepts[0] if concepts else '-'} +{top_pct:.1f}%)", file=sys.stderr)
                    break
            except Exception:
                continue

        if concepts:
            _concept_vocabulary = concepts
        else:
            print("[概念词汇] ⚠️ dc_daily 无数据", file=sys.stderr)
    except ImportError:
        print("[概念词汇] ⚠️ tushare 未安装", file=sys.stderr)
    except Exception as e:
        print(f"[概念词汇] ⚠️ 初始化失败: {e}", file=sys.stderr)

    _vocabulary_loaded = True


def supplement_concept_vocabulary(extra_concepts: List[str]):
    """补充外部概念名（如盘中 concept-fund-flow 实时刷新）"""
    global _concept_vocabulary
    existing = set(_concept_vocabulary)
    added = 0
    for c in extra_concepts:
        if c and c not in existing:
            _concept_vocabulary.append(c)
            existing.add(c)
            added += 1
    if added:
        print(f"[概念词汇] ➕ 补充 {added} 个概念，总计 {len(_concept_vocabulary)} 个", file=sys.stderr)


def _fuzzy_match_in_list(raw_lower: str, candidates_list: List[str]) -> str:
    """在候选列表中模糊匹配，返回最佳匹配或空字符串"""
    # 精确匹配
    for c in candidates_list:
        if c.strip().lower() == raw_lower:
            return c
    # 包含匹配
    best = []
    for c in candidates_list:
        c_lower = c.strip().lower()
        if raw_lower in c_lower or c_lower in raw_lower:
            best.append((len(c), c))
    if best:
        best.sort(key=lambda x: -x[0])
        return best[0][1]
    return ""


def normalize_concept_name(raw_name: str, vocabulary: List[str] = None) -> str:
    """
    将 AI 输出的概念名映射到标准概念名。

    词汇表来源: dc_daily 概念板块日线（与 stock_concept_map 概念名一致）。
    匹配策略: 精确匹配 → 包含匹配 → 返回原值。
    """
    global _vocabulary_loaded
    if not _vocabulary_loaded:
        _init_concept_vocabulary()

    if not vocabulary:
        vocabulary = _concept_vocabulary
    if not vocabulary:
        return raw_name

    matched = _fuzzy_match_in_list(raw_name.strip().lower(), vocabulary)
    if matched:
        if matched.strip().lower() != raw_name.strip().lower():
            print(f"[概念标准化] 「{raw_name}」→「{matched}」", file=sys.stderr)
        return matched

    # 词汇表未匹配时，回退到 stock_concept_map 全量查询（处理简称如"AI"→"AI芯片"）
    try:
        import sqlite3
        db = _ws / "data" / "stock_pool.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            all_concepts = [r[0] for r in conn.execute("SELECT DISTINCT concept_name FROM stock_concept_map").fetchall()]
            conn.close()
            db_match = _fuzzy_match_in_list(raw_name.strip().lower(), all_concepts)
            if db_match:
                print(f"[概念标准化] 「{raw_name}」→「{db_match}」(DB兜底)", file=sys.stderr)
                return db_match
    except Exception:
        pass

    print(f"[概念标准化] ⚠️ 「{raw_name}」未匹配（词汇表{len(vocabulary)}个）", file=sys.stderr)
    return raw_name


def normalize_concepts_batch(raw_concepts: List[str], vocabulary: List[str] = None) -> List[str]:
    """批量标准化概念名，去重"""
    global _vocabulary_loaded
    if not _vocabulary_loaded:
        _init_concept_vocabulary()
    if not vocabulary:
        vocabulary = _concept_vocabulary
    seen = set()
    result = []
    for c in raw_concepts:
        norm = normalize_concept_name(c, vocabulary)
        if norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def get_concept_vocabulary_sample(limit: int = 50) -> str:
    """获取概念词汇表样本（用于 AI prompt）"""
    global _vocabulary_loaded
    if not _vocabulary_loaded:
        _init_concept_vocabulary()
    if not _concept_vocabulary:
        return ""
    return "、".join(_concept_vocabulary[:limit])


# ============= 测试入口 =============

if __name__ == '__main__':
    print("=" * 60)
    print("📰 Marcus 新闻分析模块测试")
    print("=" * 60)
    
    # 测试 1: 完整分析
    print("\n【测试 1】完整新闻分析（AI）")
    result = get_news_analysis(news_limit=20, use_ai=True)
    print(f"情绪分数：{result['sentiment']['score']:.1f}")
    print(f"正面/负面/中性：{result['sentiment']['positive']}/{result['sentiment']['negative']}/{result['sentiment']['neutral']}")
    print(f"S/A/B/C 级：{result['summary']['s_level_count']}/{result['summary']['a_level_count']}/{result['summary']['b_level_count']}/{result['summary']['c_level_count']}")
    print(f"热点概念：{result['sentiment'].get('hot_concepts', result['sentiment'].get('hot_industries', []))[:3]}")
    
    # 测试 2: 快速分析
    print("\n【测试 2】快速情绪分析（关键词）")
    ak = AKShareEnhancedEngine(data_dir=str(DATA_DIR))
    news = ak.get_finance_news(limit=20)
    simple_result = get_news_sentiment_simple(news)
    print(f"情绪分数：{simple_result['score']:.1f}")
    print(f"正面/负面/中性：{simple_result['positive']}/{simple_result['negative']}/{simple_result['neutral']}")
    
    print("\n" + "=" * 60)
    print("✓ 测试完成")
    print("=" * 60)
