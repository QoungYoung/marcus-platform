#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 盘中扫描脚本
由调度器定时调用，每小时 20 分和 50 分执行市场扫描

输出格式:Markdown 报告，可直接发送到聊天
"""

import sys
import json
from typing import List
from datetime import datetime
from pathlib import Path

# Cross-platform workspace detection
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
from workspace_detector import WORKSPACE, get_vnpy_dir, get_xueqiu_dir, get_akshare_dir, get_data_dir

VNPY_DIR = get_vnpy_dir()
XUEQIU_DIR = get_xueqiu_dir()
AKSHARE_DIR = get_akshare_dir()
DATA_DIR = get_data_dir()

sys.path.insert(0, str(VNPY_DIR))
sys.path.insert(0, str(XUEQIU_DIR))
sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "deepseek"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "paper-trading"))

from paper_engine import PaperTradingEngine
from marcus_trade import MarcusVNPyExecutor
from trade_day_utils import is_today_trade_day
from xueqiu_engine import XueqiuEngine
from akshare_engine import AKShareEngine
from news_analyzer import get_news_analysis, get_stock_news, get_news_sentiment_simple, get_hot_sectors_from_cache
from deepseek_analyzer import filter_news_with_deepseek, _call_deepseek_api
from strategy_chain import StrategyChain

# Tushare 数据获取
from _api_config import get_tushare_pro


# ============= Tushare 数据获取（右侧交易技术面筛选） =============

def _to_ts_code(symbol: str) -> str:
    """
    将股票代码转换为 tushare 标准格式 (xxxxxx.SH / xxxxxx.SZ)。

    支持输入格式:
    - SH600570 → 600570.SH
    - SZ002230 → 002230.SZ
    - 600570   → 600570.SH
    - 002230   → 002230.SZ
    """
    symbol = symbol.strip().upper()
    if symbol.startswith("SH"):
        return f"{symbol[2:]}.SH"
    elif symbol.startswith("SZ"):
        return f"{symbol[2:]}.SZ"
    elif symbol.startswith("6"):
        return f"{symbol}.SH"
    elif symbol.startswith(("0", "3")):
        return f"{symbol}.SZ"
    return symbol


def get_daily_kline(symbol: str, days: int = 30) -> dict:
    """
    获取个股日K线数据（含手动计算的均线）。

    数据源: tushare pro.daily()

    Args:
        symbol: 股票代码，支持 SH600570 / SZ002230 / 600570 等格式
        days: 获取近 N 个交易日的数据，默认 30 天

    Returns:
        dict: {
            'available': bool,
            'symbol': str,
            'ts_code': str,
            'latest': {  # 最近一个交易日
                'trade_date': str,
                'open': float, 'high': float, 'low': float, 'close': float,
                'vol': float, 'amount': float, 'pct_chg': float,
            },
            'ma5': float, 'ma10': float, 'ma20': float,
            'avg_vol_5': float,      # 近 5 日均量
            'near_20_low': float,    # 近 20 日最低价（止损参考）
            'klines': list[dict],    # 完整 K 线列表（按日期升序）
        }
    """
    result = {'available': False, 'symbol': symbol, 'ts_code': '', 'latest': {},
              'ma5': 0, 'ma10': 0, 'ma20': 0, 'avg_vol_5': 0, 'near_20_low': 0, 'klines': []}

    try:
        pro = get_tushare_pro()
        ts_code = _to_ts_code(symbol)
        result['ts_code'] = ts_code

        from datetime import datetime as dt, timedelta
        end_date = dt.now().strftime("%Y%m%d")
        start_date = (dt.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            return result

        # 按交易日期升序排列
        df = df.sort_values("trade_date", ascending=True).reset_index(drop=True)
        if len(df) < 5:
            return result  # 数据不足，无法计算均线

        # 手动计算均线（用最近 N 条数据的 close）
        closes = df["close"].astype(float)
        volumes = df["vol"].astype(float)
        df["ma5"] = closes.rolling(5, min_periods=5).mean()
        df["ma10"] = closes.rolling(10, min_periods=10).mean()
        df["ma20"] = closes.rolling(20, min_periods=20).mean()
        df["avg_vol_5"] = volumes.rolling(5, min_periods=5).mean()

        # 最近一个交易日的数据
        last = df.iloc[-1]
        result['latest'] = {
            'trade_date': str(last["trade_date"]),
            'open': float(last["open"]), 'high': float(last["high"]),
            'low': float(last["low"]), 'close': float(last["close"]),
            'vol': float(last["vol"]), 'amount': float(last["amount"]),
            'pct_chg': float(last.get("pct_chg", 0) or 0),
        }
        result['ma5'] = float(last.get("ma5", 0) or 0)
        result['ma10'] = float(last.get("ma10", 0) or 0)
        result['ma20'] = float(last.get("ma20", 0) or 0)
        result['avg_vol_5'] = float(last.get("avg_vol_5", 0) or 0)

        # 近 20 日最低价（取最近 20 条数据的 low 最小值）
        tail = df.tail(20)
        result['near_20_low'] = float(tail["low"].min())

        # 完整 K 线列表
        result['klines'] = [
            {
                'trade_date': str(r["trade_date"]),
                'open': float(r["open"]), 'high': float(r["high"]),
                'low': float(r["low"]), 'close': float(r["close"]),
                'vol': float(r["vol"]), 'ma5': float(r.get("ma5", 0) or 0),
                'ma10': float(r.get("ma10", 0) or 0), 'ma20': float(r.get("ma20", 0) or 0),
            }
            for _, r in df.iterrows()
        ]

        result['available'] = True

    except ImportError:
        print(f"[日K线] ⚠️ tushare 未安装，无法获取 {symbol} 日K线", file=sys.stderr)
    except EnvironmentError as e:
        print(f"[日K线] ⚠️ Tushare Token 错误: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[日K线] ⚠️ 获取 {symbol} 失败: {e}", file=sys.stderr)

    return result


def get_technical_indicators(symbol: str) -> dict:
    """
    获取个股最新技术指标（MACD、RSI、BOLL 等）。

    数据源: tushare pro.stk_factor_pro()

    Args:
        symbol: 股票代码

    Returns:
        dict: {
            'available': bool,
            'symbol': str,
            'trade_date': str,
            'close': float,
            'macd': float, 'macd_dif': float, 'macd_dea': float,
            'macd_golden_cross': bool,    # MACD 金叉 (DIF > DEA 且 MACD > 0)
            'macd_bullish': bool,         # MACD 多头发散 (DIF > DEA)
            'rsi_6': float, 'rsi_12': float,
            'boll_upper': float, 'boll_mid': float, 'boll_lower': float,
        }
    """
    result = {'available': False, 'symbol': symbol, 'trade_date': '', 'close': 0,
              'macd': 0, 'macd_dif': 0, 'macd_dea': 0,
              'macd_golden_cross': False, 'macd_bullish': False,
              'rsi_6': 0, 'rsi_12': 0,
              'boll_upper': 0, 'boll_mid': 0, 'boll_lower': 0}

    try:
        pro = get_tushare_pro()
        ts_code = _to_ts_code(symbol)

        from datetime import datetime as dt, timedelta
        end_date = dt.now().strftime("%Y%m%d")
        start_date = (dt.now() - timedelta(days=10)).strftime("%Y%m%d")

        df = pro.stk_factor_pro(ts_code=ts_code, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            return result

        df = df.sort_values("trade_date", ascending=False)
        last = df.iloc[0]

        result['trade_date'] = str(last["trade_date"])
        result['close'] = float(last.get("close", 0) or 0)
        # tushare stk_factor_pro 返回的列名带 _bfq/_hfq/_qfq 后缀，使用不复权 _bfq
        result['macd'] = float(last.get("macd_bfq", 0) or 0)
        result['macd_dif'] = float(last.get("macd_dif_bfq", 0) or 0)
        result['macd_dea'] = float(last.get("macd_dea_bfq", 0) or 0)
        result['macd_bullish'] = result['macd_dif'] > result['macd_dea']
        result['macd_golden_cross'] = result['macd_bullish'] and result['macd'] > 0
        result['rsi_6'] = float(last.get("rsi_bfq_6", 0) or 0)
        result['rsi_12'] = float(last.get("rsi_bfq_12", 0) or 0)
        result['boll_upper'] = float(last.get("boll_upper_bfq", 0) or 0)
        result['boll_mid'] = float(last.get("boll_mid_bfq", 0) or 0)
        result['boll_lower'] = float(last.get("boll_lower_bfq", 0) or 0)

        result['available'] = True

    except ImportError:
        print(f"[技术指标] ⚠️ tushare 未安装", file=sys.stderr)
    except EnvironmentError as e:
        print(f"[技术指标] ⚠️ Tushare Token 错误: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[技术指标] ⚠️ 获取 {symbol} 失败: {e}", file=sys.stderr)

    return result


# ============= 缺口检测功能 =============

def detect_gap_risk(indices: dict, threshold: float = -1.5) -> dict:
    """
    检测隔夜缺口风险。

    缺口定义：gap = (今开 - 昨收) / 昨收 * 100%
    - threshold=-1.5：缺口低于 -1.5% 视为显著风险
    - 综合三大指数缺口判断市场整体隔夜情绪

    Args:
        indices: 指数 dict，每项含 gap/last_close/change 字段
        threshold: 缺口阈值（默认 -1.5%）

    Returns:
        dict: {
            'has_gap_risk': bool,          # 是否存在显著缺口风险
            'gap_count': int,              # 缺口低于阈值的指数数量
            'worst_gap': float,            # 最大缺口幅度（负值）
            'worst_gap_name': str,          # 缺口最大的指数名
            'gap_detail': dict,             # 各指数缺口详情
            'adjustment_needed': bool,      # 是否需要调整策略
            'adjustment_reason': str,       # 调整原因描述
        }
    """
    gap_detail = {}
    negative_gaps = []

    for name, data in indices.items():
        gap = data.get('gap', 0.0)
        last_close = data.get('last_close', 0)
        open_price = data.get('open', 0)
        change = data.get('change', 0)
        gap_detail[name] = {
            'gap': gap,
            'open': open_price,
            'last_close': last_close,
            'change': change,
        }
        if gap < threshold:
            negative_gaps.append((name, gap))

    gap_count = len(negative_gaps)
    worst_gap = min([g[1] for g in negative_gaps]) if negative_gaps else 0.0
    worst_gap_name = [g[0] for g in negative_gaps if g[1] == worst_gap][0] if negative_gaps else ''

    has_gap_risk = gap_count >= 2  # 至少2个指数同时出现显著缺口
    adjustment_needed = has_gap_risk
    adjustment_reason = (
        f'缺口预警：{gap_count}个指数低开缺口超{threshold}% '
        f'(最弱: {worst_gap_name} {worst_gap:+.2f}%)'
    ) if has_gap_risk else ''

    print(f"[缺口检测] threshold={threshold}% | 低于阈值: {gap_count}个 | "
          f"最弱: {worst_gap_name} {worst_gap:+.2f}% | 风险: {has_gap_risk}")

    return {
        'has_gap_risk': has_gap_risk,
        'gap_count': gap_count,
        'worst_gap': worst_gap,
        'worst_gap_name': worst_gap_name,
        'gap_detail': gap_detail,
        'adjustment_needed': adjustment_needed,
        'adjustment_reason': adjustment_reason,
    }


# ============= 缩量上涨风险检测（Marcus 纪律：缩量上涨需警惕） =============

def detect_shrink_volume_rally(symbols: list) -> dict:
    """
    检测持仓股票是否存在「缩量上涨」风险。

    Marcus 策略：放量突破是真突破，缩量上涨需警惕。
    缩量上涨 = 今日涨幅 > 0 且 成交量 < 近 5 日均量 × 0.7。

    Args:
        symbols: 股票代码列表

    Returns:
        dict: {symbol: {'warning': bool, 'today_pct': float, 'vol_ratio': float, 'message': str}}
    """
    result = {}
    if not symbols:
        return result

    for sym in symbols:
        try:
            kline = get_daily_kline(sym, days=10)
            if not kline.get('available') or len(kline.get('klines', [])) < 6:
                continue

            kl = kline['klines']
            today = kl[-1]
            yesterday = kl[-2]
            today_close = today.get('close', 0)
            yesterday_close = yesterday.get('close', 0)
            today_vol = today.get('vol', 0)
            avg_vol_5 = kline.get('avg_vol_5', 0)

            if today_close <= 0 or yesterday_close <= 0 or avg_vol_5 <= 0:
                continue

            pct_chg = (today_close - yesterday_close) / yesterday_close * 100
            vol_ratio = today_vol / avg_vol_5

            # 缩量上涨判定：涨了但量不足 70% 均量
            if pct_chg > 0 and vol_ratio < 0.7:
                risk_level = 'high' if pct_chg > 3 and vol_ratio < 0.5 else 'medium'
                result[sym] = {
                    'warning': True,
                    'risk_level': risk_level,
                    'today_pct': round(pct_chg, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'message': f"缩量上涨 {pct_chg:+.2f}% (量比 {vol_ratio:.2f})，警惕假突破"
                }
            elif pct_chg > 0 and vol_ratio < 0.85:
                # 轻度缩量：预警但不标红
                result[sym] = {
                    'warning': True,
                    'risk_level': 'low',
                    'today_pct': round(pct_chg, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'message': f"轻度缩量上涨 {pct_chg:+.2f}% (量比 {vol_ratio:.2f})"
                }
        except Exception as e:
            print(f"[缩量检测] ⚠️ {sym} 失败: {e}", file=sys.stderr)

    if result:
        warnings = [f"{s}({v['message']})" for s, v in result.items()]
        print(f"[缩量检测] 🚨 {len(result)}只缩量上涨: {', '.join(warnings)}", file=sys.stderr)

    return result


# ============= 策略验证功能 (从 v2 合并) =============

# ========== 指数确认配置（配置化，改这里无需改代码） ==========
# 只确认可交易市场对应的指数，避免"用创业板指数给自己壮胆买主板"
INDEX_CONFIRMATION = {
    # 参与确认的指数名（需与 get_market_status() 返回的 key 一致）
    'indices': ['上证指数', '深证成指', '沪深300'],
    # 确认条件：至少 N 个指数涨幅 > threshold%
    'min_count': 2,
    'threshold': 0.5,
    # 开户满2年可加回: 'indices': ['上证指数','深证成指','沪深300','创业板指','科创50']
}


def validate_pre_market_strategy(pre_market: dict, current_market: dict, news_sentiment: dict, gap_risk: dict = None, fund_flow: dict = None) -> dict:
    """
    验证盘前策略是否需要调整（右侧交易版本）。

    决策优先级：价格行为确认 → 缺口风险 → 情绪辅助加分（+5%）。
    价格定方向，资金流不参与方向判断——只在 adjust_strategy() Step 8 做幅度微调。

    Args:
        pre_market: 盘前策略
        current_market: 当前市场数据
        news_sentiment: 新闻情绪（降级为辅助参考）
        gap_risk: 缺口风险检测结果
        fund_flow: 资金流向数据（传给 adjust_strategy 做微调，此处不参与方向判断）

    Returns:
        dict: 验证结果 {
            price_action_confirm:  价格行为确认（主导信号）
            sentiment_bonus:       情绪加分（仅 +5% 仓位）
            adjustment_needed:     是否需要降低仓位
            adjustment_reason:     调整原因
        }
    """
    validation = {
        'price_action_confirm': False,   # 价格行为确认（技术面）
        'sentiment_bonus': 0,            # 情绪加分（0 或 5%）
        'fund_flow_confirm': False,      # 资金流确认（adjust 层使用，validate 不参与）
        'adjustment_needed': False,
        'adjustment_reason': '',
        'gap_risk': False,
    }

    # 1. 价格行为确认（主导信号，配置化：只确认可交易市场的指数）
    indices = current_market.get('indices', {})
    confirm_indices = INDEX_CONFIRMATION['indices']
    threshold = INDEX_CONFIRMATION['threshold']
    min_count = INDEX_CONFIRMATION['min_count']
    bullish_count = sum(1 for name in confirm_indices
                        if indices.get(name, {}).get('change', 0) > threshold)
    if bullish_count >= min_count:
        validation['price_action_confirm'] = True
        print(f"[价格确认] ✅ {bullish_count}/{len(confirm_indices)}个可交易指数涨幅>{threshold}%，趋势确认")
    else:
        detail = ', '.join(f"{name}={indices.get(name, {}).get('change', 0):+.1f}%"
                          for name in confirm_indices)
        print(f"[价格确认] ⚠️ {bullish_count}/{min_count}个指数确认 ({detail})，趋势未成立")

    # 1b. 下跌检测：统计跌幅超过阈值的指数数量（对称反向信号，用于持续降级判断）
    bearish_count = sum(1 for name in confirm_indices
                        if indices.get(name, {}).get('change', 0) <= -threshold)
    validation['bearish_count'] = bearish_count
    if bearish_count >= min_count:
        bearish_detail = ', '.join(f"{name}={indices.get(name, {}).get('change', 0):+.1f}%"
                                   for name in confirm_indices)
        print(f"[价格确认] 🔻 {bearish_count}/{min_count}个可交易指数跌幅≤-{threshold}%，空头压力 ({bearish_detail})")

    # 2. 情绪辅助加分（降级为辅助，权重 12%，最多 +5% 仓位）
    sentiment_score = news_sentiment.get('score', 50)
    if sentiment_score >= 60:
        validation['sentiment_bonus'] = 5  # 情绪好 +5%，不改变方向
        print(f"[情绪辅助] 情绪={sentiment_score:.0f} → +5% 仓位加成")

    # 3. 资金流 — validate 层不参与方向判断，交给 adjust_strategy() Step 8 做幅度微调

    # 4. 缺口风险验证（保持不变）
    if gap_risk and gap_risk.get('adjustment_needed'):
        validation['adjustment_needed'] = True
        validation['gap_risk'] = True
        existing_reason = validation.get('adjustment_reason', '')
        if existing_reason:
            validation['adjustment_reason'] = f"{existing_reason} + {gap_risk['adjustment_reason']}"
        else:
            validation['adjustment_reason'] = gap_risk['adjustment_reason']
        print(f"[缺口验证] ✅ 触发缺口风险: {gap_risk['adjustment_reason']}")

    # 5. 价格行为未确认时的谨慎信号
    if not validation['price_action_confirm'] and not validation.get('adjustment_needed'):
        pre_stance = pre_market.get('initial_strategy', {}).get('stance', '')
        if pre_stance == '🟢 aggressive_buy':
            validation['adjustment_needed'] = True
            validation['adjustment_reason'] = '价格行为未确认趋势，谨慎降低仓位'
            print(f"[价格确认] ⚠️ 仅{bullish_count}个指数>0.5%，趋势未确认")

    return validation


# ========== 持续降级检测（跨轮次下跌追踪） ==========

# 降级配置（配置化，改这里无需改代码）
DOWNGRADE_CONFIG = {
    'persist_rounds': 2,       # 连续 N 轮扫描触发降级
    'bearish_threshold': 0.5,  # 跌幅阈值（与 INDEX_CONFIRMATION.threshold 对称）
    'min_bearish_indices': 2,  # 至少 N 个指数满足条件
}


def check_persistent_downgrade(validation: dict, chain: StrategyChain = None) -> dict:
    """
    检测空头压力是否持续多轮，触发 stance 降级。

    触发条件：
    - 至少 DOWNGRADE_CONFIG['min_bearish_indices'] 个可交易指数跌幅 ≤ -0.5%
    - 该条件持续超过 DOWNGRADE_CONFIG['persist_rounds'] 轮扫描（约 20 分钟）

    降级规则：
    - 当前 green → 降级为 yellow
    - 当前 yellow → 降级为 red（hold 观望）
    - 当前已是 red → 不再继续降级

    Args:
        validation: 当前轮的 validation（含 bearish_count）
        chain: 策略链管理器（读取 intraday_scans 历史）

    Returns:
        dict: {
            'downgrade_triggered': bool,
            'downgrade_from': str,
            'downgrade_to': str,
            'consecutive_rounds': int,  # 连续下跌轮数（含当前）
            'reason': str,
        }
    """
    result = {
        'downgrade_triggered': False,
        'downgrade_from': '',
        'downgrade_to': '',
        'consecutive_rounds': 0,
        'reason': '',
    }

    current_bearish = validation.get('bearish_count', 0)
    min_bearish = DOWNGRADE_CONFIG['min_bearish_indices']
    persist_rounds = DOWNGRADE_CONFIG['persist_rounds']

    # 本轮不满足下跌条件，不触发
    if current_bearish < min_bearish:
        return result

    # 当前轮满足下跌条件，检查历史
    consecutive = 1  # 含当前轮

    if chain:
        intraday_scans = chain.state.get('intraday_scans', [])
        # 从最近一轮往前数，连续的 bearish
        for scan in reversed(intraday_scans):
            validation_hist = scan.get('validation', {})
            hist_bearish = validation_hist.get('bearish_count', 0)
            if hist_bearish >= min_bearish:
                consecutive += 1
            else:
                break  # 连续性中断

    result['consecutive_rounds'] = consecutive

    if consecutive < persist_rounds:
        print(f"[持续降级] 🔻 下跌{consecutive}轮 < {persist_rounds}轮阈值，继续观察")
        return result

    # 触发降级：读取上轮 stance 作为降级起点
    prev_stance_code = 'yellow'  # 默认
    from_stance = '未知'

    if chain:
        intraday_scans = chain.state.get('intraday_scans', [])
        if intraday_scans:
            prev_scan = intraday_scans[-1]
            prev_stance_code = prev_scan.get('stance_code', 'yellow')
            from_stance = prev_scan.get('stance', '未知')
        else:
            # 无历史扫描时，检查盘前策略
            pre_market = chain.state.get('pre_market', {})
            initial = pre_market.get('initial_strategy', {})
            from_stance = initial.get('stance', '未知')

    # 降级映射
    downgrade_map = {
        'green': ('yellow', '🟡 cautious_buy (降级: 空头持续)'),
        'yellow': ('red', '🔴 hold (降级: 空头持续)'),
        'red': ('red', '🔴 hold (持续空头中)'),
    }

    to_code, to_stance = downgrade_map.get(prev_stance_code, ('yellow', '🟡 cautious_buy (降级: 空头持续)'))

    if to_code == prev_stance_code:
        print(f"[持续降级] 🔴 已处于 {prev_stance_code}，不再继续降级")
        return result

    result['downgrade_triggered'] = True
    result['downgrade_from'] = from_stance
    result['downgrade_to'] = to_stance
    result['reason'] = (
        f"空头压力持续 {consecutive} 轮（≥{persist_rounds}轮阈值），"
        f"{current_bearish}/{len(INDEX_CONFIRMATION['indices'])}个可交易指数跌幅≤-{DOWNGRADE_CONFIG['bearish_threshold']}%"
    )
    result['to_code'] = to_code

    print(f"[持续降级] 🚨 触发降级! {prev_stance_code} → {to_code} ({consecutive}轮空头，从 '{from_stance}' 降级)")
    print(f"[持续降级] 原因: {result['reason']}")

    return result


def analyze_trade_feedback(chain: StrategyChain) -> list:
    """
    分析已有交易的反馈，并将结果写回策略链（闭环核心）

    Args:
        chain: 策略链管理器

    Returns:
        list: 交易反馈列表
    """
    all_trades = chain.state.get('trades', [])
    # 获取最近 10 笔（但保留原始索引用于 update_trade_feedback）
    recent_trades = all_trades[-10:] if len(all_trades) > 10 else all_trades
    feedback_list = []

    if not recent_trades:
        return feedback_list

    # 获取当前持仓用于计算盈亏
    try:
        paper_engine = PaperTradingEngine()
        positions = paper_engine.get_positions()
        position_map = {p['symbol']: p for p in positions}
    except:
        position_map = {}

    for relative_i, trade in enumerate(recent_trades):
        # 计算实际索引（全量列表中的位置）
        actual_trade_index = len(all_trades) - len(recent_trades) + relative_i

        if trade.get('action') not in ('buy', '买入', 'sell', '卖出'):
            continue

        symbol = trade.get('symbol', '')
        buy_price = trade.get('price', 0)

        # 计算当前盈亏
        if symbol in position_map:
            pos = position_map[symbol]
            current_price = pos.get('current_price', pos.get('avg_price', buy_price))
            pnl_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        else:
            # 持仓已不在，说明已卖出（止盈/止损），从 trade.pnl 获取（单位：元，需换算为百分比）
            pnl_yuan = trade.get('pnl', 0)
            cost_basis = buy_price * trade.get('volume', 1)
            pnl_pct = (pnl_yuan / cost_basis * 100) if cost_basis > 0 else 0

        # 策略有效性判断
        strategy_valid = pnl_pct > 0  # 盈利即策略有效

        # 下一步行动建议
        if pnl_pct > 10:
            next_action = 'hold_with_trailing_stop'  # 启用移动止盈
        elif pnl_pct > 0:
            next_action = 'hold'  # 继续持有
        elif pnl_pct < -8:
            next_action = 'cut_loss'  # 止损
        else:
            next_action = 'watch'  # 观察

        feedback = {
            'trade_index': actual_trade_index,
            'symbol': symbol,
            'current_pnl': round(pnl_pct, 2),
            'strategy_valid': strategy_valid,
            'next_action': next_action
        }

        feedback_list.append(feedback)

        # 写回策略链，使 analyze_strategy_effectiveness 能读到
        try:
            chain.update_trade_feedback(actual_trade_index, feedback)
            print(f"[反馈闭环] ✅ {symbol} 反馈已写入 (盈亏 {pnl_pct:+.1f}%, 下步: {next_action})")
        except Exception as e:
            print(f"[反馈闭环] ⚠️ 写入失败: {e}")

    return feedback_list


# ============= 连续亏损检测（Marcus 纪律：连续亏损 3 笔强制休息） =============

def check_consecutive_losses(chain: StrategyChain) -> dict:
    """
    检测连续亏损交易，触发强制休息机制。

    Marcus 纪律: 连续亏损 3 笔后强制休息 30 分钟，不允许新入场。

    Args:
        chain: 策略链管理器

    Returns:
        dict: {
            'force_pause': bool,          # 是否触发强制暂停
            'consecutive_losses': int,    # 连续亏损笔数
            'loss_symbols': list[str],    # 亏损股票代码
            'reason': str,                # 暂停原因描述
        }
    """
    result = {'force_pause': False, 'consecutive_losses': 0,
              'loss_symbols': [], 'reason': ''}

    trades = chain.state.get('trades', [])
    if not trades:
        return result

    # 逆序遍历，统计连续亏损
    consecutive_count = 0
    loss_symbols = []
    for trade in reversed(trades):
        if trade.get('action') not in ('buy', '买入'):
            continue

        # 读取已写入的反馈数据
        feedback = trade.get('feedback', {})
        pnl = feedback.get('current_pnl', None)

        # 如果 feedback 中还没有 current_pnl（尚未分析过），尝试从 position 计算
        if pnl is None:
            pnl_yuan = trade.get('pnl', 0)
            buy_price = trade.get('price', 0)
            volume = trade.get('volume', 1)
            if buy_price > 0 and volume > 0:
                pnl = (pnl_yuan / (buy_price * volume)) * 100
            else:
                continue  # 无法判断，跳过

        if pnl < 0:
            consecutive_count += 1
            loss_symbols.append(trade.get('symbol', '?'))
        else:
            # 遇到盈利交易，中断计数
            break

    result['consecutive_losses'] = consecutive_count
    result['loss_symbols'] = loss_symbols

    if consecutive_count >= 3:
        result['force_pause'] = True
        syms = ', '.join(loss_symbols[:3])
        result['reason'] = f'连续 {consecutive_count} 笔亏损 ({syms})，触发 Marcus 强制休息机制，暂停新交易入场'
        print(f"[连续亏损] 🚨 {result['reason']}")

    return result


# ============= 个股技术面筛选（右侧交易硬过滤） =============

def screen_candidates_technically(candidates: list) -> dict:
    """
    对候选股票执行右侧交易技术面筛选。

    三项硬过滤条件:
    1. 价格站稳 5 日均线: close > ma5
    2. MACD 金叉或多头发散: macd_dif > macd_dea 或 macd > 0 或 近叉(DIF-DEA<0.05)
    3. 放量突破: vol > avg_vol_5 * 1.2

    三项全过才入选，同时计算每只通过股票的止损价 + 趋势阶段。

    Args:
        candidates: 候选股票代码列表

    Returns:
        dict: {
            'passed': list[dict],      # 通过筛选的股票（含详细数据 + 止损价 + 趋势阶段）
            'failed': list[dict],      # 未通过的股票（含失败原因）
            'total_scanned': int,
            'total_passed': int,
        }
    """

    def _assess_trend_stage(kline: dict, tech: dict) -> dict:
        """
        判断趋势所处阶段（右侧交易核心：初期入场，末期回避）。

        Returns:
            {'stage': '初期'|'加速期'|'末期', 'score': 1-5, 'warning': str}
        """
        stage = '初期'
        score = 3  # 默认中性
        warning = ''

        kl = kline.get('klines', [])
        if len(kl) < 5:
            return {'stage': '初期', 'score': 1, 'warning': '数据不足'}

        # 1. 连续站稳 5 日线天数
        days_above_ma5 = 0
        for k in reversed(kl):
            if k.get('close', 0) > k.get('ma5', 0) > 0:
                days_above_ma5 += 1
            else:
                break

        # 2. MACD 柱体方向（取最近 3 个柱体比较）
        macd_recent = [k.get('macd', 0) for k in kl[-5:] if k.get('macd') is not None]
        # 用 tech 中的 macd 值作为补充（klines 中没有 macd 字段，用 ma diff 模拟）
        macd_expanding = False
        macd_contracting = False
        if len(macd_recent) >= 3:
            macd_contracting = abs(macd_recent[-1]) < abs(macd_recent[-3])
            macd_expanding = abs(macd_recent[-1]) > abs(macd_recent[-3])

        # 3. RSI 超买判断
        rsi6 = tech.get('rsi_6', 0)
        rsi12 = tech.get('rsi_12', 0)

        # --- 阶段判定 ---
        if days_above_ma5 <= 3:
            stage = '初期'
            score = 4  # 最佳入场时机
            warning = ''
        elif days_above_ma5 <= 8:
            if macd_expanding and rsi6 < 75:
                stage = '加速期'
                score = 5  # 趋势最强
            elif macd_contracting:
                stage = '加速期'
                score = 3
                warning = 'MACD柱体收缩，动能减弱'
            else:
                stage = '加速期'
                score = 4
        else:
            # 10+ 天站上5日线
            if rsi6 > 75:
                stage = '末期'
                score = 1
                warning = f'RSI={rsi6:.0f}超买 + 连续{days_above_ma5}天站稳，追高风险大'
            elif macd_contracting:
                stage = '末期'
                score = 2
                warning = f'连续{days_above_ma5}天站稳且MACD收缩，趋势衰竭'
            elif rsi6 > 70:
                stage = '末期'
                score = 2
                warning = f'连续{days_above_ma5}天站稳，RSI={rsi6:.0f}接近超买'
            else:
                stage = '加速期'
                score = 3
                warning = f'趋势持续{days_above_ma5}天，注意风险'

        return {
            'stage': stage,
            'score': score,
            'days_above_ma5': days_above_ma5,
            'rsi6': round(rsi6, 1),
            'macd_expanding': macd_expanding,
            'warning': warning,
        }
    result = {'passed': [], 'failed': [], 'total_scanned': len(candidates), 'total_passed': 0}

    if not candidates:
        return result

    print(f"\n[技术面筛选] 开始扫描 {len(candidates)} 只候选股...", file=sys.stderr)

    for i, candidate in enumerate(candidates):
        # 兼容 watchlist 中可能是 dict 或 str
        symbol = candidate['symbol'] if isinstance(candidate, dict) else candidate

        # 限制单次扫描 top 20，避免 tushare API 过载
        if i >= 20:
            print(f"[技术面筛选] ⚠️ 候选股超过 20 只，仅扫描前 20 只", file=sys.stderr)
            break

        fail_reasons = []

        # Step 1: 获取日K线（含均线）
        kline = get_daily_kline(symbol, days=30)
        if not kline.get('available'):
            failed = {'symbol': symbol, 'reasons': ['无法获取日K线数据']}
            result['failed'].append(failed)
            print(f"  ❌ {symbol}: 无法获取日K线", file=sys.stderr)
            continue

        latest = kline['latest']
        close = latest.get('close', 0)
        vol = latest.get('vol', 0)
        ma5 = kline.get('ma5', 0)
        avg_vol_5 = kline.get('avg_vol_5', 0)
        near_20_low = kline.get('near_20_low', 0)

        # 条件 1: 价格站稳 5 日均线
        if close <= 0 or ma5 <= 0:
            fail_reasons.append('无有效均线数据')
        elif close <= ma5:
            fail_reasons.append(f'未站稳5日线 (close={close:.2f} ≤ ma5={ma5:.2f})')

        # Step 2: 获取技术指标
        tech = get_technical_indicators(symbol)
        macd_bullish = tech.get('macd_bullish', False)
        macd = tech.get('macd', 0)

        if tech.get('available'):
            # 条件 2: MACD 金叉或多头发散（含近叉容差）
            macd_dif = tech.get('macd_dif', 0)
            macd_dea = tech.get('macd_dea', 0)
            near_cross = abs(macd_dif - macd_dea) < 0.05  # DIF接近DEA，即将金叉
            if not macd_bullish and macd <= 0 and not near_cross:
                fail_reasons.append(f'MACD未金叉 (DIF={macd_dif:.3f} DEA={macd_dea:.3f})')
            elif near_cross and not macd_bullish:
                print(f"  ⚡ {symbol}: MACD近叉 (DIF={macd_dif:.3f} DEA={macd_dea:.3f})，放行", file=sys.stderr)
        else:
            fail_reasons.append('无法获取技术指标')

        # 条件 3: 放量突破（量比 1.2x，盘中适度放宽）
        if avg_vol_5 > 0:
            vol_ratio = vol / avg_vol_5
            if vol_ratio < 1.2:
                fail_reasons.append(f'未放量 (量比={vol_ratio:.2f} < 1.2)')
        else:
            fail_reasons.append('无有效成交量数据')

        if fail_reasons:
            failed = {'symbol': symbol, 'reasons': fail_reasons}
            result['failed'].append(failed)
            summary = '; '.join(fail_reasons[:2])
            print(f"  ❌ {symbol}: {summary}", file=sys.stderr)
        else:
            # 三项全过 → 计算止损价
            stop_loss_candidates = []
            if near_20_low > 0:
                stop_loss_candidates.append(near_20_low * 0.99)
            if close > 0:
                stop_loss_candidates.append(close * 0.93)
            stop_loss = min(stop_loss_candidates) if stop_loss_candidates else 0

            vol_ratio = vol / avg_vol_5 if avg_vol_5 > 0 else 0

            # 趋势阶段判断（右侧交易核心：初期入场，末期回避）
            trend = _assess_trend_stage(kline, tech)

            passed_item = {
                'symbol': symbol,
                'close': round(close, 2),
                'ma5': round(ma5, 2),
                'ma_position': f'{(close/ma5 - 1)*100:+.1f}%' if ma5 > 0 else 'N/A',
                'macd_dif': round(tech.get('macd_dif', 0), 4),
                'macd_dea': round(tech.get('macd_dea', 0), 4),
                'macd_direction': '金叉' if tech.get('macd_golden_cross') else '发散',
                'vol_ratio': round(vol_ratio, 2),
                'stop_loss': round(stop_loss, 2),
                'stop_loss_pct': f'{(stop_loss/close - 1)*100:+.1f}%' if close > 0 and stop_loss > 0 else 'N/A',
                # 趋势阶段
                'trend_stage': trend['stage'],
                'trend_score': trend['score'],
                'trend_days': trend['days_above_ma5'],
                'rsi6': trend['rsi6'],
                'trend_warning': trend['warning'],
            }
            result['passed'].append(passed_item)
            trend_tag = f" [{trend['stage']}⭐{trend['score']}]" + (f" ⚠️{trend['warning']}" if trend['warning'] else '')
            print(f"  ✅ {symbol}: close={close:.2f} ma5={ma5:.2f} 量比={vol_ratio:.2f} 止损={stop_loss:.2f}{trend_tag}", file=sys.stderr)

    result['total_passed'] = len(result['passed'])
    result['total_scanned'] = min(len(candidates), 20)
    print(f"[技术面筛选] 完成: {result['total_passed']}/{result['total_scanned']} 通过", file=sys.stderr)

    return result


# ============= 🤖 节点C+D：假突破过滤 + 催化质量打分 =============

def _read_catalyst(symbol: str) -> dict:
    """从 news_catalysts.json 读取个股催化数据"""
    try:
        cat_file = WORKSPACE / "data" / "news_catalysts.json"
        if not cat_file.exists():
            return {}
        with open(cat_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # symbol 可能是 SH600570 或 600570 两种格式
        for key in (symbol, symbol[2:] if symbol.startswith(('SH', 'SZ')) else ''):
            if key and key in data:
                item = data[key]
                return {
                    'name': item.get('name', ''),
                    'catalyst_count': item.get('catalyst_count', 0),
                    'catalyst_keywords': item.get('catalyst_keywords', []),
                    'days_without_catalyst': item.get('days_without_catalyst', 99),
                    'news_score': item.get('news_score', 50),
                    'sentiment': item.get('sentiment', ''),
                    'risk_keywords': item.get('risk_keywords', []),
                }
    except Exception:
        pass
    return {}


def assess_candidates(passed_list: list) -> dict:
    """
    合并假突破过滤 + 催化质量打分，一次 DeepSeek API 调用完成。

    对通过技术面筛选的候选股：
    1. 假突破过滤：检查减持公告/问询函/暴雷/业绩变脸 → NO-GO
    2. 催化质量打分：A级(政策/重大订单)/B级(业绩/景气)/C级(游资/话题)

    Args:
        passed_list: screen_candidates_technically() 返回的 passed 列表

    Returns:
        {
            'passed': [{symbol, grade, catalyst, reason, ...}],
            'rejected': [{symbol, reason}],
        }
    """
    result = {'passed': [], 'rejected': []}
    if not passed_list:
        return result

    print(f"\n[AI候选评估] 🤖 假突破过滤 + 催化打分 ({len(passed_list)}只)...", file=sys.stderr)

    # 为每只候选拉新闻和催化数据
    symbols = [p['symbol'] for p in passed_list]
    stock_news_map = {}
    try:
        stock_news_map = get_stock_news(symbols, limit=3)
    except Exception as e:
        print(f"[AI候选评估] ⚠️ 新闻获取失败: {e}", file=sys.stderr)

    candidates_input = []
    for p in passed_list:
        sym = p['symbol']
        cat = _read_catalyst(sym)
        news = stock_news_map.get(sym, [])
        news_titles = [n.get('title', '')[:60] for n in (news if isinstance(news, list) else [])]

        candidates_input.append({
            'symbol': sym,
            'close': p.get('close', 0),
            'ma_position': p.get('ma_position', ''),
            'macd_direction': p.get('macd_direction', ''),
            'vol_ratio': p.get('vol_ratio', 0),
            'trend_stage': p.get('trend_stage', ''),
            'trend_score': p.get('trend_score', 3),
            'trend_days': p.get('trend_days', 0),
            'rsi6': p.get('rsi6', 0),
            'trend_warning': p.get('trend_warning', ''),
            'catalyst': {
                'keywords': cat.get('catalyst_keywords', [])[:3],
                'count': cat.get('catalyst_count', 0),
                'days_without': cat.get('days_without_catalyst', 99),
            },
            'risk_keywords': cat.get('risk_keywords', []),
            'recent_news': news_titles[:3],
        })

    try:
        system_prompt = """你是 A 股短线交易筛股专家。对每只技术面通过的候选股做两件事：

