#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 盘前扫描脚本 (策略联动版)
执行时间：每个交易日 9:00

功能:
1. 分析隔夜美股行情 (道指/纳指/中概股)
2. 获取 A50 期货和汇率数据
3. 收集 overnight 催化剂
4. 制定初步策略 (仓位方向 + 风险预警)
5. 保存到策略链
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# 加载项目根目录 .env 到 os.environ（不覆盖已有环境变量）
def _load_env():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value

_load_env()

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))  # 项目根目录，支持 from core.xxx 导入
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "utils"))
from workspace_detector import WORKSPACE, get_akshare_dir, get_xueqiu_dir, get_core_dir, get_data_dir

AKSHARE_DIR = get_akshare_dir()
DATA_DIR = get_data_dir()
XUEQIU_DIR = get_xueqiu_dir()
CORE_DIR = get_core_dir()

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR / "utils"))
sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(XUEQIU_DIR))

# 美股联动分析
from us_market_linkage import generate_us_market_report
# 策略链管理
from strategy_chain import StrategyChain
# 新闻情绪分析
from akshare_engine_enhanced import AKShareEnhancedEngine


# ── 方案 B：概念板块行情获取（量价驱动） ──────────────────────────

def get_hot_concepts_by_flow(top_n: int = 10) -> list:
    """
    获取概念板块实时行情排名。

    数据源（优先级）:
    1. 东财 push2 实时接口（盘中实时，含涨跌幅+资金拆分明细+板块广度+领涨股）
    2. Tushare dc_daily（降级兜底，含历史成交量/换手率补充）

    逻辑: 按 pct_change 降序（涨幅最大），取 top_n 个概念。
    概念名与 stock_concept_map 完全一致。

    Returns:
        list[dict]: [
            {'name': '半导体', 'pct_change': 2.35, 'main_net_fmt': '+18.00亿',
             'flow_nature': '温和流入', 'advancing': 160, 'declining': 16,
             'lead_stock_name': '杰华特', 'lead_stock_code': '688141', ...},
            ...
        ]
    """
    # ── 优先：东财 push2 实时接口 ──
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
        from utils.em_sector_flow import get_top_change_sectors, classify_flow_nature

        em_sectors = get_top_change_sectors("concept", top_n=top_n, use_cache=True)
        concepts = []
        for es in em_sectors:
            concepts.append({
                'name': es['name'],
                'pct_change': es['pct_change'],
                'main_net': es['main_net'],
                'main_net_fmt': es['main_net_fmt'],
                'main_net_rate': es['main_net_rate'],
                'super_large_net': es['super_large_net'],
                'large_net': es['large_net'],
                'medium_net': es['medium_net'],
                'small_net': es['small_net'],
                'advancing': es['advancing'],
                'declining': es['declining'],
                'total_stocks': es['total_stocks'],
                'lead_stock_name': es.get('lead_stock_name', ''),
                'lead_stock_code': es.get('lead_stock_code', ''),
                'flow_nature': classify_flow_nature(es['main_net'], es['main_net_rate']),
                'source': 'em_push2_realtime',
                # Tushare 补充字段默认值
                'vol': 0, 'amount': 0, 'turnover_rate': 0, 'trade_date': '',
            })

        debug_names = [(c['name'], f"{c['pct_change']:+.1f}%",
                        c.get('main_net_fmt', 'N/A'), c.get('flow_nature', ''))
                       for c in concepts]
        print(f"[热点概念] ✅ 东财实时 Top {len(concepts)}: {debug_names}", file=sys.stderr)

        # ── 补充 Tushare 成交量数据（非阻塞，失败不影响）──
        try:
            import tushare as ts
            import pandas as pd
            token = os.getenv("TUSHARE_TOKEN", "")
            if token:
                from datetime import datetime as dt, timedelta
                from core._api_config import get_tushare_pro as _get_ts_pro; pro = _get_ts_pro()
                now = dt.now()
                for offset in range(3):
                    attempt_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
                    attempt_dt = now - timedelta(days=offset)
                    if attempt_dt.weekday() >= 5:
                        continue
                    try:
                        df_daily = pro.dc_daily(
                            trade_date=attempt_date, idx_type='概念板块',
                            fields='ts_code,pct_change,name,vol,amount,turnover_rate'
                        )
                        if df_daily is not None and len(df_daily) > 0:
                            ts_map = {}
                            for _, row in df_daily.iterrows():
                                ts_map[row.get('name', '')] = row
                                name = row.get('name', '')
                                for sfx in ['概念', '板块']:
                                    if name.endswith(sfx):
                                        ts_map[name[:-len(sfx)]] = row
                            for c in concepts:
                                matched = ts_map.get(c['name'])
                                if not matched:
                                    for ts_name, ts_row in ts_map.items():
                                        if ts_name in c['name'] or c['name'] in ts_name:
                                            matched = ts_row
                                            break
                                if matched is not None:
                                    c['vol'] = float(matched.get('vol', 0) or 0)
                                    c['amount'] = float(matched.get('amount', 0) or 0)
                                    c['turnover_rate'] = round(float(matched.get('turnover_rate', 0) or 0), 2)
                                    c['trade_date'] = attempt_date
                                    c['source'] = 'em_realtime+tushare_supplement'
                            print(f"[热点概念] ✅ Tushare 量价补充完成 (date={attempt_date})", file=sys.stderr)
                            break
                    except Exception:
                        continue
        except Exception as e:
            print(f"[热点概念] Tushare 量价补充失败(非致命): {e}", file=sys.stderr)

        return concepts

    except Exception as e:
        print(f"[热点概念] ⚠️ 东财 push2 获取失败，降级到 Tushare dc_daily: {e}", file=sys.stderr)

    # ── 降级：纯 Tushare dc_daily ──
    try:
        import tushare as ts
        import pandas as pd
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise EnvironmentError("TUSHARE_TOKEN 未配置")
        from core._api_config import get_tushare_pro as _get_ts_pro; pro = _get_ts_pro()

        from datetime import datetime as dt, timedelta
        now = dt.now()

        for offset in range(3):
            attempt_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df_daily = pro.dc_daily(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,pct_change,vol,amount,turnover_rate'
                )
                df_index = pro.dc_index(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,name'
                )
                if df_daily is not None and len(df_daily) > 0 and df_index is not None and len(df_index) > 0:
                    df = pd.merge(df_index, df_daily, on='ts_code', how='inner')
                    df = df.sort_values('pct_change', ascending=False)
                    print(f"[热点概念] ✅ Tushare dc_daily {attempt_date}, {len(df)} 个概念", file=sys.stderr)
                    break
            except Exception as e:
                print(f"[热点概念] {attempt_date} 无数据: {e}", file=sys.stderr)
                continue
        else:
            print("[热点概念] ⚠️ 最近3个交易日均无数据", file=sys.stderr)
            return []

        concepts = []
        for _, row in df.head(top_n).iterrows():
            concepts.append({
                'name': row['name'],
                'pct_change': round(float(row.get('pct_change', 0) or 0), 2),
                'vol': float(row.get('vol', 0) or 0),
                'amount': float(row.get('amount', 0) or 0),
                'turnover_rate': round(float(row.get('turnover_rate', 0) or 0), 2),
                'trade_date': attempt_date,
                'source': 'tushare_daily',
            })

        debug_names = [(c['name'], f"{c['pct_change']:+.1f}%") for c in concepts]
        print(f"[热点概念] ✅ Tushare Top {len(concepts)}: {debug_names}", file=sys.stderr)
        return concepts

    except Exception as e:
        print(f"[热点概念] ⚠️ 所有数据源均失败: {e}", file=sys.stderr)
        return []


