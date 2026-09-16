# -*- coding: utf-8 -*-
"""wolf_eod.py — 盘后任务共用的「EOD 数据就绪」守卫（2026-09-11）。

**为什么需要它（实测证据）**：2026-09-11 15:46 在生产容器里逐源探测：
    fut_holding / margin / top_inst / daily / limit_list_d 对**当天**全部返回 **0 行**，
    对**前一交易日**分别是 4000 / 3 / 640 / 5549 / 68 行。
→ tushare 的 EOD 数据在收盘后要过一段时间才更新（龙虎榜/两融/期指持仓更晚）。
后果（本轮实际踩到）：
  · `wolf_limit_ladder_scan`（15:35）取不到当日 → `fetch()` 返回 None → **fail-safe 不覆盖旧文件**
    → 表面每天"成功退出"、实际**永远不产出当天数据**（典型的静默失效，被 fail-safe 掩盖）；
  · `wolf_review_score_run`（15:40）当日计分项几乎全缺 → 参评项 < 4 → 恒为"样本不足"；
  · `wolf_theme_resilience`（15:45）当日日线缺失 → 窗口停在昨天（不致命但过期）。
处置：① 三者的 cron 挪到 EOD 之后（18:40 / 18:50 / 19:40）；② 任务内先做**就绪检查**，
   没就绪就**有限等待**（默认 6 次 × 5 分钟 = 30 分钟），仍没就绪则**非 0 退出**交给调度器重试
   （不要像以前那样"假装成功"）；③ 非交易日**直接跳过**（退出 0），避免节假日反复失败。

**2026-09-16 修：盘前任务不能探「当天」日线（探针必须能指向上一交易日）**
    实测（生产）：`daily_decision_am`（08:25）沿用 `gate(d8)` 默认探**当天**
    `pro.daily(trade_date=今天)` → 开盘前当天日线必然是 0 行 → 09-15 / 09-16 两天都是
    5×300s 白等 25 分钟后 rc=2 → **AM 决策对象从未产出**（`data/decision/` 只有盘后对象），
    盘中腿一直回退用前一天的对象；探针事实（09-16 09:34 生产实测）：
        20260914 → 5550 行就绪 / 20260915 → 5548 行就绪 / 20260916 → 0 行。
    → 新增 `probe` 语义：`self`（默认，探 date8 当天 —— 盘后任务用）/
      `last-closed`（探 date8 **之前**最近一个交易日 —— **盘前任务用**）/ `none` / 显式 `YYYYMMDD`。
"""
from __future__ import annotations

import os
import time
from typing import Optional


def today8() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y%m%d")


def is_trade_day(date8: Optional[str] = None) -> Optional[bool]:
    """是否交易日；拿不到交易日历时返回 None（调用方按"未知"处理，不拦）。"""
    try:
        from app.services.t_backtest_data import resolve_trade_days
        d = date8 or today8()
        ds = resolve_trade_days(d, d) or []
        return any(str(x)[:8] == d for x in ds)
    except Exception as e:
        print(f"[eod] 交易日历不可用: {type(e).__name__}: {str(e)[:60]}")
    # 日历不可用 → 工作日近似（周末一定跳过，避免节假日反复"未就绪"重试刷告警）
    try:
        import datetime as _dt
        d = date8 or today8()
        return _dt.datetime.strptime(d, "%Y%m%d").weekday() < 5
    except Exception:
        return None


