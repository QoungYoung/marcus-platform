# -*- coding: utf-8 -*-
"""个股波段逻辑止损（狼大止损六层之①）+ 建仓初期 / 趋势中段的阶段切换（六层之④口径）。

────────────────────────────────────────────────────────────────
狼大原话（语料，2026-03-05）:
  「13日内跌破波段低点的-3%没有收回 直接止损 。。。按我0.618买入 。。。-6%左右」

狼大原话（语料，2026-03-06，**他自己划的适用边界**）:
  「**已经成为趋势后** 。。。这个就没意义了 更多应该转为我之前说的趋势波段止盈止损方法
    也就是用**趋势线**的方法 。。。**不是一个策略用到底的**」

狼大原话（语料，2026-08-19，止损六层之⑤「止损预设」）:
  「我肯定按计划做的 然后**设定好止损**就行了」
────────────────────────────────────────────────────────────────

落地口径（本模块）:
  · **建仓初期**（建仓后 `WOLF_EARLY_STOP_DAYS` 个交易日内，默认 13 —— 与狼大原话同数）:
      止损线 = **建仓时点的波段低点** × (1 − `WOLF_EARLY_STOP_PCT`%，默认 3)。
  · **已成趋势后**（超出上述窗口）: 本模块**不产生止损线**，交回既有 `stop_loss_price`。
      —— 这就是六层之②（趋势线法）的**暂定口径**: 语料里狼大**没有给出任何趋势线参数**
         （唯一出现的"破5日减仓/破趋势线止损"是 2022-04-26 **一位用户自己的规则**，狼大未背书），
         用户 2026-09-10 决策「趋势中段止损口径我们回测之后看情况再决定，先用 stop_loss_price」。
         故此处不新造趋势线算法 —— 避免又一次"自造机制"（见审计 §5.2）。

关于 ⑤「止损预设」（**锁定时点**）:
  波段低点必须是**建仓时点的事实**，不能每天滚动重算。
  本模块用**锚定建仓日**的方式天然满足这一点:
    · 波段低点 = 建仓日（含）之前 `WOLF_SWING_LOW_WIN`（默认 13）根日K 的**最低价**；
    · 持有交易日数 = 建仓日**之后**的日K根数。
  两者都以不可变的"建仓日"为锚 → 之后任何一天重算都得同一个值，**等价于建仓时锁定**，
  且**不需要**新增存储/新表/在生产买卖路径上写状态（避免在动钱路径上加副作用）。

"没有收回"这一半由既有 `t_monitor._stop_close_confirm`（收盘确认 / 假跌破守卫）承担，
  本模块只负责**给出一条止损线**（狼大 2026-01-29「收盘跌破我才出」）。

开关:
  · `WOLF_EARLY_STOP=0`          → 关闭本模块（退回"一律用 stop_loss_price"）；
  · `WOLF_EARLY_STOP_DAYS=13`    → 建仓初期窗口（交易日）；
  · `WOLF_SWING_LOW_WIN=13`      → 波段低点回看窗口（交易日）；
  · `WOLF_EARLY_STOP_PCT=3`      → 波段低点下方几个百分点（狼大原话 -3%）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# 狼大 2026-03-05 原话里的数字（默认值全部有出处，不另设"调参"）
EARLY_STAGE_DAYS_DEFAULT = 13
SWING_WIN_DEFAULT = 13
STOP_PCT_DEFAULT = 3.0
MIN_SWING_BARS = 5          # 波段低点至少要有 5 根日K 才认（否则回看太短、低点无意义）


def _env_int(name: str, dflt: int) -> int:
    try:
        return int(str(os.getenv(name, str(dflt))).strip())
    except Exception:
        return dflt


def _env_float(name: str, dflt: float) -> float:
    try:
        return float(str(os.getenv(name, str(dflt))).strip())
    except Exception:
        return dflt


def enabled() -> bool:
    return os.getenv("WOLF_EARLY_STOP", "1").strip() not in ("0", "false", "no")


def _norm_date(s: Any) -> str:
    """'2026-09-01' / '20260901' / date → 'YYYYMMDD'（空值返回 ''）。"""
    t = "".join(ch for ch in str(s or "") if ch.isdigit())
    return t[:8] if len(t) >= 8 else ""


def swing_low_asof(bars: List[Dict[str, Any]], buy_date: Any, win: Optional[int] = None) -> Optional[float]:
    """**建仓时点的波段低点**: 建仓日（含）之前 win 根日K 的最低价。

    bars: [{'date': 'YYYYMMDD'|'YYYY-MM-DD', 'high','low','close','vol'}, ...]（顺序不限）。
    数据不足 MIN_SWING_BARS 根 → None（调用方退回 stop_loss_price）。
    """
    w = int(win if win is not None else _env_int("WOLF_SWING_LOW_WIN", SWING_WIN_DEFAULT))
    if w <= 0:
        return None
    bd = _norm_date(buy_date)
    if not bd:
        return None
    rows = []
    for b in bars or []:
        d = _norm_date(b.get("date"))
        if not d or d > bd:
            continue
        try:
            lo = float(b.get("low") or 0)
        except Exception:
            continue
        if lo > 0:
            rows.append((d, lo))
    rows.sort(key=lambda x: x[0])
    rows = rows[-w:]
    if len(rows) < MIN_SWING_BARS:
        return None
    return min(lo for _, lo in rows)


def held_trading_days(bars: List[Dict[str, Any]], buy_date: Any) -> Optional[int]:
    """建仓日**之后**的日K根数 = 持有交易日数（无建仓日 / 无数据 → None）。

    用行情自身的交易日序列计数，不依赖交易日历（节假日自动跳过）。
    """
    bd = _norm_date(buy_date)
    if not bd:
        return None
    n = 0
    for b in bars or []:
        d = _norm_date(b.get("date"))
        if d and d > bd:
            n += 1
    return n


def early_stop_price(swing_low: float, pct: Optional[float] = None) -> float:
    """波段低点下方 pct% —— 狼大「跌破波段低点的 -3%」。"""
    p = float(pct if pct is not None else _env_float("WOLF_EARLY_STOP_PCT", STOP_PCT_DEFAULT))
    return round(float(swing_low) * (1.0 - p / 100.0), 3)


def resolve_stop(cond_stop: Optional[float],
                 bars: List[Dict[str, Any]],
                 buy_date: Any,
                 early_days: Optional[int] = None,
                 win: Optional[int] = None,
                 pct: Optional[float] = None) -> Tuple[Optional[float], str, str]:
    """阶段化止损线解析 → (stop_price, source, reason)。

    source ∈ {'wolf_early_swing'（① 建仓初期波段逻辑止损）, 'stop_loss_price'（④ 趋势中段: 既有口径）,
              'none'}
    · 建仓初期（held <= WOLF_EARLY_STOP_DAYS）且有波段低点 → 狼大 ① 结构止损；
    · 其它（已成趋势 / 无波段低点 / 无建仓日 / 关闭）→ 交回 cond_stop（既有 stop_loss_price）。

    注意: **不**在两者之间取 min/max 做"复合" —— 狼大「不是一个策略用到底的」，
    阶段之外就该换口径，把两条线叠加是自造机制。
    """
    d_days = int(early_days if early_days is not None else _env_int("WOLF_EARLY_STOP_DAYS", EARLY_STAGE_DAYS_DEFAULT))
    cs = float(cond_stop or 0) or 0.0
    if not enabled():
        return (cs or None), ("stop_loss_price" if cs else "none"), "WOLF_EARLY_STOP=0 → 用 stop_loss_price"
    held = held_trading_days(bars, buy_date)
    if held is None:
        return (cs or None), ("stop_loss_price" if cs else "none"), "无建仓日 → 用 stop_loss_price"
    if held > d_days:
        return (cs or None), ("stop_loss_price" if cs else "none"), (
            "已成趋势(持有 %d 交易日 > %d) → 六层之②暂用 stop_loss_price" % (held, d_days))
    sl = swing_low_asof(bars, buy_date, win)
    if sl is None:
        return (cs or None), ("stop_loss_price" if cs else "none"), (
            "建仓初期但波段低点数据不足 → 用 stop_loss_price")
    sp = early_stop_price(sl, pct)
    return sp, "wolf_early_swing", (
        "建仓初期(持有 %d <= %d 交易日): 波段低点 %.3f -%.1f%% → 止损 %.3f（狼大2026-03-05）"
        % (held, d_days, sl, float(pct if pct is not None else _env_float("WOLF_EARLY_STOP_PCT", STOP_PCT_DEFAULT)), sp))


def first_buy_date(account_id: str, symbol: str) -> Optional[str]:
    """该标的**首笔未作废买入**的日期 'YYYY-MM-DD'（建仓日锚点）。

    权威口径 = paper_trades（与 stop_loss_monitor._get_holding_days 同源）；
    无成交流水时退回 paper_positions.entry_date（人工录入持仓的情形）。
    任何异常 → None（调用方退回 stop_loss_price）。
    """
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            v = db.execute(text(
                "SELECT MIN(created_at) FROM paper_trades "
                "WHERE account_id = :a AND symbol = :s AND direction = '买入' "
                "AND (voided = 0 OR voided IS NULL)"),
                {"a": account_id, "s": symbol}).scalar()
            if v:
                return str(v)[:10]
            v2 = db.execute(text(
                "SELECT entry_date FROM paper_positions "
                "WHERE account_id = :a AND symbol = :s"),
                {"a": account_id, "s": symbol}).scalar()
            return str(v2)[:10] if v2 else None
        finally:
            db.close()
    except Exception:
        return None
