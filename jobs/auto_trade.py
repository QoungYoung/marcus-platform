#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 智能自动交易执行器
基于盘中扫描结果 + 实时数据 + 现有持仓 进行智能交易决策

风控规则:
- 单笔最大仓位：40% 初始资金
- 单只股票上限：40% 总仓位
- 现金保留：最低 10%
- 最大持仓数量：5 只
- 日最大回撤：10% 触发风控 review
"""

import json
import sys
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from datetime import datetime, timedelta
from pathlib import Path

# Cross-platform workspace detection
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
from workspace_detector import WORKSPACE, get_vnpy_dir, get_xueqiu_dir, get_akshare_dir, get_data_dir

VNPY_DIR = get_vnpy_dir()
DATA_DIR = get_data_dir()
XUEQIU_DIR = get_xueqiu_dir()
AKSHARE_DIR = get_akshare_dir()
DATA_DIR = get_data_dir()

sys.path.insert(0, str(VNPY_DIR))
sys.path.insert(0, str(XUEQIU_DIR))
sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "paper-trading"))

from paper_engine import PaperTradingEngine
from marcus_trade import MarcusVNPyExecutor
from xueqiu_engine import XueqiuEngine
from trade_day_utils import get_latest_trade_day, is_today_trade_day
from strategy_chain import StrategyChain
from marcus_trade import MarcusVNPyExecutor
from xueqiu_engine import XueqiuEngine
from trade_day_utils import get_latest_trade_day, is_today_trade_day
from strategy_chain import StrategyChain


# ============= 股票名称查询（stock_pool.db 优先，ms级） =============




def _score_candidates_from_news_db(candidates: list, hot_concepts: list, concept_scores: dict) -> dict:
    """
    基于 news.db 近期个股新闻打分。
    news.db 的 keyword 字段存储的是股票代码（如 '600438'），不是行业名。

    评分逻辑：
    1. 提取候选股代码（去掉 SH/SZ 前缀）
    2. news.db 按股票代码查近期新闻（48h内）
    3. sentiment × catalyst关键词命中 × recency → 打分
    4. 无近期新闻 → 剔除，不给虚假分数

    Args:
        candidates: 候选股列表，每项包含 symbol 字段
        hot_concepts: 热点概念列表（用于加成）
        concept_scores: 行业评分字典

    Returns:
        {symbol: {'score': int, 'label': str, 'sentiment': str, 'reasons': list} 或 None（剔除）}
    """
    import sqlite3
    from datetime import datetime as _dt, timedelta

    news_db = DATA_DIR / "news.db"
    pool_db = WORKSPACE / "data" / "stock_pool.db"
    if not news_db.exists():
        print(f"[评分验证] ⚠️ news.db 缺失，返回空让候选全剔除")
        return {}

    # 提取候选股代码（去掉 SH/SZ 前缀）
    candidate_codes = []
    code_to_symbol = {}  # code -> symbol with prefix
    for cnd in candidates:
        sym = cnd.get('symbol', '')
        code4 = sym[2:] if len(sym) > 4 else sym
        candidate_codes.append(code4)
        code_to_symbol[code4] = sym

    if not candidate_codes:
        return {}

    # 批量查 news.db（按股票代码，48h内）
    cutoff = (_dt.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
    hot_set = set(hot_concepts or [])
    catalyst_kw = ["增长", "超预期", "业绩", "中标", "合作", "突破", "创新",
                   "涨停", "大涨", "反弹", "订单", "签约", "投产", "政策", "获批"]
    risk_kw = ["亏损", "处罚", "调查", "诉讼", "跌停", "减持", "ST", "下滑", "退市", "违约"]
    sentiment_map = {'positive': 1, 'neutral': 0, 'negative': -1}

    # 查 stock_pool 获取候选股的行业（用于热点加成）
    code_industry = {}
    if pool_db.exists():
        conn_p = sqlite3.connect(str(pool_db))
        conn_p.row_factory = sqlite3.Row
        placeholders = ','.join('?' * len(candidate_codes))
        cur = conn_p.execute(
            f"SELECT symbol, industry FROM stock_pool WHERE symbol IN ({placeholders})",
            candidate_codes
        )
        for row in cur.fetchall():
            ind = row['industry'] or ''
            code_industry[row['symbol']] = ind
        conn_p.close()

    # 查 news.db（按候选股代码）
    code_news = {c: [] for c in candidate_codes}  # code -> list of news
    placeholders = ','.join('?' * len(candidate_codes))
    conn_n = sqlite3.connect(str(news_db))
    conn_n.row_factory = sqlite3.Row
    c_n = conn_n.cursor()
    c_n.execute(
        f"SELECT keyword, sentiment, title, publish_time FROM news "
        f"WHERE keyword IN ({placeholders}) AND publish_time >= ? "
        f"ORDER BY publish_time DESC LIMIT 2000",
        candidate_codes + [cutoff]
    )
    rows = c_n.fetchall()
    conn_n.close()

    for row in rows:
        kw = row['keyword']
        if kw in code_news:
            code_news[kw].append({
                'sentiment': row['sentiment'],
                'title': row['title'],
                'pub_time': row['publish_time']
            })

    results = {}
    for cnd in candidates:
        sym = cnd.get('symbol', '')
        code4 = sym[2:] if len(sym) > 4 else sym
        news_items = code_news.get(code4, [])

        # 无近期新闻 → 直接剔除，不给虚假分数
        if not news_items:
            results[sym] = None
            continue

        # 有新闻 → 打分
        total = 0
        pos_hits, neg_hits, recent_count = 0, 0, 0

        for item in news_items:
            s_val = sentiment_map.get(item['sentiment'], 0)
            total += s_val
            if s_val > 0:
                pos_hits += 1
            elif s_val < 0:
                neg_hits += 1

            title_lower = item['title'].lower()
            for kw in catalyst_kw:
                if kw in title_lower:
                    total += 1
                    break
            for kw in risk_kw:
                if kw in title_lower:
                    total -= 1
                    break

            if item['pub_time']:
                try:
                    pt = _dt.strptime(str(item['pub_time'])[:16], "%Y-%m-%d %H:%M")
                    if (_dt.now() - pt).total_seconds() < 14400:
                        total += 3
                        recent_count += 1
                except:
                    pass

        avg = total / max(len(news_items), 1)
        industry = code_industry.get(code4, '')
        base = 15 if industry in hot_set else 0
        score = int(max(0, min(100, 50 + avg * 12 + base)))

        if recent_count > 0:
            label = f'盘中新发+{recent_count}'
        elif pos_hits > neg_hits * 2:
            label = f'强势催化+{pos_hits}/-{neg_hits}'
        elif neg_hits > pos_hits:
            label = f'偏空-{neg_hits}'
        else:
            label = f'行业整理'

        sentiment = 'positive' if pos_hits > neg_hits else ('negative' if neg_hits > pos_hits else 'neutral')
        results[sym] = {
            'score': score,
            'label': label,
            'sentiment': sentiment,
            'reasons': [f'{industry}', f'新闻{len(news_items)}条', f'+{pos_hits}/-{neg_hits}']
        }

    return results

def _get_industry_candidates_from_pool(hot_concepts: list, exclude_symbols: set, limit: int = 30) -> list:
    """
    从 stock_pool.db 中获取热点概念内的股票（作为候选股来源）。
    不调 DeepSeek，不走 subprocess，直接查数据库。

    Args:
        hot_concepts: 热点概念列表
        exclude_symbols: 排除的标的（如已有持仓、今日已卖出）
        limit: 最多返回多少只

    Returns:
        [{'symbol': 'SH600438', 'name': '...', 'industry': '...', 'reason': '...', 'score': None, 'source': 'hot_concept_pool'}, ...]
    """
    import sqlite3
    pool_db = WORKSPACE / "data" / "stock_pool.db"
    if not pool_db.exists() or not hot_concepts:
        return []

    hot_set = set(hot_concepts)
    candidates = []
    conn = sqlite3.connect(str(pool_db))
    conn.row_factory = sqlite3.Row
    placeholders = ','.join('?' * len(hot_concepts))
    cur = conn.execute(
        f"SELECT symbol, name, industry FROM stock_pool WHERE industry IN ({placeholders}) ORDER BY industry",
        hot_concepts
    )
    for row in cur.fetchall():
        sym = row['symbol']
        # 补全前缀
        if not (sym.startswith('SH') or sym.startswith('SZ')):
            sym = f"SH{sym}" if sym.startswith('6') else f"SZ{sym}"
        if sym in exclude_symbols:
            continue
        candidates.append({
            'symbol': sym,
            'name': row['name'] or sym,
            'industry': row['industry'],
            'reason': f'热点概念: {row["industry"]}',
            'score': None,
            'source': 'hot_concept_pool'
        })
        if len(candidates) >= limit:
            break
    conn.close()
    return candidates


def _get_stock_name(sym: str) -> str:
    """查询股票中文名：stock_pool.db(ms级) → symbol 本身"""
    if not sym:
        return sym
    try:
        pool_db = WORKSPACE / "data" / "stock_pool.db"
        if pool_db.exists():
            conn = sqlite3.connect(str(pool_db))
            row = conn.execute(
                "SELECT name FROM stock_pool WHERE symbol = ? OR symbol = ? LIMIT 1",
                (sym, sym[2:] if len(sym) > 6 else sym)
            ).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return sym


# ============= 策略配置 =============

STRATEGY = {
    'max_positions': 4,              # 最大持仓 4 只（Marcus: 4×15%=60%）
    'single_position_max': 0.15,     # 单只股票最大仓位 15%（Marcus 铁律）
    'min_cash_reserve': 0.40,        # 最低现金保留 40%（Marcus: 单日仓位≤60%）
    'stop_loss': -0.08,              # 基础止损线 -8%（动态调整）
    'take_profit': 0.20,             # 基础止盈线 +20%
    'take_profit_stages': [          # 分批止盈（Marcus: 盈利时分批止盈）
        (0.10, 0.5),                 # +10% → 卖出 50%
        (0.15, 0.5),                 # +15% → 再卖 50%
        (0.20, 1.0),                 # +20% → 剩余全部止盈
    ],
    'rebalance_threshold': -0.05,    # 调仓阈值：落后大盘 5%
    'max_drawdown': -0.05,           # 总回撤 ≥ 5% 停止交易（Marcus 强制冷静期）
    'consecutive_loss_pause': 3,     # 连续亏损 3 笔强制休息 30 分钟
}

# 动态止损配置
DYNAMIC_STOP_LOSS = {
    'enabled': True,                 # 启用动态止损
    'high_vol_stop': -0.12,          # 高波动止损 -12%
    'low_vol_stop': -0.06,           # 低波动止损 -6%
    'trailing_stop_pct': 0.05,       # 移动止盈回撤 5%
}

# 加仓单只仓位上限（不超过 Marcus 15% 铁律）
ADD_POSITION_SINGLE_MAX = 0.15

# 加仓条件（Marcus 右侧交易：趋势确认 + 强催化 + 已有盈利）
ADD_POSITION_REQUIRE = {
    'min_profit_pct': 0.05,          # 已有盈利 ≥ 5%
    'min_news_score': 70,            # 催化评分 ≥ 70（强催化）
}

# 选股条件（动态筛选）
STOCK_FILTERS = {
    'min_market_cap': 100,           # 最小市值 100 亿
    'max_pe_ratio': 50,              # 最大市盈率 50
    'min_volume_ratio': 1.5,         # 最小量比 1.5
    'require_positive_news': True,   # 要求有正面新闻
}

# ========== 账户配置（配置化，改这里无需改代码） ==========
ACCOUNT_CONFIG = {
    'initial_capital': 100000,       # 初始资金
    'allow_markets': ['主板', '中小板'],  # 允许交易的市场：主板/中小板/创业板/科创板
    # 开户满2年且资产≥50万后可改为 ['主板','中小板','创业板','科创板']
}

def is_symbol_allowed(symbol: str) -> bool:
    """检查股票代码是否在当前账户权限范围内"""
    if not symbol:
        return False
    code = symbol.strip()
    if code.startswith('300') or code.startswith('301'):
        return '创业板' in ACCOUNT_CONFIG['allow_markets']
    if code.startswith('688'):
        return '科创板' in ACCOUNT_CONFIG['allow_markets']
    if code.startswith('8') or code.startswith('4'):
        return '北交所' in ACCOUNT_CONFIG['allow_markets'] if '北交所' in ACCOUNT_CONFIG else False
    return True  # 000/600/002 默认允许

# =======================================

# ========== Xueqiu API 超时控制 + Circuit Breaker ==========
QUOTE_TIMEOUT = 15  # 【修复】15秒足够，不易被cron外层timeout误杀
_xq_fail_count = 0  # Xueqiu 连续失败计数
_xq_degraded = False  # Xueqiu 降级标志（连续失败后切 cache）

def _get_quote_with_timeout(engine, symbol: str, use_cache: bool = True, force_cache: bool = False) -> Optional[dict]:
    """
    带超时保护的雪球行情获取
    - 超时/失败后自动降级使用 cache
    - 连续3次失败进入 degraded 模式，后续全部走 cache/fallback
    """
    import signal

    global _xq_fail_count, _xq_degraded

    class TimeoutError(Exception):
        pass

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"行情获取超时（>{QUOTE_TIMEOUT}s）")

    # 【新增】degraded 模式：直接跳过 Xueqiu，尝试 cache
    if _xq_degraded or force_cache:
        try:
            result = engine.get_stock_quote(symbol, use_cache=True) if engine else None
            if result:
                print(f"[_get_quote] 🔄 {symbol} degraded-cache → {result.get('current')}")
            return result
        except Exception:
            return None

    result = None

    try:
        import time as _time
        if sys.platform != 'win32':
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, QUOTE_TIMEOUT)
        try:
            result = engine.get_stock_quote(symbol, use_cache=use_cache)
            _xq_fail_count = 0
        finally:
            if sys.platform != 'win32':
                signal.setitimer(signal.ITIMER_REAL, 0)
    except TimeoutError:
        print(f"[_get_quote] ⚠️ {symbol} 行情超时（>{QUOTE_TIMEOUT}s）", file=sys.stderr)
        _xq_fail_count += 1
    except Exception as e:
        print(f"[_get_quote] ⚠️ {symbol} 行情失败: {e}", file=sys.stderr)
        _xq_fail_count += 1

    # 连续3次失败 → 进入 degraded 模式
    if _xq_fail_count >= 3:
        _xq_degraded = True
        print(f"[_get_quote] 🚨 Xueqiu 连续{_xq_fail_count}次失败，进入降级模式（后续走 cache）", file=sys.stderr)
        _xq_fail_count = 0  # 重置计数，防止无限触发

    return result

    return result


def get_news_sentiment(news_list: List[dict]) -> dict:
    """
    分析新闻情绪（已废弃，保留兼容性）
    请使用 news_analyzer.get_news_sentiment_simple()
    """
    from news_analyzer import get_news_sentiment_simple
    return get_news_sentiment_simple(news_list)


def get_position_impact() -> dict:
    """
    读取持仓影响分析结果
    从 data/position_impact.json 读取 AI 分析的持仓影响
    
    Returns:
        dict: {
            'available': bool,  # 是否有有效数据
            'data': {},        # 持仓影响数据
            'error': str,      # 错误信息（如果有）
            'updated_at': str   # 数据更新时间
        }
    """
    impact_file = WORKSPACE / "data" / "position_impact.json"
    
    # 默认返回：无数据
    default_result = {
        'available': False,
        'data': {},
        'error': 'No position impact data available',
        'updated_at': ''
    }
    
    # 检查文件是否存在
    if not impact_file.exists():
        default_result['error'] = 'position_impact.json not found'
        return default_result
    
    try:
        # 读取文件
        with open(impact_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 检查数据是否过期（超过2小时）
        updated_at = data.get('updated_at', '')
        if updated_at:
            try:
                updated_time = datetime.fromisoformat(updated_at)
                age_minutes = (datetime.now() - updated_time).total_seconds() / 60
                if age_minutes > 120:  # 超过2小时
                    default_result['error'] = f'Position impact data expired (age: {age_minutes:.0f} minutes)'
                    default_result['data'] = data
                    default_result['updated_at'] = updated_at
                    return default_result
            except Exception:
                pass  # 如果解析时间失败，仍然使用数据
        
        return {
            'available': True,
            'data': data.get('impacts', {}),
            'updated_at': updated_at,
            'error': ''
        }
        
    except json.JSONDecodeError as e:
        default_result['error'] = f'JSON decode error: {e}'
        return default_result
    except Exception as e:
        default_result['error'] = f'Read error: {e}'
        return default_result


def get_ai_decision() -> dict:
    """
    读取 AI 决策结果
    从 data/strategy_decision.json 读取 AI 生成的交易决策
    
    Returns:
        dict: {
            'available': bool,  # 是否有有效数据
            'data': {},        # 决策数据
            'error': str,      # 错误信息（如果有）
        }
    """
    decision_file = WORKSPACE / "data" / "strategy_decision.json"
    
    # 默认返回：无数据
    default_result = {
        'available': False,
        'data': {},
        'error': 'No AI decision data available'
    }
    
    # 检查文件是否存在
    if not decision_file.exists():
        default_result['error'] = 'strategy_decision.json not found'
        return default_result
    
    try:
        # 读取文件
        with open(decision_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 验证必要字段
        if not data.get('sell') and not data.get('buy') and not data.get('hold'):
            default_result['error'] = 'AI decision is empty'
            return default_result
        
        return {
            'available': True,
            'data': data,
            'error': ''
        }
        
    except json.JSONDecodeError as e:
        default_result['error'] = f'JSON decode error: {e}'
        return default_result
    except Exception as e:
        default_result['error'] = f'Read error: {e}'
        return default_result


def execute_ai_decision(executor: MarcusVNPyExecutor, ai_decision: dict) -> list:
    """
    执行 AI 决策
    
    Args:
        executor: 交易执行器
        ai_decision: AI 决策数据
    
    Returns:
        list: 执行结果
    """
    results = []
    
    # 获取当前持仓
    positions = executor.get_positions()
    position_dict = {p['symbol']: p for p in positions}
    
    # 执行卖出
    for sell_item in ai_decision.get('sell', []):
        symbol = sell_item.get('symbol', '')
        reason = sell_item.get('reason', 'AI决策卖出')
        
        if symbol in position_dict:
            pos = position_dict[symbol]
            result = executor.sell(
                symbol=symbol,
                price=pos.get('current_price', 0),
                volume=pos.get('volume', 0),
                reason=reason
            )
            results.append({
                'type': 'sell',
                'symbol': symbol,
                'result': result
            })
            print(f"[AI决策] 🔴 卖出 {symbol}: {reason}")
    
    # 执行买入
    for buy_item in ai_decision.get('buy', []):
        symbol = buy_item.get('symbol', '')
        volume = buy_item.get('volume', 0)
        reason = buy_item.get('reason', 'AI决策买入')
        
        # 获取实时价格
        price = 0
        try:
            engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
            quote = _get_quote_with_timeout(engine, symbol, use_cache=False)
            if quote:
                price = float(quote.get('current', 0))
        except:
            pass
        
        if price > 0 and volume > 0:
            result = executor.buy(
                symbol=symbol,
                price=price,
                volume=volume,
                reason=reason
            )
            results.append({
                'type': 'buy',
                'symbol': symbol,
                'result': result
            })
            print(f"[AI决策] 🟢 买入 {symbol}: {reason}")
        else:
            print(f"[AI决策] ⚠️ 跳过 {symbol}: 无法获取价格")
    
    return results


def get_market_data() -> dict:
    """
    获取市场数据 - 与盘中扫描任务逻辑保持一致
    使用雪球实时获取指数数据 + AKShare 获取新闻情绪
    """
    from typing import List
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 判断市场状态（A 股交易时间：9:30-11:30, 13:00-15:00）
    now = datetime.now()
    hour, minute = now.hour, now.minute
    time_minutes = hour * 60 + minute  # 转换为分钟便于比较
    
    # 交易时段：9:30-11:30 (570-690 分钟), 13:00-15:00 (780-900 分钟)
    morning_session = 570 <= time_minutes <= 690  # 9:30-11:30（包含11:30:00）
    afternoon_session = 780 <= time_minutes < 900  # 13:00-15:00
    
    if time_minutes < 570:  # 9:30 前
        market_status = '未开盘'
    elif morning_session or afternoon_session:
        market_status = '交易中'
    elif 690 <= time_minutes < 780:  # 11:30-13:00 午休
        market_status = '未开盘'
    else:  # 15:00 后
        market_status = '已收盘'
    
    # ========== 1. 获取实时指数数据（雪球） ==========
    indices = {}
    index_map = {
        '上证指数': 'SH000001',
        '深证成指': 'SZ399001', 
        '科创 50': 'SH000688'
    }
    
    try:
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        
        for name, code in index_map.items():
            try:
                quote = _get_quote_with_timeout(engine, code, use_cache=False)
                if quote:
                    indices[name] = {
                        'close': float(quote.get('current', 0)),
                        'change': float(quote.get('percent', 0)),
                        'change_amt': float(quote.get('chg', 0)),
                        'volume': float(quote.get('volume', 0)),
                    }
                else:
                    indices[name] = {'close': 0, 'change': 0}
            except Exception as e:
                indices[name] = {'close': 0, 'change': 0}
    except Exception as e:
        print(f"[错误] 初始化雪球引擎失败：{e}")
        for name in index_map.keys():
            indices[name] = {'close': 0, 'change': 0}
    
    # ========== 2. 读取最新热点情绪（复用 news_collector 预生成的缓存，不调 DeepSeek） ==========
    news_sentiment = {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0, 'total': 0}
    hot_sectors = []
    sector_scores = {}

    try:
        import json as _json
        hs_file = WORKSPACE / 'data' / 'latest_hot_sectors.json'
        if hs_file.exists():
            with open(hs_file, encoding='utf-8') as _f:
                hs = _json.load(_f)
            news_sentiment['score'] = hs.get('sentiment_score', 50)
            hot_sectors = hs.get('hot_concepts', [])
            sector_scores = hs.get('concept_scores', {})
            source = hs.get('source', '?')
            ts = hs.get('generated_at', '')[:19]
            print(f"[新闻情绪] 缓存 ← {source} | 分数={news_sentiment['score']} | {ts}")
        else:
            print("[新闻情绪] ⚠️ 无缓存，从 strategy_state.json 兜底")
            raise FileNotFoundError("无缓存文件")
    except Exception as _e:
        print(f"[新闻情绪] 缓存读取失败：{_e}，从 strategy_state.json 兜底")
        try:
            state_file = WORKSPACE / 'data' / 'strategy_state.json'
            if state_file.exists():
                with open(state_file, encoding='utf-8') as _f:
                    state = _json.load(_f)
                scans = state.get('intraday_scans', [])
                if scans:
                    fallback_score = scans[-1].get('sentiment_score')
                    if fallback_score and fallback_score != 50:
                        news_sentiment['score'] = fallback_score
                        print(f"[新闻情绪兜底] strategy_state.json → score={fallback_score}")
                if news_sentiment.get('score', 50) == 50:
                    pm = state.get('pre_market', {}).get('sentiment', {})
                    if pm.get('score', 0) not in (0, 50):
                        news_sentiment['score'] = pm['score']
                        print(f"[新闻情绪兜底] pre_market → score={pm['score']}")
        except Exception:
            pass
    
    # ========== 3. 计算平均涨跌幅 ==========
    changes = [i['change'] for i in indices.values() if i['change'] != 0]
    avg_change = sum(changes) / len(changes) if changes else 0.0
    
    # ========== 4. 综合判断市场立场（指数 + 新闻情绪 + 板块热度） ==========
    # 【优化】放宽条件，支持结构性行情
    
    # 计算有多少热门板块处于强势状态（hot_sectors/sector_scores 已在缓存读取中赋值）
    strong_sectors = 0
    for sector in hot_sectors:
        score = sector_scores.get(sector, 0)
        if score >= 60:
            strong_sectors += 1
    
    # 判断逻辑（优化后）
    # green: 大涨 OR (小涨 + 情绪正面 + 热门板块强势) OR (板块强但大盘横盘)
    # yellow: 震荡或小幅下跌，但情绪不差
    # red: 大跌 OR 情绪负面
    
    # 默认立场
    stance = 'yellow'
    stance_text = '保守买入'
    stance_reason = '市场中性，保持观望'
    
    if avg_change > 1.0 and news_sentiment['score'] > 40:
        stance = 'green'
        stance_text = '激进买入'
        stance_reason = '市场放量上涨 + 新闻情绪正面'
    elif avg_change > 0.3 and news_sentiment['score'] > 45:
        # 【优化】小涨 + 情绪OK = green（不要求1%以上）
        stance = 'green'
        stance_text = '激进买入'
        stance_reason = '市场小幅上涨 + 新闻情绪偏多'
    elif strong_sectors >= 2 and news_sentiment['score'] > 40:
        # 【优化】板块强势（≥2个热门板块）+ 情绪不差 = green（忽略大盘横盘）
        stance = 'green'
        stance_text = '激进买入'
        stance_reason = f'{strong_sectors}个热门板块强势，忽略大盘横盘'
    elif strong_sectors >= 1 and news_sentiment['score'] > 50 and avg_change > -0.5:
        # 【优化】有强势板块 + 情绪正面 = yellow（可以参与）
        first_sector = hot_sectors[0] if hot_sectors else "?"
        stance = 'yellow'
        stance_text = '保守买入'
        stance_reason = f'热门板块 {first_sector} 强势，可小仓位参与'
    elif avg_change > -0.8 and news_sentiment['score'] > 40:
        # 【优化】放宽到 -0.8%
        stance = 'yellow'
        stance_text = '保守买入'
        stance_reason = '市场震荡 + 新闻情绪中性'
    elif avg_change <= -1.5 or news_sentiment['score'] < 30:
        # 【优化】大跌或情绪极差才 red
        stance = 'red'
        stance_text = '持币观望'
        stance_reason = '市场大幅调整或新闻情绪负面'
    
    return {
        'timestamp': timestamp,
        'market_status': market_status,
        'indices': indices,
        'avg_change': avg_change,
        'stance': stance,
        'stance_text': stance_text,
        'stance_reason': stance_reason,
        'news_sentiment': news_sentiment
    }


def get_latest_scan_report() -> dict:
    """
    获取最新的盘中扫描报告
    返回扫描结果供交易决策参考
    """
    scan_log_dir = WORKSPACE / "memory" / "market-scan-logs"
    today = datetime.now().strftime('%Y-%m-%d')
    scan_log = scan_log_dir / f"{today}-scans.jsonl"
    
    # 如果今天没有，使用交易日检测工具获取最近交易日
    if not scan_log.exists():
        trade_day, method = get_latest_trade_day(method='auto')
        if trade_day:
            scan_log = scan_log_dir / f"{trade_day}-scans.jsonl"
    
    if scan_log.exists():
        with open(scan_log, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if lines:
                last_scan = json.loads(lines[-1])
                scan_time = last_scan.get('timestamp', '')
                report = last_scan.get('report', '')
                
                # 从报告中解析市场立场
                # 优先级：1.stance_code 字段 > 2.market_stance 字段 > 3.report 文本解析
                stance = 'yellow'
                avg_change = 0.0
                news_score = 50
                
# 【BugFix】优先级：adjusted_strategy.stance > stance_code > scan_result.stance
                # scan_result['stance'] 是 None，真实值在 adjusted_strategy['stance']
                adj_stance = last_scan.get('adjusted_strategy', {}).get('stance', '')
                if adj_stance and adj_stance != 'None':
                    if 'aggressive' in adj_stance.lower() or '🟢' in adj_stance:
                        stance = 'green'
                    elif 'cut_loss' in adj_stance.lower() or '🔴' in adj_stance:
                        stance = 'red'
                    elif 'hold' in adj_stance.lower() or '⚪' in adj_stance or '⚠' in adj_stance:
                        stance = 'yellow'
                elif 'stance_code' in last_scan:
                    stance = last_scan['stance_code']
                elif 'market_stance' in last_scan:
                    ms = last_scan['market_stance'].upper()
                    if 'AGGRESSIVE' in ms or 'BUY' in ms or ms == 'GREEN':
                        stance = 'green'
                    elif 'RED' in ms or 'WAIT' in ms or ms == 'RED':
                        stance = 'red'
                    elif 'YELLOW' in ms or 'HOLD' in ms:
                        stance = 'yellow'
                
                # 如果 stance 还是默认值，尝试从 report 文本解析
                if stance == 'yellow' and report:
                    if '🟢 激进买入' in report:
                        stance = 'green'
                        avg_change = 1.5
                    elif '🔴 持币观望' in report:
                        stance = 'red'
                        avg_change = -1.0
                    elif '🟡 保守买入' in report:
                        stance = 'yellow'
                        avg_change = 0.0
                
                # 尝试解析新闻情绪分数（如果报告中有）
                if '情绪分数' in report:
                    import re
                    match = re.search(r'情绪分数 \| ([\d.]+)', report)
                    if match:
                        news_score = float(match.group(1))

                # 如果 scan log 有热点概念则返回，否则 fall through 到 hs_file
                if last_scan.get('hot_concepts'):
                    return {
                        'scan_time': scan_time,
                        'stance': stance,
                        'avg_change': avg_change,
                        'news_score': news_score,
                        'report': report,
                        'from_scan': True,
                        'watchlist': last_scan.get('watchlist', []),
                        'position_limit': last_scan.get('position_limit', 80),
                        'sector_allocation': last_scan.get('sector_allocation', {}),
                        'hot_concepts': last_scan.get('hot_concepts', []),
                        'concept_scores': last_scan.get('concept_scores', {}),
                    }

    # 降级方案：从 data/latest_hot_sectors.json 读取（即使 scan log 存在但 hot_concepts 为空也触发）
    hs_file = WORKSPACE / 'data' / 'latest_hot_sectors.json'
    if hs_file.exists():
        try:
            with open(hs_file, 'r', encoding='utf-8') as f:
                hs_data = json.load(f)
            hot_concepts = hs_data.get('hot_concepts', [])
            concept_scores = hs_data.get('concept_scores', {})
            if hot_concepts:
                print(f"[选股] ✅ 从 latest_hot_sectors.json 加载热点概念: {hot_concepts}")
                return {
                    'scan_time': hs_data.get('timestamp', datetime.now().isoformat()),
                    'stance': 'green',
                    'avg_change': 0.0,
                    'news_score': 50,
                    'report': '',
                    'from_scan': True,
                    'watchlist': hs_data.get('watchlist', []),
                    'position_limit': 80,
                    'sector_allocation': {},
                    'hot_concepts': hot_concepts,
                    'concept_scores': concept_scores,
                }
        except Exception as e:
            print(f"[选股] ⚠️ 读取 latest_hot_sectors.json 失败: {e}")

    return None


def get_today_sold_symbols() -> set:
    """
    读取 trades.db，获取今日卖出过的股票代码集合。
    用于防止当日卖出后当日买回（同日回转过滤）。
    """
    from datetime import date
    today = date.today().isoformat()  # 'YYYY-MM-DD'
    try:
        db_path = WORKSPACE / 'data' / 'trades.db'
        if not db_path.exists():
            return set()
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT symbol FROM trades
            WHERE direction = '卖出' AND created_at >= ?
        """, (today,))
        sold = {row[0] for row in cur.fetchall()}
        conn.close()
        if sold:
            print(f"[同日过滤] 今日已卖出: {sold}")
        return sold
    except Exception as e:
        print(f"[警告] 读取今日卖出记录失败: {e}")
        return set()