def get_hot_concepts_by_fund_flow(top_n: int = 10) -> list:
    """
    按主力资金净流入排名获取概念板块（资金驱动选股）。

    与 get_hot_concepts_by_flow 不同，此函数按主力净流入排序而非涨跌幅。
    适合「资金先行」策略：找到主力资金大幅流入但涨幅尚未完全体现的板块。

    Returns:
        list[dict]: 与 get_hot_concepts_by_flow 相同结构，但按 main_net 降序排列
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
        from utils.em_sector_flow import get_top_inflow_sectors, classify_flow_nature

        em_sectors = get_top_inflow_sectors("concept", top_n=top_n, use_cache=True)
        concepts = []
        for es in em_sectors:
            concepts.append({
                'name': es['name'],
                'pct_change': es['pct_change'],
                'vol': 0, 'amount': 0, 'turnover_rate': 0, 'trade_date': '',
                'main_net': es['main_net'],
                'main_net_fmt': es['main_net_fmt'],
                'main_net_rate': es['main_net_rate'],
                'super_large_net': es['super_large_net'],
                'large_net': es['large_net'],
                'medium_net': es['medium_net'],
                'small_net': es['small_net'],
                'advancing': es['advancing'],
                'declining': es['declining'],
                'total_stocks': es['total_stocks'],
                'flow_nature': classify_flow_nature(es['main_net'], es['main_net_rate']),
                'source': 'em_realtime_inflow',
            })

        debug_names = [(c['name'], c.get('main_net_fmt', 'N/A'), f"{c['pct_change']:+.1f}%")
                       for c in concepts]
        print(f"[热点概念·资金] ✅ Top {len(concepts)}: {debug_names}", file=sys.stderr)
        return concepts
    except Exception as e:
        print(f"[热点概念·资金] ⚠️ 东财 push2 获取失败: {e}", file=sys.stderr)
        return []


# ── Layer 2: 逻辑验证层（方案 A：新闻密度趋势 + 负面检测） ─────

def get_concept_news_density(concept_name: str, window_hours: int = 24) -> dict:
    """
    按概念名检索 news.db，计算新闻密度趋势和情绪分布。

    搜索策略（fuzzy matching）:
    - category 精确匹配（优先，news.db 已有分类标签）
    - title 或 content 模糊匹配（兜底）

    Returns: {
        'total': int,           # 总新闻数
        'positive': int,        # 正面新闻数
        'negative': int,        # 负面新闻数
        'neutral': int,         # 中性新闻数
        'density_4h': int,      # 近4小时密度
        'density_8h': int,      # 近8小时密度
        'density_24h': int,     # 近24小时密度
        'trend': 'rising'|'stable'|'declining',  # 密度趋势
        'titles': [str],        # 代表性新闻标题 (最多5条)
        'has_negative_risk': bool,  # 是否有明显负面风险
    }
    """
    import sqlite3
    from datetime import datetime as dt, timedelta

    try:
        news_db = DATA_DIR / "news.db"
        if not news_db.exists():
            return {'total': 0, 'positive': 0, 'negative': 0, 'neutral': 0,
                    'density_4h': 0, 'density_8h': 0, 'density_24h': 0,
                    'trend': 'stable', 'titles': [], 'has_negative_risk': False}

        conn = sqlite3.connect(str(news_db))
        conn.row_factory = sqlite3.Row
        now = dt.now()

        # 模糊搜索：多模式 fallback
        # "半导体概念" → 先搜原词，无结果则去后缀("概念"/"板块")、去括号、取首词
        _suffixes_to_strip = ["概念", "板块", "行业", "产业"]
        patterns = [concept_name]
        if "(" in concept_name:
            patterns.append(concept_name.split("(")[0])  # "光刻机(胶)" → "光刻机"
        for sfx in _suffixes_to_strip:
            if concept_name.endswith(sfx):
                patterns.append(concept_name[:-len(sfx)])  # "半导体概念" → "半导体"
        patterns = list(dict.fromkeys(patterns))  # 去重

        cutoff = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M")
        rows = []
        for pattern in patterns:
            like_pattern = f"%{pattern}%"
            # 排除纯数字匹配和过短关键词
            if len(pattern.replace("(", "").replace(")", "").strip()) < 2:
                continue
            c = conn.execute("""
                SELECT title, sentiment, publish_time, category
                FROM news
                WHERE publish_time >= ?
                  AND (category LIKE ? OR title LIKE ? OR content LIKE ?)
                ORDER BY publish_time DESC
                LIMIT 50
            """, (cutoff, like_pattern, like_pattern, like_pattern))
            batch = c.fetchall()
            if batch:
                rows = batch
                if pattern != concept_name:
                    print(f"[Layer2] 🔍 '{concept_name}' → fuzzy match '{pattern}' ({len(rows)}条)", file=sys.stderr)
                break
        conn.close()

        if not rows:
            return {'total': 0, 'positive': 0, 'negative': 0, 'neutral': 0,
                    'density_4h': 0, 'density_8h': 0, 'density_24h': 0,
                    'trend': 'stable', 'titles': [], 'has_negative_risk': False}

        total = len(rows)
        sentiments = [r['sentiment'] for r in rows]
        pos = sum(1 for s in sentiments if s == 'positive')
        neg = sum(1 for s in sentiments if s == 'negative')
        neu = sum(1 for s in sentiments if s == 'neutral')

        # 时间窗口密度
        density_4h = sum(1 for r in rows
                         if r['publish_time'] and str(r['publish_time']) >= (now - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M"))
        density_8h = sum(1 for r in rows
                         if r['publish_time'] and str(r['publish_time']) >= (now - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"))
        density_24h = total

        # 密度趋势判断：按比例比较（消除绝对数量差异）
        # density_4h/4 vs density_8h/8: 前者大 → 近期加速 → rising
        rate_4h = density_4h / 4.0
        rate_8h = (density_8h - density_4h) / 4.0 if density_8h > density_4h else 0.01
        if rate_4h > rate_8h * 1.3:
            trend = 'rising'
        elif rate_4h < rate_8h * 0.5:
            trend = 'declining'
        else:
            trend = 'stable'

        # 负面风险检测：负面占比 > 40% 或 出现连续负面
        has_negative_risk = (neg / max(total, 1) > 0.4) or (neg >= 3)

        # 代表标题
        titles = [r['title'][:80] for r in rows[:5]]

        return {
            'total': total,
            'positive': pos,
            'negative': neg,
            'neutral': neu,
            'density_4h': density_4h,
            'density_8h': density_8h,
            'density_24h': density_24h,
            'trend': trend,
            'titles': titles,
            'has_negative_risk': has_negative_risk,
        }
    except Exception as e:
        print(f"[Layer2] {concept_name} 新闻密度查询失败: {e}", file=sys.stderr)
        return {'total': 0, 'positive': 0, 'negative': 0, 'neutral': 0,
                'density_4h': 0, 'density_8h': 0, 'density_24h': 0,
                'trend': 'stable', 'titles': [], 'has_negative_risk': False}


def score_concept_confidence(hot_concepts: list) -> list:
    """
    Layer 2: 对每个资金热概念做逻辑验证。

    验证维度:
    ① 新闻密度是否上升？（催化剂接力 → +1 级）
    ② 是否有密集负面？（避雷 → 降级或排除）
    ③ 新闻情绪与资金方向是否一致？（逻辑自洽）

    置信度分级:
    - high:      密度 rising + 无负面风险 + 情绪偏正面 → "资金先行+新闻接力"
    - standard:  密度 stable + 无负面风险 → 标准信号
    - downgraded: 密度 declining 或有负面 → 需警惕
    - excluded:   密集负面 >40% 且 density declining → 直接排除

    Returns:
        list[dict]: 输入列表附加 confidence/trend/news 字段
    """
    results = []
    for c in hot_concepts:
        name = c['name']
        density = get_concept_news_density(name, window_hours=24)

        # ── 评分逻辑 ──
        total = density['total']
        pos = density['positive']
        neg = density['negative']
        trend = density['trend']
        has_risk = density['has_negative_risk']

        # 正面情绪占比
        pos_ratio = pos / max(total, 1)

        # 置信度判定
        if has_risk and trend == 'declining':
            confidence = 'excluded'
            reason = f"密集负面({neg}/{total})且趋势减弱"
        elif has_risk:
            confidence = 'downgraded'
            reason = f"有负面风险({neg}/{total})，趋势={trend}"
        elif trend == 'declining' and total > 0:
            confidence = 'downgraded'
            reason = f"新闻密度下降(4h={density['density_4h']}, 8h={density['density_8h']})"
        elif trend == 'rising' and total >= 3 and pos_ratio >= 0.4:
            confidence = 'high'
            reason = f"共振信号: 密度上升({density['density_4h']}/4h) + 正面{pos}/{total}"
        elif trend == 'rising' and total >= 2:
            confidence = 'high'
            reason = f"密度上升，催化剂在接力"
        elif pos_ratio >= 0.5 and total >= 2:
            confidence = 'high'
            reason = f"情绪正面({pos}/{total})"
        elif total >= 2:
            confidence = 'standard'
            reason = f"有新闻覆盖({total}条)"
        else:
            confidence = 'standard'
            reason = "新闻覆盖不足，仅靠资金信号"

        entry = dict(c)
        entry.update({
            'confidence': confidence,
            'reason': reason,
            'news_total': total,
            'news_positive': pos,
            'news_negative': neg,
            'news_trend': trend,
            'news_titles': density['titles'][:3],
        })
        results.append(entry)

        icon = {'high': '🔥', 'standard': '✅', 'downgraded': '⚠️', 'excluded': '❌'}.get(confidence, '?')
        print(f"[Layer2] {icon} {name}: {confidence} | {reason} | "
              f"trend={trend} pos={pos}/{total}", file=sys.stderr)

    return results


def analyze_overnight_news() -> dict:
    """
    分析 overnight 新闻情绪
    
    Returns:
        dict: {score, positive, negative, neutral, catalysts, risks, hot_concepts}
    """
    try:
        # 【修复】直接从 news.db 读取已评分的新闻，不用 AKShare API
        ak_engine = AKShareEnhancedEngine(data_dir=str(DATA_DIR))
        news_list = ak_engine.get_recent_news_from_db(hours=16, limit=100)

        if not news_list:
            return {
                'score': 50,
                'positive': 0,
                'negative': 0,
                'neutral': 0,
                'total': 0,
                'catalysts': [],
                'risks': [],
                'hot_concepts': []
            }

        # 用数据库已有的 sentiment 字段聚合情绪
        positive_count = sum(1 for n in news_list if n.get('sentiment') == 'positive')
        negative_count = sum(1 for n in news_list if n.get('sentiment') == 'negative')
        neutral_count = sum(1 for n in news_list if n.get('sentiment') == 'neutral')
        total = len(news_list)

        # 综合情绪分：基于 sentiment 比例计算
        sentiment_score_map = {'positive': 75, 'neutral': 50, 'negative': 25}
        score_sum = sum(sentiment_score_map.get(n.get('sentiment', 'neutral'), 50) for n in news_list)
        score = score_sum / total if total > 0 else 50

        # 催化剂：取 positive 新闻的标题
        catalysts = []
        for n in news_list:
            if n.get('sentiment') == 'positive':
                title = n.get('title', '')
                if title and title not in catalysts:
                    catalysts.append(title[:50])
                    if len(catalysts) >= 5:
                        break

        # 风险：取 negative 新闻的标题
        risks = []
        for n in news_list:
            if n.get('sentiment') == 'negative':
                title = n.get('title', '')
                if title and title not in risks:
                    risks.append(title[:50])
                    if len(risks) >= 5:
                        break

        # 热点概念：按行业聚合 positive 新闻数量
        industry_count = {}
        for n in news_list:
            cat = n.get('category', '')
            if cat and cat not in ('财经综合', '个股新闻', '综合'):
                weight = 2 if n.get('sentiment') == 'positive' else 1 if n.get('sentiment') == 'neutral' else 0.5
                industry_count[cat] = industry_count.get(cat, 0) + weight

        hot_concepts = [ind[0] for ind in sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:5]]

        return {
            'score': round(score, 1),
            'positive': positive_count,
            'negative': negative_count,
            'neutral': neutral_count,
            'total': total,
            'catalysts': catalysts[:5],
            'risks': risks[:5],
            'hot_concepts': hot_concepts
        }

    except Exception as e:
        print(f"[新闻分析] 失败：{e}", file=sys.stderr)
        return {
            'score': 50,
            'positive': 0,
            'negative': 0,
            'neutral': 0,
            'total': 0,
            'catalysts': [],
            'risks': [],
            'hot_concepts': []
        }

class SectorConfigManager:
    """
    板块配置管理器（非硬编码，数据存储在 trades.db）
    首次加载时从 DEFAULT_SECTOR_LINKAGE 种子数据，之后读写 DB。
    """

    _instance = None
    _db_path = None
    _cache = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if SectorConfigManager._db_path is None:
            SectorConfigManager._db_path = (
                Path(__file__).resolve().parents[1]
                / "data" / "trades.db"
            )
        if SectorConfigManager._cache is None:
            self._load_or_seed()

    def _conn(self):
        import sqlite3
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._db_path))

    def _load_or_seed(self):
        import json
        import sqlite3
        conn = self._conn()
        try:
            rows = conn.execute("SELECT sector_key, name, indices, etfs, weight, stocks, etf_codes FROM sector_config").fetchall()
            if not rows:
                # 首次：种子 DEFAULT_SECTOR_LINKAGE
                now = datetime.now().isoformat()
                for key, cfg in DEFAULT_SECTOR_LINKAGE.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO sector_config "
                        "(sector_key, name, indices, etfs, weight, stocks, etf_codes, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, cfg['name'], json.dumps(cfg['indices']),
                         json.dumps(cfg['etfs']), cfg['weight'],
                         json.dumps(cfg['stocks']),
                         json.dumps(cfg.get('etf_codes', [])), now))
                conn.commit()
                print(f"[板块配置] 首次加载，已种子 {len(DEFAULT_SECTOR_LINKAGE)} 个板块")
                rows = conn.execute(
                    "SELECT sector_key, name, indices, etfs, weight, stocks, etf_codes FROM sector_config"
                ).fetchall()
        finally:
            conn.close()

        cache = {}
        for row in rows:
            cache[row[0]] = {
                'name': row[1],
                'indices': json.loads(row[2]),
                'etfs': json.loads(row[3]),
                'weight': row[4],
                'stocks': json.loads(row[5]),
                'etf_codes': json.loads(row[6]) if row[6] else [],
            }
        SectorConfigManager._cache = cache

    def get(self, key: str) -> dict:
        return SectorConfigManager._cache.get(key, {
            'name': key, 'indices': [], 'etfs': [], 'weight': 0.1, 'stocks': []
        })

    def items(self):
        return SectorConfigManager._cache.items()

    def update_stocks(self, key: str, stocks: list):
        """动态更新板块成分股（写入 DB）"""
        import json
        import sqlite3
        if key in SectorConfigManager._cache:
            SectorConfigManager._cache[key]['stocks'] = stocks
            conn = self._conn()
            conn.execute(
                "UPDATE sector_config SET stocks = ?, updated_at = ? WHERE sector_key = ?",
                (json.dumps(stocks), datetime.now().isoformat(), key)
            )
            conn.commit()
            conn.close()

    def sync_from_etf(self, key: str = None, top_n: int = 5):
        """
        从关联的 ETF 成分股自动同步板块成分股到 sector_config。

        Args:
            key: 板块 key，为 None 时同步全部有 ETF 配置的板块
            top_n: 每个 ETF 取前几只（按市值权重）
        """
        import os

        token = os.getenv('TUSHARE_TOKEN')
        if not token:
            raise EnvironmentError("TUSHARE_TOKEN 未在环境变量或 .env 中配置")
        import tushare as ts
        from core._api_config import get_tushare_pro as _get_ts_pro; pro = _get_ts_pro()

        keys_to_sync = [key] if key else [
            k for k, v in SectorConfigManager._cache.items() if v.get('etf_codes')
        ]

        total_updated = 0
        for k in keys_to_sync:
            cfg = SectorConfigManager._cache.get(k, {})
            etf_codes = cfg.get('etf_codes', [])
            if not etf_codes:
                continue

            all_stocks = {}  # symbol -> weight
            for etf_code in etf_codes:
                try:
                    df = pro.fund_portfolio(ts_code=etf_code)
                    if df.empty:
                        continue
                    # 取最新报告期，去重
                    latest = df.sort_values('ann_date', ascending=False).drop_duplicates('symbol')
                    top = latest.nlargest(top_n, 'stk_mkv_ratio')
                    for _, row in top.iterrows():
                        sym = row['symbol'].replace('.SH', '').replace('.SZ', '')
                        # A股过滤（6位数字）
                        if sym.isdigit() and len(sym) == 6:
                            all_stocks[sym] = max(all_stocks.get(sym, 0), row['stk_mkv_ratio'])
                except Exception as e:
                    print(f"[ETF同步] {k}/{etf_code} 失败: {e}")

            if all_stocks:
                sorted_stocks = sorted(all_stocks.items(), key=lambda x: x[1], reverse=True)
                new_stocks = [s for s, _ in sorted_stocks[:top_n * len(etf_codes)]]
                self.update_stocks(k, new_stocks)
                print(f"[ETF同步] ✅ {k}: {new_stocks}")
                total_updated += 1
            else:
                print(f"[ETF同步] ⚠️ {k}: 无ETF成分股数据，保留原配置")

        return total_updated


# ---------- 懒加载：首次访问时初始化 ----------
_sector_mgr = None

def _get_sector_mgr():
    global _sector_mgr
    if _sector_mgr is None:
        _sector_mgr = SectorConfigManager()
    return _sector_mgr


# 兼容旧代码：SECTOR_LINKAGE 属性代理到 Manager
class _SECTOR_LINKAGE_VIEW:
    """SECTOR_LINKAGE 的 dict-like 视图，代理到 DB-backed SectorConfigManager"""
    def items(self):
        return _get_sector_mgr().items()

    def get(self, key, default=None):
        return _get_sector_mgr().get(key) if default is None else _get_sector_mgr().get(key) or default

    def keys(self):
        return _get_sector_mgr()._cache.keys()

    def values(self):
        return _get_sector_mgr()._cache.values()

    def __len__(self):
        return len(_get_sector_mgr()._cache)

    def __iter__(self):
        return iter(_get_sector_mgr()._cache)


SECTOR_LINKAGE = _SECTOR_LINKAGE_VIEW()


# 【修复】stocks 字段已废弃，watchlist 必须来自 stock_pool_manager 实时查询
# 硬编码 fallback 会绕过催化剂验证，导致无催化垃圾股混入观察列表
DEFAULT_SECTOR_LINKAGE = {
    '科技': {
        'indices': ['纳斯达克', '标普 500'],
        'etfs': ['KWEB'],
        'weight': 0.8,
        'stocks': [],  # 废弃，不用硬编码
        'name': '科技/半导体'
    },
    '中概互联': {
        'indices': ['纳斯达克'],
        'etfs': ['KWEB', 'PGJ'],
        'weight': 0.9,
        'stocks': [],  # 废弃，不用硬编码
        'name': '中概互联'
    },
    '新能源': {
        'indices': ['纳斯达克'],
        'etfs': [],
        'weight': 0.5,
        'stocks': [],
        'name': '新能源'
    },
    '医药': {
        'indices': ['纳斯达克'],
        'etfs': [],
        'weight': 0.4,
        'stocks': [],
        'name': '医药/生物科技'
    },
    '金融': {
        'indices': [],
        'etfs': [],
        'weight': 0.1,
        'stocks': [],
        'name': '金融'
    },
    '消费': {
        'indices': [],
        'etfs': [],
        'weight': 0.1,
        'stocks': [],
        'name': '消费'
    },
    '出口链': {
        'indices': [],
        'etfs': [],
        'weight': 0.4,
        'stocks': [],
        'name': '出口链'
    },
}


def calculate_sector_sentiment(sector: str, us_report: dict, news_sentiment: dict) -> dict:
    """
    计算单个板块的外盘影响分数
    
    Args:
        sector: 板块名称
        us_report: 美股联动报告
        news_sentiment: 新闻情绪
    
    Returns:
        dict: {sector, external_impact, news_impact, combined_score, stance}
    """
    config = SECTOR_LINKAGE.get(sector, {'weight': 0.1, 'indices': [], 'etfs': []})
    
    # 1. 计算外盘影响
    external_score = 50  # 基准
    us_indices = us_report.get('us_market', {}).get('indices', {})
    us_etfs = us_report.get('us_market', {}).get('china_etfs', [])
    
    # 关联指数
    for idx_name in config.get('indices', []):
        if idx_name in us_indices:
            idx_chg = us_indices[idx_name].get('change_pct', 0)
            external_score += idx_chg * config['weight'] * 10
    
    # 关联 ETF
    for etf in config.get('etfs', []):
        etf_data = next((e for e in us_etfs if e['symbol'] == etf), None)
        if etf_data:
            etf_chg = etf_data.get('change_pct', 0)
            external_score += etf_chg * config['weight'] * 15
    
    # 汇率影响 (出口链)
    if sector == '出口链':
        usd_cny = us_report.get('us_market', {}).get('usd_cny', {})
        # 人民币贬值利好出口
        external_score += 5  # 简化处理
    
    external_score = max(0, min(100, external_score))
    
    # 2. 新闻情绪影响
    news_score = news_sentiment.get('score', 50)
    
    # 3. 综合分数 (外盘 + 新闻)
    if config['weight'] >= 0.5:  # 高关联板块
        combined = external_score * 0.5 + news_score * 0.5
    elif config['weight'] >= 0.3:  # 中关联板块
        combined = external_score * 0.3 + news_score * 0.7
    else:  # 低关联板块
        combined = external_score * 0.1 + news_score * 0.9
    
    # 4. 确定立场
    if combined >= 70:
        stance = '🟢 超配'
    elif combined >= 55:
        stance = '🟡 标配'
    elif combined >= 45:
        stance = '⚪ 低配'
    elif combined >= 30:
        stance = '🟠 减仓'
    else:
        stance = '🔴 回避'
    
    return {
        'sector': config.get('name', sector),
        'external_impact': round(external_score, 1),
        'news_impact': round(news_score, 1),
        'combined_score': round(combined, 1),
        'stance': stance,
        'weight': config['weight']
    }


def analyze_all_sectors(us_report: dict, news_sentiment: dict) -> list:
    """
    分析所有板块
    
    Returns:
        list: 各板块分析结果
    """
    results = []
    for sector in SECTOR_LINKAGE.keys():
        result = calculate_sector_sentiment(sector, us_report, news_sentiment)
        results.append(result)
    
    # 按综合分数排序
    results.sort(key=lambda x: x['combined_score'], reverse=True)
    return results


def generate_pre_market_report() -> str:
    import json

    # 初始化
    report = ""
    initial_strategy = {'stance': 'N/A', 'risk_warning': 'N/A'}

    # ═══════════ Layer 1: 趋势确认层（方案 B：概念资金流向）═══════════
    print("[盘前扫描] Layer1: 获取概念资金流向 (Tushare)...", file=sys.stderr)
    raw_concepts = get_hot_concepts_by_flow(top_n=10)

    # ═══════════ Layer 2: 逻辑验证层（方案 A：新闻密度 + 负面检测）════
    print("[盘前扫描] Layer2: 逻辑验证 (新闻密度+负面检测)...", file=sys.stderr)
    if raw_concepts:
        scored_concepts = score_concept_confidence(raw_concepts)
        # 过滤：仅保留 high + standard 置信度
        confirmed = [c for c in scored_concepts if c['confidence'] in ('high', 'standard')]
        downgraded = [c for c in scored_concepts if c['confidence'] == 'downgraded']
        excluded = [c for c in scored_concepts if c['confidence'] == 'excluded']
        hot_concept_names = [c['name'] for c in confirmed[:5]]  # 取 Top 5 确信概念

        print(f"[Layer2] 统计: 🔥high={sum(1 for c in scored_concepts if c['confidence']=='high')} "
              f"✅standard={sum(1 for c in scored_concepts if c['confidence']=='standard')} "
              f"⚠️downgraded={len(downgraded)} ❌excluded={len(excluded)}", file=sys.stderr)
    else:
        scored_concepts = []
        confirmed = []
        hot_concept_names = []

    # ═══════════ 新闻叙事背景（全局 catalysts + risks）═══════════
    print("[盘前扫描] 获取新闻叙事背景...", file=sys.stderr)
    news_sentiment = analyze_overnight_news()
    catalysts = news_sentiment.get('catalysts', [])

    # ═══════════ 美股联动 + 板块情绪 ═══════════
    print("[盘前扫描] 分析美股隔夜行情...", file=sys.stderr)
    us_report = generate_us_market_report()

    print("[盘前扫描] 分析板块情绪...", file=sys.stderr)
    sector_analysis = analyze_all_sectors(us_report, news_sentiment)

    # ═══════════ 写入策略链 ═══════════
    try:
        chain = StrategyChain()
        initial_strategy = chain.get_current_strategy()
    except Exception as e:
        print(f"[盘前扫描] ⚠️ 策略链初始化失败: {e}", file=sys.stderr)

    initial_strategy = dict(initial_strategy) if initial_strategy else {}
    initial_strategy['hot_concepts'] = confirmed  # Layer2 过滤后的确信概念
    initial_strategy['all_concepts'] = scored_concepts  # 完整评分的概念（供下游参考）
    try:
        report_dict = {
            "us_market": us_report.get('us_market', {}),
            "sentiment": us_report.get('sentiment', {}),
            "initial_strategy": initial_strategy,
            "hot_concepts": confirmed,       # Layer2 过滤后
            "all_concepts": scored_concepts, # 全部评分（含降级/排除）
        }
        chain.set_pre_market_strategy(report_dict)
        print(f"[盘前策略] ✅ 已写入策略链 (concepts={len(confirmed)})", file=sys.stderr)
    except Exception as e:
        print(f"[盘前策略] ⚠️ 写入策略链失败: {e}", file=sys.stderr)

    # ═══════════ 构造报告 ═══════════
    us_indices = us_report.get('us_market', {}).get('indices', {})
    commodities = us_report.get('us_market', {}).get('commodities', {})
    sentiment = us_report.get('sentiment', {})

    report += f"""---

