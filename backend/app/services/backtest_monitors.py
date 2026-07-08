# -*- coding: utf-8 -*-
"""
回测分钟级监控器 — 模拟实盘 daemon 线程的逐分钟轮询。

在每个 Pi 交易窗口之间，逐分钟遍历历史分钟数据，执行：
  1. 止损检查 (复用 backtest_stop_loss 规则)
  2. 加仓层级检查 (复用 PositionTierMonitor 规则)
  3. 候选池自动建仓 (复用 CandidatePoolMonitor 规则)

与实盘的区别:
  - 数据源: local_data parquet 替代实时 HTTP API
  - 轮询粒度: 1分钟 (等效实盘30s轮询的平均)
  - 早盘冷静期 09:30-09:45: 仅止损, 不加仓/候选池
  - 尾盘 14:30 后: 仅止损, 不加仓/候选池
  - 午休 11:30-13:00: 不监控
"""

import logging
from datetime import date as dt_date, timedelta
from typing import Optional, Dict, List, Any

from app.services.local_data_provider import local_data

logger = logging.getLogger(__name__)

# ── 加仓层级常量 (与 PositionTierMonitor 一致) ──
TIER_CAPS = {'probe': 0.10, 'confirm': 0.18, 'sprint': 0.25}
MAX_ADDS_PER_SYMBOL_PER_DAY = 3

# ── 候选池每分钟最大检查数 ──
MAX_POOL_CHECKS_PER_MINUTE = 5


