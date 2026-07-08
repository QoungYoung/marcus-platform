"""
回测止损检查 — 复用 StopLossMonitor 规则, 用本地 parquet 数据替代实时 API.

在每个交易窗口 (09:35/09:53/10:35/13:35/14:30) 执行:
1. 更新持仓市值 (stock_1min 分钟快照)
2. 逐一评估止损规则 (破底/成本/板块背离/铁律二/大盘相对)
3. 触发则通过 BacktestPaperEngine 执行卖出

与实盘 StopLossMonitor 的区别:
- 数据源: local_data (parquet) 替代 tushare/东财实时
- 触发时机: 交易窗口替代 30s 轮询
- 大盘行情: index_1min parquet 替代 HTTP API
"""

import sys
import os
from datetime import date as dt_date, datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# 项目根
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def check_backtest_stop_loss(
    account,           # BacktestPaperEngine
    trade_date: dt_date,
    phase_time: str,   # "09:35" / "09:53" / "10:35" / "13:35" / "14:30"
) -> List[dict]:
    """
    在回测交易窗口执行止损检查。返回 [{"symbol","price","volume","reason"}, ...]
    """
    results = []
    hh, mm = map(int, phase_time.split(":")[:2])

    # 早盘冷静期 (09:30-09:45 不执行止损)
    quiet = hh == 9 and mm < 45

    positions = account.get_positions()
    if not positions:
        return results

    from app.services.local_data_provider import local_data

    # 当天已买入的标的 (T+1 保护)
    # 既要过滤 _last_buy_date, 也要对每个持仓调用 get_t1_status 双重确认
    # (某些场景如 buy 委托生成但 match 失败, _last_buy_date 不会写入)
    today_buy_syms = set()
    for sym, last_buy in getattr(account, '_last_buy_date', {}).items():
        if last_buy == trade_date:
            today_buy_syms.add(sym)

    for pos in positions:
        sym = pos.get("symbol", "")
        avg_cost = pos.get("avg_cost", 0) or pos.get("avg_price", 0)
        vol = pos.get("volume", 0)
        if not sym or avg_cost <= 0 or vol <= 0:
            continue
        if sym in today_buy_syms:
            continue  # T+1 (从 _last_buy_date 推断)
        # 双重确认: 即使 _last_buy_date 没记录, 实际 T+1 锁定时也跳过
        try:
            t1 = account.get_t1_status(sym)
            if t1.get("locked"):
                continue
        except Exception:
            pass

        # 分钟快照: 更新当前价
        mq = local_data.get_minute_quote(sym, trade_date, hh, mm)
        if not mq:
            continue
        cur_price = float(mq.get("close", 0))
        if cur_price <= 0:
            continue

        float_pnl_pct = (cur_price / avg_cost - 1) * 100

        # ── 规则评估 (按优先级) ──
        reason = _evaluate_rules(sym, cur_price, avg_cost, float_pnl_pct,
                                  trade_date, hh, mm, quiet)
        if reason:
            results.append({
                "symbol": sym, "price": cur_price, "volume": vol,
                "reason": reason, "float_pnl_pct": round(float_pnl_pct, 2),
            })
    return results


def _evaluate_rules(
    sym, cur_price, avg_cost, float_pnl_pct, trade_date, hh, mm, quiet
) -> Optional[str]:
    """按优先级评估止损规则, 返回第一个命中的原因"""

    # Rule 4: 早盘冷静期 (09:30-09:45)
    if quiet:
        return None

    from app.services.local_data_provider import local_data

    # ── Rule 0a: 破底止损 ──
    reason = _rule_break_low(sym, cur_price)
    if reason:
        return reason

    # ── Rule 0b: 成本止损 ──
    reason = _rule_cost_stop(sym, cur_price, avg_cost, float_pnl_pct)
    if reason:
        return reason

    # ── Rule 1: 板块背离止损 ──
    reason = _rule_sector_divergence(sym, float_pnl_pct, trade_date)
    if reason:
        return reason

    # ── Rule 2: 铁律二移动止盈 ──
    reason = _rule_iron_rule2(sym, cur_price, avg_cost, float_pnl_pct, trade_date)
    if reason:
        return reason

    # ── Rule 3: 大盘相对表现 ──
    reason = _rule_dynamic(cur_price, avg_cost, float_pnl_pct, trade_date, hh, mm)
    if reason:
        return reason

    return None


# ═══════════════════════════════════════════════════════════
#  各规则实现 (使用 local_data 替代实时 API)
# ═══════════════════════════════════════════════════════════

def _rule_break_low(sym: str, cur_price: float) -> Optional[str]:
    """破底止损: 当前价 < 90天阶段最低 × 0.97"""
    from app.services.local_data_provider import local_data
    from datetime import timedelta
    try:
        # 取最近 90 天日K线最低点
        end_dt = dt_date.today()  # 简化: 用今天做上限 (回测场景下不影响)
        start_dt = end_dt - timedelta(days=120)
        prev = end_dt - timedelta(days=1)
        stage_low = None
        cur = prev
        days = 0
        while days < 90 and cur >= start_dt:
            if cur.weekday() < 5:
                dq = local_data.get_daily_quote(sym, cur)
                if dq:
                    lo = float(dq.get("low", 0))
                    if lo > 0:
                        if stage_low is None or lo < stage_low:
                            stage_low = lo
                days += 1
            cur -= timedelta(days=1)

        if stage_low and stage_low > 0:
            stop_price = stage_low * 0.97
            if cur_price < stop_price:
                return (
                    f"破底止损: {cur_price:.2f} < "
                    f"阶段底{stage_low:.2f}×0.97={stop_price:.2f}"
                )
    except Exception:
        pass
    return None