def get_stock_candidates(market_stance: str = 'yellow', watchlist: list = None, sector_allocation: dict = None,
                         hot_concepts: list = None, concept_scores: dict = None,
                         exclude_symbols: set = None) -> list:
    """
    真正的右侧选股逻辑（hot_concepts → stock_selector 技术面+催化 一次搞定）。

    候选股来源 = hot_concepts 内的股票（来自 market_scan DeepSeek 分析）
        ↓
    stock_selector.py subprocess：技术面过滤（量比、RSI、5日 momentum）
        ↓
    news.db 催化剂打分（内嵌在 stock_selector）
        ↓
    综合排序 → top candidates

    watchlist 参数保留但不再作为候选股来源（只用 hot_concepts 选股）。

    Args:
        market_stance: 市场立场 (green/yellow/red)
        hot_concepts: 热点概念列表（来自 market_scan DeepSeek 分析）
        concept_scores: 行业评分字典
        exclude_symbols: 排除的标的（持仓已满/同日回转）

    Returns:
        [{'symbol': 'SH600519', 'name': '贵州茅台', 'price': 1420, 'reason': '...', 'score': 85}, ...]
    """
    import os
    candidates = []

    # ===== 主路径：hot_concepts → stock_selector.py（技术面 + 催化 一次搞定） =====
    if not hot_concepts:
        print("[选股] ⚠️ 无热点概念，跳过新建仓")
        return []

    print(f"[选股] ✅ 使用热点概念: {hot_concepts}")
    selector_path = Path(__file__).parent / "stock_selector.py"
    if not selector_path.exists():
        print(f"[选股] ⚠️ stock_selector.py 不存在，跳过")
        return []
    selector_args = [sys.executable, str(selector_path)]
    env = os.environ.copy()
    env['_STOCK_SELECTOR_HOT_CONCEPTS'] = json.dumps(hot_concepts)
    env['_STOCK_SELECTOR_INDUSTRY_SCORES'] = json.dumps(concept_scores or {})
    print(f"[选股] 调用 stock_selector.py（hot_concepts → stock_concept_map）...")

    result = subprocess.run(
        selector_args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace', timeout=300, env=env
    )

    if result.stdout.strip():
        try:
            candidates = json.loads(result.stdout)
            print(f"[选股] stock_selector 返回 {len(candidates)} 只候选")
        except json.JSONDecodeError as e:
            stderr_tail = '\n'.join(result.stderr.strip().split('\n')[-3:])
            print(f"[选股] ❌ stock_selector JSON 解析失败: {e} | {stderr_tail}", file=sys.stderr)
            candidates = []
    else:
        stderr_tail = '\n'.join(result.stderr.strip().split('\n')[-3:])
        print(f"[选股] ❌ stock_selector 无输出: {stderr_tail}", file=sys.stderr)

    # ===== 排除同日已卖出标的 =====
    if exclude_symbols and candidates:
        before = len(candidates)
        candidates = [c for c in candidates if c.get('symbol') not in exclude_symbols]
        if before != len(candidates):
            print(f"[同日过滤] 排除 {before - len(candidates)} 只，剩余 {len(candidates)} 只")

    # ===== stock_selector 无输出时，用 watchlist 兜底 =====
    if not candidates and watchlist:
        print(f"[选股] stock_selector 无输出，降级使用 watchlist: {watchlist}")
        for w in watchlist:
            symbol = w.get('symbol', w) if isinstance(w, dict) else str(w)
            if exclude_symbols and symbol in exclude_symbols:
                continue
            name = w.get('name', symbol) if isinstance(w, dict) else symbol
            candidates.append({
                'symbol': symbol,
                'name': name,
                'price': 0,  # 后续由实时行情补全
                'reason': 'watchlist兜底',
                'score': w.get('score', 50) if isinstance(w, dict) else 50,
            })

    if not candidates:
        print("[选股] ⚠️ 所有候选股均已卖出/排除，跳过新建仓")

    print(f"[选股] 最终候选: {len(candidates)} 只")
    return candidates