def eod_ready(date8: Optional[str] = None) -> Optional[bool]:
    """当日 EOD 是否就绪：用 `pro.daily(trade_date=)` 做探针（全市场日线是其它源的先决条件）。

    返回 True/False；探测本身失败 → None（未知，调用方自行决定放行与否）。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        d = date8 or today8()
        df = get_tushare_pro().daily(trade_date=d)
        n = 0 if df is None else len(df)
        print(f"[eod] {d} 全市场日线 {n} 行 → {'就绪' if n > 0 else '未就绪'}")
        return n > 0
    except Exception as e:
        print(f"[eod] 就绪探测失败: {type(e).__name__}: {str(e)[:60]}")
        return None


def wait_ready(date8: Optional[str] = None, tries: Optional[int] = None,
               interval: Optional[float] = None) -> bool:
    """有限等待 EOD 就绪。就绪/未知 → True；明确未就绪且等待耗尽 → False。"""
    d = date8 or today8()
    t = int(tries if tries is not None else os.getenv("WOLF_EOD_TRIES", "6"))
    iv = float(interval if interval is not None else os.getenv("WOLF_EOD_WAIT", "300"))
    for i in range(max(1, t)):
        r = eod_ready(d)
        if r is None or r is True:
            return True
        if i < max(1, t) - 1:
            print(f"[eod] {d} 未就绪，{iv:.0f}s 后重试（{i + 1}/{t}）")
            time.sleep(iv)
    print(f"[eod] {d} 等待耗尽仍未就绪 → 本次不产出（交给调度器重试）")
    return False


def gate(date8: Optional[str] = None, probe: Optional[str] = "self") -> int:
    """任务入口统一调用：返回 0=可以继续；1=跳过（非交易日）；2=未就绪（应重试）。

    `probe` —— 探针要探**哪一天**的日线：
      · `"self"`（默认）= date8 当天 → **盘后**任务用（它们确实要读当天数据）
      · `"last-closed"`  = date8 **之前**最近一个交易日 → **盘前**任务必须用这个
        （08:25 探当天 = 永远 0 行 = 每天白等 30 分钟后失败，2026-09-15/16 实锤）
      · `"none"`         = 不做就绪探测，只保留「非交易日跳过」
      · `"YYYYMMDD"`     = 显式指定
    """
    d = date8 or today8()
    td = is_trade_day(d)
    if td is False:
        print(f"[eod] {d} 非交易日 → 跳过")
        return 1
    p = resolve_probe_date(probe, d)
    if p is None:
        return 0
    if p != d:
        print(f"[eod] 盘前/指定探针：探 {p}（对象日 {d}）的就绪")
    if not wait_ready(p):
        return 2
    return 0


def resolve_probe_date(probe: Optional[str], date8: Optional[str] = None) -> Optional[str]:
    """把 `probe` 语义解析成**实际要探测的日期**；返回 None = 不探测（跳过就绪检查）。"""
    d = date8 or today8()
    if probe is None:
        return d
    p = str(probe).strip()
    low = p.lower()
    if low in ("", "self", "today", "d"):
        return d
    if low in ("none", "skip", "off", "no"):
        return None
    if low in ("last-closed", "last_closed", "lastclosed", "prev", "previous",
               "prev-trade-day", "prev_trade_day"):
        return prev_trade_day(d)
    if len(p) == 8 and p.isdigit():
        return p
    print(f"[eod] 未知 probe={probe!r} → 按 self 处理")
    return d


def prev_trade_day(date8: Optional[str] = None) -> Optional[str]:
    """**严格早于** date8 的最近一个交易日（YYYYMMDD）。

    盘前任务（08:25）要探的正是它：当天日线还没生成，昨天的一定在。
    日历拿不到 → 按工作日近似往回找（周一向回落到周五），保证不返回 None 把任务卡死。
    """
    import datetime as _dt
    d = date8 or today8()
    try:
        base = _dt.datetime.strptime(d, "%Y%m%d")
    except ValueError:
        return None
    try:
        from app.services.t_backtest_data import resolve_trade_days
        start = (base - _dt.timedelta(days=60)).strftime("%Y%m%d")
        ds = resolve_trade_days(start, d) or []
        cands = sorted({str(x)[:8] for x in ds if str(x)[:8] < d})
        if cands:
            return cands[-1]
    except Exception as e:
        print(f"[eod] 取上一交易日失败: {type(e).__name__}: {str(e)[:60]}")
    cur = base - _dt.timedelta(days=1)
    for _ in range(10):
        if cur.weekday() < 5:
            return cur.strftime("%Y%m%d")
        cur -= _dt.timedelta(days=1)
    return None