## 🌍 隔夜外盘（收盘数据）

| 指数 | 最新价 | 涨跌幅 |
|------|--------|--------|
"""
    for name, data in us_indices.items():
        chg = data.get('change_pct', 0)
        sign = '+' if chg >= 0 else ''
        report += f"| {name} | {data.get('current', 'N/A')} | {sign}{chg}% |\n"

    if commodities:
        report += f"""
| 商品 | 最新价 | 涨跌幅 |
|------|--------|--------|
"""
        for name, data in commodities.items():
            chg = data.get('change_pct', 0)
            sign = '+' if chg >= 0 else ''
            report += f"| {name} | {data.get('current', 'N/A')} | {sign}{chg:.2f}% |\n"

    if sentiment:
        score = sentiment.get('score', 0)
        level = sentiment.get('level', 'N/A')
        report += f"\n**情绪分数：{score}（{level}）**  —  数据来源：akshare 收盘价\n"

    # ── 💰 概念资金流向 + Layer2 验证 ──
    if scored_concepts:
        trade_date = scored_concepts[0].get('trade_date', '') if scored_concepts else ''
        report += f"""
---

## 💰 概念资金流向 → Layer2 逻辑验证

**数据日期**: {trade_date} | **候选池**: {len(raw_concepts)} → **确信**: {len(confirmed)}

