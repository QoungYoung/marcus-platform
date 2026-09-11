# -*- coding: utf-8 -*-
"""wolf_index_breadth.py — 指数级**黄白线**（日内强弱总开关），落地 A4（2026-09-11）。

────────────────────────────────────────────────────────────────
狼大原话（XLS 2025-04-15「我的买卖做T方法」条件 3）:
  「当天如果提前预判是 **黄线高于白线**，意味着科技类 消费类这些主线会强于银保地券商这些指数标…
    这个时候**做T成功率高**；如果预判是**白线高于黄线**，那就是以抗指数或者带指数突破的可能性比较大，
    这种时候一般**减少做T**（成功率低，行情和大家用来赚钱的个股低）」
另见他日常用法：2026-01-20「谨慎对小盘做加仓的T，特别是如果开盘后出现**白线在上**的情况下」；
  2026-01-23「第二天**大概率黄线在上** 那第二天开盘只用判断量能，缩量上冲…可以做正T」。
────────────────────────────────────────────────────────────────

**语义**（A 股分时图的黄白线）：
  · **白线 = 加权指数**（上证综指，按总市值加权 → 权重股/银保地券商主导）；
  · **黄线 = 不含加权的平均股价**（等权 → 小盘/科技消费主导）。
  → **黄线在上**（等权强于加权）= 普涨、小票活跃 → 做 T 成功率高；
    **白线在上**（加权强于等权）= 权重护盘、二八分化 → 减少做 T。

**口径实现（代理，必须标注）**：
  白线 = 上证指数当日涨跌幅（腾讯 qt `sh000001`，与 `index.sh_drop` 同源）；
  黄线 = **沪市个股等权平均涨跌幅**（新浪全A实时 `ak.stock_zh_a_spot`，按代码前缀取沪市）。
  两者相减 = `spread`。
  ⚠️ 这是**代理**：他说的黄线是行情软件里那条等权线，我们用"沪市等权平均涨跌幅"近似
  （同一市场、同为等权口径、方向语义一致），**不是同一条线**；差异已写入文档，未声称等价。

**取数与安全**：
  · 新浪全A一次约 16s（70 页）→ **必须缓存**（默认 TTL 180s）且 **fail-open**：
    取不到就返回上一次成功值（标 stale），再没有就返回 None —— **绝不让它阻塞或改变交易判断**。
  · 开关 `WOLF_HUANG_BAI=0` 关闭；`WOLF_HUANG_BAI_TTL` 调缓存；`WOLF_HUANG_BAI_SCOPE=sh|all` 换样本域。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.getenv("WOLF_HUANG_BAI", "1").strip() not in ("0", "false", "no")


def _ttl() -> float:
    try:
        return float(os.getenv("WOLF_HUANG_BAI_TTL", "180"))
    except Exception:
        return 180.0


def classify(spread: Optional[float], eps: float = 0.05) -> Optional[str]:
    """spread = 黄线 − 白线（均以"涨跌幅%"计）→ 'huang' / 'bai' / None。

    |spread| <= eps 视为"纠缠"（他成文里专门提过"黄白线交织"这一情形）→ 返回 None。
    """
    if spread is None:
        return None
    try:
        v = float(spread)
    except Exception:
        return None
    if abs(v) <= float(eps):
        return None
    return "huang" if v > 0 else "bai"


def _equal_weight_pct(scope: str) -> Tuple[Optional[float], int]:
    """沪市(或全A)个股**等权平均涨跌幅%** → (value, n)；失败返回 (None, 0)。"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()          # 新浪全A实时（东财 stock_zh_a_spot_em 在本机被拒）
        if df is None or len(df) == 0:
            return None, 0
        code = df["代码"].astype(str)
        pct = df["涨跌幅"]
        if scope == "sh":
            m = code.str.lower().str.startswith("sh") | code.str.startswith("6")
            df = df[m]
            pct = df["涨跌幅"]
        pct = pct.dropna()
        if len(pct) == 0:
            return None, 0
        return round(float(pct.mean()), 3), int(len(pct))
    except Exception as e:
        print(f"[huang_bai] 等权取数失败: {type(e).__name__}: {str(e)[:90]}")
        return None, 0


def _index_pct() -> Optional[float]:
    """上证指数当日涨跌幅%（腾讯 qt，与 index.sh_drop 同源）。"""
    try:
        from app.services.t_data_sources import fetch_tencent_quote
        q = (fetch_tencent_quote(["sh000001"]) or {}).get("sh000001") or {}
        v = q.get("change_pct")
        return round(float(v), 3) if v is not None else None
    except Exception:
        return None


def huang_bai(force: bool = False) -> Optional[Dict[str, Any]]:
    """黄白线状态 → dict 或 None（不可用时）。

    返回: {equal_pct, index_pct, spread, side, n, ts, stale}
      · side = 'huang'(黄线在上) / 'bai'(白线在上) / None(纠缠)
      · stale = True 表示本次取数失败、用的是上次成功值
    """
    if not enabled():
        return None
    now = time.time()
    if not force and _CACHE["value"] is not None and (now - _CACHE["at"]) < _ttl():
        return _CACHE["value"]
    if not _LOCK.acquire(blocking=False):        # 已有线程在取数 → 直接用旧值，绝不等待
        return _CACHE["value"]
    try:
        scope = (os.getenv("WOLF_HUANG_BAI_SCOPE", "sh") or "sh").strip().lower()
        eq, n = _equal_weight_pct(scope)
        ix = _index_pct()
        if eq is None or ix is None:
            old = _CACHE["value"]
            if old:
                return {**old, "stale": True}
            return None
        spread = round(eq - ix, 3)
        val = {"equal_pct": eq, "index_pct": ix, "spread": spread,
               "side": classify(spread), "n": n, "scope": scope,
               "ts": int(now), "stale": False}
        _CACHE.update({"at": now, "value": val})
        return val
    finally:
        _LOCK.release()


def snapshot() -> Dict[str, Any]:
    """给 t_monitor snapshot / 上下文用的扁平字段（不可用时给 0/None，不抛）。"""
    hb = huang_bai()
    if not hb:
        return {"huang_bai_spread": None, "huang_bai_side": None,
                "huang_bai_equal": None, "huang_bai_index": None, "huang_bai_stale": None}
    return {"huang_bai_spread": hb.get("spread"), "huang_bai_side": hb.get("side"),
            "huang_bai_equal": hb.get("equal_pct"), "huang_bai_index": hb.get("index_pct"),
            "huang_bai_stale": bool(hb.get("stale"))}


def directive() -> str:
    """给 agent/纪律上下文的提示句（他的用法：决定今天做 T 的力度）。"""
    hb = huang_bai()
    if not hb:
        return ""
    side = hb.get("side")
    base = ("日内黄白线：等权(黄)%.2f%% vs 上证(白)%.2f%% → spread %+.2fpp"
            % (hb["equal_pct"], hb["index_pct"], hb["spread"]))
    if side == "huang":
        tail = ("**黄线在上** → 科技/消费这类主线强于银保地券商这类指数标，"
                "**做T成功率较高**（狼大 2025-04-15 条件3）")
    elif side == "bai":
        tail = ("**白线在上** → 偏『抗指数/带指数突破』，**减少做T**（成功率低，"
                "狼大 2025-04-15 条件3；另 2026-01-20「谨慎对小盘做加仓的T」）")
    else:
        tail = "**黄白线交织** → 按狼大 2025-04-15「黄白线交织+缩大量」属『操作谨慎』情形之一"
    if hb.get("stale"):
        tail += "（注：本次取数失败，用的是上次成功值）"
    return base + "；" + tail