def analyze_positions(executor: MarcusVNPyExecutor, market_data: dict, scan_report: dict = None, position_impacts: dict = None) -> list:
    """
    分析现有持仓，决定是否需要调仓
    结合最新盘中扫描报告和持仓影响分析进行决策
    
    Args:
        executor: 交易执行器
        market_data: 市场数据
        scan_report: 扫描报告
        position_impacts: 持仓影响分析结果
    
    Returns: [{'symbol': 'SH600519', 'action': 'hold'|'sell', 'reason': '...', 'priority': 1}, ...]
    """
    positions = executor.get_positions()
    account = executor.get_account()
    actions = []
    
    # 处理 None 情况
    if position_impacts is None:
        position_impacts = {}
    
    # 【修复】从 positions 表直接读取 highest_price（持久化存储，不是从 get_positions 返回的 dict）
    highest_prices = {}  # {symbol: highest_price}
    try:
        import sqlite3 as _sq3
        conn = _sq3.connect(str(VNPY_DIR / "data" / "trades.db"))
        cursor = conn.cursor()
        cursor.execute('SELECT symbol, highest_price FROM positions WHERE highest_price IS NOT NULL AND highest_price > 0')
        for row in cursor.fetchall():
            highest_prices[row[0]] = row[1]
        conn.close()
        print(f"[持仓追踪] ✅ 从 DB 加载 {len(highest_prices)} 只持仓的历史最高价", file=sys.stderr)
    except Exception as e:
        print(f"[持仓追踪] ⚠️ DB 读取失败: {e}，降级为 avg_price", file=sys.stderr)
        positions = executor.get_positions()
        for p in positions:
            highest_prices[p['symbol']] = p.get('avg_price', 0)
    
    # 优先使用扫描报告的市场立场（如果扫描报告更新）
    if scan_report and scan_report.get('from_scan'):
        market_stance = scan_report.get('stance', market_data.get('stance', 'yellow'))
        market_change = scan_report.get('avg_change', market_data.get('avg_change', 0))
        scan_time = scan_report.get('scan_time', '未知')
    else:
        market_stance = market_data.get('stance', 'yellow')
        market_change = market_data.get('avg_change', 0)
        scan_time = None
    
    # 初始化雪球引擎获取实时行情
    try:
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
    except:
        engine = None
    
    # 判断是否在交易时间（9:30 前不获取实时价格）
    now = datetime.now()
    hour, minute = now.hour, now.minute
    time_minutes = hour * 60 + minute
    is_market_open = (570 <= time_minutes < 690) or (780 <= time_minutes < 900)  # 9:30-11:30, 13:00-15:00
    is_pre_market = (560 <= time_minutes < 570)  # 9:20-9:29 预上市时段

    # 【新增】Pre-fetch 持仓行情：一次性批量获取所有持仓价格，避免逐只串行超时
    # 只在交易时间/预上市时预抓，否则直接用成本价
    _quote_cache = {}  # {symbol: quote_dict}
    if (is_market_open or is_pre_market) and engine:
        _all_symbols = list(position_impacts.keys())
        # 【P3 优化】并发获取持仓行情：4只持仓串行需0.6s，并发约0.2s
        if not _all_symbols:
            print("[预抓行情] 无持仓，跳过")
        else:
            print(f"[预抓行情] 并发获取 {_all_symbols} ...")
            with ThreadPoolExecutor(max_workers=min(4, len(_all_symbols))) as ex:
                future_map = {ex.submit(_get_quote_with_timeout, engine, sym, False): sym for sym in _all_symbols}
                for f in as_completed(future_map):
                    sym = future_map[f]
                    try:
                        q = f.result()
                    except Exception:
                        q = None
                    if q:
                        _quote_cache[sym] = q
                        print(f"[预抓行情] ✅ {sym}: {q.get('current')} ({q.get('percent', 0):+.2f}%)")
                    else:
                        print(f"[预抓行情] ❌ {sym}: 失败，降级到成本价")
            print(f"[预抓行情] 完成：{len(_quote_cache)}/{len(_all_symbols)} 成功")

    for pos in positions:
        holding_days = 0  # 【P1 BugFix】修复 UnboundLocalError：循环内提前初始化
        symbol = pos.get('symbol', '')
        volume = pos.get('volume', 0)
        avg_price = pos.get('avg_price', 0)
        
        # 获取股票名称：stock_pool.db（ms级，权威）→ Xueqiu 覆盖
        stock_name = _get_stock_name(symbol)
        quote = _quote_cache.get(symbol)
        if not quote and engine:
            quote = _get_quote_with_timeout(engine, symbol, use_cache=True, force_cache=True)
        if quote:
            name_from_quote = quote.get('name', '')
            if name_from_quote:
                stock_name = name_from_quote

        # 获取当前价格（使用预抓结果，否则降级到成本价）
        current_price = avg_price  # 默认使用成本价
        today_pct = 0

        if is_market_open or is_pre_market:
            quote = _quote_cache.get(symbol)  # 再次从 cache 读（上面可能已覆盖）
            if not quote and engine:
                quote = _get_quote_with_timeout(engine, symbol, use_cache=False, force_cache=True)
            if quote:
                current_price = float(quote.get('current', avg_price))
                today_pct = float(quote.get('percent', 0))
            else:
                print(f"[警告] {symbol} 无行情，使用成本价 ¥{avg_price:.2f}")
        elif not is_market_open:
            print(f"[提示] {symbol} 非交易时间，使用成本价 ¥{avg_price:.2f}")
        
        profit_ratio = (current_price - avg_price) / avg_price if avg_price > 0 else 0
        
        # 【新增】动态止损计算
        dynamic_stop_result = None
        trailing_stop_result = None
        # 【修复】提前初始化 highest_price，防止 dynamic_stop_loss 模块缺失时 UnboundLocalError
        historical_high = highest_prices.get(symbol, avg_price)
        highest_price = historical_high
        
        if DYNAMIC_STOP_LOSS.get('enabled', True):
            try:
                from dynamic_stop_loss import calculate_dynamic_stop_loss, check_trailing_stop
                
                # 计算动态止损线
                dynamic_stop_result = calculate_dynamic_stop_loss(code=symbol[2:], entry_price=avg_price)
                
                # 【修复】获取持仓历史最高价（持久化存储）
                # 优先使用历史记录的最高价，如果当前价格更高则更新
                if current_price > historical_high:
                    highest_price = current_price  # 创历史新高
                else:
                    highest_price = historical_high  # 使用历史最高价
                
                trailing_stop_result = check_trailing_stop(
                    code=symbol[2:],
                    entry_price=avg_price,
                    highest_price=highest_price,
                    current_price=current_price
                )
            except Exception as e:
                print(f"[动态止损] ⚠ 计算失败：{e}", file=sys.stderr)
        
        action = {
            'symbol': symbol,
            'name': stock_name,
            'volume': volume,
            'avg_price': avg_price,
            'current_price': current_price,
            'today_pct': today_pct,
            'profit_ratio': profit_ratio,
            'action': 'hold',
            'reason': '',
            'priority': 5
        }
        
        # ===== P0 持仓催化剂检查 =====
        try:
            from auto_trade_patch import check_position_catalyst_for_hold
            action = check_position_catalyst_for_hold(action)
            # 无催化剂+亏损 → 立即止损
            if action.get('_catalyst_urgent'):
                action['action'] = 'sell'
                action['reason'] = action.get('_catalyst_reason', '无催化剂止损')
                action['priority'] = 1
                actions.append(action)
                continue
        except ImportError:
            pass
        # ===== P0 催化剂检查结束 =====
        
        # 【优化】止损检查（动态止损优先）
        if dynamic_stop_result:
            dynamic_stop_pct = dynamic_stop_result['stop_loss_pct']
            if profit_ratio <= dynamic_stop_pct:
                action['action'] = 'sell'
                action['reason'] = f'动态止损 ({profit_ratio:.1%} < {dynamic_stop_pct:.1%}, {dynamic_stop_result["type"]}波动)'
                action['priority'] = 1
                actions.append(action)
                continue
        else:
            # 降级：使用基础止损
            if profit_ratio <= STRATEGY['stop_loss']:
                action['action'] = 'sell'
                action['reason'] = f'止损 ({profit_ratio:.1%} < {STRATEGY["stop_loss"]:.1%})'
                action['priority'] = 1
                actions.append(action)
                continue
        
        # 【右侧交易】5日 momentum 止损：持有 >= 5个交易日仍未出现预期涨幅，强制退出
        if holding_days >= 5 and profit_ratio < 0.03:
            action['action'] = 'sell'
            action['reason'] = f'5日Momentum止损({profit_ratio:.1%}<+3%, 持有{holding_days}日)'
            action['priority'] = 1
            actions.append(action)
            print(f"[5日Momentum止损] 🔴 {symbol} 持有{holding_days}日涨幅{profit_ratio:.1%}<+3%，强制退出")
            continue

        # 【优化】止盈检查（移动止盈优先）
        if trailing_stop_result and trailing_stop_result['action'] == 'sell':
            action['action'] = 'sell'
            action['reason'] = trailing_stop_result['reason']
            action['priority'] = 2
            actions.append(action)
            continue
        else:
            # 分批止盈（Marcus: 盈利时分批止盈锁利）
            stages = STRATEGY.get('take_profit_stages', [])
            for stage_pct, sell_ratio in stages:
                if profit_ratio >= stage_pct:
                    action['action'] = 'sell'
                    action['reason'] = f'分批止盈 +{stage_pct:.0%}卖{sell_ratio:.0%}'
                    action['sell_ratio'] = sell_ratio  # 卖出比例
                    action['priority'] = 2
                    actions.append(action)
                    break
            else:
                # 降级：使用基础止盈
                if profit_ratio >= STRATEGY['take_profit']:
                    action['action'] = 'sell'
                    action['reason'] = f'止盈 ({profit_ratio:.1%} > {STRATEGY["take_profit"]:.1%})'
                    action['priority'] = 2
                    action['sell_ratio'] = 1.0
                    actions.append(action)
                    continue
        
        # 市场转红时减仓
        if market_stance == 'red':
            action['action'] = 'sell'
            action['reason'] = '市场转红，清仓避险'
            action['priority'] = 1
            actions.append(action)
            continue
        
        # 【新增】持仓影响分析
        if position_impacts and symbol in position_impacts:
            impact = position_impacts[symbol]
            impact_type = impact.get('影响', impact.get('impact', '中性'))
            impact_reason = impact.get('原因', impact.get('reason', ''))
            
            # 记录持仓影响信息
            action['position_impact'] = impact
            
            # 利空：根据强度决定是否卖出
            if impact_type in ['利空', '利空', 'negative', 'bearish']:
                impact_strength = impact.get('强度', impact.get('strength', '中'))
                if impact_strength in ['强', 'strong']:
                    # 强利空：建议卖出
                    action['action'] = 'sell'
                    action['reason'] = f'持仓利空: {impact_reason}'
                    action['priority'] = 2
                    actions.append(action)
                    continue
        
        # 【新增】僵尸持仓检测：无消息、无趋势、横盘太久
        # 条件：持有 > 10天 + 无利好 + 低波动 → 强制退出
        ZOMBIE_HOLDING_DAYS = 10  # 持有超过10天
        ZOMBIE_LOW_VOLATILITY = 0.03  # 日波动<3%视为低波动
        
        # 获取持仓的入场日期（优先从交易记录读取）
        entry_date = None
        
        # 方法1: 从 trade logs 读取（更准确）
        # 注意: 交易记录在 results[].result 中
        try:
            from pathlib import Path
            log_dir = WORKSPACE / "memory" / "auto-trade-logs"
            if log_dir.exists():
                # 读取最近的交易日志文件
                log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)
                for log_file in log_files[:7]:  # 最多读最近7天
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                record = json.loads(line)
                                # 交易记录在 results[].result 中
                                results = record.get('results', [])
                                for r in results:
                                    trade_info = r.get('result', {})
                                    if trade_info.get('status') == 'executed':
                                        trade_type = r.get('trade', {}).get('type', '')
                                        if trade_type == 'buy' and trade_info.get('symbol') == symbol:
                                            timestamp = trade_info.get('timestamp', '')
                                            if timestamp:
                                                entry_date = datetime.strptime(timestamp[:10], '%Y-%m-%d')
                                                break
                                if entry_date:
                                    break
                            except:
                                continue
                    if entry_date:
                        break
        except Exception as e:
            print(f"[僵尸检测] ⚠ 从交易日志获取入场日期失败: {e}")
        
        # 方法2: 回退到 account.json
        if entry_date is None:
            try:
                account_file = VNPY_DIR / "data" / "account.json"
                if account_file.exists():
                    with open(account_file) as f:
                        account_data = json.load(f)
                    pos_data = account_data.get('positions', {}).get(symbol, {})
                    entry_date_str = pos_data.get('entry_date')
                    if entry_date_str:
                        entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d')
            except Exception as e:
                print(f"[僵尸检测] ⚠ 获取入场日期失败: {e}")
        
        # 计算持有天数
        holding_days = 0
        if entry_date:
            holding_days = (datetime.now() - entry_date).days
        else:
            # 无法确定入场日期时，跳过僵尸检测（避免误判）
            print(f"[僵尸检测] ⚠️ {symbol} 无法确定入场日期，跳过僵尸检测", file=sys.stderr)
        
        # 检查是否有有效新闻催化剂（使用新鲜度阈值）
        has_catalyst = False
        CATALYST_FRESH_DAYS = 7
        try:
            from auto_trade_patch import get_catalyst_status
            code = symbol[2:] if symbol.startswith(('SH', 'SZ', 'BJ')) else symbol
            cat_status = get_catalyst_status(code)
            if cat_status:
                # 双重保险：days_without_catalyst 会被新采集重置，强制用 last_catalyst_time 独立计算
                last_cat_ts = cat_status.get('last_catalyst_time', '')
                days_no_cat = cat_status.get('days_without_catalyst', 999)
                if last_cat_ts:
                    try:
                        last_dt = datetime.fromisoformat(last_cat_ts.replace('Z', ''))
                        days_no_cat = (datetime.now() - last_dt).days
                    except Exception:
                        pass
                if (cat_status.get('news_score', 0) >= 60
                        and days_no_cat <= CATALYST_FRESH_DAYS):
                    has_catalyst = True
        except Exception as e:
            print(f"[僵尸检测] ⚠️ 获取催化剂状态失败: {e}")
            # 回退：使用旧逻辑
            if position_impacts and symbol in position_impacts:
                impact_data = position_impacts[symbol]
                impact_type = impact_data.get('影响', impact_data.get('impact', ''))
                impact_strength = impact_data.get('强度', impact_data.get('strength', ''))
                if impact_type in ['利好', '利好', 'positive', 'bullish'] and impact_strength not in ['无', 'none', '']:
                    has_catalyst = True
        
        # 检查波动率（简化版：用今日涨跌幅判断）
        is_low_volatility = abs(today_pct) < ZOMBIE_LOW_VOLATILITY * 100 if today_pct != 0 else True
        
        # 僵尸持仓判断
        is_zombie = (holding_days > ZOMBIE_HOLDING_DAYS and 
                     not has_catalyst and 
                     is_low_volatility)
        
        if is_zombie:
            action['action'] = 'sell'
            action['reason'] = f'僵尸持仓: 持有{holding_days}天无催化+低波动，强制退出'
            action['priority'] = 2
            print(f"[僵尸检测] 🔴 {symbol} 持有{holding_days}天无催化，触发强制退出", file=sys.stderr)
            actions.append(action)
            continue
        
        # 记录入场日期（如果是新股）
        if entry_date is None and action['action'] == 'hold':
            try:
                account_file = VNPY_DIR / "data" / "account.json"
                if account_file.exists():
                    with open(account_file) as f:
                        account_data = json.load(f)
                    if symbol not in account_data.get('positions', {}):
                        account_data.setdefault('positions', {})[symbol] = {}
                    account_data['positions'][symbol]['entry_date'] = datetime.now().strftime('%Y-%m-%d')
                    with open(account_file, 'w') as f:
                        json.dump(account_data, f, indent=2, ensure_ascii=False)
                    print(f"[持仓追踪] ✓ 已记录 {symbol} 入场日期", file=sys.stderr)
            except Exception as e:
                print(f"[持仓追踪] ⚠ 保存入场日期失败: {e}")
        
        # 持仓正常，继续持有
        action['reason'] = f'持有中 (盈亏 {profit_ratio:.1%}, 今日 {today_pct:+.2f}%)'
        
        # 【新增】记录当前最高价到 action 中，用于后续持久化
        action['highest_price'] = highest_price
        
        actions.append(action)
    
    # 【Step 8】持久化保存持仓历史最高价到 trades.db
    try:
        from paper_engine import PaperTradingEngine
        paper = PaperTradingEngine(data_dir=str(DATA_DIR))
        for a in actions:
            sym = a.get('symbol', '')
            if sym and a.get('highest_price'):
                paper.update_position_meta(sym, highest_price=a['highest_price'])
        print(f"[持仓追踪] ✅ 已更新 {len([a for a in actions if a.get('highest_price')])} 只持仓的历史最高价到 DB", file=sys.stderr)
    except Exception as e:
        print(f"[持仓追踪] ⚠ 更新失败：{e}", file=sys.stderr)
    # 按优先级排序（1=最高）
    actions.sort(key=lambda x: x.get('priority', 5))
    
    return actions


