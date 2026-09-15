# -*- coding: utf-8 -*-
"""wolf_ma144_regime.py — 「144 线」大级别状态（买入资格**候选**；2026-09-15 round 10 落地为**影子**）。

狼大原话（逐字，见 `docs/wolf-buy-parameter-ledger.md` §5 #6）：
  · 2016-07-07「当K线盘整**144线稍微走平**，那就表明**可以做一个波段趋势**了」
  · 2016-08-15「把周线开出来，均线144都已经**走平向上**了。。太棒了，接下来**找买点进大波段**了」
  · 2026-03-20「我的**牛熊分界线是 日K144线**…那是我的**最后底线**」
  · 2026-03-24「哪怕**破了144三天** 我还是要做到我能接受的位置我才出去」

**离线验收结论（`jobs/eval_ma144_regime.py`，总账 §17）：证据不足以开闸** ——
① 斜率口径被拦日**全在一个连续时段**（2026-08-03→09-11）= 伪显著；② 收盘口径跨 4 段、方向一致但不显著，
且**未与现有大盘门做增量对照**。所以本模块**默认只记录（影子）**，攒跨时段样本后再定。

口径（全部可用开关覆盖；带 ⛔ 的是我们的代理值、不是他的话）：
  · 指数：`WOLF_MA144_INDEX`（默认 `000001.SH` 上证，他的话是"大盘/日K"）
  · 均线：`MA_N=144`（他的话）
  · 走平/向上：`MA144` 的斜率 ≥0，斜率窗口 `WOLF_MA144_SLOPE_WIN`（默认 20 交易日 ⛔代理值）
  · 备选无阈值口径：收盘价 ≥ MA144（他的"牛熊分界线/最后底线"读法）
  · 数据：优先中继 `tushare_relay.index_daily`（日缓存 `data/index_daily_cache_<code>.json`），
    失败则退 `data/index_daily_<code>.json`；**取不到数据一律 fail-open（放行）**，绝不因为读数失败而停买。

开关：`WOLF_MA144_GATE`（默认 **0**，置 1 才真拦买腿）｜`WOLF_MA144_SHADOW`（默认 **1**，只记录）
自检：`python apps/main_line/wolf_ma144_regime.py`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

DATA = os.environ.get("DATA_DIR", "/app/data")
MA_N = 144                      # 他的话（日K144线）


def index_code() -> str:
    return os.getenv("WOLF_MA144_INDEX", "000001.SH").strip() or "000001.SH"


def slope_win() -> int:
    """斜率窗口（交易日）。⛔自设：他只说"走平/向上"，没给量化阈值。"""
    try:
        # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4/§17)
        return max(int(float(os.getenv("WOLF_MA144_SLOPE_WIN", "20"))), 1)
    except (TypeError, ValueError):
        return 20


def gate_enabled() -> bool:
    return os.getenv("WOLF_MA144_GATE", "0").strip() in ("1", "true", "yes")


def shadow_enabled() -> bool:
    return os.getenv("WOLF_MA144_SHADOW", "1").strip() not in ("0", "false", "no")


# ─────────────────────────── 数据层 ───────────────────────────
def _cache_file(code: str) -> str:
    return os.path.join(DATA, "index_daily_cache_%s.json" % code.replace(".", "_"))


def _norm(rows: Any) -> List[Tuple[str, float]]:
    """把两种来源（relay items / index_daily_<code>.json 列表）统一成 [(date8, close)] 升序。"""
    out: List[Tuple[str, float]] = []
    for r in rows or []:
        try:
            if isinstance(r, dict):
                d, c = r.get("trade_date"), r.get("close")
            else:                                     # relay items（fields 顺序固定见下）
                d, c = r[1], r[2]
            if d is None or c is None:
                continue
            out.append((str(d)[:8], float(c)))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def _from_relay(code: str, start: str) -> List[Tuple[str, float]]:
    import importlib
    import sys as _s
    relay = None
    for name in ("tushare_relay",):
        try:
            relay = importlib.import_module(name)
            break
        except ImportError:
            continue
    if relay is None:
        _p = os.path.dirname(os.path.abspath(__file__))
        cur = _p
        for _ in range(6):
            cand = os.path.join(cur, "core")
            if os.path.exists(os.path.join(cand, "tushare_relay.py")):
                if cand not in _s.path:
                    _s.path.insert(0, cand)
                relay = importlib.import_module("tushare_relay")
                break
            cur = os.path.dirname(cur)
    if relay is None:
        return []
    try:
        fields, items = relay.relay_items("index_daily", fields="ts_code,trade_date,close",
                                          ts_code=code, start_date=start,
                                          end_date=time.strftime("%Y%m%d"))
    except Exception as e:                      # 中继不可用/参数不被支持 → 交给上层兜底
        print("[MA144] 中继取指数失败: %s" % str(e)[:100])
        return []
    return _norm(items or [])


def closes(code: Optional[str] = None, refresh: bool = True) -> List[Tuple[str, float]]:
    """指数收盘序列（升序）。优先用当日缓存；缓存不含今天则尝试中继刷新一次。"""
    code = code or index_code()
    cf = _cache_file(code)
    cached: List[Tuple[str, float]] = []
    try:
        cached = _norm(json.load(open(cf, encoding="utf-8")))
    except Exception:
        cached = []
    today8 = time.strftime("%Y%m%d")
    if cached and (not refresh or cached[-1][0] >= today8):
        return cached
    start = cached[0][0] if cached else "20240101"
    try:
        fresh = _from_relay(code, start)
    except Exception as e:
        print("[MA144] 中继异常: %s" % str(e)[:100])
        fresh = []
    if fresh:
        merged = {d: c for d, c in cached}
        merged.update({d: c for d, c in fresh})
        rows = sorted(merged.items())
        try:
            os.makedirs(DATA, exist_ok=True)
            tmp = cf + ".tmp"
            json.dump([{"trade_date": d, "close": c} for d, c in rows], open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, cf)
        except Exception as e:
            print("[MA144] 缓存写盘失败: %s" % str(e)[:80])
        return rows
    if cached:
        return cached
    # 兜底：生产自带的指数文件（data/index_daily_<code>.json）
    try:
        p = os.path.join(DATA, "index_daily_%s.json" % code.split(".")[0])
        return _norm(json.load(open(p, encoding="utf-8")))
    except Exception:
        return []


# ─────────────────────────── 状态层 ───────────────────────────
def state_from(rows: List[Tuple[str, float]], as_of: Optional[str] = None,
               win: Optional[int] = None) -> Dict[str, Any]:
    """纯函数：算 MA144 / 斜率 / 是否在 MA144 上方（严格 PIT：只用 ≤ as_of 的收盘）。"""
    win = win or slope_win()
    seq = [(d, c) for d, c in rows if (as_of is None or d <= str(as_of))]
    if len(seq) < MA_N:
        return {"date": as_of or (seq[-1][0] if seq else None), "ok": False, "n": len(seq),
                "why": "序列不足 %d 天" % MA_N}
    ma = sum(c for _, c in seq[-MA_N:]) / MA_N
    slope = None
    if len(seq) >= MA_N + win:
        ma0 = sum(c for _, c in seq[-MA_N - win:-win]) / MA_N
        if ma0:
            slope = (ma / ma0 - 1.0) * 100.0
    close = seq[-1][1]
    return {"date": seq[-1][0], "ok": True, "n": len(seq), "close": round(close, 2),
            "ma144": round(ma, 2), "slope_pct": None if slope is None else round(slope, 3),
            "slope_win": win, "above": bool(close >= ma),
            "allow_slope": None if slope is None else bool(slope >= 0)}


def state(as_of: Optional[str] = None) -> Dict[str, Any]:
    return state_from(closes(), as_of=as_of)


def gate_mode() -> str:
    """闸门口径：`WOLF_MA144_MODE`（2026-09-15 用户拍板改挂 ②）。

    · `above`（**默认，现用**）= **收盘 ≥ MA144**（他的话「我的牛熊分界线是 日K144线…那是我的最后底线」2026-03-20）；
    · `slope`（旧口径）= 斜率(20天) ≥ 0 为主，斜率算不出才退收盘 —— 该口径经组合级对照实测
      **与"关门"几乎无差别（+2.7pp、回撤不变）**，且属伪显著（被拦日全在 2026-08-03→09-11 一段）→ 已停用。
    """
    return (os.getenv("WOLF_MA144_MODE", "above").strip().lower() or "above")


def allow(st: Optional[Dict[str, Any]] = None) -> bool:
    """闸门语义：**数据缺失一律放行**（fail-open）。

    默认口径 = ② **收盘 ≥ MA144**（`WOLF_MA144_MODE=above`）；置 `slope` 可回到旧的斜率口径。
    """
    st = st if st is not None else state()
    if not st.get("ok"):
        return True
    if gate_mode() == "slope":
        if st.get("allow_slope") is None:
            return bool(st.get("above", True))
        return bool(st["allow_slope"])
    return bool(st.get("above", True))


def shadow_record(extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """影子：把当日 144 状态 + **当日其它门的状态**写进 `data/ma144_shadow_<date>.json`。

    记其它门是为了下一轮做**增量对照**（144 拦掉的日子里，有多少是现有大盘门已经在拦的）——
    只有"增量部分"才值得做成门（见总账 §17）。
    """
    if not shadow_enabled():
        return None
    st = state()
    d8 = str(st.get("date") or time.strftime("%Y%m%d"))
    fn = os.path.join(DATA, "ma144_shadow_%s.json" % d8)
    rec = {"date": d8, "mode": "shadow", "index": index_code(), "state": st,
           "extra": extra or {}, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = fn + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fn)
    except Exception as e:
        print("[MA144] 影子写盘失败: %s" % str(e)[:80])
        return None
    return fn


if __name__ == "__main__":
    print(json.dumps({"index": index_code(), "gate": gate_enabled(), "shadow": shadow_enabled(),
                      "slope_win": slope_win(), "state": state()}, ensure_ascii=False, indent=1))
