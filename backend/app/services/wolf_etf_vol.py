# -*- coding: utf-8 -*-
"""wolf_etf_vol.py — ETF 波动下限（C4 参数对齐，2026-09-15）。

狼大原话（逐字，xls2026）:
> 2026-08-21「选半导体仅仅只是因为他**波动大 ETF都有3个点以上的波动** 不然选个别的1个点的ETF没意思」

口径（他的话 = **3 个点**）:
  · 指标 = 近 **20** 个交易日**日均振幅** `mean((high-low)/close) × 100`（"波动"的可算代理）；
  · 阈值 = **3.0%**（他的数值）；
  · 适用范围 = **ETF**（他这句就是在讲 ETF 选择）。个股**不套用**——② 已三次证明"他的板块级/品种级
    判据搬到个股级会变负"，故本模块只对 ETF 生效，且默认**关闭**（`WOLF_ETF_VOL_GATE=0`）。

数据源：ETF 不在 PG `mkt_bars_daily`（实测 0 行）→ 走中继 `fund_daily`（`ETF_API.md` 同源）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

VOL_THR = 3.0     # 他的话："3个点以上"
VOL_WIN = 20      # 20 日窗口（我们的代理：他说"波动"，没给窗口）


def enabled() -> bool:
    """`WOLF_ETF_VOL_GATE` 默认 0（关）：只影响 ETF 买入，开启是行为变更，待拍板。"""
    return os.getenv("WOLF_ETF_VOL_GATE", "0").strip().lower() not in ("0", "false", "no", "")


def is_etf(symbol: str) -> bool:
    """A 股 ETF 代码段（沪 51x/56x/58x、深 15x/16x）。"""
    s = str(symbol or "").upper().replace("SH", "").replace("SZ", "").strip()
    s = s.split(".")[0]
    return s[:2] in ("51", "56", "58", "15", "16") and len(s) >= 6


def amplitude_pct(bars: List[Dict[str, Any]], n: int = VOL_WIN) -> Optional[float]:
    """近 n 根日线的日均振幅（%）。bars 需含 high/low/close（升序）。数据不足 → None。"""
    use = [b for b in (bars or [])[-n:]
           if b.get("high") is not None and b.get("low") is not None and b.get("close")]
    if len(use) < max(5, n // 2):
        return None
    vals = []
    for b in use:
        try:
            c = float(b["close"])
            if c <= 0:
                continue
            vals.append((float(b["high"]) - float(b["low"])) / c * 100.0)
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else None


def check(bars: List[Dict[str, Any]], thr: float = VOL_THR, n: int = VOL_WIN) -> Tuple[bool, str, Dict[str, Any]]:
    """纯函数：ETF 波动是否达标。数据不足 → **放行**（fail-open，与其他数据门一致）。"""
    amp = amplitude_pct(bars, n)
    if amp is None:
        return True, "ETF 波动数据不足 → 放行", {"amp": None}
    # "3个点**以上**"含相等；浮点下 (1.015-0.985)*100 = 2.9999999999999916 → 加 1e-9 容差
    ok = amp + 1e-9 >= float(thr)
    return ok, "ETF 近%d日日均振幅 %.2f%% %s %.1f%%" % (n, amp, "≥" if ok else "<", thr), {"amp": round(amp, 3)}


def etf_daily(symbol: str, days: int = 40) -> List[Dict[str, Any]]:
    """取 ETF 日线（中继 fund_daily）。失败 → []。"""
    try:
        import importlib
        import sys as _s
        _p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for cand in (os.path.join(_p, "core"),):
            if os.path.exists(os.path.join(cand, "tushare_relay.py")) and cand not in _s.path:
                _s.path.insert(0, cand)
        relay = importlib.import_module("tushare_relay")
        ts = str(symbol).upper().replace("SH", "").replace("SZ", "")
        ts = (ts[:6] + "." + ("SH" if str(symbol).upper().startswith("SH") else "SZ")) if "." not in ts else ts
        fields, items = relay.relay_items("fund_daily", fields="ts_code,trade_date,high,low,close",
                                          ts_code=ts, limit=days)
        idx = {n: i for i, n in enumerate(fields or [])}
        out = []
        for it in items or []:
            try:
                out.append({"trade_date": str(it[idx["trade_date"]]), "high": float(it[idx["high"]]),
                            "low": float(it[idx["low"]]), "close": float(it[idx["close"]])})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(out, key=lambda x: x["trade_date"])
    except Exception as e:
        print("[etf_vol] 取数失败 %s: %s" % (symbol, str(e)[:80]))
        return []


def etf_vol_ok(symbol: str) -> Tuple[bool, str]:
    """生产入口：ETF 波动是否达标（非 ETF 或数据缺失 → 放行）。"""
    if not is_etf(symbol):
        return True, "非 ETF → 波动门不适用"
    ok, why, _ = check(etf_daily(symbol))
    return ok, why