def run_minute_by_minute(
    account,
    trade_date: dt_date,
    start_hh: int, start_mm: int,
    end_hh: int, end_mm: int,
    pi_stance: str = 'yellow',
    daily_adds: dict = None,
    tier_states: dict = None,
    pool_state: dict = None,
    allow_buys: bool = True,
    db=None,
    task_id: str = '',
    phase_time: str = '',
    emit_fn=None,
    progress: float = 0,
) -> dict:
    """
    分钟级监控器主循环 — 在 [start, end] 时间区间内逐分钟轮询。

    Args:
        account: BacktestPaperEngine
        trade_date: 当前交易日
        start_hh, start_mm: 开始时间
        end_hh, end_mm: 结束时间
        pi_stance: Pi 市场立场
        daily_adds: {symbol: count} 当日已加仓计数 (原地修改)
        tier_states: {symbol: {tier, ...}} 层级状态 (原地修改)
        pool_state: 候选池状态 dict (原地修改)
        allow_buys: 是否允许买入 (早盘冷静期/尾盘禁止)
        db: SQLAlchemy Session (用于写 trade 记录)
        task_id: 回测任务 ID
        phase_time: 当前关联的 Pi 窗口时间 (用于日志)
        emit_fn: async emit 回调
        progress: 进度百分比

    Returns:
        {
            'stop_triggers': [{symbol, price, volume, reason, ...}],
            'tier_triggers': [{symbol, price, volume, target_tier, reason, ...}],
            'pool_triggers': [{symbol, price, volume, reason, ...}],
            'minutes_checked': int,
            'daily_adds': dict,
            'tier_states': dict,
            'pool_state': dict,
        }
    """
    if daily_adds is None:
        daily_adds = {}
    if tier_states is None:
        tier_states = {}
    if pool_state is None:
        pool_state = {'candidates': {}, 'today_buys': {}}

    stop_triggers = []
    tier_triggers = []
    pool_triggers = []

    # 计算总分钟数
    total_minutes = (end_hh * 60 + end_mm) - (start_hh * 60 + start_mm)
    if total_minutes < 0:
        total_minutes += 24 * 60

    # ── 午休跳过 ──
    def _in_lunch(h: int, m: int) -> bool:
        t = h * 60 + m
        return 11 * 60 + 30 <= t < 13 * 60

    hh, mm = start_hh, start_mm
    minute_count = 0
    checked_count = 0

    while True:
        # 到达结束时间
        if hh == end_hh and mm > end_mm:
            break
        if hh > end_hh or (hh == end_hh and mm > end_mm):
            break

        # 午休跳过
        if _in_lunch(hh, mm):
            mm += 1
            if mm >= 60:
                mm = 0
                hh += 1
            continue

        minute_count += 1

        # ── 获取当前分钟行情 ──
        positions = account.get_positions()
        held_syms = [p['symbol'] for p in positions if p.get('volume', 0) > 0]

        minute_quotes = {}
        if held_syms:
            minute_quotes = local_data.get_minute_quotes_for_held(
                trade_date, hh, mm, held_syms
            )

        if not minute_quotes and not held_syms:
            # 无持仓也无候选池 → skip
            mm += 1
            if mm >= 60:
                mm = 0
                hh += 1
            continue

        checked_count += 1

        # ── 1. 止损检查 (每分钟都做) ──
        for pos in positions:
            symbol = pos.get('symbol', '')
            volume = pos.get('volume', 0)
            avg_cost = pos.get('avg_cost', pos.get('avg_price', 0))
            if not symbol or volume <= 0 or avg_cost <= 0:
                continue

            # 当日买入跳过 (T+1 不可卖)
            t1 = account.get_t1_status(symbol) if hasattr(account, 'get_t1_status') else {}
            if t1.get('locked', False):
                continue

            # 获取分钟价格
            q = minute_quotes.get(symbol)
            if not q:
                continue
            cur_price = float(q.get('close', 0))
            if cur_price <= 0:
                continue

            float_pnl_pct = (cur_price / avg_cost - 1) * 100

            # 止损规则评估
            reason = _eval_stop_rules(symbol, cur_price, avg_cost, float_pnl_pct,
                                      trade_date, hh, mm, account)
            if reason:
                # 执行卖出
                result = account.place_order(symbol, "sell", cur_price, volume)
                if result.get("success"):
                    stop_triggers.append({
                        'symbol': symbol, 'price': cur_price, 'volume': volume,
                        'float_pnl_pct': round(float_pnl_pct, 2),
                        'reason': f'[回测止损@{hh:02d}:{mm:02d}] {reason}',
                        'minute': f'{hh:02d}:{mm:02d}',
                    })
                    # 从持仓列表移除 (避免后续分钟重复触发)
                    held_syms = [s for s in held_syms if s != symbol]

        # 重新获取持仓 (止损后可能变了)
        positions = account.get_positions()
        acc = account.get_account()
        total_asset = acc.get('total_asset', 100000)

        # ── 2. 加仓层级检查 ──
        if allow_buys and pi_stance != 'red':
            for pos in positions:
                symbol = pos.get('symbol', '')
                volume = pos.get('volume', 0)
                avg_cost = pos.get('avg_cost', pos.get('avg_price', 0))
                if not symbol or volume <= 0 or avg_cost <= 0:
                    continue
                # T+1: 今日买入不能加仓
                t1 = account.get_t1_status(symbol) if hasattr(account, 'get_t1_status') else {}
                if t1.get('locked', False):
                    continue

                q = minute_quotes.get(symbol)
                if not q:
                    continue
                cur_price = float(q.get('close', 0))
                if cur_price <= 0:
                    continue

                float_pnl = cur_price / avg_cost - 1
                current_tier = tier_states.get(symbol, {}).get('tier', 'probe')
                eval_result = _eval_tier(float_pnl, current_tier)
                if eval_result is None:
                    continue

                target_tier, max_pct, signal = eval_result

                # 快速门控
                if not _tier_gate_pass(float_pnl, target_tier, symbol, trade_date,
                                       daily_adds, max_pct, cur_price, volume,
                                       total_asset, acc):
                    continue

                # 计算加仓量
                current_mv = cur_price * volume
                current_pct = current_mv / total_asset if total_asset > 0 else 0
                add_pct = max_pct - current_pct
                if add_pct <= 0.005:
                    continue

                add_amount = total_asset * add_pct
                add_shares = int(add_amount / cur_price / 100) * 100
                if add_shares < 100:
                    continue

                # 检查现金
                available = acc.get('available_cash', 0)
                cost = cur_price * add_shares * 1.001  # 含手续费
                if cost > available:
                    continue

                # 执行买入
                result = account.place_order(symbol, "buy", cur_price, add_shares)
                if result.get("success"):
                    tier_states[symbol] = {'tier': target_tier,
                                           'updated_at': str(trade_date)}
                    daily_adds[symbol] = daily_adds.get(symbol, 0) + 1
                    tier_triggers.append({
                        'symbol': symbol, 'price': cur_price, 'volume': add_shares,
                        'target_tier': target_tier, 'current_tier': current_tier,
                        'float_pnl_pct': round(float_pnl * 100, 2),
                        'reason': (
                            f'[回测加仓@{hh:02d}:{mm:02d}] '
                            f'{current_tier}→{target_tier} | {signal} | '
                            f'仓位{current_pct:.1%}→{current_pct+add_pct:.1%} | '
                            f'+{add_shares}股 @{cur_price:.2f}'
                        ),
                        'minute': f'{hh:02d}:{mm:02d}',
                    })

        # ── 3. 候选池检查 ──
        if allow_buys and pi_stance != 'red':
            pool_candidates = pool_state.get('candidates', {})
            waiting = {s: c for s, c in pool_candidates.items()
                       if c.get('status') == 'waiting'}
            if waiting:
                # 每日自动建仓上限
                today_pool_buys = sum(
                    1 for c in pool_candidates.values()
                    if c.get('promoted_date') == str(trade_date)
                )
                if today_pool_buys < 3:
                    checked = 0
                    for sym, candidate in list(waiting.items()):
                        if checked >= MAX_POOL_CHECKS_PER_MINUTE:
                            break

                        q = minute_quotes.get(sym)
                        if not q:
                            q = local_data.get_minute_quote(sym, trade_date, hh, mm)
                        if not q:
                            continue
                        cur_price = float(q.get('close', 0))
                        if cur_price <= 0:
                            continue

                        checked += 1
                        ready = _check_pool_ready(sym, cur_price, candidate, trade_date)
                        if ready:
                            # 计算试探仓
                            probe_pct = 0.05
                            probe_amount = total_asset * probe_pct
                            probe_shares = int(probe_amount / cur_price / 100) * 100
                            if probe_shares < 100:
                                probe_shares = 100

                            available = acc.get('available_cash', 0)
                            cost = cur_price * probe_shares * 1.001
                            if cost > available:
                                continue

                            result = account.place_order(sym, "buy", cur_price, probe_shares)
                            if result.get("success"):
                                pool_candidates[sym]['status'] = 'promoted'
                                pool_candidates[sym]['promoted_date'] = str(trade_date)
                                pool_candidates[sym]['promoted_price'] = cur_price
                                pool_triggers.append({
                                    'symbol': sym, 'price': cur_price,
                                    'volume': probe_shares,
                                    'name': candidate.get('name', ''),
                                    'reason': (
                                        f'[回测候选池@{hh:02d}:{mm:02d}] '
                                        f'回调到位自动建仓 | '
                                        f'{candidate.get("last_reject_reason", "")}'
                                    ),
                                    'minute': f'{hh:02d}:{mm:02d}',
                                })

        # ── 前进到下一分钟 ──
        mm += 1
        if mm >= 60:
            mm = 0
            hh += 1

    return {
        'stop_triggers': stop_triggers,
        'tier_triggers': tier_triggers,
        'pool_triggers': pool_triggers,
        'minutes_scanned': minute_count,
        'minutes_checked': checked_count,
        'daily_adds': daily_adds,
        'tier_states': tier_states,
        'pool_state': pool_state,
    }