def generate_trade_plan(executor: MarcusVNPyExecutor, market_data: dict, position_analysis: list, candidates: list, scan_report: dict = None, position_limit: int = 80) -> list:
    """
    生成交易计划
    结合最新盘中扫描报告进行决策

    执行顺序（Step 7 核心修复）：
      1. 止损卖出（priority=1）→ 最高优先级，不受仓位限制
      2. 移动止盈卖出（priority=2）
      3. 仓位超标调仓（最后处理）
      4. 买入

    Returns: [{'type': 'buy'|'sell', 'symbol': 'SH600519', 'price': 1420, 'volume': 100, 'reason': '...'}, ...]
    """
    account = executor.get_account()
    trades = []

    initial_capital = account.get('initial_capital', 1000000)
    position_value = account.get('position_value', 0)
    position_ratio = position_value / initial_capital if initial_capital > 0 else 0
    strategy_limit = position_limit / 100.0 if position_limit else 0.8

    # ============= Step 7 核心：止损/止盈卖出优先执行 =============
    # 按优先级排序：priority=1（止损）> priority=2（移动止盈）> 其他
    sorted_by_priority = sorted(position_analysis, key=lambda x: x.get('priority', 5))

    stop_loss_sells = []    # priority=1：止损
    trailing_sells = []     # priority=2：移动止盈
    regular_holds = []      # priority>=3：持有/其他

    for pos in sorted_by_priority:
        if pos.get('action') == 'sell':
            p = pos.get('priority', 5)
            if p == 1:
                stop_loss_sells.append(pos)
            elif p == 2:
                trailing_sells.append(pos)

    # 1. 执行止损卖出（最高优先级，强制执行）
    for pos in stop_loss_sells:
        trades.append({
            'type': 'sell',
            'symbol': pos['symbol'],
            'volume': pos['volume'],
            'price': pos.get('current_price', 0),
            'reason': pos.get('reason', '止损')
        })
        _sell_sym = pos['symbol']
        vol = pos.get('volume', 0)
        price = pos.get('current_price', 0)
        position_value -= vol * price
        print(f"[止损] ⚡ 强制执行止损 {_sell_sym} {vol} 股 @ {price}")

        # 【新增】止损后更新 symbol 冷却期（5 个交易日内禁止重新建仓）
        try:
            _cooldown_file = WORKSPACE / "data" / "symbol_cooldown.json"
            _now_ts = datetime.now().timestamp()
            if _cooldown_file.exists():
                with open(_cooldown_file) as _f:
                    _cooldown_map = json.load(_f)
            else:
                _cooldown_map = {}
            _cooldown_map[_sell_sym] = _now_ts
            with open(_cooldown_file, "w") as _f:
                json.dump(_cooldown_map, _f)
            print(f"[Symbol冷却] ✅ 止损后 {_sell_sym} 已进入冷却", file=sys.stderr)
        except Exception:
            pass

    # 2. 执行移动止盈卖出（次优先级）
    for pos in trailing_sells:
        trades.append({
            'type': 'sell',
            'symbol': pos['symbol'],
            'volume': pos['volume'],
            'price': pos.get('current_price', 0),
            'reason': pos.get('reason', '移动止盈')
        })
        _sell_sym2 = pos['symbol']
        vol = pos.get('volume', 0)
        price = pos.get('current_price', 0)
        position_value -= vol * price
        print(f"[移动止盈] ⚡ 执行 {_sell_sym2} {vol} 股 @ {price}")

        # 【新增】移动止盈后更新 symbol 冷却期
        try:
            _cooldown_file = WORKSPACE / "data" / "symbol_cooldown.json"
            _now_ts = datetime.now().timestamp()
            if _cooldown_file.exists():
                with open(_cooldown_file) as _f:
                    _cooldown_map = json.load(_f)
            else:
                _cooldown_map = {}
            _cooldown_map[_sell_sym2] = _now_ts
            with open(_cooldown_file, "w") as _f:
                json.dump(_cooldown_map, _f)
            print(f"[Symbol冷却] ✅ 止盈后 {_sell_sym2} 已进入冷却", file=sys.stderr)
        except Exception:
            pass
    # 重新计算仓位比例（止损/止盈后）
    position_ratio = position_value / initial_capital if initial_capital > 0 else 0

    # 3. 仓位超标调仓（止损止盈后才处理）
    if position_ratio >= strategy_limit:
        target_ratio = strategy_limit
        excess_ratio = position_ratio - target_ratio
        excess_amount = excess_ratio * initial_capital

        print(f"[仓位调整] 止损止盈后仓位 {position_ratio*100:.1f}% 超过策略限制 {position_limit}%，卖出 {excess_ratio*100:.1f}% 持仓 ({excess_amount:.0f}元)")

        # 只对 remaining 的持仓（priority>=3 且 action!=sell）调仓
        remaining_positions = [p for p in sorted_by_priority
                              if p.get('action') != 'sell' and p.get('priority', 5) >= 3]

        for pos in remaining_positions:
            if excess_amount <= 0:
                break
            sell_volume = int(pos['volume'] * 0.5 / 100) * 100
            if sell_volume < 100:
                sell_volume = pos['volume']

            sell_amount = sell_volume * pos.get('current_price', 0)
            if sell_amount > excess_amount:
                sell_volume = int(excess_amount / pos.get('current_price', 1) / 100) * 100
                sell_amount = sell_volume * pos.get('current_price', 0)

            if sell_volume > 0:
                trades.append({
                    'type': 'sell',
                    'symbol': pos['symbol'],
                    'volume': sell_volume,
                    'price': pos.get('current_price', 0),
                    'reason': f'仓位超标调仓 ({position_ratio*100:.1f}% → {target_ratio*100:.1f}%)'
                })
                print(f"[仓位调整] 卖出 {pos['symbol']} {sell_volume} 股，金额 {sell_amount:.2f}")
                excess_amount -= sell_amount

    # 4. 常规卖出（非止损/止盈，action=sell 且 priority>=3）
    for pos in position_analysis:
        if pos.get('action') == 'sell' and pos.get('priority', 5) >= 3:
            # 避免重复追加（止损和止盈已追加过了）
            if not any(t.get('symbol') == pos['symbol'] and t.get('type') == 'sell' for t in trades):
                trades.append({
                    'type': 'sell',
                    'symbol': pos['symbol'],
                    'price': pos.get('current_price', 0),
                    'volume': pos['volume'],
                    'reason': pos.get('reason', '卖出')
                })

    # 5. 买入决策
    # 优先使用扫描报告的市场立场
    if scan_report and scan_report.get('from_scan'):
        market_stance = scan_report.get('stance', 'yellow')
    else:
        market_stance = market_data.get('stance', 'yellow')

    # ========== 立场冷却期（防止立场震荡触发反复买卖）============
    # 立场从非 green 切换到 green 后，等待 5 分钟再执行买入
    COOLDOWN_SECONDS = 300  # 5分钟，强信号日快速响应
    from datetime import datetime as _dt
    COOLDOWN_FILE = WORKSPACE / "data" / "stance_cooldown.json"
    now_ts = _dt.now().timestamp()
    cooldown_data = {'last_stance': market_stance, 'last_change_ts': now_ts}
    stance_changed = True
    if COOLDOWN_FILE.exists():
        try:
            with open(COOLDOWN_FILE) as _f:
                prev = json.load(_f)
            prev_stance = prev.get('last_stance', market_stance)
            prev_ts = prev.get('last_change_ts', now_ts)
            if prev_stance == market_stance:
                stance_changed = False  # 立场未变
            else:
                elapsed = now_ts - prev_ts
                if elapsed < COOLDOWN_SECONDS:
                    print(f"[立场冷却] 立场 {prev_stance}→{market_stance}，冷却中（已过{int(elapsed//60)}分钟，还剩{int((COOLDOWN_SECONDS-elapsed)//60)}分钟）")
                    if market_stance not in ('green', 'aggressive_buy'):
                        pass
                    else:
                        print(f"[立场冷却] 🛑 禁止买入，等待冷却期结束")
                        return trades
        except Exception:
            pass
    # 写入当前立场（立场没变时不更新冷却计时）
    if stance_changed:
        with open(COOLDOWN_FILE, 'w') as _f:
            json.dump(cooldown_data, _f)
    # ========== 立场冷却 end ==========================================

    if market_stance not in ('green', 'aggressive_buy'):
        print(f"[仓位限制] 市场立场 {market_stance} 非 green，不买入")
        return trades

    # 重新计算止损止盈后的仓位
    current_position_ratio = position_value / initial_capital if initial_capital > 0 else 0
    if current_position_ratio >= strategy_limit:
        print(f"[仓位限制] 当前仓位 {current_position_ratio*100:.1f}% 已达策略限制 {position_limit}%，禁止新建仓")
        return trades

    print(f"[仓位限制] 策略仓位限制 {position_limit}%，当前 {current_position_ratio*100:.1f}%，可建仓")
    
    # 计算可用资金
    available_cash = account['available_cash']
    min_reserve = account['initial_capital'] * STRATEGY['min_cash_reserve']
    max_per_stock = account['initial_capital'] * STRATEGY['single_position_max']
    
    # 根据策略仓位限制计算可用资金
    max_position_value = initial_capital * (position_limit / 100.0)
    available_position = max(0, max_position_value - position_value)
    available_for_buy = min(
        max(0, available_cash - min_reserve),  # 现金约束
        available_position  # 仓位约束
    )
    
    # 当前持仓数量
    current_positions = len([p for p in position_analysis if p['action'] == 'hold'])

    # ==============================================================
    # 【补】Step N: 加仓现有强势持仓（Marcus 右侧加仓：趋势确认 + 强催化 + 有盈利）
    # 触发条件：绿色立场 + news_score≥70 + 盈利>5%
    # ==============================================================
    def _get_news_score(pos):
        cat = pos.get('_catalyst_status', {}) or {}
        return cat.get('news_score', 0)

    ADD_MIN_PROFIT = ADD_POSITION_REQUIRE['min_profit_pct']
    ADD_MIN_NEWS = ADD_POSITION_REQUIRE['min_news_score']

    if market_stance in ('green', 'aggressive_buy') and available_for_buy >= 1000:
        # 取现持有的票（action='hold'），按催化剂强度降序排列
        hold_positions = [
            p for p in position_analysis
            if p.get('action') == 'hold'
            and _get_news_score(p) >= ADD_MIN_NEWS      # Marcus: 强催化
            and p.get('profit_ratio', 0) >= ADD_MIN_PROFIT  # Marcus: 已有盈利
        ]
        # 按 news_score 降序，优先加仓最强催化
        hold_positions.sort(key=_get_news_score, reverse=True)

        add_budget = available_for_buy * 0.25   # 最多用 25% 可用资金加仓
        added_count = 0

        for pos in hold_positions:
            if add_budget < 500:
                break
            news_score = _get_news_score(pos)
            profit_ratio = pos.get('profit_ratio', 0)
            symbol = pos.get('symbol', '')
            current_price = pos.get('current_price', 0)
            existing_vol = pos.get('volume', 0)

            # 催化剂阈值 50 分（系统有效催化最低 40 分，50 分为合理下沿）
            # 且当前有盈利
            if news_score < 50 or profit_ratio <= 0:
                print(f"[加仓跳过] {symbol} news_score={news_score}(<50) 或 profit={profit_ratio:.1%}(<=0)")
                continue
            if current_price <= 0:
                continue

            # 【右侧交易】加仓同样需要量价确认
            tech = pos.get('technical_data', {}) or {}
            vol_ratio = tech.get('vol_ratio', tech.get('volume_ratio', 1.0))
            ma5 = tech.get('ma5', 0)
            if vol_ratio < 1.0:
                print(f"[加仓跳过] {symbol} 量比={vol_ratio:.1f}<1.0，右侧放量未确认")
                continue
            if ma5 > 0 and current_price < ma5:
                print(f"[加仓跳过] {symbol} 现价{current_price}<ma5({ma5:.2f})，短期趋势向下")
                continue

            # 【单股20%上限】加仓后也不能超过 20% 上限
            existing_value = existing_vol * current_price
            single_max_value = initial_capital * STRATEGY['single_position_max']  # 20%
            if existing_value >= single_max_value:
                print(f"[加仓跳过] {symbol} 已持仓 ¥{existing_value:.0f}，已达单股 20% 上限（¥{single_max_value:.0f}）")
                continue

            # 可加仓空间
            room_value = single_max_value - existing_value
            add_value = min(add_budget * 0.5, room_value, available_for_buy * 0.25)
            add_vol = int(add_value / current_price / 100) * 100
            if add_vol < 100:
                continue

            # 加仓后再次检查总仓位
            new_total_value = position_value + (add_vol * current_price)
            new_ratio = new_total_value / initial_capital if initial_capital > 0 else 0
            if new_ratio > strategy_limit:
                print(f"[加仓跳过] {symbol} 加仓后总仓位 {new_ratio*100:.1f}% 超限")
                continue

            trades.append({
                'type': 'buy',
                'symbol': symbol,
                'price': current_price,
                'volume': add_vol,
                'reason': f'强催化加仓: news_score={news_score}, 盈利{profit_ratio:.1%}'
            })
            print(f"[✅ 加仓] {symbol} +{add_vol}股 @{current_price} | 催化{news_score} | 盈利{profit_ratio:.1%}")
            position_value += add_vol * current_price
            available_for_buy -= add_vol * current_price
            add_budget -= add_vol * current_price
            added_count += 1

        if added_count > 0:
            print(f"[加仓完成] 共加仓 {added_count} 只，消耗资金 ¥{available_for_buy:.0f} → 剩余 ¥{available_for_buy:.0f}")

    # ==============================================================
    # 遍历候选股票，生成买入计划（新建仓，5只上限内）
    # ==============================================================
    for stock in candidates:
        if current_positions >= STRATEGY['max_positions']:
            break
        
        if available_for_buy < max_per_stock * 0.5:
            break
        
        # 账户权限过滤（创业板/科创板等）
        sym = stock.get('symbol', '')
        if not is_symbol_allowed(sym):
            print(f"[权限过滤] {sym} 不在允许交易的市场范围内，跳过")
            continue
        
        # 涨停/接近涨停排除（右侧交易：不追涨停，趋势确认后入场）
        today_pct = stock.get('pct_change', stock.get('today_pct', 0))
        if today_pct and abs(today_pct) > 9.5:
            print(f"[涨停过滤] {sym} 今日涨幅 {today_pct:+.1f}%，追高风险大，跳过")
            continue
        
        # 【关键】买入前再次检查总仓位
        current_check = executor.get_account()
        
        # 检查是否已持仓
        if any(p['symbol'] == stock['symbol'] and p['action'] == 'hold' for p in position_analysis):
            continue
        
        # 获取实时价格（如果候选股票没有价格或价格不合理）
        price = stock.get('price', 0)
        if price <= 0:
            try:
                engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
                quote = _get_quote_with_timeout(engine, stock['symbol'], use_cache=False)
                if quote:
                    price = float(quote.get('current', 0))
            except:
                pass
        
        if price <= 0:
            print(f"[跳过] {stock['symbol']} 无法获取实时价格")
            continue
        
        # ========== 【右侧交易】量价确认硬过滤 ==========
        tech = stock.get('technical_data', {}) or {}
        vol_ratio = tech.get('vol_ratio', tech.get('volume_ratio', 1.0))
        ma5 = tech.get('ma5', 0)
        price_above_ma5 = (ma5 > 0 and price >= ma5)
        if vol_ratio < 1.0:
            print(f"[量价过滤] {stock['symbol']} 量比={vol_ratio:.1f}<1.0，右侧放量未确认，跳过")
            continue
        if not price_above_ma5:
            print(f"[趋势过滤] {stock['symbol']} 现价{price}<ma5({ma5:.2f})，短期趋势向下，跳过")
            continue

        # 【右侧交易 momentum 强化】近5日涨幅需 >= 2%，过滤"有故事不涨"的弱标的
        # 趋势向下（负值）或横盘（0~+2%）均不符合右侧确认逻辑
        pct_5d = tech.get('pct_5d', 0)
        if pct_5d < 2.0:
            print(f"[Momentum过滤] {stock['symbol']} 近5日涨幅{pct_5d:.1f}<+2%，趋势未确认，跳过")
            continue

        # ========== 【右侧交易】趋势阶段 + 缩量检查 ==========
        trend_stage = tech.get('trend_stage', stock.get('trend_stage', ''))
        if trend_stage == '末期':
            print(f"[趋势阶段] {stock['symbol']} 处于趋势末期，右侧追高风险大，跳过")
            continue
        shrink = stock.get('shrink_volume')
        if shrink and shrink.get('warning') and shrink.get('risk_level') == 'high':
            print(f"[缩量风险] {stock['symbol']} 缩量上涨(量比{shrink.get('vol_ratio',0):.1f})，警惕假突破，跳过")
            continue

        # ========== 量价确认 end ==========

        # ========== 【右侧交易】催化强度分层仓位 ==========
        cat_status = stock.get('_catalyst_status', {}) or {}
        news_score = cat_status.get('news_score', 50)
        if news_score < 50:  # 降为50，C级允许半仓入场
            print(f"[催化过滤] {stock['symbol']} news_score={news_score}(<50)，催化不足，跳过")
            continue

        # ========== 【新增】per-symbol 交易冷却期（防止同一只反复建仓止损）============
        # 读取历史交易记录，5 个交易日内禁止重复建仓同一只股票
        _cooldown_file = WORKSPACE / "data" / "symbol_cooldown.json"
        _now_ts = _dt.now().timestamp()
        _sym = stock['symbol']
        _can_buy = True
        _cooldown_days = 5  # 交易日冷却天数
        if _cooldown_file.exists():
            try:
                with open(_cooldown_file) as _f:
                    _cooldown_map = json.load(_f)
                _last_trade_ts = _cooldown_map.get(_sym, 0)
                if _last_trade_ts > 0:
                    # 简单按天数估算：5个交易日 ≈ 5*86400 = 432000 秒
                    _elapsed = _now_ts - _last_trade_ts
                    if _elapsed < _cooldown_days * 86400:
                        _remaining = (_cooldown_days * 86400 - _elapsed) / 3600
                        print(f"[Symbol冷却] {_sym} 刚交易过，冷却中（还剩{_remaining:.1f}h），跳过")
                        _can_buy = False
            except Exception:
                pass
        if not _can_buy:
            continue

        # 更新冷却时间戳（每次新建仓成功时由 execute_trades 写入，此处仅作保险）
        # （实际更新时间在 execute_trades 里，这里只是标记候选股通过检查）
        if news_score >= 70:
            size_ratio = 1.0
            tier = '强催化'
        elif news_score >= 60:
            size_ratio = 0.75
            tier = '中催化'
        else:
            size_ratio = 0.5   # news_score 50-60: C级半仓，信任三维评分
            tier = '弱催化'
        # ========== 催化分层 end ==========

        # 计算买入数量（按催化强度分层）
        buy_amount = min(available_for_buy, max_per_stock * size_ratio)
        volume = int(buy_amount / price / 100) * 100  # 100 股整数倍
        
        # 仓位限制检查（使用计算后的 volume 和 price）
        new_position_value = current_check.get('position_value', 0) + (volume * price)
        new_ratio = new_position_value / initial_capital if initial_capital > 0 else 0
        if new_ratio > strategy_limit:
            print(f"[仓位限制] 买入后仓位 {new_ratio*100:.1f}% 将超过限制 {position_limit}%，跳过 {stock['symbol']}")
            continue
        
        # 【单股20%上限】已有持仓加仓时禁止超20%；新建仓1手即使超20%也放行（避免彻底买不进）
        single_max_value = initial_capital * STRATEGY['single_position_max']  # 20%
        existing_for_symbol = current_check.get('positions', {}).get(stock['symbol'], 0)
        if existing_for_symbol > 0 and existing_for_symbol + (volume * price) > single_max_value:
            print(f"[单股限仓] {stock['symbol']} 已持仓 ¥{existing_for_symbol:.0f}，加仓 {volume*price:.0f} 将超 20% 上限（¥{single_max_value:.0f}），跳过")
            continue
        
        if volume < 100:
            continue

        # ========== 【新增】尾盘 14:30 后不新开仓（减少噪音）===============
        from datetime import datetime as _dt_local
        current_hour = _dt_local.now().hour
        current_min = _dt_local.now().minute
        if current_hour == 14 and current_min > 30:
            print(f"[尾盘过滤] 14:30 后不新开仓，跳过 {stock['symbol']}")
            continue
        if current_hour >= 15:
            print(f"[尾盘过滤] 已收盘(15:00)，不新开仓，跳过 {stock['symbol']}")
            continue
        # ========== 尾盘过滤 end ==========================================

        trades.append({
            'type': 'buy',
            'symbol': stock['symbol'],
            'price': price,
            'volume': volume,
            'reason': f'{tier}建仓: news_score={news_score}, 量比={vol_ratio:.1f}x'
        })
        print(f"[✅ 新建仓({tier})] {stock['symbol']} {volume}股 @{price}，¥{price*volume:.0f}，score={news_score}，量={vol_ratio:.1f}x")
        available_for_buy -= price * volume
        current_positions += 1
    
    return trades