1. 假突破过滤 — 一票否决的信号：
   - 大股东减持公告 / 高管减持
   - 收到问询函 / 监管函 / 立案调查
   - 业绩变脸 / 大幅下修 / 商誉暴雷
   - 限售股大规模解禁
   - 以上任何一项 → go_nogo = "NO-GO"

2. 催化质量打分（结合趋势阶段调整）：
   - A级：国家级政策 / 重大合同订单（金额>年营收10%）/ 新产品获批
   - B级：行业景气度上升 / 业绩预增 / 技术突破 / 机构调研密集
   - C级：游资炒作 / 题材概念跟风 / 无实质催化
   - 无催化 → grade = "C"

3. 趋势阶段降级规则（重要）：
   - trend_stage="末期" 且 RSI>70 → grade 降一级（A→B, B→C）
   - trend_stage="初期" 且有实质催化 → grade 升一级概率
   - trend_warning 非空时，grade 不低于 B（除非有实质风险）

输出严格 JSON 数组：
[{"symbol": "SH600570", "go_nogo": "GO", "grade": "A", "catalyst": "AI金融政策落地", "reason": "一句话"}]"""

        user_prompt = json.dumps(candidates_input, ensure_ascii=False)
        ai_result = _call_deepseek_api(system_prompt, user_prompt)

        if ai_result and isinstance(ai_result, list):
            for item in ai_result:
                sym = item.get('symbol', '')
                go = item.get('go_nogo', 'GO')
                # 找到对应的原始数据
                original = next((p for p in passed_list if p['symbol'] == sym), None)
                if not original:
                    print(f"  ⚠️ AI 返回未知 symbol: {sym}，跳过", file=sys.stderr)
                    continue
                if go == 'GO':
                    entry = dict(original)
                    entry['grade'] = item.get('grade', 'B')
                    entry['catalyst'] = item.get('catalyst', '')
                    entry['ai_reason'] = item.get('reason', '')
                    result['passed'].append(entry)
                    print(f"  ✅ {sym}: {entry['grade']}级 ({entry.get('catalyst', '无')})", file=sys.stderr)
                else:
                    result['rejected'].append({
                        'symbol': sym,
                        'reason': item.get('reason', '假突破过滤'),
                    })
                    print(f"  ❌ {sym}: NO-GO ({item.get('reason', '')})", file=sys.stderr)
        else:
            # AI 失败 → 全部放行，默认 B 级
            for p in passed_list:
                p['grade'] = 'B'
                p['catalyst'] = ''
                p['ai_reason'] = 'AI 不可用，默认放行'
            result['passed'] = passed_list
            print(f"[AI候选评估] ⚠️ API 返回异常，全部默认 B 级放行", file=sys.stderr)

    except Exception as e:
        print(f"[AI候选评估] ⚠️ 调用失败: {e}，全部默认 B 级放行", file=sys.stderr)
        for p in passed_list:
            p['grade'] = 'B'
            p['catalyst'] = ''
            p['ai_reason'] = f'AI 异常: {e}'
        result['passed'] = passed_list

    print(f"[AI候选评估] 完成: {len(result['passed'])} GO / {len(result['rejected'])} NO-GO", file=sys.stderr)
    return result


# ============= 节点E：持仓相关性预警（纯规则，零 AI 调用） =============

def check_correlation(existing_positions: list, candidate_symbol: str, sector_warnings: list = None) -> dict:
    """
    纯规则检查：候选股与现有持仓是否存在行业相关性过载。

    从 stock_pool.db 查行业分类，统计同一行业的持仓数。
    同一行业已有 2+ 只持仓 → 报警。

    Args:
        existing_positions: 现有持仓列表 [{symbol, ...}, ...]
        candidate_symbol: 候选股票代码
        sector_warnings: 外部传入的警告列表引用，用于累积

    Returns:
        {'warning': bool, 'level': 'yellow'/'red', 'reason': str}
    """
    if not existing_positions:
        return {'warning': False, 'level': '', 'reason': ''}

    try:
        import sqlite3
        db = WORKSPACE / "data" / "stock_pool.db"
        if not db.exists():
            return {'warning': False, 'level': '', 'reason': ''}

        conn = sqlite3.connect(str(db))

        # 查候选股的行业
        short = candidate_symbol[2:] if candidate_symbol.startswith(('SH', 'SZ')) else candidate_symbol
        row = conn.execute(
            "SELECT industry FROM stock_pool WHERE symbol = ? OR symbol = ? LIMIT 1",
            (candidate_symbol, short)
        ).fetchone()
        cand_industry = row[0] if row and row[0] else None
        if not cand_industry:
            conn.close()
            return {'warning': False, 'level': '', 'reason': ''}

        # 统计已有持仓中同行业的
        same_industry_positions = []
        for pos in existing_positions:
            sym = pos.get('symbol', '')
            short2 = sym[2:] if sym.startswith(('SH', 'SZ')) else sym
            r2 = conn.execute(
                "SELECT industry FROM stock_pool WHERE symbol = ? OR symbol = ? LIMIT 1",
                (sym, short2)
            ).fetchone()
            if r2 and r2[0] == cand_industry:
                same_industry_positions.append(sym)

        conn.close()

        same_count = len(same_industry_positions)
        if same_count >= 3:
            msg = f"{cand_industry}已有{same_count}只持仓({', '.join(same_industry_positions[:3])})，加入{candidate_symbol}后同行业敞口过高"
            if sector_warnings is not None:
                sector_warnings.append({'symbol': candidate_symbol, 'industry': cand_industry,
                                         'level': 'red', 'reason': msg})
            return {'warning': True, 'level': 'red', 'reason': msg}
        elif same_count >= 2:
            msg = f"{cand_industry}已有{same_count}只持仓({', '.join(same_industry_positions)})，加入{candidate_symbol}需注意集中度"
            if sector_warnings is not None:
                sector_warnings.append({'symbol': candidate_symbol, 'industry': cand_industry,
                                         'level': 'yellow', 'reason': msg})
            return {'warning': True, 'level': 'yellow', 'reason': msg}

        return {'warning': False, 'level': '', 'reason': ''}

    except Exception as e:
        print(f"[相关性检查] ⚠️ 失败: {e}", file=sys.stderr)
        return {'warning': False, 'level': '', 'reason': ''}


# ============= 🤖 节点F：持仓三合一（风控 + 止损 + 止盈） =============

def assess_positions(positions: list, catalyst_db: dict | None = None,
                     atr_map: dict | None = None) -> dict:
    """
    一次 DeepSeek API 调用同时输出三个维度的持仓建议。

    输入: 持仓列表(PnL/成本/现价/ATR) + 催化数据
    输出: {symbol: {risk_action, new_stop, stop_reason, take_profit_stage, batch_plan}}

    Args:
        positions: [{symbol, name, avg_price, current_price, profit_ratio, today_pct}, ...]
        catalyst_db: news_catalysts.json 的快照
        atr_map: {symbol: atr_pct}  个股波动率（ATR/现价），用于精准止损校准
    """
    result = {}
    if not positions:
        return result

    # 过滤：只对盈亏≥3% 或 催化数据存在的持仓做分析（降低阈值，更早预警）
    interesting = []
    for p in positions:
        pnl = abs(p.get('profit_ratio', 0)) * 100
        sym = p.get('symbol', '')
        cat = catalyst_db.get(sym, {}) if catalyst_db else {}
        if pnl >= 3 or cat.get('catalyst_count', 0) > 0:
            entry = {
                'symbol': sym,
                'name': p.get('name', ''),
                'cost': p.get('avg_price', 0),
                'current': p.get('current_price', 0),
                'pnl_pct': round(p.get('profit_ratio', 0) * 100, 1),
                'today_pct': p.get('today_pct', 0),
                'catalyst': {
                    'keywords': cat.get('catalyst_keywords', [])[:2],
                    'count': cat.get('catalyst_count', 0),
                    'days_without': cat.get('days_without_catalyst', 99),
                },
            }
            # 个股 ATR（波动率）— 替代大盘波动率，精准校准止损
            if atr_map and sym in atr_map:
                entry['atr_pct'] = round(atr_map[sym], 2)
            interesting.append(entry)

    if not interesting:
        return result

    print(f"[持仓评估] 🤖 分析 {len(interesting)} 只持仓...", file=sys.stderr)

    try:
        system_prompt = '''你是 A 股持仓风控专家。对每只持仓输出三个维度：

1. risk_action: "持有" / "减半" / "清仓"
   判断依据：催化是否被证伪？突发利空？技术面破位？止损线触发？

2. new_stop: 建议的新止损价（元，2位小数）
   - atr_pct 是该股正常波动范围（如 1.5% 指该股正常日波动 1.5%）
   - 止损距离建议 = 1.5~2.5 倍 atr_pct，高波动股放宽，低波动股收紧
   - 盈利>5%：止损上移到成本价以上；亏损中：按 atr 设硬止损
   new_stop_reason: 调整理由（15字内）

3. take_profit:
   stage: "催化前段" / "催化中段" / "催化末段" / "无催化"
   suggestion: "分批止盈" / "现价减半" / "持有观望" / "无需操作"
   reason: 建议理由（15字内）

输出严格 JSON 数组：[{"symbol":"SH600570","risk_action":"持有","new_stop":28.50,"new_stop_reason":"...","take_profit":{"stage":"催化末段","suggestion":"分批止盈","reason":"..."}}]'''

        user_prompt = json.dumps({
            'positions': interesting,
        }, ensure_ascii=False)

        ai_result = _call_deepseek_api(system_prompt, user_prompt)

        # 构建 ATR 查找表（供系统规则兜底用）
        atr_lookup = {entry['symbol']: entry.get('atr_pct', 0) for entry in interesting}

        if ai_result and isinstance(ai_result, list):
            for item in ai_result:
                sym = item.get('symbol', '')
                ai_stop = item.get('new_stop', 0)
                ai_stop_reason = item.get('new_stop_reason', '')
                current_price = next((e['current'] for e in interesting if e['symbol'] == sym), 0)

                # 🔒 系统规则兜底：ATR 止损不低于 current * (1 - 2.5 * atr_pct)
                sys_stop = 0
                sys_override = False
                atr_pct = atr_lookup.get(sym, 0)
                if atr_pct > 0 and current_price > 0:
                    sys_stop = round(current_price * (1 - 2.5 * atr_pct / 100), 2)
                    # AI 止损有效但比系统规则更松（更低）→ 系统接管
                    if 0 < ai_stop < sys_stop:
                        ai_stop = sys_stop
                        ai_stop_reason = f'系统收紧: ATR={atr_pct}% (原{ai_stop_reason})'
                        sys_override = True
                    # AI 未给出止损 → 系统兜底
                    elif ai_stop <= 0 and sys_stop > 0:
                        ai_stop = sys_stop
                        ai_stop_reason = f'系统兜底: ATR={atr_pct}%'
                        sys_override = True

                result[sym] = {
                    'risk_action': item.get('risk_action', '持有'),
                    'new_stop': ai_stop,
                    'new_stop_reason': ai_stop_reason,
                    'take_profit': item.get('take_profit', {}),
                    'sys_stop': sys_stop,           # 系统规则计算的止损（调试用）
                    'sys_override': sys_override,   # 是否被系统规则接管
                    'atr_pct': atr_pct,
                }
                override_tag = ' [系统接管]' if sys_override else ''
                print(f"  {sym}: {result[sym]['risk_action']} | "
                      f"止损={result[sym]['new_stop']} ("
                      f"{result[sym]['new_stop_reason']}){override_tag} | "
                      f"{result[sym]['take_profit'].get('suggestion', '')}",
                      file=sys.stderr)
        else:
            print(f"[持仓评估] ⚠️ API 返回异常", file=sys.stderr)
    except Exception as e:
        print(f"[持仓评估] ⚠️ 调用失败: {e}", file=sys.stderr)

    return result


# ============= 🤖 节点B：资金流语义分析 =============

def classify_fund_flow(fund_flow: dict) -> str:
    """
    AI 判断资金流向的真实性质，区分「真买」和「对倒诱多」。

    输入: fund_flow 原始数据（主力净额、板块排行）
    输出: "主力建仓" / "对倒出货" / "量化噪音" / "护盘维稳"

    默认为 "量化噪音"（不调整仓位）
    """
    if not fund_flow:
        return "量化噪音"

    fscore = fund_flow.get('fund_score', 50)
    # 快速规则判断：极端值不调 AI
    if fscore >= 75:
        return "主力建仓"
    if fscore <= 25:
        return "对倒出货"
    if 45 <= fscore <= 55:
        return "量化噪音"

    try:
        top_inflow = fund_flow.get('top_inflow', [])
        main_net = fund_flow.get('market', {}).get('main_net_fmt', 'N/A')

        system_prompt = '''你是 A 股资金流分析专家。根据有限数据判断资金性质。

重要限制：你只有汇总数据（没有大单/小单拆分明细），请保守判断：
- 板块高度集中 + 净额方向明确 → "主力建仓"
- 板块分散 + 净额小 → "量化噪音"
- 权重板块独涨 + 其余流出 → "护盘维稳"
- 无法确定 → "量化噪音"

输出严格 JSON：{"nature": "主力建仓", "reason": "15字内理由"}'''

        user_prompt = json.dumps({
            '主力净额': main_net,
            '资金评分': fscore,
            '资金信号': fund_flow.get('fund_signal', '中性'),
            '流入板块': [x.get('industry', '') for x in top_inflow[:5]],
            '涨停家数': fund_flow.get('limit_up', {}).get('zt_count', 0),
            '提示': '无大单/小单拆分明细，请基于板块集中度+净额方向保守判断',
        }, ensure_ascii=False)

        print(f"[资金流语义] 🤖 AI 判断中 (score={fscore})...", file=sys.stderr)
        result = _call_deepseek_api(system_prompt, user_prompt)
        if result and result.get('nature') in ('主力建仓', '对倒出货', '量化噪音', '护盘维稳'):
            print(f"[资金流语义] → {result['nature']} ({result.get('reason', '')})", file=sys.stderr)
            return result['nature']
    except Exception as e:
        print(f"[资金流语义] ⚠️ AI 失败: {e}", file=sys.stderr)

    return "量化噪音"


# ============= 🤖 节点A：缺口原因解读 =============

def interpret_gap(gap_risk: dict, news_sentiment: dict) -> str:
    """
    用 DeepSeek AI 判断隔夜缺口性质，替代一刀切降仓。

    根据三大指数缺口数据 + 当日新闻催化剂/风险，
    输出: "系统性" / "事件性" / "技术性"

    Args:
        gap_risk: detect_gap_risk() 的返回值
        news_sentiment: 新闻情绪 dict

    Returns:
        str: 缺口性质，默认 "技术性"（最宽松）
    """
    if not gap_risk or not gap_risk.get('has_gap_risk'):
        return "技术性"

    try:
        gap_detail = gap_risk.get('gap_detail', {})
        catalysts = news_sentiment.get('catalysts', [])
        risks = news_sentiment.get('risks', [])

        gap_info = {name: f"{info['gap']:+.2f}%" for name, info in gap_detail.items()}

        system_prompt = """你是 A 股开盘缺口分析专家。根据三大指数隔夜缺口和当日新闻，判断缺口性质（仅输出一个词）：