# ═══════════════════════════════════════════════════════════════
# 止损规则 (从 backtest_stop_loss 提取，略作调整以适应分钟级)
# ═══════════════════════════════════════════════════════════════

def _eval_stop_rules(symbol: str, cur_price: float, avg_cost: float,
                     float_pnl_pct: float, trade_date: dt_date,
                     hh: int, mm: int, account) -> Optional[str]:
    """评估止损规则，返回触发原因或 None"""

    # 早盘冷静期(09:30-09:45)不触发止损 (波动大)
    if hh == 9 and mm < 45:
        return None

    # ── Rule 0a: 破底止损 ──
    stage_low = _get_stage_low(symbol, trade_date)
    if stage_low > 0 and cur_price < stage_low * 0.97:
        return f'破底止损: {cur_price:.2f} < 阶段底{stage_low:.2f}×0.97={stage_low*0.97:.2f}'

    # ── Rule 0b: 成本止损 ──
    if float_pnl_pct <= -6.0:
        return f'成本止损(-6%): 浮亏{float_pnl_pct:.2f}%'
    if float_pnl_pct <= -4.0:
        return f'成本止损(-4%): 浮亏{float_pnl_pct:.2f}%'

    # ── Rule 1: 板块背离 ──
    divergence_reason = _check_sector_divergence(symbol, float_pnl_pct, trade_date)
    if divergence_reason:
        return divergence_reason

    # ── Rule 2: 铁律二 ──
    iron_rule_reason = _check_iron_rule2(symbol, float_pnl_pct, trade_date)
    if iron_rule_reason:
        return iron_rule_reason

    # ── Rule 3: 大盘相对 ──
    market_reason = _check_market_relative(symbol, float_pnl_pct, trade_date)
    if market_reason:
        return market_reason

    return None


