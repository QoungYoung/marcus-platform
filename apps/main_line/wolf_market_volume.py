# -*- coding: utf-8 -*-
"""wolf_market_volume.py — **大盘级"缩量/地量"**（C1 的前置条件；2026-09-15 round 22，纯影子）。

背景（总账 §31：自设参数全表复查的发现 #11）：他讲"缩量/地量"用的是**市场绝对成交额**，不是个股量比：
  · 2025-08-04「有点出乎我的意料了 所以先不能买，等 **1.5WE 这个地量**走几天，震荡到出现**负地量**就能重新起涨了」
  · 2026-02-11「**缩量到 2WE 以下**了 这里怕什么 怕大跌」
  · 2025-05-26「而且现在**缩量到 1WE**，要想直接出个放量大黑K也不现实」
  · 2026-02-02「**地量后**…大盘没过前低是前提, 观察板块/个股也没低于前低是基础条件」
  → 我们的 C1（254 触发）用的是"**个股 5 分钟量比 ≤ 0.9**"，属**层级错**（他的话在大盘级）。

口径（阈值全部来自他的话，无自设数值）：
  · 两市成交额 = 上证综指 `000001.SH` + 深证综指 `399106.SZ` 的 `index_daily.amount`（千元）之和
    —— 实测 2026-09-14：7793亿 + 8499亿 = **1.63WE**，与他的 1.5WE/2WE 同量级 ✓；
  · 分档（他的原话）：`<1WE` 极地量 / `1–1.5WE` **地量** / `1.5–2WE` **缩量** / `≥2WE` 常态；
  · `shrink_vs_prev`：今日 < 昨日（"缩量"的日间口径）。

**本模块只做测量 + 影子**（`data/market_vol_shadow_<date>.json`），**不设闸门、不改任何决策** ——
"地量才买"这个语义要怎么落到买腿上（拦？放宽？只做提示？）需要用户拍板；先把事实记下来。
数据缺失一律返回 `ok=False`（不猜）。

自检：`python apps/main_line/wolf_market_volume.py`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

DATA = os.environ.get("DATA_DIR", "/app/data")
CODES: Tuple[str, ...] = ("000001.SH", "399106.SZ")     # 上证综指 + 深证综指（两市）
# 他的话里的档位（万亿）：<1 极地量 / 1–1.5 地量 / 1.5–2 缩量 / ≥2 常态
BUCKETS: Tuple[Tuple[float, str], ...] = ((1.0, "极地量(<1WE)"), (1.5, "地量(1-1.5WE)"),
                                          (2.0, "缩量(1.5-2WE)"), (1e9, "常态(≥2WE)"))


def enabled() -> bool:
    """本模块无闸门语义；保留开关只为将来把"地量→买腿"落成规则时用（默认 0）。"""
    return os.getenv("WOLF_MARKET_VOL_GATE", "0").strip() in ("1", "true", "yes")


def shadow_enabled() -> bool:
    return os.getenv("WOLF_MARKET_VOL_SHADOW", "1").strip() not in ("0", "false", "no")


def _cache_file() -> str:
    return os.path.join(DATA, "market_vol_cache.json")


def _from_relay(start: str) -> Dict[str, List[Tuple[str, float]]]:
    import importlib
    import sys as _s
    relay = None
    try:
        relay = importlib.import_module("tushare_relay")
    except ImportError:
        cur = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            cand = os.path.join(cur, "core")
            if os.path.exists(os.path.join(cand, "tushare_relay.py")):
                if cand not in _s.path:
                    _s.path.insert(0, cand)
                relay = importlib.import_module("tushare_relay")
                break
            cur = os.path.dirname(cur)
    if relay is None:
        return {}
    out: Dict[str, List[Tuple[str, float]]] = {}
    for code in CODES:
        try:
            _f, items = relay.relay_items("index_daily", fields="ts_code,trade_date,amount",
                                          ts_code=code, start_date=start, end_date=time.strftime("%Y%m%d"))
            rows = sorted([(str(x[1])[:8], float(x[2] or 0)) for x in items if x[2] is not None],
                          key=lambda r: r[0])
            if rows:
                out[code] = rows
        except Exception as e:
            print("[MktVol] 中继取 %s 失败: %s" % (code, str(e)[:80]))
    return out


def series(refresh: bool = True) -> Dict[str, List[Tuple[str, float]]]:
    """→ {code: [(date8, amount千元)]}（缓存合并；取不到就返回缓存）。"""
    cf = _cache_file()
    cached: Dict[str, List[Tuple[str, float]]] = {}
    try:
        raw = json.load(open(cf, encoding="utf-8"))
        for code, rows in (raw or {}).items():
            cached[code] = [(str(d)[:8], float(a)) for d, a in rows]
    except Exception:
        cached = {}
    last = max((r[-1][0] for r in cached.values() if r), default="")
    today8 = time.strftime("%Y%m%d")
    if cached and (not refresh or last >= today8):
        return cached
    start = min((r[0][0] for r in cached.values() if r), default="20250101")
    fresh = _from_relay(start)
    if fresh:
        merged = {c: dict(rows) for c, rows in cached.items()}
        for c, rows in fresh.items():
            merged.setdefault(c, {}).update(dict(rows))
        out = {c: sorted(d.items()) for c, d in merged.items()}
        try:
            os.makedirs(DATA, exist_ok=True)
            tmp = cf + ".tmp"
            json.dump({c: rows for c, rows in out.items()}, open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, cf)
        except Exception as e:
            print("[MktVol] 缓存写盘失败: %s" % str(e)[:80])
        return out
    return cached


def bucket_of(we: Optional[float]) -> Optional[str]:
    if we is None:
        return None
    for thr, name in BUCKETS:
        if we < thr:
            return name
    return None


def state(ser: Optional[Dict[str, List[Tuple[str, float]]]] = None) -> Dict[str, Any]:
    ser = ser if ser is not None else series()
    if len(ser) < len(CODES):
        return {"ok": False, "why": "两市指数成交额取不全（需要 %s）" % ",".join(CODES)}
    dates = sorted(set.intersection(*[set(d for d, _ in ser[c]) for c in CODES]))
    if len(dates) < 2:
        return {"ok": False, "why": "两市共同交易日不足 2 天"}
    d, dprev = dates[-1], dates[-2]
    per = {c: {x: a for x, a in ser[c]} for c in CODES}
    tot = sum(per[c][d] for c in CODES) / 1e5      # 千元 → 亿元（1 亿元 = 1e5 千元）
    prev = sum(per[c][dprev] for c in CODES) / 1e5
    we, we_prev = tot / 1e4, prev / 1e4            # 亿元 → 万亿（1 万亿 = 1e4 亿元）
    return {"ok": True, "date": d, "prev_date": dprev,
            "sh_yi": round(per["000001.SH"][d] / 1e5, 1), "sz_yi": round(per["399106.SZ"][d] / 1e5, 1),
            "total_yi": round(tot, 1), "we": round(we, 3), "we_prev": round(we_prev, 3),
            "ratio_vs_prev": round(we / we_prev, 3) if we_prev else None,
            "shrink_vs_prev": bool(we < we_prev) if we_prev else None,
            "bucket": bucket_of(we)}


def shadow_record(extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if not shadow_enabled():
        return None
    st = state()
    d8 = str(st.get("date") or time.strftime("%Y%m%d"))
    fn = os.path.join(DATA, "market_vol_shadow_%s.json" % d8)
    rec = {"date": d8, "mode": "shadow", "state": st, "extra": extra or {},
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = fn + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fn)
    except Exception as e:
        print("[MktVol] 影子写盘失败: %s" % str(e)[:80])
        return None
    return fn


if __name__ == "__main__":
    print(json.dumps({"gate": enabled(), "shadow": shadow_enabled(), "state": state()},
                     ensure_ascii=False, indent=1))