def execute_trades(executor: MarcusVNPyExecutor, trades: list) -> list:
    """执行交易计划，返回执行结果"""
    results = []
    
    for trade in trades:
        if trade['type'] == 'buy':
            result = executor.buy(
                symbol=trade['symbol'],
                price=trade['price'],
                volume=trade['volume'],
                reason=trade['reason']
            )
            
            # 【新增】记录买入日期到 account.json
            if result.get('status') == 'executed':
                try:
                    # Step 8：改为调用 paper_engine 写入 trades.db
                    paper = executor  # executor 本身就是 PaperTradingEngine 实例
                    paper.update_position_meta(trade['symbol'], entry_date=datetime.now().strftime('%Y-%m-%d'))
                    print(f"[持仓追踪] ✅ 已记录 {trade['symbol']} 入场日期: {datetime.now().strftime('%Y-%m-%d')}", file=sys.stderr)
                except Exception as e:
                    print(f"[持仓追踪] ⚠ 记录入场日期失败: {e}", file=sys.stderr)

                # 【新增】更新 symbol 冷却期：每次成功建仓后，记录时间戳到 symbol_cooldown.json
                try:
                    _cooldown_file = WORKSPACE / "data" / "symbol_cooldown.json"
                    _now_ts = datetime.now().timestamp()
                    _sym = trade['symbol']
                    if _cooldown_file.exists():
                        with open(_cooldown_file) as _f:
                            _cooldown_map = json.load(_f)
                    else:
                        _cooldown_map = {}
                    _cooldown_map[_sym] = _now_ts
                    with open(_cooldown_file, 'w') as _f:
                        json.dump(_cooldown_map, _f)
                    print(f"[Symbol冷却] ✅ 已更新 {_sym} 冷却时间戳: {datetime.now().strftime('%Y-%m-%d %H:%M')}", file=sys.stderr)
                except Exception as _e:
                    print(f"[Symbol冷却] ⚠ 更新冷却失败: {_e}", file=sys.stderr)
        else:  # sell
            result = executor.sell(
                symbol=trade['symbol'],
                price=trade['price'],
                volume=trade['volume'],
                reason=trade['reason']
            )
            
            # 【新增】卖出后清除入场日期记录
            if result.get('status') == 'executed':
                # Step 8：卖出后 paper_engine.remove_position_meta() 已自动调用（见 paper_engine.py）
                print(f"[持仓追踪] ✅ {trade['symbol']} 已清仓，positions 表记录已删除", file=sys.stderr)
        
        results.append({
            'trade': trade,
            'result': result
        })
    
    return results