def _get_stage_low(symbol: str, trade_date: dt_date) -> float:
    """获取90天阶段最低价"""
    try:
        current = trade_date
        days_checked = 0
        lows = []
        while days_checked < 120 and len(lows) < 90:
            if current.weekday() < 5:
                bar = local_data.get_daily_quote(symbol, current)
                if bar and bar.get('low', 0) > 0:
                    lows.append(float(bar['low']))
                days_checked += 1
            current -= timedelta(days=1)
        return min(lows) if lows else 0.0
    except Exception:
        return 0.0


def _check_sector_divergence(symbol: str, float_pnl_pct: float,
                             trade_date: dt_date) -> Optional[str]:
    """板块背离止损"""
    if float_pnl_pct >= 0:
        return None
    try:
        mapping = local_data.get_concept_mapping(symbol, trade_date)
        concepts = mapping.get('concepts', []) if mapping else []
        if not concepts:
            return None

        flows = local_data.get_concept_flow(trade_date, top_n=30) or []
        flow_map = {f.get('name', ''): f.get('pct_change', 0) for f in flows}

        for cn in concepts[:3]:
            cn_name = cn.get('concept_name', '') if isinstance(cn, dict) else str(cn)
            sector_pct = flow_map.get(cn_name, 0)
            if sector_pct == 0:
                continue
            divergence = float_pnl_pct - sector_pct
            if divergence < -3.0:
                return f'板块背离: 板块{cn_name}({sector_pct:+.1f}%), 个股{float_pnl_pct:+.1f}%, 跑输{divergence:.1f}pp'
    except Exception:
        pass
    return None


def _check_iron_rule2(symbol: str, float_pnl_pct: float,
                      trade_date: dt_date) -> Optional[str]:
    """铁律二移动止盈（渐进保护版）"""
    if float_pnl_pct >= 0:
        return None
    amp = _calc_avg_amplitude(symbol, trade_date)
    if amp <= 0.03:
        t1, t1_5 = 1.0, 2.0
        tier = '低波'
    elif amp <= 0.06:
        t1, t1_5 = 2.0, 3.5
        tier = '中波'
    else:
        t1, t1_5 = 3.0, 5.0
        tier = '高波'
    if float_pnl_pct < -t1_5:
        return f'铁律二({tier}): 浮亏{float_pnl_pct:.2f}% > T1.5={-t1_5}%'
    elif float_pnl_pct < -t1:
        return f'铁律二({tier}): 浮亏{float_pnl_pct:.2f}% > T1={-t1}%'
    return None