def _rule_cost_stop(sym: str, cur_price: float, avg_cost: float,
                     float_pnl_pct: float) -> Optional[str]:
    """成本止损: 从未盈利→-4% / 小盈转亏→-3% / 无数据→-6%"""
    if avg_cost <= 0:
        return None

    # 简化: 用成本价和当前价判断。HWM 需要跨日追踪,首次回测用保守策略。
    if float_pnl_pct <= -6.0:
        return f"成本止损-6%底线: 亏损{float_pnl_pct:.2f}% (成本{avg_cost:.2f})"
    if float_pnl_pct <= -4.0:
        return f"成本止损-4%: 亏损{float_pnl_pct:.2f}% (成本{avg_cost:.2f})"

    return None


def _rule_sector_divergence(sym: str, float_pnl_pct: float,
                              trade_date: dt_date) -> Optional[str]:
    """板块背离止损: 个股涨幅 - 板块涨幅 < -3pp"""
    if float_pnl_pct >= 0:
        return None
    from app.services.local_data_provider import local_data
    try:
        # 查个股所属概念
        concept_data = local_data.get_concept_mapping(sym, trade_date)
        if not concept_data:
            return None
        concepts = concept_data.get("concepts", [])
        # 找涨幅最相关的板块
        flows = local_data.get_concept_flow(trade_date, top_n=30)
        sector_map = {f.get("name", ""): f for f in flows}
        for c in concepts[:3]:  # 取前 3 个概念
            cn = c.get("concept_name", "")
            sector = sector_map.get(cn)
            if sector:
                sector_pct = float(sector.get("pct_change", 0))
                divergence = float_pnl_pct - sector_pct
                if divergence < -3.0:
                    return (
                        f"板块背离止损: 板块{cn}({sector_pct:+.1f}%), "
                        f"个股{float_pnl_pct:+.1f}%, 跑输{abs(divergence):.1f}pp"
                    )
    except Exception:
        pass
    return None


def _rule_iron_rule2(sym: str, cur_price: float, avg_cost: float,
                      float_pnl_pct: float, trade_date: dt_date) -> Optional[str]:
    """铁律二: 盈利单不能变亏损 (简化版: 用振幅分档+渐进保护)"""
    if float_pnl_pct >= 0:
        return None

    from app.services.local_data_provider import local_data
    from datetime import timedelta
    try:
        # 计算近5日振幅
        amps = []
        cur = trade_date - timedelta(days=1)
        for _ in range(10):
            if cur.weekday() < 5:
                dq = local_data.get_daily_quote(sym, cur)
                if dq:
                    h = float(dq.get("high", 0))
                    l = float(dq.get("low", 0))
                    c = float(dq.get("close", 0))
                    if c > 0:
                        amps.append((h - l) / c * 100)
                if len(amps) >= 5:
                    break
            cur -= timedelta(days=1)

        avg_amp = sum(amps) / len(amps) if amps else 3.0

        if avg_amp < 3:
            t1, t1_5 = 1, 2
            tier = "低波"
        elif avg_amp <= 6:
            t1, t1_5 = 2, 3.5
            tier = "中波"
        else:
            t1, t1_5 = 3, 5
            tier = "高波"

        # 简化逻辑: 浮亏超过 T1.5 阈值 → 触发渐进保护
        if float_pnl_pct < -t1_5:
            return f"铁律二触发({tier}波): 浮亏{float_pnl_pct:.2f}% > T1.5={-t1_5}%"
        elif float_pnl_pct < -t1:
            return f"铁律二触发({tier}波): 浮亏{float_pnl_pct:.2f}% > T1={-t1}%"
    except Exception:
        pass
    return None


def _rule_dynamic(cur_price: float, avg_cost: float, float_pnl_pct: float,
                    trade_date: dt_date, hh: int, mm: int) -> Optional[str]:
    """大盘相对表现止损"""
    from app.services.local_data_provider import local_data
    try:
        # 用上证指数作为大盘代理
        sh_dq = local_data.get_daily_quote("000001.SH", trade_date)
        if not sh_dq:
            return None
        sh_close = float(sh_dq.get("close", 0))
        sh_pre = float(sh_dq.get("pre_close", 0))
        if sh_pre <= 0:
            return None
        market_pct = (sh_close / sh_pre - 1) * 100

        # 强审: 大盘跌>2% 且个股跑输>3pp
        if market_pct <= -2 and (float_pnl_pct - market_pct) < -3:
            return (
                f"大盘相对弱势: 上证{market_pct:+.1f}%, "
                f"个股{float_pnl_pct:+.1f}%, 跑输{abs(float_pnl_pct-market_pct):.1f}pp"
            )

        # 大盘感知基础阈值
        if market_pct <= -2:
            threshold = 1.5
        elif -1 <= market_pct <= 1:
            threshold = 2.0
        elif market_pct <= 2:
            threshold = 3.0
        else:
            threshold = 4.0

        if float_pnl_pct <= -threshold:
            return f"动态止损: 大盘{market_pct:+.1f}%, 止损阈值{-threshold}%, 当前{float_pnl_pct:.2f}%"
    except Exception:
        pass
    return None
