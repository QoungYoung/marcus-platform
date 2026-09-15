# -*- coding: utf-8 -*-
"""wolf_line_regime.py — 「白线在上 ∧ 缩量 → 没有买点」的**买入资格**（2026-09-15 round 17 落地为影子）。

狼大原话（XLS 全表定向抽取，总账 §24.2，多时点一致）：
  · 2025-07-15「这种**缩量 白线在上**的局**千万别没事加仓**，太重的逢高减减，**60%仓位内**就行了」
  · 2026-03-03「**缩量**反弹到4141-4150区间上不去并且**白线在上**，那就把今天抄底的有盈利的T出去，尽量保持 **70-75%仓位**」
  · 2026-03-25「如果明天冲3950以上的时候发现**缩量 白线在上** 我会**减回50%内** 安全第一」
  · 2026-04-09「**今天白线在上 肯定没有买点** 下午不管涨还是跌 我都会找个地方**减回50%-55%仓位**」
  → 规则：**白线在上（权重强于小票）∧ 缩量 ⇒ 当日没有买点 / 不加仓**。

离线验收（`jobs/eval_line_regime.py`，总账 §26）：**方向一致、跨 19 段/24 周，但周级显著度不足**
  · 253 腿：他说"没买点"的日子 **−1.556%**（n=608，t −3.60，周块状 t −1.35）vs 放行 **+0.157%**；
  · 254 腿：−1.226%（n=426）vs −0.450%；
  · 周内配对：**16/24 周为负**，周差均值 −1.277pp，周级 t **−1.19**（不显著）。
  → 所以**默认只记录（影子）**；`WOLF_LINE_REGIME_GATE=1` 才真拦买腿（待拍板）。

口径（带 ⛔ 的是我们的代理，他没给量化）：
  · **黄白线**：复用生产 A4 `wolf_index_breadth.huang_bai()`（`side='bai'` 即白线在上）；
  · **缩量**：指数（默认 `000001.SH`）当日成交额 < 前一交易日成交额（他只用"缩量"两个字，无阈值⛔）；
  · 数据：中继 `index_daily`（含 amount），日缓存 `data/index_amount_cache_<code>.json`；**取不到一律 fail-open**。

开关：`WOLF_LINE_REGIME_GATE`（默认 **0**）｜`WOLF_LINE_REGIME_SHADOW`（默认 **1**）
自检：`python apps/main_line/wolf_line_regime.py`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

DATA = os.environ.get("DATA_DIR", "/app/data")


def index_code() -> str:
    return os.getenv("WOLF_LINE_REGIME_INDEX", "000001.SH").strip() or "000001.SH"


def gate_enabled() -> bool:
    return os.getenv("WOLF_LINE_REGIME_GATE", "0").strip() in ("1", "true", "yes")


def shadow_enabled() -> bool:
    return os.getenv("WOLF_LINE_REGIME_SHADOW", "1").strip() not in ("0", "false", "no")


# ─────────────────────────── 数据层 ───────────────────────────
def _cache_file(code: str) -> str:
    return os.path.join(DATA, "index_amount_cache_%s.json" % code.replace(".", "_"))


def _norm(rows: Any) -> List[Tuple[str, float, float]]:
    """→ [(date8, close, amount)] 升序；兼容 dict 与 relay items 两种形态。"""
    out: List[Tuple[str, float, float]] = []
    for r in rows or []:
        try:
            if isinstance(r, dict):
                d, c, a = r.get("trade_date"), r.get("close"), r.get("amount")
            else:
                d, c, a = r[1], r[2], (r[3] if len(r) > 3 else None)
            if d is None or c is None:
                continue
            out.append((str(d)[:8], float(c), float(a or 0)))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def _from_relay(code: str, start: str) -> List[Tuple[str, float, float]]:
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
        return []
    try:
        _f, items = relay.relay_items("index_daily", fields="ts_code,trade_date,close,amount",
                                      ts_code=code, start_date=start, end_date=time.strftime("%Y%m%d"))
    except Exception as e:
        print("[LineRegime] 中继取指数失败: %s" % str(e)[:100])
        return []
    return _norm(items or [])


def series(code: Optional[str] = None, refresh: bool = True) -> List[Tuple[str, float, float]]:
    code = code or index_code()
    cf = _cache_file(code)
    cached: List[Tuple[str, float, float]] = []
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
        print("[LineRegime] 中继异常: %s" % str(e)[:100])
        fresh = []
    if fresh:
        merged = {d: (c, a) for d, c, a in cached}
        merged.update({d: (c, a) for d, c, a in fresh})
        rows = [(d, v[0], v[1]) for d, v in sorted(merged.items())]
        try:
            os.makedirs(DATA, exist_ok=True)
            tmp = cf + ".tmp"
            json.dump([{"trade_date": d, "close": c, "amount": a} for d, c, a in rows],
                      open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, cf)
        except Exception as e:
            print("[LineRegime] 缓存写盘失败: %s" % str(e)[:80])
        return rows
    return cached


# ─────────────────────────── 状态层 ───────────────────────────
def shrink_from(rows: List[Tuple[str, float, float]]) -> Optional[Dict[str, Any]]:
    """缩量判据（纯函数）：最后一日成交额 < 前一日 → shrink。"""
    if len(rows) < 2:
        return None
    d1, _c1, a1 = rows[-2]
    d2, _c2, a2 = rows[-1]
    if a1 <= 0:
        return None
    return {"date": d2, "prev_date": d1, "amount": a2, "prev_amount": a1,
            "ratio": round(a2 / a1, 4), "shrink": bool(a2 < a1)}


def huang_bai_side() -> Dict[str, Any]:
    """黄白线（复用生产 A4 `huang_bai()`）；失败 → side=None（fail-open）。"""
    try:
        import sys as _s
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for p in (os.path.join(root, "backend"), root):
            if p not in _s.path:
                _s.path.insert(0, p)
        from app.services.wolf_index_breadth import huang_bai
        hb = huang_bai() or {}
        return {"side": hb.get("side"), "spread": hb.get("spread"), "stale": hb.get("stale")}
    except Exception as e:
        print("[LineRegime] 黄白线不可用: %s" % str(e)[:100])
        return {"side": None, "spread": None}


def state(rows: Optional[List[Tuple[str, float, float]]] = None) -> Dict[str, Any]:
    rows = rows if rows is not None else series()
    sh = shrink_from(rows)
    hb = huang_bai_side()
    no_buy = bool(hb.get("side") == "bai" and sh and sh.get("shrink"))
    return {"index": index_code(), "ok": bool(sh) and hb.get("side") is not None,
            "side": hb.get("side"), "spread": hb.get("spread"),
            "amount": None if not sh else sh["amount"], "prev_amount": None if not sh else sh["prev_amount"],
            "vol_ratio": None if not sh else sh["ratio"], "shrink": None if not sh else sh["shrink"],
            "date": None if not sh else sh["date"], "no_buy": no_buy}


def allow(st: Optional[Dict[str, Any]] = None) -> bool:
    """闸门语义：**数据不全一律放行**；只有明确「白线在上 ∧ 缩量」才拦。"""
    st = st if st is not None else state()
    if st.get("side") is None or st.get("shrink") is None:
        return True
    return not bool(st.get("no_buy"))


def shadow_record(extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """影子：写 `data/line_regime_shadow_<date>.json`（状态 + 当日其它门状态，供后续增量对照）。"""
    if not shadow_enabled():
        return None
    st = state()
    d8 = str(st.get("date") or time.strftime("%Y%m%d"))
    fn = os.path.join(DATA, "line_regime_shadow_%s.json" % d8)
    rec = {"date": d8, "mode": "shadow", "state": st, "extra": extra or {},
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = fn + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fn)
    except Exception as e:
        print("[LineRegime] 影子写盘失败: %s" % str(e)[:80])
        return None
    return fn


if __name__ == "__main__":
    print(json.dumps({"gate": gate_enabled(), "shadow": shadow_enabled(), "state": state()},
                     ensure_ascii=False, indent=1))