def _check_market_relative(symbol: str, float_pnl_pct: float,
                           trade_date: dt_date) -> Optional[str]:
    """大盘相对表现止损"""
    try:
        sh_bar = local_data.get_daily_quote('000001.SH', trade_date)
        if not sh_bar:
            return None
        sh_close = float(sh_bar.get('close', 0))
        sh_pre = float(sh_bar.get('pre_close', 0))
        if sh_close <= 0 or sh_pre <= 0:
            return None
        market_pct = (sh_close / sh_pre - 1) * 100

        # 大盘走弱+个股更弱
        if market_pct <= -2 and (float_pnl_pct - market_pct) < -3:
            return f'大盘相对: 大盘{market_pct:+.1f}%, 个股{float_pnl_pct:+.1f}%, 跑输{(float_pnl_pct-market_pct):.1f}pp'

        # 动态阈值
        if market_pct <= -2:
            threshold = 1.5
        elif market_pct <= 1:
            threshold = 2.0
        elif market_pct <= 2:
            threshold = 3.0
        else:
            threshold = 4.0

        if float_pnl_pct <= -threshold:
            return f'大盘相对(动态): 大盘{market_pct:+.1f}%, 阈值{-threshold}%, 个股{float_pnl_pct:+.1f}%'
    except Exception:
        pass
    return None


def _calc_avg_amplitude(symbol: str, trade_date: dt_date) -> float:
    """计算近5日平均振幅"""
    amps = []
    current = trade_date - timedelta(days=1)
    days_checked = 0
    while days_checked < 30 and len(amps) < 5:
        if current.weekday() < 5:
            try:
                bar = local_data.get_daily_quote(symbol, current)
                if bar and bar.get('high', 0) > 0 and bar.get('low', 0) > 0:
                    amp = (float(bar['high']) - float(bar['low'])) / float(bar['low'])
                    amps.append(amp)
                days_checked += 1
            except Exception:
                days_checked += 1
        current -= timedelta(days=1)
    return sum(amps) / len(amps) if amps else 0.03


# ═══════════════════════════════════════════════════════════════
# 加仓层级逻辑 (从 PositionTierMonitor 提取)
# ═══════════════════════════════════════════════════════════════

def _eval_tier(float_pnl: float, current_tier: str) -> Optional[tuple]:
    """
    评估加仓层级。
    Returns: (target_tier, max_position_pct, signal) or None
    """
    if current_tier == 'sprint':
        return None

    if current_tier in ('probe', 'unknown', '') and float_pnl >= 0.03:
        return ('sprint', TIER_CAPS['sprint'], f'浮盈{float_pnl:.1%}≥3%→冲刺仓')
    if current_tier in ('probe', 'unknown', '') and float_pnl >= 0.01:
        return ('confirm', TIER_CAPS['confirm'], f'浮盈{float_pnl:.1%}≥1%→确认仓')
    if current_tier == 'confirm' and float_pnl >= 0.03:
        return ('sprint', TIER_CAPS['sprint'], f'浮盈{float_pnl:.1%}≥3%→冲刺仓')

    return None