- "系统性"：全球宏观风险引发，如美股暴跌/美联储加息/地缘冲突升级/重大政策利空
  → 所有板块受影响，应大幅降仓
- "事件性"：单只权重股或单个行业暴雷拖累指数，如茅台利空/某行业黑天鹅
  → 不影响全局，但短期承压
- "技术性"：正常获利回吐/冲高回落/节前避险，无实质利空
  → 常规波动，无需过激反应

输出严格 JSON：{"nature": "系统性", "reason": "一句话理由（20字以内）"}"""

        user_prompt = json.dumps({
            '指数缺口': gap_info,
            '正面催化': catalysts[:5],
            '风险事件': risks[:5],
        }, ensure_ascii=False)

        print(f"[缺口解读] 🤖 AI 判断中...", file=sys.stderr)
        result = _call_deepseek_api(system_prompt, user_prompt)
        if result and result.get('nature') in ('系统性', '事件性', '技术性'):
            print(f"[缺口解读] → {result['nature']} ({result.get('reason', '')})", file=sys.stderr)
            return result['nature']
    except Exception as e:
        print(f"[缺口解读] ⚠️ AI 调用失败: {e}，回退到技术性", file=sys.stderr)

    return "技术性"


def adjust_strategy(pre_market: dict, validation: dict, feedback_list: list,
                   chain: StrategyChain = None, daily_strategy: dict = None,
                   gap_risk: dict = None, fund_flow: dict = None,
                   gap_nature: str = "技术性", flow_nature: str = "量化噪音") -> dict:
    """
    微调策略（右侧交易版本）。

    决策优先级：价格行为确认 → 量价配合 → 资金流 → 情绪加分。
    仓位硬上限 60%（Marcus 铁律），立场简化为 3 档。

    Args:
        pre_market: 盘前策略
        validation: 验证结果（含 price_action_confirm / sentiment_bonus / fund_flow_confirm）
        feedback_list: 交易反馈
        chain: 策略链管理器（可选）
        daily_strategy: 昨日策略迭代结果
        gap_risk: 缺口风险
        fund_flow: 资金流向

    Returns:
        dict: 调整后的策略
    """
    MARCUS_POSITION_CAP = 60  # Marcus 单日仓位铁律

    initial = pre_market.get('initial_strategy', {}).copy()

    # Step 1: 初始仓位 —— 优先使用昨日迭代结果（如昨日亏损严重，强制降低）
    if daily_strategy and daily_strategy.get('position_limit', MARCUS_POSITION_CAP) < MARCUS_POSITION_CAP:
        position_limit = daily_strategy['position_limit']
        print(f"[策略调整] ⚡ 使用昨日迭代仓位: {position_limit}%（{daily_strategy.get('reason', '')}）")
    else:
        position_limit = initial.get('position_limit', 60)
        # 盘前策略如果设了 >60%，硬封顶
        position_limit = min(position_limit, MARCUS_POSITION_CAP)

    # Step 2: 根据验证结果调整（价格行为驱动的调整）
    if validation.get('adjustment_needed'):
        reason = validation.get('adjustment_reason', '')
        if '降低仓位' in reason or '谨慎' in reason:
            position_limit = min(position_limit, 30)
            print(f"[策略调整] 降低仓位 → limit={position_limit}% ({reason})")

    # Step 3: 情绪辅助加分（仅在价格行为已确认时生效，最多 +5%）
    sentiment_bonus = validation.get('sentiment_bonus', 0)
    if sentiment_bonus > 0 and validation.get('price_action_confirm'):
        position_limit = min(position_limit + sentiment_bonus, MARCUS_POSITION_CAP)
        print(f"[情绪辅助] +{sentiment_bonus}% → limit={position_limit}%")

    # Step 4: 交易反馈调整（仅计当日持仓亏损）
    if feedback_list:
        from datetime import date
        today = date.today().isoformat()
        losing_trades = [f for f in feedback_list
                         if f.get('current_pnl', 0) < -5
                         and f.get('timestamp', '').startswith(today)]
        losing_trades_all = [f for f in feedback_list
                             if f.get('current_pnl', 0) < 0
                             and f.get('timestamp', '').startswith(today)]
        if len(losing_trades) > 2:
            position_limit = min(position_limit, 20)
            print(f"[持仓反馈] 今日{len(losing_trades)}只显著亏损 → limit={position_limit}%")
        elif len(losing_trades_all) > 2:
            position_limit = min(position_limit, 30)
            print(f"[持仓反馈] 今日{len(losing_trades_all)}只亏损 → limit={position_limit}%")

    # Step 5: 策略链反馈（仅当日有效）
    if chain:
        from datetime import date
        today = date.today().isoformat()
        feedback_loop = chain.state.get('feedback_loop', [])
        applied_types = set()
        for fb in feedback_loop[-10:]:
            fb_time = fb.get('timestamp', '')
            if not fb_time or not fb_time.startswith(today):
                continue
            fb_type = fb.get('type', '')
            if fb_type in applied_types:
                continue
            if fb_type == 'position_over_limit':
                if fb.get('current_ratio', 0) > 0.6:
                    position_limit = min(position_limit, 50)
                    applied_types.add(fb_type)
                    print(f"[策略链反馈] 当日仓位超标 {fb.get('current_ratio')*100:.1f}% → limit={position_limit}%")
            elif fb_type == 'trade_failure':
                position_limit = min(position_limit, 40)
                applied_types.add(fb_type)
                print(f"[策略链反馈] 当日交易失败 → limit={position_limit}%")
        if not applied_types:
            print("[策略链反馈] 无当日反馈")

    # Step 6: 缺口风险影响（按 AI 判断的缺口性质分层降仓）
    if gap_risk and gap_risk.get('adjustment_needed'):
        worst_gap = gap_risk.get('worst_gap', 0)
        max_by_nature = {
            '系统性': 20,   # 全球宏观风险 → 大幅降仓
            '事件性': 40,   # 权重股暴雷拖累 → 中等降仓
            '技术性': 50,   # 获利回吐 / 冲高回落 → 轻微降仓
        }
        cap = max_by_nature.get(gap_nature, 40)
        position_limit = min(position_limit, cap)
        print(f"[缺口风控] ⚠️ 性质={gap_nature} 最弱缺口{worst_gap:+.2f}% → limit={position_limit}%")

    # Step 7: 初始化板块配置（从盘前策略获取，后续资金流会补充）
    watchlist = initial.get('watchlist', [])
    sector_analysis = initial.get('sector_analysis', [])
    sector_allocation = {}
    for sector in sector_analysis:
        sector_allocation[sector.get('sector', '')] = {
            'stance': sector.get('stance', ''),
            'weight': sector.get('weight', 0.5),
            'position_limit': sector.get('position_limit', 10),
            'news_score': sector.get('news_score', 50),
        }

    # Step 8: 资金流向影响（AI 语义分类驱动，不再只看净额）
    if fund_flow:
        fscore = fund_flow.get('fund_score', 50)
        fsignal = fund_flow.get('fund_signal', '中性')
        top_inflow = fund_flow.get('top_inflow', [])
        print(f"[资金流] nature={flow_nature} score={fscore} signal={fsignal}")

        flow_adjustments = {
            '主力建仓': 8,    # 确认度最高，多加
            '对倒出货': -20,  # 危险信号，多减
            '护盘维稳': 0,    # 不是进攻信号，不调整
            '量化噪音': 0,    # 无方向性，忽略
        }
        delta = flow_adjustments.get(flow_nature, 0)
        if delta > 0:
            position_limit = min(position_limit + delta, MARCUS_POSITION_CAP)
            print(f"[资金流] ✅ {flow_nature}: +{delta}% → {position_limit}%")
        elif delta < 0:
            position_limit = max(position_limit + delta, 10)
            print(f"[资金流] ⚠️ {flow_nature}: {delta}% → {position_limit}%")
        else:
            print(f"[资金流] ⏭️ {flow_nature}: 不调整仓位")

        # 用资金流入行业补充 sector_allocation
        if top_inflow:
            for item in top_inflow[:3]:
                ind = item.get('industry', '')
                if ind and ind not in sector_allocation:
                    sector_allocation[ind] = {
                        'stance': '🟢 超配',
                        'weight': 0.9,
                        'position_limit': 20,
                        'fund_net': item.get('net_fmt', 'N/A')
                    }

    # Step 9: 硬封顶 Marcus 60% 铁律
    position_limit = min(position_limit, MARCUS_POSITION_CAP)

    # Step 9.5: 持续降级检测（跨轮次空头追踪）
    # 当多个指数持续下跌超过多轮扫描，自动触发 stance 降级
    downgrade = check_persistent_downgrade(validation, chain)

    # Step 10: 确定市场立场（信号驱动，不绑定仓位）
    # 价格确认 → green；未确认但情绪支持 → yellow；否则 → hold
    price_confirmed = validation.get('price_action_confirm', False)
    if price_confirmed:
        adjusted_stance = '🟢 aggressive_buy'
        stance_code = 'green'
    elif position_limit >= 40:
        adjusted_stance = '🟡 cautious_buy'
        stance_code = 'yellow'
    else:
        adjusted_stance = '⚪ hold'
        stance_code = 'yellow'

    # Step 10.5: 应用持续降级（优先级高于 Step 10 常规判定）
    if downgrade.get('downgrade_triggered'):
        adjusted_stance = downgrade.get('downgrade_to', adjusted_stance)
        stance_code = downgrade.get('to_code', stance_code)
        # 降级为 red 时强制仓位上限 ≤ 20%（防御模式）
        if stance_code == 'red':
            position_limit = min(position_limit, 20)
            print(f"[持续降级] 🛑 强制仓位上限 → {position_limit}%（防御模式）")
    

    return {
        'stance': adjusted_stance,
        'stance_code': stance_code,
        'position_limit': position_limit,
        'stop_loss': '-8%',
        'take_profit': '+20%',
        'priority': [],
        'adjustment_reason': validation.get('adjustment_reason', '无调整'),
        'gap_risk': gap_risk,
        'watchlist': watchlist,
        'sector_analysis': sector_analysis,
        'sector_allocation': sector_allocation,
        'downgrade': downgrade,  # 持续降级结果，供报告和 scan_result 引用
    }


# ============= 新闻情绪分析 =============

def get_news_sentiment(news_list: List[dict]) -> dict:
    """
    分析新闻情绪 - 使用 DeepSeek AI 语义理解

    Marcus 策略:
    1. **AI 过滤**:去重 + 排除无用新闻（政治八卦、信息量低）
    2. AI 理解上下文（"增长放缓"=负面，不是正面）
    3. AI 识别反讽（"业绩大增，股价暴跌"=负面）
    4. AI 区分影响程度（"暴涨"权重远高于"上涨"）
    5. AI 提取真正重要的催化剂和风险
    6. AI 识别热点概念并单独评分

    流程:
    原始 30 条 → AI 过滤 → 有效 15-20 条 → AI 分析 → 情绪分数
    """
    if not news_list:
        return {
            'score': 50,
            'positive': 0,
            'negative': 0,
            'neutral': 0,
            'total': 0,
            'hot_concepts': [],
            'concept_scores': {},
            'catalysts': [],
            'risks': [],
            'filtered_out': 0,
            'effective_total': 0
        }

    # 步骤 1:AI 过滤（去重 + 排除无用）
    filtered_news = filter_news_with_deepseek(news_list)
    filtered_count = len(news_list) - len(filtered_news)

    # 步骤 2:AI 分析过滤后的新闻
    ai_result = analyze_news_with_deepseek(filtered_news)

    # 添加过滤统计
    ai_result['filtered_out'] = filtered_count
    ai_result['effective_total'] = len(filtered_news)
    ai_result['total_received'] = len(news_list)

    return ai_result


def get_market_status() -> dict:
    """
    获取市场状态 - 从缓存读取热点分析 + 雪球实时指数 + 新闻情绪

    指数代码:
    - 上证指数:SH000001
    - 深证成指:SZ399001
    - 科创 50:SH000688
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 判断市场状态
    now = datetime.now()
    if now.hour < 9 or now.hour > 15 or (now.hour == 12 and now.minute < 30):
        market_status = '未开盘'
    elif now.hour >= 15:
        market_status = '已收盘'
    else:
        market_status = '交易中'

    # ========== 1. 获取实时指数数据（雪球） ==========
    indices = {}
    index_map = {
        '上证指数': 'SH000001',
        '深证成指': 'SZ399001',
        '沪深300': 'SH000300',
        '创业板指': 'SZ399006',
        '科创 50': 'SH000688'
    }

    try:
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        
        # 强制清空缓存，确保获取最新数据
        engine.clear_cache()
        
        for name, code in index_map.items():
            try:
                quote = engine.get_stock_quote(code, use_cache=False)
                if quote:
                    # 处理 percent/chg 可能为 None 的情况
                    def _safe_float(val, default=0.0):
                        if val is None:
                            return default
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            return default
                    last_close = _safe_float(quote.get('last_close'))
                    open_price = _safe_float(quote.get('open'))
                    # Gap = (今开 - 昨收) / 昨收
                    gap_pct = round((open_price - last_close) / last_close * 100, 3) if last_close and last_close > 0 else 0.0
                    indices[name] = {
                        'close': _safe_float(quote.get('current')),
                        'change': _safe_float(quote.get('percent')),
                        'change_amt': _safe_float(quote.get('chg')),
                        'volume': _safe_float(quote.get('volume')),
                        'high': _safe_float(quote.get('high')),
                        'low': _safe_float(quote.get('low')),
                        'open': open_price,
                        'last_close': last_close,
                        'gap': gap_pct,  # 缺口率 (今开-昨收)/昨收
                    }
                else:
                    indices[name] = {'close': 0, 'change': 0}
            except Exception as e:
                print(f"[警告] 获取 {name} 数据失败:{e}")
                indices[name] = {'close': 0, 'change': 0}
    except Exception as e:
        print(f"[错误] 初始化雪球引擎失败:{e}")
        for name in index_map.keys():
            indices[name] = {'close': 0, 'change': 0}

    # ========== 2. 获取多源新闻并分析情绪（热点概念不使用缓存，由东财实时资金流驱动） ==========
    news_sentiment = {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0, 'total': 0}
    news_impact = {'impact_analysis': [], 'summary': {'s_level_count': 0, 'a_level_count': 0, 'b_level_count': 0, 'c_level_count': 0, 'top_sectors': []}}
    hot_concepts = []  # 热点概念 → 由东财实时资金流填充（下方 concept_flow 段）
    catalysts = []  # 重大催化剂
    risks = []  # 重大风险
    sentiment_score = 50
    cached_concept_scores = {}

    try:
        # 读取缓存仅用于情绪分和新闻影响摘要（热点概念由实时资金流驱动，不用缓存）
        cache = get_hot_sectors_from_cache()

        if cache.get('available'):
            print(f"[新闻缓存] 📊 情绪分参考（生成于 {cache.get('generated_at', '')[:19]}），热点概念由东财实时资金流驱动", file=sys.stderr)
            sentiment_score = cache.get('sentiment_score', 50)
            impact_summary = cache.get('summary', {})
            news_impact = {'summary': impact_summary, 'impact_analysis': cache.get('impact_analysis', [])}
            cached_concept_scores = cache.get('concept_scores', {})
            sentiment_positive = impact_summary.get('s_level_count', 0) + impact_summary.get('a_level_count', 0)
            sentiment_negative = 0
        else:
            print(f"[新闻分析] 无缓存，调用 get_news_analysis()（仅情绪）...", file=sys.stderr)
            analysis_result = get_news_analysis(news_limit=30, use_ai=True)
            news_sentiment = analysis_result.get('sentiment', {})
            news_impact = {
                'impact_analysis': analysis_result.get('impact_analysis', []),
                'summary': analysis_result.get('summary', {})
            }
            catalysts = news_sentiment.get('catalysts', [])
            risks = news_sentiment.get('risks', [])
            sentiment_score = news_sentiment.get('score', 50)
            sentiment_positive = news_sentiment.get('positive', 0)
            sentiment_negative = news_sentiment.get('negative', 0)

        print(f"[新闻分析] 情绪={sentiment_score:.1f}, 正面={sentiment_positive}, 负面={sentiment_negative}")
        impact_summary = news_impact.get('summary', {})
        print(f"[新闻分析] S 级={impact_summary.get('s_level_count', 0)}, "
              f"A 级={impact_summary.get('a_level_count', 0)}, "
              f"B 级={impact_summary.get('b_level_count', 0)}, "
              f"C 级={impact_summary.get('c_level_count', 0)}")

        if cached_concept_scores:
            print(f"[AI概念评分] {cached_concept_scores}")
    except Exception as e:
        print(f"[警告] 获取新闻情绪失败:{e}")
        try:
            ak_engine = AKShareEngine(data_dir=str(DATA_DIR))
            finance_news = ak_engine.get_finance_news(limit=30)
            if finance_news:
                news_sentiment = get_news_sentiment(finance_news)
        except:
            pass
        news_impact = {'summary': {}, 'impact_analysis': []}
        if 'news_sentiment' not in dir():
            news_sentiment = {'score': 50, 'positive': 0, 'negative': 0}
        if news_sentiment.get('score', 50) == 50:
            try:
                import json
                state_file = WORKSPACE / 'data' / 'strategy_state.json'
                if state_file.exists():
                    with open(state_file, encoding='utf-8') as f:
                        state = json.load(f)
                    scans = state.get('intraday_scans', [])
                    if scans:
                        fb = scans[-1].get('sentiment_score')
                        if fb and fb != 50:
                            news_sentiment['score'] = fb
                            print(f"[新闻情绪兜底] strategy_state → score={fb}", file=sys.stderr)
                    if news_sentiment.get('score', 50) == 50:
                        pm = state.get('pre_market', {}).get('sentiment', {})
                        if pm.get('score', 0) not in (0, 50):
                            news_sentiment['score'] = pm['score']
                            print(f"[新闻情绪兜底] pre_market → score={pm['score']}", file=sys.stderr)
            except Exception as e2:
                print(f"[新闻情绪兜底] 失败: {e2}", file=sys.stderr)

    # ========== 3. 计算平均涨跌幅 ==========
    changes = [i['change'] for i in indices.values() if i['change'] != 0]
    avg_change = sum(changes) / len(changes) if changes else 0.0

    # ========== 5. 获取持仓股票新闻分析 ==========
    holdings_news = {}
    try:
        # 从 VN.PY 获取持仓
        vnpy_dir = WORKSPACE / "skills" / "vnpy-paper-trading"
        account_file = vnpy_dir / "data" / "account.json"
        if account_file.exists():
            import json
            with open(account_file) as f:
                account_data = json.load(f)

            stock_codes = list(account_data.get('positions', {}).keys())

            if stock_codes:
                print(f"[持仓新闻] 分析 {len(stock_codes)} 只持仓股...", file=sys.stderr)
                holdings_news = get_stock_news(stock_codes, limit=3)

                # 为每只股票计算情绪
                for code, news_list in holdings_news.items():
                    if news_list:
                        sentiment = get_news_sentiment_simple(news_list)
                        holdings_news[code] = {
                            'news': news_list,
                            'sentiment': sentiment,
                            'score': sentiment['score']
                        }

                print(f"[持仓新闻] ✓ 分析完成", file=sys.stderr)
    except Exception as e:
        print(f"[持仓新闻] ⚠️ 分析失败:{e}", file=sys.stderr)

    return {
        'timestamp': timestamp,
        'market_status': market_status,
        'indices': indices,
        'avg_change': avg_change,
        'news_sentiment': news_sentiment,  # 新闻情绪分数
        'news_impact': news_impact,  # 新闻影响力分析（新增）
        'holdings_news': holdings_news  # 持仓股新闻分析（新增）
    }



