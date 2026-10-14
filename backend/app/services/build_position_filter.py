# -*- coding: utf-8 -*-
"""build_position_filter.py — **选股端**的位置/涨幅闸（§9.844，用户 2026-10-13「把位置/涨幅条件前移到选股端」✓）

【为什么要前移 ✗】下游闸（`t_monitor._build_chase_block` ✓ §9.843）已能拦住"贴新高/已急涨"的建仓腿 ✓，
  但实测（t47 臂头 5 天 ✓）：**腿照样产生、全被拦** ✗ ⇒ 0 成交 ✓ —— 相当于"用下游闸替上游选股擦屁股" ✗。
  正确做法 ✓：**在选股/布腿这一步就不产生**这类腿 ✓（少而精 ✓），下游闸保留做**兜底** ✓。

【口径 ✓（与下游闸同源，便于 A/B 对照 ✓）】
  · `WOLF_BUILD_NEAR_HIGH_PCT`（默认 **0 = 关** ✓；标定值 2）⇒ 现价距 250 日高 > −2% ⇒ 不选 ✓
  · `WOLF_BUILD_GAIN20_MAX`（默认 **0 = 关** ✓；标定值 30）⇒ 20 日涨幅 > 30% ⇒ 不选 ✓
  标定出处 ✓：`scripts/calib_chase_position2.py`（1796 笔真实买入 ✓）——
    距 250 日高 −2~0%（贴新高）⇒ +10d 中位 −7.19%、胜率 22.0%（n=59 ✓）
    20 日涨幅 >30% ⇒ +10d 中位 −4.45%、胜率 36.9%、MAE −8.93%（n=195 ✓）
    对照「回调里买」（涨幅<0）⇒ +0.83% / 52.8%（n=286 ✓）

【数据与 as-of ✓】读 `$DATA_DIR/mkt_bars_local.db`（跑批天目录里那份 ✓）⇒ 退化到 `data/_bt_full/bars.sqlite` ✓；
  **只用 ≤ 传入 day 的行** ✓（防未来函数 ✗）；取不到数据 ⇒ **放行**（fail-open ✓，绝不因此漏建仓 ✓）
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Optional, Tuple


def thresholds() -> Tuple[float, float]:
    def _f(name: str) -> float:
        try:
            return abs(float(os.getenv(name, "0") or 0))
        except Exception:
            return 0.0
    return _f("WOLF_BUILD_NEAR_HIGH_PCT"), _f("WOLF_BUILD_GAIN20_MAX")


def _ts_code(sym: str) -> str:
    s = str(sym or "").strip().upper()
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", s)
    if m:
        return "%s.%s" % (m.group(2), m.group(1))
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return s
    m = re.match(r"^(\d{6})$", s)
    if m:
        c = m.group(1)
        return "%s.%s" % (c, "SH" if c[0] == "6" else ("BJ" if c[:2] in ("43", "83", "87", "92") else "SZ"))
    return ""


def _db_paths() -> list:
    out = []
    dd = os.environ.get("DATA_DIR")
    if dd:
        out.append(os.path.join(dd, "mkt_bars_local.db"))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out += [os.path.join(here, "data", "_bt_full", "bars.sqlite"), "data/_bt_full/bars.sqlite"]
    return [p for p in out if p and os.path.exists(p)]


def block_reason(symbol: str, day: Optional[str] = None, price: Optional[float] = None) -> str:
    """返回拦阻原因；"" = 放行 ✓（开关关 / 缺数 / 异常 ⇒ 一律放行 ✓）"""
    near, gmax = thresholds()
    if near <= 0 and gmax <= 0:
        return ""
    ts = _ts_code(symbol)
    if not ts:
        return ""
    d8 = "".join(ch for ch in str(day or "") if ch.isdigit())[:8]
    for db in _db_paths():
        try:
            cn = sqlite3.connect(db)
            cur = cn.cursor()
            if d8:
                rows = list(cur.execute(
                    "select trade_date, close, high from mkt_bars_daily where ts_code=? and trade_date<=? "
                    "order by trade_date desc limit 260", (ts, d8)))
                if not rows:
                    rows = list(cur.execute(
                        "select trade_date, close, high from bars where ts_code=? and trade_date<=? "
                        "order by trade_date desc limit 260", (ts, d8)))
            else:
                rows = []
            cn.close()
        except Exception:
            continue
        if len(rows) < 25:
            continue
        rows = list(reversed(rows))                     # 升序 ✓
        try:
            hi250 = max(float(r[2] or r[1] or 0) for r in rows[-250:])
            px = float(price) if price else float(rows[-1][1] or 0)
            c20 = float(rows[-21][1] or 0)
        except Exception:
            continue
        if px <= 0 or hi250 <= 0 or c20 <= 0:
            continue
        if near > 0:
            d = (px / hi250 - 1) * 100
            if d > -near:
                return ("[SEL-POS] 距 250 日高仅 %+.2f%%（> −%.1f%%）⇒ **选股端不选** ✓"
                        "（标定：该档 +10d 中位 −7.19%%、胜率 22%% ✓）" % (d, near))
        if gmax > 0:
            g = (px / c20 - 1) * 100
            if g > gmax:
                return ("[SEL-GAIN] 20 日已涨 %.1f%%（> %.0f%%）⇒ **选股端不选** ✓"
                        "（标定：该档 +10d 中位 −4.45%%、胜率 36.9%%、MAE −8.93%% ✓）" % (g, gmax))
        return ""
    return ""
