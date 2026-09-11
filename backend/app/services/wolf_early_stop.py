# -*- coding: utf-8 -*-
"""个股波段逻辑止损（狼大止损六层之①）+ 建仓初期 / 趋势中段的阶段切换（六层之④口径）。

────────────────────────────────────────────────────────────────
狼大原话（语料，2026-03-05，**完整一段**）:
  「我说一下我用的 **买入有时间** 然后**13日内跌破波段低点的-3%没有收回 直接止损**，
    **13日内需要碰新高或者新高。否则这个票呆的意义就不大，证明自己的买入逻辑和时间有问题**。
    按我0.618买入的情况下 这样止损就是-6%左右 是可以接受的。」

狼大原话（语料，2026-03-05，回答「-3%再卖是因为破了-3%就是有效跌破了吧」——**
  **这就是「无利空」前提的出处**）:
  「是**自己逻辑**的有效跌破 **除非是意外事件，黑天鹅那种**。如果是**无利空**13日内下跌
    那新低后-3%就是逻辑问题 要控制损失就必须止损。后面涨是别的逻辑 就比如今天京东方A大涨
    你说是他本身的逻辑么？并不是啊 所以意外事件导致的上涨是路上捡到钱了，也不是自己的逻辑」

  → 「无利空」的**正确语义**（与直觉相反，必须照原话）:
      · **无利空** → 跌破新低后 -3% = **自己的买入逻辑被证伪** → **必须止损**（本规则适用）；
      · **有利空 / 意外事件 / 黑天鹅** → 那不是自己逻辑的问题，跌下去也可能因为"别的逻辑"涨回来
        → **不按此结构线止损**（退回 stop_loss_price 兜底，而不是放大风险）。

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

**「无利空」前提**（本模块 `negative_event()` 读标记；`resolve_stop(..., neg_event=/symbol=)` 应用）:
  见上文原话 —— 标记为"意外事件/黑天鹅"的标的不套用 ① 的结构线，退回 stop_loss_price。
  数据来源：本仓**拿不到可靠的个股利空/公告面数据**，故做成**显式输入**
  `data/wolf_negative_events.json`，格式 `{"SH600000": {"date": "20260910", "note": "…"}}`，
  由人工或上游 AI 标记；**持有期内**（事件日 >= 建仓日）才算，且超过 `WOLF_NEG_EVENT_DAYS`
  （默认 13，与建仓初期同窗）后自动失效。
  误标代价可控：只是不再用结构线止损，仍有 stop_loss_price 兜底。

开关:
  · `WOLF_EARLY_STOP=0`          → 关闭本模块（退回"一律用 stop_loss_price"）；
  · `WOLF_EARLY_STOP_DAYS=13`    → 建仓初期窗口（交易日）；
  · `WOLF_SWING_LOW_WIN=13`      → 波段低点回看窗口（交易日）；
  · `WOLF_EARLY_STOP_PCT=3`      → 波段低点下方几个百分点（狼大原话 -3%）；
  · `WOLF_NEG_EVENT=0`           → 忽略"无利空"前提（一律按 ① 执行，即永远当作无利空）。
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


def _norm_sym(s: Any) -> str:
    """符号归一：'SH600000' / '600000.SH' → 'SH600000'（去空格/大写）。"""
    return str(s or "").replace(" ", "").upper()


def _digits(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())[:6]


NEG_EVENT_FILE = "wolf_negative_events.json"


def negative_event(symbol: Any, buy_date: Any = None, today: Any = None,
                   max_age_days: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """该标的是否有**意外事件/黑天鹅级别**的利空 → 记录 dict 或 None。

    狼大原话（2026-03-05，见模块头）: 「是自己逻辑的有效跌破 **除非是意外事件，黑天鹅那种**。
    如果是**无利空**13日内下跌那新低后-3%就是逻辑问题 要控制损失就必须止损。后面涨是别的逻辑」
    → 所以本函数返回**非 None** 时的语义是「**不要**套用 ① 的结构止损线」。

    数据源: `data/wolf_negative_events.json`
      {"SH600000": {"date": "20260910", "note": "突发利空…"}, ...}
      · `date` = 事件发生日（必填，用于"是否在持有期内"与过期判定）；
      · `note` = 可选备注（会出现在日志 reason 里，便于人工核对）。
    只认**持有期内**（事件日 >= 建仓日）的事件 —— 建仓前就有的利空不属于"持有期内的意外"，
    那种情况该在建仓决策层解决。超过 `WOLF_NEG_EVENT_DAYS`（默认 13，与建仓初期同窗）自动失效。

    `WOLF_NEG_EVENT=0` → 恒返回 None（忽略该前提，永远按"无利空"执行）。
    任何异常 → None（**fail-open 到"无利空"**，即仍按 ① 止损；不能因为读文件失败就不止损）。
    """
    if os.getenv("WOLF_NEG_EVENT", "1").strip() in ("0", "false", "no"):
        return None
    try:
        import json
        p = os.path.join(os.environ.get("DATA_DIR", "/app/data"), NEG_EVENT_FILE)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
        if not isinstance(d, dict) or not d:
            return None
        sym = _norm_sym(symbol)
        rec = d.get(sym)
        if rec is None:                       # 容错: 允许 '600000.SH' 之类的键
            for k, v in d.items():
                if _digits(k) and _digits(k) == _digits(sym):
                    rec = v
                    break
        if not isinstance(rec, dict):
            return None
        ed = _norm_date(rec.get("date"))
        if not ed:
            return None
        bd = _norm_date(buy_date)
        if bd and ed < bd:                    # 建仓前的利空 → 不属于"持有期内的意外事件"
            return None
        n = int(max_age_days if max_age_days is not None
                else _env_int("WOLF_NEG_EVENT_DAYS", EARLY_STAGE_DAYS_DEFAULT))
        td = _norm_date(today)
        if td and n >= 0:
            import datetime as _dt
            try:
                d1 = _dt.date(int(ed[:4]), int(ed[4:6]), int(ed[6:8]))
                d2 = _dt.date(int(td[:4]), int(td[4:6]), int(td[6:8]))
                if (d2 - d1).days > n:
                    return None
            except Exception:
                pass
        return {"date": ed, "note": str(rec.get("note") or "")}
    except Exception:
        return None


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


def swing_high_asof(bars: List[Dict[str, Any]], buy_date: Any, win: Optional[int] = None) -> Optional[float]:
    """**建仓时点的前高**: 建仓日（含）之前 win 根日K 的**最高价**（与 swing_low_asof 对称）。

    狼大 2026-03-05 语境: 他「参考 618 点位建仓、786 去补仓」——目标就是回到并突破
    回调之前那个高点（"新高"）。故前高 = 建仓日之前那段的高点。
    数据不足 MIN_SWING_BARS 根 → None（调用方不动作，详情见 made_new_high）。
    """
    w = int(win if win is not None else _env_int("WOLF_SWING_HIGH_WIN", SWING_WIN_DEFAULT))
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
            hi = float(b.get("high") or 0)
        except Exception:
            continue
        if hi > 0:
            rows.append((d, hi))
    rows.sort(key=lambda x: x[0])
    rows = rows[-w:]
    if len(rows) < MIN_SWING_BARS:
        return None
    return max(hi for _, hi in rows)


def made_new_high(bars: List[Dict[str, Any]], buy_date: Any, win: Optional[int] = None,
                  days: Optional[int] = None, extra_high: Optional[float] = None) -> Optional[bool]:
    """建仓后 `days`（默认 WOLF_LOGIC_TIME_STOP_DAYS=13）个交易日内**是否碰过/创过前高**。

    `extra_high`: 当日**盘中最高**（腾讯 quote 的 high）。
      必须传 —— 否则窗口最后一天的"盘中碰新高"会被漏判，白白多拿一天再卖。
    返回:
      · True  = 窗口内碰过前高（或窗口尚未走完但已碰过）→ 买入逻辑成立;
      · False = 窗口已走完且**从未**碰过前高 → 逻辑/时间有问题 → 离场;
      · None  = 数据不足（前高算不出）→ **不动作**（fail-open 到"继续持有"，不凭缺失数据卖票）。
    """
    bd = _norm_date(buy_date)
    if not bd:
        return None
    ph = swing_high_asof(bars, buy_date, win)
    if ph is None:
        return None
    n = int(days if days is not None else _env_int("WOLF_LOGIC_TIME_STOP_DAYS", EARLY_STAGE_DAYS_DEFAULT))
    rows = []
    for b in bars or []:
        d = _norm_date(b.get("date"))
        if not d or d <= bd:
            continue
        try:
            hi = float(b.get("high") or 0)
        except Exception:
            continue
        rows.append((d, hi))
    rows.sort(key=lambda x: x[0])
    try:
        eh = float(extra_high or 0)
    except Exception:
        eh = 0.0
    touched = any(hi >= ph for _, hi in rows)
    if eh > 0 and eh >= ph:
        touched = True
    if touched:
        return True
    return False if len(rows) >= n else None


def logic_time_stop(bars: List[Dict[str, Any]], buy_date: Any,
                    days: Optional[int] = None, win: Optional[int] = None,
                    extra_high: Optional[float] = None,
                    neg_event: Optional[Dict[str, Any]] = None,
                    symbol: Any = None, today: Any = None) -> Tuple[bool, str]:
    """**建仓初期「逻辑与时间」离场**（狼大止损六层之① 的**后半句**）→ (should_exit, reason)。

    狼大原话（2026-03-05，与 -3% 那条**同一句**）:
      「我说一下我用的 **买入有时间** 然后13日内跌破波段低点的-3%没有收回 直接止损，
        **13日内需要碰新高或者新高。否则这个票呆的意义就不大，证明自己的买入逻辑和时间有问题**。
        按我0.618买入的情况下 这样止损就是-6%左右 是可以接受的。」

    → 语义: 建仓后的 13 个交易日是一次**观察窗**; 窗口内既没碰新高、也没跌破波段低点-3%的票,
      「呆的意义就不大」→ **离场**（不是等它慢慢跌到止损线）。
      这是**时间/逻辑维度**的离场, 与 ① 的价格止损互补, 共用同一个 13 日窗口。

    口径说明:
      · 前高 = 建仓日（含）之前 `WOLF_SWING_HIGH_WIN`（默认 13）根日K 的最高价（与波段低点同窗同源）;
      · 「碰新高或者新高」按原话取 **>= 前高**（碰即算, 不要求严格突破）; 盘中最高也计入;
      · **窗口未走完（持有 < days）不动作** —— 不能买了三天没新高就卖;
      · 数据不足（前高算不出）→ **不动作**（fail-open, 不凭缺失数据卖票）。

    「无利空」前提: 原话里该前提是针对 -3% 那条说的; 对新高要求属**同窗逻辑的外推**
      （狼大对黑天鹅的一贯态度是"那是别的逻辑"，见 `negative_event`）→ 同样豁免。
      不认同这个外推的话，`WOLF_NEG_EVENT=0` 会把两条一起关掉。

    开关: `WOLF_LOGIC_TIME_STOP=0` 关闭本机制。
    """
    if os.getenv("WOLF_LOGIC_TIME_STOP", "1").strip() in ("0", "false", "no"):
        return False, "WOLF_LOGIC_TIME_STOP=0 → 不动作"
    ne = neg_event
    if ne is None and symbol is not None:
        ne = negative_event(symbol, buy_date=buy_date, today=today)
    if ne:
        return False, ("有利空/意外事件(%s) → 不按逻辑时间离场（狼大: 那是\"别的逻辑\"）"
                       % ne.get("date"))
    d = int(days if days is not None else _env_int("WOLF_LOGIC_TIME_STOP_DAYS", EARLY_STAGE_DAYS_DEFAULT))
    # reason 必须区分三种"不动作"（生产实测踩过: 把"窗口未走完"说成"前高算不出"会误导排查）
    if swing_high_asof(bars, buy_date, win) is None:
        return False, "前高数据不足（建仓日之前不足 %d 根日K）→ 不动作" % (
            int(win if win is not None else _env_int("WOLF_SWING_HIGH_WIN", SWING_WIN_DEFAULT)))
    m = made_new_high(bars, buy_date, win=win, days=d, extra_high=extra_high)
    if m is True:
        return False, "窗口内已碰过前高 → 买入逻辑成立, 继续持有"
    if m is None:                      # 未碰前高 **且** 窗口还没走完
        _h = held_trading_days(bars, buy_date)
        return False, "观察窗未走完（已持有 %s < %d 交易日）→ 不动作" % (
            "?" if _h is None else _h, d)
    return True, ("建仓后 %d 个交易日内**从未碰过前高**(狼大2026-03-05「13日内需要碰新高或者新高。"
                  "否则这个票呆的意义就不大，证明自己的买入逻辑和时间有问题」) → 离场" % d)


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
                 pct: Optional[float] = None,
                 neg_event: Optional[Dict[str, Any]] = None,
                 symbol: Any = None,
                 today: Any = None) -> Tuple[Optional[float], str, str]:
    """阶段化止损线解析 → (stop_price, source, reason)。

    source ∈ {'wolf_early_swing'（① 建仓初期波段逻辑止损）,
              'neg_event'（①被"无利空"前提豁免 → 退回 stop_loss_price）,
              'stop_loss_price'（④ 趋势中段: 既有口径）, 'none'}
    · 建仓初期（held <= WOLF_EARLY_STOP_DAYS）且有波段低点 → 狼大 ① 结构止损；
    · 但该标的有**意外事件/黑天鹅级利空**（持有期内）→ **不用结构线**，退回 cond_stop
      （狼大 2026-03-05: 「是自己逻辑的有效跌破 **除非是意外事件，黑天鹅那种**…如果是**无利空**…就必须止损」）；
    · 其它（已成趋势 / 无波段低点 / 无建仓日 / 关闭）→ 交回 cond_stop（既有 stop_loss_price）。

    `neg_event`: 调用方可**显式传入**已取到的事件记录（便于测试/复用）；
                 传 None 且给了 `symbol` → 自动读模块函数 `negative_event()`。
    `today` 仅用于事件过期判定。

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
    # ── 「无利空」前提: 有意外事件/黑天鹅 → 不套结构线（狼大 2026-03-05 原话）──
    ne = neg_event
    if ne is None and symbol is not None:
        ne = negative_event(symbol, buy_date=buy_date, today=today)
    if ne:
        return (cs or None), ("neg_event" if cs else "none"), (
            "有利空/意外事件(%s%s) → 不套建仓初期结构线, 退回 stop_loss_price"
            "（狼大2026-03-05: 那是\"别的逻辑\", 非自己逻辑被证伪）"
            % (ne.get("date"), ("·" + ne["note"]) if ne.get("note") else ""))
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