def generate_scan_report():
    """生成简化版扫描报告"""
    from datetime import datetime

    # 初始化所有可能在条件分支赋值的变量（避免 UnboundLocalError）
    news_impact = {'summary': {}, 'impact_analysis': []}
    news_sentiment = {'score': 50, 'positive': 0, 'negative': 0}
    hot_concepts = []
    catalysts, risks = [], []
    sentiment_positive, sentiment_negative = 0, 0
    concept_scores = {}

    # 初始化策略链
    chain = StrategyChain()

    # 获取市场数据
    executor = MarcusVNPyExecutor()
    account = executor.get_account()
    positions = executor.get_positions()

    # ====== 资金流向数据（东财实时 / Tushare日频 自适应） ======
    fund_flow = None
    now = datetime.now()
    is_intraday = now.weekday() < 5 and (
        (now.hour == 9 and now.minute >= 30) or
        (now.hour in (10, 11)) or
        (now.hour in (13, 14)) or
        (now.hour == 15 and now.minute == 0)
    )

    # ── 盘中：东财实时大盘资金流 + 个股资金流（个股仍用Tushare前日数据）──
    if is_intraday:
        print(f"[资金流] 盘中({now.hour}:{now.minute:02d})，使用东财实时大盘+板块资金流")
        try:
            # 大盘实时资金流（东财 ulist.np）
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).parent.parent / "core"))
            from utils.em_sector_flow import get_market_moneyflow_realtime
            rt_market = get_market_moneyflow_realtime()
            if rt_market:
                combined = rt_market['combined']
                fund_flow = {
                    'market': {
                        'main_net_fmt': combined['main_net_fmt'],
                        'source_date': now.strftime('%Y%m%d'),
                        'source_stock_count': 0,
                    },
                    'market_wide': {
                        'net_amount_fmt': combined['main_net_fmt'],
                        'net_amount_yi': round(combined['main_net'] / 10000, 2),
                        'flow_nature': rt_market['flow_nature'],
                        'trade_date': now.strftime('%Y%m%d'),
                        'total_amount_fmt': combined['total_amount_fmt'],
                        'source': 'em_push2_realtime',
                    },
                    'north': {'total_net': 0, 'sh_net': 0, 'sz_net': 0},
                    'limit_up': {'zt_count': 0, 'market_heat': 50},
                    'fund_score': 50.0,
                    'fund_signal': '中性',
                    'top_inflow': [],
                }
                print(f"[资金流] ✅ 大盘实时: {combined['main_net_fmt']} | "
                      f"沪:{rt_market['sh']['main_net_fmt']} 深:{rt_market['sz']['main_net_fmt']} | "
                      f"总成交:{combined['total_amount_fmt']} | {rt_market['flow_nature']}")
        except Exception as e:
            print(f"[资金流] ⚠️ 东财实时大盘获取失败: {e}")

        # 个股资金流（盘中用 Tushare 昨日数据，仍有参考价值）
        try:
            from fund_flow import get_fund_flow_summary
            position_symbols = [p['symbol'] for p in positions] if positions else []
            pre_watchlist = (chain.state.get('pre_market') or {}).get('initial_strategy', {}).get('watchlist', [])
            pre_symbols = [w['symbol'] if isinstance(w, dict) else w for w in pre_watchlist]
            target_symbols = list(dict.fromkeys(position_symbols + pre_symbols))
            if target_symbols:
                print(f"[资金流] 获取个股资金流 ({len(target_symbols)}只，Tushare昨日数据)...")
                stock_flow = get_fund_flow_summary(symbols=target_symbols)
                if stock_flow:
                    if fund_flow:
                        fund_flow['limit_up'] = stock_flow.get('limit_up', fund_flow['limit_up'])
                        fund_flow['fund_score'] = stock_flow.get('fund_score', 50)
                        fund_flow['fund_signal'] = stock_flow.get('fund_signal', '中性')
                        fund_flow['top_inflow'] = stock_flow.get('top_inflow', [])
                        fund_flow['market'] = stock_flow.get('market', fund_flow['market'])
        except Exception as e:
            print(f"[资金流] ⚠️ 个股资金流获取失败: {e}")

    # ── 盘后：Tushare 日频（已有当日数据）──
    else:
        try:
            from fund_flow import get_fund_flow_summary
            position_symbols = [p['symbol'] for p in positions] if positions else []
            pre_watchlist = (chain.state.get('pre_market') or {}).get('initial_strategy', {}).get('watchlist', [])
            pre_symbols = [w['symbol'] if isinstance(w, dict) else w for w in pre_watchlist]
            target_symbols = list(dict.fromkeys(position_symbols + pre_symbols))
            print(f"[资金流] 正在获取资金流向数据 ({len(target_symbols)}只，同时拉取全市场大盘)...")
            fund_flow = get_fund_flow_summary(symbols=target_symbols)
            if fund_flow:
                mflow = fund_flow.get('market', {})
                source_note = f"({mflow.get('source_date', '')})" if mflow.get('source_date') else ""
                mw = fund_flow.get('market_wide')
                mw_str = ""
                if mw:
                    src_label = "日频(Tushare)"
                    mw_str = f" | 全市场({src_label}): {mw.get('net_amount_fmt', 'N/A')}({mw.get('flow_nature', '')}, date={mw.get('trade_date', '')})"
                print(f"[资金流] ✅ 自选股{source_note}主力净额: {mflow.get('main_net_fmt', 'N/A')} (共{mflow.get('source_stock_count', 0)}只) | "
                      f"涨停: {fund_flow.get('limit_up', {}).get('zt_count', 0)}家 | "
                      f"资金信号: {fund_flow.get('fund_signal', 'N/A')} (score={fund_flow.get('fund_score', 50)}){mw_str}")
        except Exception as e:
            print(f"[资金流] ⚠️ 获取失败: {e}")
            fund_flow = None

    # 🤖 节点B：资金流语义分析
    flow_nature = "量化噪音"
    if fund_flow:
        flow_nature = classify_fund_flow(fund_flow)

    # ====== 概念板块实时行情 Top 50（东财push2实时 + Tushare降级，含资金流） ======
    concept_flow_concepts = []
    concept_flow_details = []  # 完整资金流明细（主力净流入排序），供报告使用
    concept_fund_inflow_concepts = []  # 主力净流入 Top 概念名列表
    try:
        # ── 优先：东财 push2 实时接口，主力净流入排序 ──
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).parent.parent / "core"))
        from utils.em_sector_flow import get_top_inflow_sectors, get_top_change_sectors, classify_flow_nature

        # 主力净流入榜（资金驱动，主排序）
        em_inflow = get_top_inflow_sectors("concept", top_n=50, use_cache=True)
        concept_flow_details = em_inflow
        concept_flow_concepts = [s['name'] for s in em_inflow]
        concept_fund_inflow_concepts = concept_flow_concepts
        if em_inflow:
            top_names = [(s['name'], s['main_net_fmt'], f"{s['pct_change']:+.1f}%")
                        for s in em_inflow[:5]]
            print(f"[概念资金] ✅ 东财实时主力净流入 Top 50: {top_names}", file=sys.stderr)

        # 涨幅榜补充（价格驱动，合并涨幅领先但资金未进前50的概念名）
        em_change = get_top_change_sectors("concept", top_n=30, use_cache=True)
        top_pct = em_change[0]['pct_change'] if em_change else 0
        for s in em_change:
            if s['name'] not in concept_flow_concepts:
                concept_flow_concepts.append(s['name'])
                concept_flow_details.append(s)

        if concept_flow_concepts:
            print(f"[概念行情] ✅ 东财实时 Top {len(concept_flow_concepts)} 领涨概念(含资金驱动): "
                  f"{', '.join(concept_flow_concepts[:5])}... (最强 +{top_pct:.1f}%)", file=sys.stderr)
            # 主推资金流入 Top 5 概念（覆盖 DeepSeek 缓存的旧数据）
            if concept_flow_details:
                inflow_preview = [(s['name'], s.get('main_net_fmt', 'N/A'))
                                  for s in concept_flow_details[:5]]
                print(f"[热点概念·实时资金] {inflow_preview}", file=sys.stderr)
                # 实时资金流概念名合并到 hot_concepts（DeepSeek 缓存可能过期，用实时数据补强）
                for s in concept_flow_details[:8]:
                    if s['name'] not in hot_concepts:
                        hot_concepts.append(s['name'])
            from news_analyzer import supplement_concept_vocabulary
            supplement_concept_vocabulary(concept_flow_concepts)
    except Exception as e:
        print(f"[概念行情] ⚠️ 东财实时获取失败，降级到Tushare: {e}", file=sys.stderr)
        # ── 降级：Tushare dc_daily ──
        try:
            import pandas as pd
            pro = get_tushare_pro()
            from datetime import datetime as dt, timedelta
            for offset in range(3):
                attempt_date = (dt.now() - timedelta(days=offset)).strftime("%Y%m%d")
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
                        df = df.sort_values('pct_change', ascending=False).head(50)
                        concept_flow_concepts = df['name'].tolist()
                        # 构造简化明细
                        for _, row in df.iterrows():
                            concept_flow_details.append({
                                'name': row['name'],
                                'pct_change': round(float(row.get('pct_change', 0) or 0), 2),
                                'main_net_fmt': 'N/A',
                            })
                        top_pct = df.iloc[0]['pct_change'] if len(df) > 0 else 0
                        break
                except Exception:
                    continue
            if concept_flow_concepts:
                print(f"[概念行情] ✅ Tushare Top {len(concept_flow_concepts)} 领涨概念: "
                      f"{', '.join(concept_flow_concepts[:5])}... (最强 +{top_pct:.1f}%)", file=sys.stderr)
                from news_analyzer import supplement_concept_vocabulary
                supplement_concept_vocabulary(concept_flow_concepts)
        except Exception as e2:
            print(f"[概念行情] ⚠️ Tushare也失败: {e2}", file=sys.stderr)

    def _get_stock_name(sym: str) -> str:
        """两层名称 lookup：stock_pool.db（主力）→ Xueqiu（兜底）→ symbol 本身"""
        # Layer 1: stock_pool.db（ms级，覆盖全A股 3459 只，含科创板+创业板+北交所）
        try:
            import sqlite3 as _sq3
            pool_db = WORKSPACE / "data" / "stock_pool.db"
            if pool_db.exists():
                _conn = _sq3.connect(str(pool_db))
                _row = _conn.execute(
                    "SELECT name FROM stock_pool WHERE symbol = ? OR symbol = ? LIMIT 1",
                    (sym, sym[2:])  # 兼容 SH600570 和 600570
                ).fetchone()
                _conn.close()
                if _row:
                    return _row[0]
        except Exception:
            pass
        # Layer 2: Xueqiu 兜底
        try:
            if 'xq' not in dir():
                xq = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
            _q = xq.get_stock_quote(sym, use_cache=True)
            if _q and _q.get('name'):
                return _q['name']
        except Exception:
            pass
        # Layer 3: 彻底查不到才用代码本身
        return sym


    # ====== 获取持仓实时价格（新鲜，不走缓存） ======
    position_analysis = []
    if positions:
        try:
            xq = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
            # 批量获取持仓行情（强制不走缓存）
            syms = [p['symbol'] for p in positions]
            quotes = {}
            for sym in syms:
                q = xq.get_stock_quote(sym, use_cache=False)
                if q:
                    quotes[sym] = q
            # 读昨日收盘（从 cache.db 读取 last_close）
            import sqlite3
            from pathlib import Path
            cache_db = WORKSPACE / "data" / "cache.db"
            last_close_map = {}
            if cache_db.exists():
                c2 = sqlite3.connect(str(cache_db))
                c2_cur = c2.cursor()
                c2_cur.execute("SELECT symbol, data FROM stock_quotes")
                for row in c2_cur.fetchall():
                    try:
                        d = json.loads(row[1])
                        last_close_map[row[0]] = d.get('last_close', 0)
                    except:
                        pass
                c2.close()
            # 构建 position_analysis（含今日涨跌幅）
            for pos in positions:
                sym = pos['symbol']
                avg_price = pos.get('avg_price', 0)
                volume = pos.get('volume', 0)
                current_price = 0
                today_pct = 0.0
                last_close = 0
                if sym in quotes:
                    q = quotes[sym]
                    current_price = q.get('current', 0)
                    today_pct = q.get('percent', 0)
                    last_close = q.get('last_close', 0)
                elif sym in last_close_map:
                    last_close = last_close_map[sym]
                profit_ratio = (current_price - avg_price) / avg_price if avg_price > 0 else 0
                # 名称 lookup：stock_pool.db → Xueqiu → symbol
                display_name = _get_stock_name(sym)
                position_analysis.append({
                    'symbol': sym,
                    'name': display_name,
                    'volume': volume,
                    'avg_price': avg_price,
                    'current_price': current_price,
                    'last_close': last_close,
                    'today_pct': today_pct,
                    'profit_ratio': profit_ratio,
                })
        except Exception as e:
            print(f"[警告] 获取持仓实时价格失败: {e}")

    # ====== 缩量上涨风险检测（Marcus 纪律） ======
    shrink_volume_warnings = {}
    if position_analysis:
        pos_symbols = [p['symbol'] for p in position_analysis]
        shrink_volume_warnings = detect_shrink_volume_rally(pos_symbols)
        # 将缩量警告合并到 position_analysis
        if shrink_volume_warnings:
            for p in position_analysis:
                sym = p['symbol']
                if sym in shrink_volume_warnings:
                    p['shrink_volume'] = shrink_volume_warnings[sym]

    # ====== 热点概念已由东财实时资金流驱动（上方 concept_flow 段），无需从缓存重新读取 ======
    # 仅更新情绪分（如有更新的缓存数据）
    try:
        cache = get_hot_sectors_from_cache()
        if cache.get('available') and cache.get('sentiment_score', 50) != 50:
            news_sentiment['score'] = cache['sentiment_score']
            print(f"[情绪分] 缓存更新: {cache['sentiment_score']}", file=sys.stderr)
    except Exception as e:
        print(f"[警告] 情绪分缓存读取失败: {e}", file=sys.stderr)

    # 获取市场状态
    market = get_market_status()

    # ====== 读取策略链的完整策略 ======
    # 1. 读取盘前策略
    pre_market = chain.state.get('pre_market') or {}
    print(f"[策略链] 盘前策略: {pre_market.get('initial_strategy', {}).get('stance', 'N/A')}")
    print(f"[策略链] 盘前仓位限制: {pre_market.get('initial_strategy', {}).get('position_limit', 'N/A')}%")
    print(f"[策略链] 观察列表: {pre_market.get('initial_strategy', {}).get('watchlist', [])}")

    # Step 6 核心：读取昨日策略迭代结果（影响今日仓位上限）
    daily_strategy = chain.state.get('daily_strategy') or {}
    if daily_strategy:
        ds_pl = daily_strategy.get('position_limit', 60)
        ds_sl = daily_strategy.get('stop_loss', -0.08)
        print(f"[策略链] 昨日迭代仓位限制: {ds_pl}% (止损 {ds_sl:.0%})")
        print(f"[策略链] 迭代原因: {daily_strategy.get('reason', '')}")

    # 2. 读取盘中扫描历史（之前的调整）
    intraday_scans = chain.state.get('intraday_scans', [])
    if intraday_scans:
        latest_scan = intraday_scans[-1]
        print(f"[策略链] 最近扫描立场: {latest_scan.get('stance', 'N/A')}")
        print(f"[策略链] 最近扫描仓位: {latest_scan.get('position_limit', 'N/A')}%")
    
    # 3. 读取交易反馈
    trades = chain.state.get('trades', [])
    if trades:
        print(f"[策略链] 最近交易: {trades[-1]}")
    
    # 4. 读取仓位调整反馈
    feedback_loop = chain.state.get('feedback_loop', [])
    if feedback_loop:
        latest_feedback = feedback_loop[-1]
        print(f"[策略链] 最近反馈: {latest_feedback.get('type', 'N/A')} - {latest_feedback.get('lesson', 'N/A')[:50]}...")

    # 验证策略
    # 计算隔夜缺口风险
    indices_for_gap = market.get('indices', {})
    gap_risk = detect_gap_risk(indices_for_gap) if indices_for_gap else {}

    # 🤖 节点A：AI 判断缺口性质（有缺口时才调）
    gap_nature = "技术性"
    if gap_risk.get('has_gap_risk'):
        gap_nature = interpret_gap(gap_risk, market.get('news_sentiment', {}))

    validation = validate_pre_market_strategy(
        pre_market=pre_market,
        current_market=market,
        news_sentiment=market.get('news_sentiment', {}),
        gap_risk=gap_risk,
        fund_flow=fund_flow
    )

    # 分析反馈
    feedback_list = analyze_trade_feedback(chain)

    # 连续亏损检测（Marcus 纪律）—— 必须在节点 F 之前，供 force_pause 判断
    pause_check = check_consecutive_losses(chain)
    force_pause = pause_check.get('force_pause', False)

    # 🤖 节点F：持仓三合一（风控 + 止损 + 止盈），有持仓且非强制暂停时
    position_assessment = {}
    if position_analysis and not force_pause:
        try:
            # 读取催化数据
            cat_file = WORKSPACE / "data" / "news_catalysts.json"
            catalyst_db = json.load(open(cat_file, 'r', encoding='utf-8')) if cat_file.exists() else {}
            # 计算每只持仓的个股 ATR（替代大盘波动率，精准校准止损）
            atr_map = {}
            for p in position_analysis:
                sym = p.get('symbol', '')
                try:
                    kline = get_daily_kline(sym, days=15)
                    if kline.get('available') and kline.get('klines'):
                        # ATR ≈ avg(high-low) / close，取近 10 日
                        recent = kline['klines'][-10:]
                        ranges = [abs(k['high'] - k['low']) / k['close'] * 100
                                  for k in recent if k['close'] > 0]
                        if ranges:
                            atr_map[sym] = round(sum(ranges) / len(ranges), 2)
                except Exception:
                    pass
            position_assessment = assess_positions(position_analysis, catalyst_db, atr_map)
        except Exception as e:
            print(f"[持仓评估] ⚠️ 跳过: {e}", file=sys.stderr)

    # 调整策略（传入策略链以读取更多反馈 + 缺口性质 + 资金流性质）
    adjusted_strategy = adjust_strategy(pre_market, validation, feedback_list, chain, daily_strategy, gap_risk, fund_flow, gap_nature, flow_nature)

    # 连续亏损强制休息：覆盖 adjusted_strategy，阻止入场
    if force_pause:
        adjusted_strategy['stance'] = '⚪ hold (强制休息)'
        adjusted_strategy['stance_code'] = 'yellow'
        adjusted_strategy['position_limit'] = min(adjusted_strategy['position_limit'], 10)
        adjusted_strategy['adjustment_reason'] = f"连续亏损暂停: {pause_check.get('reason', '')}"

    stance = adjusted_strategy['stance']
    position_limit = adjusted_strategy['position_limit']
    # 优先使用 hot_sectors 缓存的情绪分（DeepSeek 或 akshare_stats fallback）
    # 不再从 get_market_status() 读（该接口无情绪分），直接用局部 sentiment_score
    news_score = news_sentiment.get('score', 50)

    # 生成报告
    # 立场使用今日动态计算的 adjusted_stance，不读昨日预盘数据
    current_stance_display = adjusted_strategy.get('stance', stance)
    report = f"""# 📈 Marcus 盘中扫描报告 (策略联动版)

**扫描时间**: {market['timestamp']}
**市场立场**: {current_stance_display}
**仓位上限**: {position_limit}%
**新闻情绪**: {news_score:.1f}

"""

    # 策略验证
    downgrade_info = adjusted_strategy.get('downgrade', {})
    if force_pause:
        report += f"🚨 **连续亏损暂停**: {pause_check.get('reason', '')}\n\n"
    elif downgrade_info.get('downgrade_triggered'):
        report += f"🔻 **持续降级触发**: {downgrade_info.get('reason', '')}\n"
        report += f"   {downgrade_info.get('downgrade_from', '')} → {downgrade_info.get('downgrade_to', stance)}\n"
        report += f"   连续 {downgrade_info.get('consecutive_rounds', 0)} 轮空头，仓位上限强制 {position_limit}%\n\n"
    elif validation.get('adjustment_needed'):
        report += f"⚠️ **策略调整**: {validation.get('adjustment_reason')}\n\n"
    else:
        price_ok = '✓' if validation.get('price_action_confirm') else '✗'
        bonus = validation.get('sentiment_bonus', 0)
        report += f"✅ **策略验证**: 价格行为{price_ok} | 情绪加分+{bonus}%\n\n"

    # 缺口风险在报告中标注
    if validation.get('gap_risk') and gap_risk:
        worst = gap_risk.get('worst_gap', 0)
        count = gap_risk.get('gap_count', 0)
        detail_lines = []
        for name, d in gap_risk.get('gap_detail', {}).items():
            detail_lines.append(f"{name}: 今开{d['open']:.2f} 昨收{d['last_close']:.2f} 缺口{d['gap']:+.2f}%")
        report += f"🚨 **缺口预警** ({count}个指数低开<-1.5%)\n"
        report += "\n".join(f"  {line}" for line in detail_lines[:3]) + "\n\n"

    # === 下一步策略 ===
    report += "### 🎯 下一步策略\n\n"
    
    # 基于市场立场
    current_stance_code = adjusted_strategy.get('stance_code', 'yellow')
    if current_stance_code == 'green':
        report += f"- **市场环境**: 🟢 积极做多，可扩大仓位\n"
        report += f"- **操作建议**: 积极建仓，目标仓位 60%\n"
    elif current_stance_code == 'yellow':
        report += f"- **市场环境**: 🟡 震荡整理，谨慎操作\n"
        report += f"- **操作建议**: 保持现有仓位，不超过 {position_limit}%\n"
    else:
        report += f"- **市场环境**: 🔴 观望为主，控制风险\n"
        report += f"- **操作建议**: 减仓避险，保留现金\n"
    
    report += f"- ℹ️ **候选股交由后续自动交易进行选股。盘中扫描不再进行选股操作**\n"
    
    # ── 盘中选股已交由 auto_trade 脚本独立执行 ──
    # watchlist / 技术面筛选 / AI评估 不再在扫描阶段运行
    watchlist = []
    tech_scan = {'passed': [], 'failed': [], 'total_scanned': 0, 'total_passed': 0}
    ai_assessment = {'passed': [], 'rejected': []}
    print(f"[盘中扫描] 选股已委托 auto_trade，扫描只负责行情+资金流+概念分析", file=sys.stderr)

    # ====== 节点E：持仓相关性预警（纯规则，基于持仓而非候选股） ======
    sector_warnings = []
    if positions:
        for pos in positions[:3]:
            sym = pos.get('symbol', '')
            if sym:
                corr = check_correlation(positions, sym, sector_warnings)
                if corr.get('warning'):
                    level_emoji = '🔴' if corr['level'] == 'red' else '🟡'
                    print(f"[相关性] {level_emoji} {corr['reason']}", file=sys.stderr)

    # 板块配置
    sector_alloc = adjusted_strategy.get('sector_allocation', {})
    if sector_alloc:
        high_weight = [(s, d.get('weight', 0)) for s, d in sector_alloc.items() if d.get('weight', 0) > 0.5]
        if high_weight:
            sectors = ', '.join([s for s, w in sorted(high_weight, key=lambda x: -x[1])[:3]])
            report += f"- **重点板块**: {sectors}\n"

    # hot_concepts 为空时显式标注
    if not hot_concepts:
        report += "- ⚠️ **热点概念**: 数据待盘中更新（新闻采集尚未完成或无新热点）\n"

    report += "\n"

    # ====== 技术面扫描表格（含 AI 评估） ======
    ai_passed = ai_assessment.get('passed', [])
    ai_rejected = ai_assessment.get('rejected', [])
    if ai_passed:
        report += "## 🔬 个股筛选（技术面 → AI 评估）\n\n"
        report += "| 代码 | 名称 | 现价 | 5日线位 | MACD | 量比 | 趋势阶段 | 催化评级 | 止损 |\n"
        report += "|------|------|------|---------|------|------|----------|----------|------|\n"
        for p in ai_passed:
            name = _get_stock_name(p['symbol'])
            grade = p.get('grade', 'B')
            catalyst = p.get('catalyst', '')
            cat_display = f"{grade}级 {catalyst}" if catalyst else f"{grade}级"
            # 趋势阶段标签
            trend_stage = p.get('trend_stage', '')
            trend_score = p.get('trend_score', 0)
            trend_warn = p.get('trend_warning', '')
            if trend_warn:
                trend_display = f"⚠️{trend_stage}"
            elif trend_score >= 4:
                trend_display = f"⭐{trend_stage}"
            else:
                trend_display = trend_stage or 'N/A'
            report += (f"| {p['symbol']} | {name} | {p.get('close', 0):.2f} | "
                       f"{p.get('ma_position', '')} | {p.get('macd_direction', '')} | "
                       f"{p.get('vol_ratio', 0):.1f}x | {trend_display} | {cat_display} | "
                       f"{p.get('stop_loss', 0):.2f} ({p.get('stop_loss_pct', '')}) |\n")
        report += "\n"
    if ai_rejected:
        report += "**AI 假突破过滤排除：**\n"
        for r in ai_rejected:
            report += f"- ❌ {r['symbol']}: {r.get('reason', '假突破风险')}\n"
        report += "\n"
    elif tech_scan.get('total_passed', 0) > 0 and not ai_passed:
        # 无 AI 结果时显示纯技术面
        report += "## 🔬 个股技术面扫描（右侧信号）\n\n"
        report += "| 代码 | 名称 | 现价 | 5日线位置 | MACD方向 | 量比 | 趋势阶段 | 建议止损 |\n"
        report += "|------|------|------|-----------|----------|------|----------|----------|\n"
        for p in tech_scan['passed']:
            name = _get_stock_name(p['symbol'])
            trend_stage = p.get('trend_stage', '')
            trend_warn = p.get('trend_warning', '')
            trend_display = f"⚠️{trend_stage}" if trend_warn else (trend_stage or 'N/A')
            report += (f"| {p['symbol']} | {name} | {p['close']:.2f} | "
                       f"{p['ma_position']} | {p['macd_direction']} | "
                       f"{p['vol_ratio']:.1f}x | {trend_display} | "
                       f"{p['stop_loss']:.2f} ({p['stop_loss_pct']}) |\n")
        report += "\n"
    elif tech_scan.get('total_scanned', 0) > 0 and not ai_passed:
        report += f"## 🔬 个股技术面扫描\n\n⚠️ 无股票通过右侧信号筛选 ({tech_scan['total_scanned']}只扫描，{tech_scan.get('total_passed', 0)}只通过)\n\n"

    # ====== 节点E：相关性预警（报告中展示） ======
    if sector_warnings:
        report += "## 🔗 相关性预警\n\n"
        for w in sector_warnings:
            emoji = '🔴' if w['level'] == 'red' else '🟡'
            report += f"- {emoji} **{w['industry']}** 敞口过高: {w['reason']}\n"
        report += "\n"

    # 账户状态（含完整盈亏汇总）
    pos_ratio = account.get('position_value', 0) / account.get('initial_capital', 1) * 100
    float_pnl = account.get('float_pnl', 0)
    realized_pnl = account.get('realized_pnl', 0)
    total_pnl = float_pnl + realized_pnl
    total_asset = account.get('total_asset', 0)
    initial_capital = account.get('initial_capital', 1)
    total_return = total_pnl / initial_capital * 100

    report += f"""## 💼 账户状态

| 项目 | 数值 |
|------|------|
| 可用资金 | {account.get('available_cash', 0):,.0f} 元 |
| 持仓市值 | {account.get('position_value', 0):,.0f} 元 |
| 仓位比例 | {pos_ratio:.1f}% |
| 已实现盈亏 | {realized_pnl:+,.0f} 元 |
| 浮动盈亏 | {float_pnl:+,.0f} 元 |
| 总盈亏 | {total_pnl:+,.0f} 元（{total_return:+.2f}%） |
| 总资产 | {total_asset:,.0f} 元 |

"""

    # 指数表现（从 market['indices'] 读取）
    indices_data = market.get('indices', {})
    if indices_data:
        valid_indices = {k: v for k, v in indices_data.items()
                         if v.get('close', 0) > 0}
        if valid_indices:
            report += "\n## 📈 指数表现\n\n"
            report += "| 指数 | 现价 | 涨跌 | 涨跌幅 |\n"
            report += "|------|------|------|--------|\n"
            for name, idx in valid_indices.items():
                close = idx.get('close', 0)
                chg = idx.get('change_amt', 0) or idx.get('chg', 0)
                pct = idx.get('change', 0) or idx.get('percent', 0)
                arrow = '▲' if pct > 0 else ('▼' if pct < 0 else '─')
                report += f"| {name} | {close:.2f} | {arrow}{abs(chg):.2f} | {arrow}{abs(pct):.2f}% |\n"
            report += "\n"

    # ====== 资金流向 ======
    if fund_flow:
        mflow = fund_flow.get('market', {})
        north = fund_flow.get('north', {})
        limitup = fund_flow.get('limit_up', {})
        fscore = fund_flow.get('fund_score', 50)
        fsignal = fund_flow.get('fund_signal', '中性')
        top_inflow = fund_flow.get('top_inflow', [])

        main_net = mflow.get('main_net_fmt', 'N/A') if mflow else 'N/A'
        source_stock_count = mflow.get('source_stock_count', 0) if mflow else 0
        source_date = mflow.get('source_date', '') if mflow else ''
        zt_count = limitup.get('zt_count', 0) if limitup else 0
        market_heat = limitup.get('market_heat', 50) if limitup else 50
        # 盘前(<09:35)涨停家数=0是正常现象，不显示"冰点"
        now = datetime.now()
        is_pre_market = now.hour < 9 or (now.hour == 9 and now.minute < 35)
        if is_pre_market:
            heat_emoji = '⏳'
            heat_note = '盘前待观察'
        else:
            heat_emoji = '🔥' if market_heat >= 65 else ('📊' if market_heat >= 50 else '❄️')
            heat_note = f'热度 {market_heat}'

        report += f"""## 💰 资金流向

| 维度 | 数据 |
|------|------|
| 主力净流入(自选) | {main_net}{f' ({source_date}，共{source_stock_count}只)' if source_date else ''} |
"""
        # 全市场大盘资金流
        market_wide = fund_flow.get('market_wide')
        if market_wide:
            is_realtime = market_wide.get('source', '').startswith('em_push2')
            mw_net = market_wide.get('net_amount_fmt', 'N/A')
            mw_nature = market_wide.get('flow_nature', '')
            mw_date = market_wide.get('trade_date', '')
            mw_sh = market_wide.get('pct_change_sh', 0)
            mw_sz = market_wide.get('pct_change_sz', 0)
            mw_total = market_wide.get('total_amount_fmt', '')
            mw_label = '实时(东财)' if is_realtime else '日频(Tushare)'
            mw_date_str = f' ({mw_date})' if mw_date else ''
            detail = f"{mw_net} ({mw_nature}"
            if mw_total:
                detail += f" | 成交:{mw_total}"
            if mw_sh or mw_sz:
                detail += f" | 沪{mw_sh:+.2f}%/深{mw_sz:+.2f}%"
            detail += f"){mw_date_str}"
            report += f"| 全市场资金流({mw_label}) | {detail} |\n"

        report += f"""| 涨停家数 | {zt_count} {heat_emoji} ({heat_note}) |
| 资金信号 | {fsignal} (score={fscore:.0f}) |
"""
        if north and north.get('total_net', 0) != 0:
            sh = north.get('sh_net', 0)
            sz = north.get('sz_net', 0)
            total = north.get('total_net', 0)
            nfmt = f"{total/1e8:.2f}亿" if abs(total) >= 1e8 else f"{total/1e4:.0f}万"
            report += f"| 北向资金 | {nfmt} |\n"
        if top_inflow:
            report += "\n**资金净流入板块 Top3：**\n"
            for item in top_inflow[:3]:
                ind = item.get('industry', '')
                net = item.get('net_fmt', 'N/A')
                lead = item.get('lead_stock', '')
                chg = item.get('change_pct', 0)
                report += f"- {ind}：{net} | 领涨 {lead}({chg:+.2f}%)\n"
        report += "\n"

    # 持仓表现（含今日涨跌幅 + 缩量检测）
    if position_analysis:
        report += "\n## 📊 持仓表现\n\n"
        for p in position_analysis:
            pct = p.get('today_pct', 0)
            profit = p.get('profit_ratio', 0)
            arrow = '▲' if pct > 0 else ('▼' if pct < 0 else '─')
            pa = position_assessment.get(p['symbol'], {})
            extra = ""
            # 缩量上涨警告（Marcus 纪律：缩量上涨需警惕）
            sv = p.get('shrink_volume')
            if sv and sv.get('warning'):
                risk_emoji = '🔴' if sv.get('risk_level') == 'high' else ('🟡' if sv.get('risk_level') == 'medium' else '⚪')
                extra += f" | {risk_emoji} 缩量上涨(量比{sv['vol_ratio']:.2f})"
            if pa.get('risk_action') and pa['risk_action'] != '持有':
                extra += f" | ⚠️ {pa['risk_action']}"
            if pa.get('new_stop'):
                extra += f" | 止损建议 {pa['new_stop']:.2f}"
            tp = pa.get('take_profit', {})
            if tp.get('suggestion') and tp['suggestion'] != '无需操作':
                extra += f" | {tp['suggestion']}"
            report += f"- **{p['symbol']} {p['name']}**: 现价{p.get('current_price',0):.2f} {arrow}{abs(pct):.2f}% | 持仓盈亏{profit*100:+.1f}%{extra}\n"
        report += "\n"

    # 持仓新闻影响力分析（读取 position_impact.json）
    impact_file = WORKSPACE / "data" / "position_impact.json"
    impacts = {}
    if impact_file.exists():
        try:
            with open(impact_file, 'r', encoding='utf-8') as f:
                impact_data = json.load(f)
                impacts = impact_data.get('impacts', {})
        except Exception as e:
            print(f"[警告] 读取持仓影响失败: {e}")

    # 持仓新闻
    holdings_news = market.get('holdings_news', {})
    
    # 构建更详细的持仓新闻报告
    if holdings_news or impacts:
        report += "## 📰 持仓新闻影响分析\n\n"
        
        # 如果有详细的 impact 分析，优先使用
        if impacts:
            for code, impact in impacts.items():
                emoji = '🟢' if impact.get('impact') == '利好' else ('🔴' if impact.get('impact') == '利空' else '🟡')
                strength = impact.get('strength', '无')
                reason = impact.get('reason', '无相关新闻')
                action = impact.get('action', '持有')
                name = _get_stock_name(code)
                
                # 如果有持仓新闻分数，也显示
                score_info = ""
                if code in holdings_news:
                    data = holdings_news[code]
                    if isinstance(data, dict):
                        score = data.get('score', 50)
                        score_info = f" | 新闻分数: {score:.0f}"
                
                report += f"- **{emoji} {code} {name}**\n"
                report += f"  - 影响: {impact.get('impact', '中性')} ({strength}) | 操作: {action}{score_info}\n"
                report += f"  - 原因: {reason}\n"
        else:
            # 降级：构造空结构
            news_impact = {'summary': {}, 'impact_analysis': []}
            news_sentiment = {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0, 'hot_concepts': [], 'catalysts': [], 'risks': []}
            hot_concepts = []
            catalysts, risks = [], []
            sentiment_positive, sentiment_negative = 0, 0
            # 降级到原来的简单格式
            for code, data in list(holdings_news.items())[:3]:
                if isinstance(data, dict):
                    score = data.get('score', 50)
                    report += f"- {code}: {score:.1f}分\n"
                else:
                    report += f"- {code}: 无新闻数据\n"

    # 记录策略链 — concept_scores 由东财实时资金流构建
    concept_scores = {}
    # 用实时概念资金流构建 concept_scores（主力净流入越大 → 分数越高）
    if concept_flow_details:
        max_inflow = max((s.get('main_net', 0) for s in concept_flow_details), default=1)
        for s in concept_flow_details[:30]:
            name = s.get('name', '')
            if name and name not in concept_scores:
                # 映射主力净流入到 50-100 分数区间
                net = s.get('main_net', 0)
                if max_inflow > 0 and net > 0:
                    score = 50 + min(50, (net / max_inflow) * 50)
                elif net < 0:
                    score = max(20, 50 + (net / max(abs(max_inflow), 1)) * 30)
                else:
                    score = 50
                concept_scores[name] = round(score, 1)
    # 补充 AI 缓存的 concept_scores（DeepSeek 主题评分，辅助参考）
    try:
        ai_cache = get_hot_sectors_from_cache()
        ai_scores = ai_cache.get('concept_scores', {}) if ai_cache.get('available') else {}
        for name, score in ai_scores.items():
            if name not in concept_scores:
                concept_scores[name] = score
    except Exception:
        pass

    scan_result = {
        'timestamp': datetime.now().isoformat(),
        'stance': adjusted_strategy.get('stance', stance),
        'stance_code': adjusted_strategy.get('stance_code', 'yellow'),
        'position_limit': position_limit,
        'sentiment_score': news_score,
        'holdings_news': holdings_news,
        'validation': validation,
        'adjusted_strategy': adjusted_strategy,
        'trade_feedback': feedback_list,
        'sector_allocation': adjusted_strategy.get('sector_allocation', {}),
        # Step 5 核心：热点概念数据
        'hot_concepts': hot_concepts,
        'concept_scores': concept_scores,
        # 持仓实时表现（含今日涨跌幅）
        'position_analysis': position_analysis,
        # 资金流向数据
        'fund_flow': fund_flow,
        # 实时概念板块行情（东财push2，含资金拆分明细+广度+领涨股）
        'concept_flow': concept_flow_details[:20] if concept_flow_details else [],
        # 主力净流入 Top 概念（资金驱动，用于发现"钱比价先动"的板块）
        'concept_fund_inflow': concept_fund_inflow_concepts[:15],
        # 连续亏损检测
        'force_pause': force_pause,
        'consecutive_losses': pause_check,
        # 选股已全部委托 auto_trade，扫描不再产出 watchlist/tech_scan/ai_assessment
        # 缺口性质
        'gap_nature': gap_nature,
        # 资金流性质
        'flow_nature': flow_nature,
        # 相关性预警
        'sector_warnings': sector_warnings,
        # 持仓 AI 评估
        'position_assessment': position_assessment,
        # 持续降级检测结果
        'downgrade': adjusted_strategy.get('downgrade', {}),
    }


    try:
        chain.record_intraday_scan(scan_result)
        print("✅ 策略链已更新")
    except Exception as e:
        print(f"⚠️ 策略链记录失败：{e}")

    return report, scan_result