def format_report_ai_decision(market_data: dict, position_analysis: list, ai_decision: dict, results: list) -> str:
    """格式化 AI 决策报告"""
    lines = []
    lines.append("## ⚡ AI 决策交易报告")
    lines.append("")
    lines.append(f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # 市场数据
    lines.append("### 📈 市场数据")
    stance = market_data.get('stance', 'yellow')
    lines.append(f"- **市场立场**: {'🟢' if stance == 'green' else '🟡' if stance == 'yellow' else '🔴'} {stance}")
    for name, data in market_data.get('indices', {}).items():
        change = data.get('change', 0)
        lines.append(f"- **{name}**: {change:+.2f}%")
    lines.append("")
    
    # 持仓
    lines.append("### 💼 当前持仓")
    if position_analysis:
        lines.append("| 代码 | 名称 | 数量 | 盈亏 |")
        lines.append("|------|------|------|------|")
        for p in position_analysis:
            lines.append(f"| {p.get('symbol', '')} | {p.get('name', '')} | {p.get('volume', 0)} | {p.get('profit_ratio', 0)*100:+.1f}% |")
    else:
        lines.append("*暂无持仓*")
    lines.append("")
    
    # AI 决策
    lines.append("### 🤖 AI 决策")
    
    sells = ai_decision.get('sell', [])
    if sells:
        lines.append("**卖出**:")
        for s in sells:
            lines.append(f"- {s.get('symbol', '')}: {s.get('reason', '')}")
    else:
        lines.append("**卖出**: 无")
    
    buys = ai_decision.get('buy', [])
    if buys:
        lines.append("**买入**:")
        for b in buys:
            lines.append(f"- {b.get('symbol', '')} ({b.get('volume', 0)}股): {b.get('reason', '')}")
    else:
        lines.append("**买入**: 无")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*下次执行：30 分钟后*")
    
    return "\n".join(lines)


def format_report(market_data: dict, position_analysis: list, trades: list, results: list, scan_report: dict = None, strategy_chain: dict = None, force_pause: bool = False, pause_reason: str = "") -> str:
    """格式化报告为 Markdown"""
    stance_emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
    stance_text = {'green': '激进买入', 'yellow': '保守买入', 'red': '持币观望'}
    
    # 优先使用扫描报告的市场立场（如果有）
    if scan_report and scan_report.get('from_scan'):
        stance = scan_report.get('stance', market_data.get('stance', 'yellow'))
        stance_reason = scan_report.get('stance_reason', '')
        scan_time = scan_report.get('scan_time', '未知')
    else:
        stance = market_data.get('stance', 'yellow')
        stance_reason = market_data.get('stance_reason', '')
        scan_time = None
    
    md = f"""# 🤖 Marcus 智能自动交易报告

**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**市场立场**: {stance_emoji.get(stance, '❓')} {stance_text.get(stance, '未知')} - {stance_reason}  
**市场涨跌**: {market_data.get('avg_change', 0):.2f}%
"""
    if force_pause:
        md += f"\n🚨 **交易暂停**: {pause_reason}\n"
    
    if scan_time:
        md += f"**参考扫描**: {scan_time}\n\n"
    
    # === 新增：策略链状态 ===
    if strategy_chain:
        watchlist = []
        md += "---\n\n## 📋 策略链状态\n\n"
        
        # 盘前策略
        pre_market = strategy_chain.get('pre_market', {})
        if pre_market:
            pre_stance = pre_market.get('initial_strategy', {}).get('stance', 'N/A')
            pre_watchlist = pre_market.get('initial_strategy', {}).get('watchlist', [])
            md += f"- **盘前立场**: {pre_stance}\n"
            if pre_watchlist:
                names = [w.get('name', w.get('symbol', str(w))) if isinstance(w, dict) else str(w) for w in pre_watchlist[:5]]
                md += f"- **观察列表**: {', '.join(names)}\n"
        
        # 盘中扫描
        intraday = strategy_chain.get('intraday_scans', [])
        if intraday:
            latest_scan = intraday[-1]
            scan_ts = latest_scan.get('timestamp', '')[:19]
            scan_stance = latest_scan.get('stance', 'N/A')
            adjusted = latest_scan.get('adjusted_strategy', {})
            if adjusted:
                md += f"- **最新调整**: {scan_stance} ({scan_ts})\n"
                md += f"- **仓位限制**: {adjusted.get('position_limit', 'N/A')}%\n"
                watchlist = adjusted.get('watchlist', [])
                if watchlist:
                    names = [w.get('name', w.get('symbol', str(w))) if isinstance(w, dict) else str(w) for w in watchlist[:5]]
                    md += f"- **调整后观察**: {', '.join(names)}\n"
        
        # 反馈循环
        feedback = strategy_chain.get('feedback_loop', [])
        if feedback:
            latest_fb = feedback[-1]
            fb_type = latest_fb.get('type', 'N/A')
            fb_lesson = latest_fb.get('lesson', '')
            if fb_lesson:
                md += f"- **策略反馈**: {fb_lesson}\n"
        
        # 下一步策略建议（基于策略链实际仓位限制，不硬编码）
        md += "\n### 🎯 下一步策略\n"

        # 从 scan_report 或策略链取实际仓位限制
        actual_limit = 80  # 兜底
        if scan_report:
            actual_limit = scan_report.get('position_limit', actual_limit)
        if strategy_chain:
            daily_strategy = strategy_chain.get('daily_strategy', {})
            actual_limit = daily_strategy.get('position_limit', actual_limit)
            # 盘中扫描可能进一步限制
            scans = strategy_chain.get('intraday_scans', [])
            if scans:
                actual_limit = scans[-1].get('position_limit', actual_limit)
        if isinstance(actual_limit, (int, float)):
            actual_limit = int(min(actual_limit, 60))  # Marcus 硬封顶

        if stance == 'green':
            md += "- **市场环境**: 🟢 积极做多\n"
            md += f"- **操作建议**: 按策略建仓，仓位上限 {actual_limit}%\n"
        elif stance == 'yellow':
            md += "- **市场环境**: 🟡 震荡整理，谨慎操作\n"
            md += f"- **操作建议**: 保持现有仓位，不超过 {actual_limit}%\n"
        else:
            md += "- **市场环境**: 🔴 观望为主，控制风险\n"
            md += "- **操作建议**: 减仓避险，保留现金\n"
        
        # 基于持仓状态给出建议
        if position_analysis:
            # 检查是否有接近止损的持仓
            at_risk = [p for p in position_analysis if p.get('action') == 'hold' and p.get('profit_ratio', 0) < -0.05]
            if at_risk:
                md += f"- **风控提醒**: {len(at_risk)} 只持仓接近止损线，需密切关注\n"
            
            # 检查是否有盈利丰厚的持仓
            high_profit = [p for p in position_analysis if p.get('action') == 'hold' and p.get('profit_ratio', 0) > 0.15]
            if high_profit:
                md += f"- **止盈关注**: {len(high_profit)} 只持仓盈利 >15%，可考虑移动止盈\n"
        
        # 基于策略链的建议
        if watchlist:
            names = [w.get('name', w.get('symbol', str(w))) if isinstance(w, dict) else str(w) for w in watchlist[:3]]
            md += f"- **观察标的**: {', '.join(names)}\n"
        
        md += "\n"
    
    md += """---

## 持仓分析

"""
    
    if position_analysis:
        md += "| 代码 | 名称 | 操作 | 理由 | 优先级 |\n"
        md += "|------|------|------|------|--------|\n"
        for pos in position_analysis:
            action_emoji = '🟢' if pos['action'] == 'hold' else '🔴'
            action_text = '持有' if pos['action'] == 'hold' else '卖出'
            priority = '⭐' * (6 - pos.get('priority', 5))
            # 显示名称（如果获取不到则显示代码）
            stock_name = pos.get('name', pos.get('symbol', ''))
            if stock_name == pos.get('symbol', ''):
                stock_name = stock_name  # 显示代码
            md += f"| {pos['symbol']} | {stock_name} | {action_emoji} {action_text} | {pos.get('reason', '-')} | {priority} |\n"
    else:
        md += "暂无持仓\n"
    
    md += "\n---\n\n## 交易执行\n\n"
    
    if results:
        for r in results:
            trade = r.get('trade', {})
            result = r.get('result', {})
            
            action_emoji = '🟢' if trade.get('type') == 'buy' else '🔴'
            action_text = '买入' if trade.get('type') == 'buy' else '卖出'
            
            md += f"### {action_emoji} {action_text} {trade.get('symbol', 'N/A')}\n"
            md += f"- **理由**: {trade.get('reason', 'N/A')}\n"
            
            if result.get('status') == 'executed':
                md += f"- **状态**: ✅ 已成交\n"
                md += f"- **价格**: ¥{result.get('price', 0):.2f}\n"
                md += f"- **数量**: {result.get('volume', 0)} 股\n"
                if trade.get('type') == 'buy':
                    md += f"- **金额**: ¥{result.get('cost', 0):,.2f}\n"
                else:
                    md += f"- **盈亏**: ¥{result.get('profit', 0):,.2f}\n"
            else:
                md += f"- **状态**: ❌ {result.get('reason', '失败')}\n"
            
            md += "\n"
    else:
        md += "无交易操作\n"
    
    md += "\n---\n\n*下次执行：30 分钟后*\n"
    
    return md


def main():
    """主函数"""
    import argparse
    import os
    import platform

    # 【P0修复】文件锁：防止 cron + 手动并发重复下单（跨平台兼容）
    LOCK_FILE = os.path.join(os.environ.get('TEMP', '/tmp'), 'marcus_auto_trade.lock')
    lock_fd = open(LOCK_FILE, 'w')
    if platform.system() == 'Windows':
        import msvcrt
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("⚠️  auto_trade 正在另一进程中执行，跳过本次运行")
            lock_fd.close()
            return
    else:
        import fcntl
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("⚠️  auto_trade 正在另一进程中执行，跳过本次运行")
            lock_fd.close()
            return


    parser = argparse.ArgumentParser(description='Marcus 智能自动交易执行器')
    parser.add_argument('--scan-only', action='store_true', help='只扫描，不执行交易')
    parser.add_argument('--debug', action='store_true', help='调试模式，输出详细日志')
    parser.add_argument('--ignore-scan', action='store_true', help='忽略扫描报告，仅用实时数据')
    parser.add_argument('--force', action='store_true', help='强制交易（忽略市场状态）')
    parser.add_argument('--ai-decision', action='store_true', help='使用 AI 决策文件执行交易')
    parser.add_argument('--closing', action='store_true', help='尾盘模式，只止损/止盈不新开仓')
    args = parser.parse_args()
    
    # 【P0修复】节假日保护：休市日禁止任何交易
    is_trade, _ = is_today_trade_day()
    if not is_trade and not args.force:
        today_str = datetime.now().strftime('%Y-%m-%d')
        print(f"⚠️  今天是休市日（{today_str}）")
        print(f"📅 今天不是 A 股交易日，不执行任何交易")
        print(f"💡 使用 --force 参数可强制交易（不推荐，风险自负）")
        return
    
    executor = MarcusVNPyExecutor()
    
    # 【新增】检查交易时间
    market_data = get_market_data()
    market_status = market_data.get('market_status', '未开盘')
    
    if market_status == '未开盘' and not args.force:
        print(f"⚠️  市场未开盘（当前状态：{market_status}）")
        print(f"⏰ A 股交易时间：9:30-11:30, 13:00-15:00")
        print(f"💡 使用 --force 参数可强制交易（不推荐）")
        print(f"\n📊 市场数据预览:")
        print(f"   时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        for name, data in market_data.get('indices', {}).items():
            print(f"   {name}: {data.get('close', 0):.2f} ({data.get('change', 0):+.2f}%)")
        return
    
    # 【修复】收盘后仍输出结构化数据（供 cron AI 不脑补）
    if market_status == '已收盘' and not args.force:
        print(f"⚠️  市场已收盘（当前状态：{market_status}）")
        print(f"⏰ A 股交易时间：9:30-11:30, 13:00-15:00")
        # 收盘后仍走完流程，生成报告供 AI 参考（只是不执行新交易）
        print(f"[提示] 收盘后模式：仅生成报告，不执行新交易")
    
    # 【新增】读取持仓影响分析
    position_impact_result = get_position_impact()
    position_impacts = {}
    
    if position_impact_result['available']:
        position_impacts = position_impact_result['data']
        print(f"[持仓影响] ✅ 已加载持仓影响分析 (更新于 {position_impact_result['updated_at']})")
        if args.debug:
            print(f"[DEBUG] 持仓影响数据: {json.dumps(position_impacts, ensure_ascii=False, indent=2)}")
    else:
        print(f"[持仓影响] ⚠️  {position_impact_result['error']}")
        print(f"[持仓影响]    将基于实时数据分析（建议先运行市场扫描生成持仓影响）")
    
    # 【修复】watchlist 优先级：scan_report > DB > intraday_scans
    # 不再从 intraday_scans 读 watchlist（其数据来自 08:40 预盘扫描，已过时）
    chain = StrategyChain()
    pre_market = chain.state.get('pre_market', {})

    watchlist = []
    sector_allocation = {}
    position_limit = 80

    # 1. 获取最新盘中扫描报告
    scan_report = None
    if not args.ignore_scan:
        scan_report = get_latest_scan_report()
        if scan_report:
            if args.debug:
                print(f"[DEBUG] 扫描报告：{scan_report.get('scan_time', '未知')} - 立场：{scan_report.get('stance', 'unknown')}")
            # 扫描报告的 watchlist 最实时，优先使用
            if scan_report.get('watchlist'):
                watchlist = scan_report['watchlist']
                position_limit = scan_report.get('position_limit', position_limit)
                print(f"[扫描报告] ✅ 读取 watchlist: {watchlist}")
                print(f"[扫描报告] ✅ 读取 position_limit: {position_limit}%")

    # 2. scan_report 无 watchlist 时，从 DB 读今日最新 watchlist
    if not watchlist:
        try:
            from pre_market_scan import get_watchlist_from_db
            db_watchlist = get_watchlist_from_db()
            if db_watchlist:
                watchlist = db_watchlist
                print(f"[DB] ✅ 读取 watchlist: {watchlist}")
        except Exception:
            pass

    # 3. DB 也无则降级到 intraday_scans（最终兜底）
    if not watchlist:
        intraday_scans = chain.state.get('intraday_scans', [])
        if intraday_scans:
            latest_scan = intraday_scans[-1]
            adjusted = latest_scan.get('adjusted_strategy', {})
            watchlist = adjusted.get('watchlist', [])
    
    # 3. 使用已获取的市场数据（已在交易时间检查中获取）
    # market_data 已在上方获取
    
    if args.debug:
        print(f"[DEBUG] 市场数据：{json.dumps(market_data, ensure_ascii=False, indent=2)}")
    
    # 4. 分析现有持仓（结合扫描报告）
    position_analysis = analyze_positions(executor, market_data, scan_report, position_impacts)
    if args.debug:
        print(f"[DEBUG] 持仓分析：{json.dumps(position_analysis, ensure_ascii=False, indent=2)}")
    
    # 5. 动态选股（传入市场立场 + 策略链观察列表）
    # 【BugFix】market_stance 优先读 scan_report['stance']（由 get_latest_scan_report 修正为 stance_code）
    # scan_report.get('stance') 在修复后返回 stance_code（green/yellow/red），不再是 None
    if scan_report and not args.ignore_scan:
        market_stance = scan_report.get('stance', market_data.get('stance', 'yellow'))
    else:
        market_stance = market_data.get('stance', 'yellow')
    
    # 获取候选股票列表
    if watchlist:
        print(f"[选股] ✅ 使用观察列表: {watchlist}")
    else:
        watchlist = ['300271', '002594', '600570', '300750', '002230']
        print(f"[选股] ⚠️ 无观察列表，使用默认: {watchlist}")
    # 【同日回转过滤】获取今日已卖出标的，防止卖出后当日买回
    today_sold = get_today_sold_symbols()
    
    candidates = get_stock_candidates(
        market_stance=market_stance,
        watchlist=watchlist,
        hot_concepts=(scan_report.get('hot_concepts') if scan_report else None),
        concept_scores=(scan_report.get('concept_scores') if scan_report else None),
        exclude_symbols=today_sold
    )

    # 【P0 修复】盘中 watchlist 股票 pct_5d 补全
    # watchlist 股票来自 stock_selector → news.db 路径（无技术数据），generate_trade_plan 需要 pct_5d 做 momentum 过滤
    if candidates:
        try:
            from stock_selector import get_technical_data
            watchlist_symbols = [c['symbol'] for c in candidates]
            print(f"[pct_5d补全] 准备补充技术数据: {watchlist_symbols}")
            tech_data = get_technical_data(watchlist_symbols)
            for c in candidates:
                sym = c['symbol']
                td = tech_data.get(sym, {})
                if td:
                    c['technical_data'] = td
                    print(f"[pct_5d补全] {sym}: pct_5d={td.get('pct_5d', 0):.2f}%, vol_ratio={td.get('vol_ratio', 1.0):.2f}")
                else:
                    print(f"[pct_5d补全] {sym}: 无技术数据（可能停牌或数据源无响应）")
                    c['technical_data'] = {}
        except Exception as e:
            print(f"[pct_5d补全] ⚠️ 补充技术数据失败: {e}")

    # 【AI 决策模式】- 跳过选股，直接执行 AI 决策
    if args.ai_decision:
        # 读取 AI 决策
        ai_decision_result = get_ai_decision()
        
        if ai_decision_result['available']:
            ai_data = ai_decision_result['data']
            print(f"[AI决策] ✅ 已加载 AI 交易决策")
            print(f"[AI决策] 卖出: {len(ai_data.get('sell', []))} 只")
            print(f"[AI决策] 买入: {len(ai_data.get('buy', []))} 只")
            
            # 执行 AI 决策
            if not args.scan_only:
                try:
                    results = execute_ai_decision(executor, ai_data)
                    print(f"[AI决策] 执行完成，成功 {len(results)} 笔")
                except Exception as e:
                    print(f"[错误] ❌ AI决策执行失败: {e}")
                    results = []
            else:
                print(f"[交易] scan-only 模式，跳过执行")
                results = []
            
            # 生成报告
            report = format_report_ai_decision(market_data, position_analysis, ai_data, results)
            print(report)
            return
        else:
            print(f"[AI决策] ⚠️  {ai_decision_result['error']}")
            print(f"[AI决策]    回退到默认交易逻辑")
    
    # 4.5 Marcus 风控检查：总回撤 + 连续亏损
    account = executor.get_account()
    force_pause = False
    pause_reason = ""

    # 总回撤 ≥ 5% 停止交易
    initial_capital = account.get('initial_capital', 1)
    total_pnl = account.get('float_pnl', 0) + account.get('realized_pnl', 0)
    total_drawdown = total_pnl / initial_capital if initial_capital > 0 else 0
    if total_drawdown <= STRATEGY['max_drawdown']:
        force_pause = True
        pause_reason = f"总回撤 {total_drawdown:.1%} ≥ 5%，触发 Marcus 强制冷静期"
        print(f"\n🚨 {pause_reason}\n", file=sys.stderr)

    # 连续亏损 3 笔强制休息
    if not force_pause:
        from market_scan import check_consecutive_losses
        pause_check = check_consecutive_losses(chain)
        if pause_check.get('force_pause'):
            force_pause = True
            pause_reason = pause_check.get('reason', '连续亏损暂停')

    # 5. 生成交易计划（结合扫描报告和仓位限制）
    if force_pause:
        trades = []  # 强制暂停，不产生任何交易
        if market_status == '交易中':
            position_limit = min(position_limit, 10)  # 最多允许 10% 仓位（仅持有不动）
    else:
        trades = generate_trade_plan(executor, market_data, position_analysis, candidates, scan_report, position_limit)
    
    # 【尾盘模式】只止损/止盈不新开仓
    if args.closing:
        buy_count = len([t for t in trades if t.get('type') == 'buy'])
        trades = [t for t in trades if t.get('type') == 'sell']
        if buy_count > 0:
            print(f"[尾盘] 🚫 过滤掉 {buy_count} 笔买入交易，尾盘只执行止损/止盈操作")
        print(f"[尾盘] 🔔 尾盘模式：仅止损/止盈，不新开仓（共 {len(trades)} 笔卖出）")
    
    # 6. 执行交易（添加错误处理）
    results = []
    trade_error = None
    
    if not args.scan_only and trades:
        try:
            print(f"[交易] 准备执行 {len(trades)} 笔交易...")
            results = execute_trades(executor, trades)
            print(f"[交易] 执行完成，成功 {len([r for r in results if r.get('success')])}/{len(results)} 笔")
        except Exception as e:
            trade_error = str(e)
            print(f"[错误] ❌ 交易执行失败: {trade_error}")
            print(f"[风控] 🛡️ 停止交易，等待人工检修")
            print(f"[建议] 请检查以下可能的问题:")
            print(f"   - 网络连接是否正常")
            print(f"   - 账户状态是否正常")
            print(f"   - 交易参数是否正确")
            # 不执行交易，返回错误报告
            report = f"""
⚠️ 自动交易执行失败

    # ===== P0 催化剂过滤补丁 =====
    if candidates:
        try:
            from auto_trade_patch import filter_candidates_by_catalyst
            market_stance_for_patch = scan_report.get('stance', 'yellow') if scan_report else 'yellow'
            candidates = filter_candidates_by_catalyst(candidates, market_stance=market_stance_for_patch)
        except ImportError:
            pass
    # ===== 补丁结束 =====



时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
错误: {trade_error}

📊 市场数据:
- 市场立场: {market_data.get('stance', 'unknown')}
- 平均涨跌幅: {market_data.get('avg_change', 0):.2f}%

📋 交易计划:
{trades}

🛡️ 风控措施: 已停止交易，等待人工检修

请检查系统后重新运行。
"""
            print(report)
            return
    
    # 【P0 Fix】交易执行后重新抓取持仓快照，确保报告数据是交易后的真实状态
    # 否则 position_analysis 和 account 仍是交易前的旧快照，导致报告数据失真
    position_analysis = analyze_positions(executor, market_data, scan_report, position_impacts)
    account = executor.get_account()
    
    # 7. 生成报告（先输出结构化市场数据，确保 AI 不会脑补）
    # 【结构化输出】供 cron 任务直接引用，不依赖 AI 理解
    _s = market_data.get('stance', 'yellow')
    _s_emoji = '🟢' if _s == 'green' else '🟡' if _s == 'yellow' else '🔴'
    _sc = scan_report.get('stance', 'unknown') if scan_report else 'N/A'
    print(f"\n[MARKET_DATA]", flush=True)
    print(f"stance={_s}", flush=True)
    print(f"stance_display={_s_emoji}{_s}", flush=True)
    print(f"avg_change={market_data.get('avg_change', 0):.2f}%", flush=True)
    print(f"sentiment_score={market_data.get('news_sentiment', {}).get('score', 50)}", flush=True)
    print(f"market_status={market_data.get('market_status', 'unknown')}", flush=True)
    print(f"scan_stance={_sc}", flush=True)
    print(f"position_limit={position_limit}%", flush=True)
    print(f"position_count={len(position_analysis)}", flush=True)
    print(f"[/MARKET_DATA]\n", flush=True)

    report = format_report(market_data, position_analysis, trades, results, scan_report, chain.state, force_pause, pause_reason)
    print(report)
    
    # 8. 记录到策略链（闭环反馈）
    try:
        chain = StrategyChain()
        # 必须在使用 account 之前获取，否则 Python 会因后续赋值把它当成局部变量
        account = executor.get_account()

        # ========== 记录完整策略执行报告 ==========
        #是一个全面的策略执行 这记录，供后续环节参考
        execution_report = {
            'timestamp': datetime.now().isoformat(),
            'source': 'auto_trade',
            'market': {
                'stance': market_data.get('stance', 'yellow'),
                'avg_change': market_data.get('avg_change', 0),
                'sentiment_score': market_data.get('news_sentiment', {}).get('score', 50),
                'market_status': market_data.get('market_status', 'unknown')
            },
            'strategy': {
                'source': 'auto_trade',
                'position_limit': position_limit,
                'watchlist': watchlist,
                'sector_allocation': sector_allocation
            },
            'positions': {
                'count': len(position_analysis),
                'total_pnl': sum(p.get('profit_ratio', 0) for p in position_analysis) / max(len(position_analysis), 1),
                'details': [
                    {
                        'symbol': p.get('symbol'),
                        'name': p.get('name', p.get('symbol', '')),
                        'action': p.get('action'),
                        'profit_ratio': p.get('profit_ratio', 0),
                        'today_pct': p.get('today_pct', 0),
                        'reason': p.get('reason', '')
                    } for p in position_analysis
                ]
            },
            'trades_executed': [
                {
                    'type': r.get('trade', {}).get('type'),
                    'symbol': r.get('trade', {}).get('symbol'),
                    'volume': r.get('trade', {}).get('volume'),
                    'price': r.get('trade', {}).get('price'),
                    'status': r.get('result', {}).get('status'),
                    'reason': r.get('trade', {}).get('reason', '')
                } for r in results
            ],
            'account': {
                'position_ratio': account.get('position_value', 0) / account.get('initial_capital', 1) if account.get('initial_capital', 0) > 0 else 0,
                'available_cash': account.get('available_cash', 0),
                'position_value': account.get('position_value', 0),
                'total_assets': account.get('total_assets', 0)
            },
            'scan_reference': scan_report.get('scan_time') if scan_report else None
        }

        # 追加到 intraday_scans 作为策略执行记录
        if 'execution_logs' not in chain.state:
            chain.state['execution_logs'] = []
        chain.state['execution_logs'].append(execution_report)
        # 保留最近 30 条
        if len(chain.state['execution_logs']) > 30:
            chain.state['execution_logs'] = chain.state['execution_logs'][-30:]

        # 记录交易执行（保留原有逻辑）
        for result in results:
            chain.record_trade({
                'timestamp': datetime.now().isoformat(),
                'action': result.get('trade', {}).get('type', 'unknown'),
                'symbol': result.get('trade', {}).get('symbol', ''),
                'volume': result.get('trade', {}).get('volume', 0),
                'price': result.get('trade', {}).get('price', 0),
                'order_id': result.get('result', {}).get('order_id', ''),
                'pnl': result.get('result', {}).get('profit', 0),
                'reason': result.get('trade', {}).get('reason', '')
            })

        # 记录反馈（仓位超标等问题）- 从账户获取仓位比例
        position_ratio = account.get('position_value', 0) / account.get('initial_capital', 1) if account.get('initial_capital', 0) > 0 else 0
        
        if position_ratio > 0.8:
            chain.record_feedback({
                'timestamp': datetime.now().isoformat(),
                'type': 'position_over_limit',
                'current_ratio': position_ratio,
                'limit': 0.8,
                'lesson': f"仓位 {position_ratio*100:.1f}% 超标，下次需再平衡至 80% 以下",
                'action_needed': 'rebalance'
            })
        
        print(f"✅ 策略链已更新")
    except Exception as e:
        print(f"⚠️ 策略链记录失败：{e}")
    
    # 9. 写入日志
    log_dir = WORKSPACE / "memory" / "auto-trade-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    today = datetime.now().strftime('%Y-%m-%d')
    log_file = log_dir / f"{today}-trades.jsonl"
    
    log_data = {
        'timestamp': datetime.now().isoformat(),
        'scan_report': scan_report,
        'market_data': market_data,
        'position_analysis': position_analysis,
        'candidates': candidates,
        'trades': trades,
        'results': results
    }
    
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_data, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # 文件锁在 main() 内部获取，此处确保退出时释放
    # （main() 使用 LOCK_NB 非阻塞锁，如重复执行会直接 return）
    main()
    # 正常结束后静默退出，锁自动随进程释放
