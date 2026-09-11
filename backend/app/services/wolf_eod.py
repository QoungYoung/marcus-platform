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


def gate(date8: Optional[str] = None) -> int:
    """任务入口统一调用：返回 0=可以继续；1=跳过（非交易日）；2=未就绪（应重试）。"""
    d = date8 or today8()
    td = is_trade_day(d)
    if td is False:
        print(f"[eod] {d} 非交易日 → 跳过")
        return 1
    if not wait_ready(d):
        return 2
    return 0