def _tier_gate_pass(float_pnl: float, target_tier: str, symbol: str,
                    trade_date: dt_date, daily_adds: dict, max_pct: float,
                    cur_price: float, cur_volume: int, total_asset: float,
                    acc: dict) -> bool:
    """门控检查 (简化为关键项)"""

    # 保护线
    if target_tier == 'confirm':
        if float_pnl < 0 or float_pnl < 0.005:
            return False
    elif target_tier == 'sprint':
        amp = _calc_avg_amplitude(symbol, trade_date)
        if amp <= 0.03:
            x = 0.01
        elif amp <= 0.06:
            x = 0.02
        else:
            x = 0.03
        if float_pnl < x or float_pnl - x < 0.005:
            return False

    # 日加仓上限
    if daily_adds.get(symbol, 0) >= MAX_ADDS_PER_SYMBOL_PER_DAY:
        return False

    # 总回撤
    initial = acc.get('initial_capital', 100000)
    total_pnl = acc.get('float_pnl', 0) + acc.get('realized_pnl', 0)
    if initial > 0 and -min(0, total_pnl) / initial >= 0.05:
        return False

    return True


# ═══════════════════════════════════════════════════════════════
# 候选池逻辑 (从 CandidatePoolMonitor 提取)
# ═══════════════════════════════════════════════════════════════

def capture_candidate(symbol: str, name: str, reason: str,
                      pool_state: dict, trade_date: dt_date) -> bool:
    """
    尝试将标的加入回测候选池。
    使用与实盘相同的 7 条件分类。
    """
    candidates = pool_state.setdefault('candidates', {})

    # 已存在则跳过
    if symbol in candidates:
        return False

    # 简化分类: 解析拒绝原因判断是否可入池
    r = reason.lower()
    # 结构性排除
    if any(kw in r for kw in ['死叉', 'ma5<ma20', 'ma5 < ma20',
                                '主力出货', '主力出逃', '5日主力为负',
                                '硬禁止', '涨停', '第一层', '第二层']):
        return False
    # 时机性确认
    is_timing = any(kw in r for kw in ['rsi', '超买', '分位', 'j值',
                                        '试探仓', '⚠️', '价格不可达',
                                        '资金不足'])
    if not is_timing:
        return False

    candidates[symbol] = {
        'symbol': symbol,
        'name': name,
        'status': 'waiting',
        'added_date': str(trade_date),
        'last_reject_reason': reason[:200],
        'checks_count': 0,
    }
    return True


def _check_pool_ready(symbol: str, cur_price: float,
                      candidate: dict, trade_date: dt_date) -> bool:
    """
    简化版入场过滤 — 用本地日线数据替代 check_entry_filters API。
    检查:
      1. MA5 > MA20 (趋势向上)
      2. RSI6 < 85 (不过热)
      3. 当日涨幅 < 5% (不追高)
    """
    try:
        bar = local_data.get_daily_quote(symbol, trade_date)
        if not bar:
            return False
        pre_close = float(bar.get('pre_close', 0))
        if pre_close <= 0:
            return False
        change_pct = (cur_price / pre_close - 1) * 100
        if change_pct > 5:
            return False
    except Exception:
        return False

    # MA5 > MA20 检查
    try:
        closes = []
        current = trade_date - timedelta(days=1)
        days_checked = 0
        while days_checked < 60 and len(closes) < 25:
            if current.weekday() < 5:
                b = local_data.get_daily_quote(symbol, current)
                if b and b.get('close', 0) > 0:
                    closes.append(float(b['close']))
                days_checked += 1
            current -= timedelta(days=1)
        closes.reverse()
        closes.append(cur_price)  # 当日盘中价

        if len(closes) >= 20:
            ma5 = sum(closes[-5:]) / 5
            ma20 = sum(closes[-20:]) / 20
            if ma5 < ma20:
                return False
    except Exception:
        pass

    # RSI6 估算 (简化: 近6日涨幅均值)
    try:
        if len(closes) >= 7:
            gains = [closes[i] - closes[i-1] for i in range(-6, 0)]
            avg_gain = sum(g for g in gains if g > 0) / 6
            avg_loss = abs(sum(g for g in gains if g < 0)) / 6
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi6 = 100 - 100 / (1 + rs)
                if rsi6 >= 85:
                    return False
    except Exception:
        pass

    return True