| 排名 | 概念 | 涨跌幅 | 成交额 | 确信度 | 判断理由 |
|------|------|--------|-----------|--------|---------|
"""
        for i, c in enumerate(scored_concepts):
            sign = '+' if c.get('pct_change', 0) >= 0 else ''
            amount_str = f"{c.get('amount', 0) / 1e8:.2f}亿"
            conf_icon = {'high': '🔥高', 'standard': '✅标', 'downgraded': '⚠️降', 'excluded': '❌排'}.get(c.get('confidence', ''), '?')
            reason = c.get('reason', '')[:30]
            report += f"| {i+1} | {c['name']} | {sign}{c.get('pct_change', 0)}% | {amount_str} | {conf_icon} | {reason} |\n"

        # 排除/降级详情
        if downgraded or excluded:
            report += "\n> "
            for c in excluded:
                report += f"❌ **{c['name']}**: {c.get('reason', '')}  "
            for c in downgraded:
                report += f"⚠️ **{c['name']}**: {c.get('reason', '')}  "

    report += f"""

---

## 📋 初步策略

| 项目 | 数值 |
|------|------|
| **市场立场** | **{initial_strategy.get('stance', 'N/A')}** |
| 风险提示 | {initial_strategy.get('risk_warning', 'N/A')} |