def main():
    """主函数 - 输出扫描报告"""
    # 节假日保护：休市日只输出提示，不执行扫描
    is_trade, _ = is_today_trade_day()
    if not is_trade:
        today_str = datetime.now().strftime('%Y-%m-%d')
        print(f"📅 今天是休市日（{today_str}），跳过盘中扫描")
        return
    
    report, scan_result = generate_scan_report()

    # 从 scan_result 获取 position_analysis（generate_scan_report 里已计算）
    position_analysis = scan_result.get('position_analysis', [])
    print(report)

    # ===== P0 催化剂+动态观察列表更新 =====
    # 催化剂更新已委派 auto_trade 处理，扫描不再更新 watchlist 标签

    # 同时输出 JSON 格式供程序处理
    scan_data = {
        'timestamp': datetime.now().isoformat(),
        'type': 'market_scan',
        'report': report,
        'workspace': 'marcus',
        'adjusted_strategy': scan_result.get('adjusted_strategy', {}),
        'sentiment_score': scan_result.get('sentiment_score', 50),
        'sector_allocation': scan_result.get('sector_allocation', {}),
        'hot_concepts': scan_result.get('hot_concepts', []),
        'concept_scores': scan_result.get('concept_scores', {}),
        'position_analysis': position_analysis,
    }

    # 写入 Marcus workspace 的扫描日志
    log_dir = WORKSPACE / "memory" / "market-scan-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')
    log_file = log_dir / f"{today}-scans.jsonl"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(scan_data, ensure_ascii=False) + "\n")

    print(f"\n[日志] 已写入:{log_file}")


if __name__ == "__main__":
    main()