---

## 📰 Overnight 催化剂

"""

    if catalysts:
        for cat in catalysts:
            report += f"- ✅ {cat}\n"
    else:
        report += "暂无重大催化剂\n"

    report += """
### ⚠️ 风险提示

"""

    risks = news_sentiment.get('risks', [])
    if risks:
        for risk in risks:
            report += f"- ⚠️ {risk}\n"
    else:
        report += "暂无重大风险\n"

    return report




def main():
    """盘前扫描主函数"""
    import json
    from datetime import datetime

    print("[盘前扫描] 执行策略迭代...", file=sys.stderr)
    try:
        chain = StrategyChain()
        iteration = chain.analyze_and_iterate()
        if iteration.get('action') != 'maintain':
            print(f"[盘前扫描] ✅ 策略迭代完成: {iteration.get('action')}", file=sys.stderr)
        else:
            print(f"[盘前扫描] ✅ 策略迭代完成: 维持现状", file=sys.stderr)
    except Exception as e:
        print(f"[盘前扫描] ⚠️ 策略迭代失败: {e}", file=sys.stderr)

    # 生成报告
    report = generate_pre_market_report()
    print(report)

    # 保存日志
    log_dir = WORKSPACE / "memory" / "market-scan-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    log_file = log_dir / f"{today}-scans.jsonl"

    scan_data = {
        'timestamp': datetime.now().isoformat(),
        'type': 'pre_market_scan',
        'workspace': 'marcus'
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(scan_data, ensure_ascii=False) + "\n")

    print(f"\n[日志] 已写入：{log_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
