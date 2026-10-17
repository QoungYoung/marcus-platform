# -*- coding: utf-8 -*-
"""t_sleeve.py — 「独立 T 仓 + 价格带驱动」的**纯逻辑**（用户 A+B ✓，2026-10-16）

为什么需要（用户观察 ✓）：
  狼大「**几乎每天做T**」✓（语料：每天倒一次做T / 每天都可以做做T / 目标3-4%实际2%收工 /
  −3%以下抄的 / 回到卖出位置才补 / 日内做T仓位20%）
  而我们的 T 是**形态信号驱动**（high_sell/vwap/0.618/上影线 ⇒ 896 次拦截、2 笔成交 ✗）

量化（dec1 20251201~1231，231 个票-日 ✓，被动挂单式重放 ✓）：
   目标+2% 收工、不强制"当日跌3%" ⇒ **62 次 T / 21 天 = 91% 的日子有 T** ✓ 均 +0.87%/笔 ✓
   目标+3% ⇒ 38 次 / 18 天（78%）✓ +1.22%/笔 ✓
   强制"当日跌3%" ⇒ 只剩 8 次 / 6 天（26%）✗ ⇒ 这条是低吸**建仓**纪律，不该套在每次 T 上 ✓

本模块只做**纯计算** ✓（无 IO 副作用，除 state 读写 ✓）：
   · `sleeve_shares()`  T 仓股数 = 持仓 × pct%，至少 1 手 ✓
   · `sell_target()`   T 卖出价 = 买入价 × (1 + target%)
   · `decide()`        给定 (现价 / 持仓 / 当日状态) ⇒ "买回 / 卖出 / 不动" ✓
开关 ✓：`WOLF_T_SLEEVE_BAND`（**库内默认 0 = 关** ✓）
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

LOT = 100


def enabled() -> bool:
    return str(os.getenv("WOLF_T_SLEEVE_BAND", "0")).strip().lower() in ("1", "true", "yes", "on")


def sleeve_pct() -> float:
    """T 仓占持仓的百分比（语料「日内做T仓位 20%」✓；`WOLF_T_SLEEVE_PCT` 可调 ✓）"""
    try:
        return abs(float(os.getenv("WOLF_T_SLEEVE_PCT", "20") or 20))
    except Exception:
        return 20.0


def target_pct() -> float:
    """卖出目标（语料「目标 3%-4%、实际 2% 后收工」⇒ 默认 **2%** 收工 ✓）"""
    try:
        return abs(float(os.getenv("WOLF_T_SLEEVE_TARGET", "2") or 2))
    except Exception:
        return 2.0


def buyback_pct() -> float:
    """回补带：现价 ≤ 上次卖价 × (1 − x%) 可买回（语料「回到卖出位置才补」⇒ 默认 0% = 回到卖价 ✓）"""
    try:
        return abs(float(os.getenv("WOLF_T_SLEEVE_BUYBACK", "0") or 0))
    except Exception:
        return 0.0


def once_per_day() -> bool:
    """每票每天最多一次完整 T（`WOLF_T_SLEEVE_ONCE=1`，默认 1 ✓ 防高频 ✓）"""
    return str(os.getenv("WOLF_T_SLEEVE_ONCE", "1")).strip().lower() in ("1", "true", "yes", "on")


def sleeve_shares(position: int, pct: Optional[float] = None, lot: int = LOT) -> int:
    """T 仓股数 ✓ = 持仓 × pct%（向下取整到手 ✓，但**至少 1 手**（持仓 ≥ 2 手时 ✓））"""
    pos = int(position or 0)
    if pos <= 0:
        return 0
    p = sleeve_pct() if pct is None else float(pct)
    n = int(pos * p / 100.0 / lot) * lot
    if n <= 0 and pos >= 2 * lot:
        n = lot                      # 小仓位兜底 ✓（用户 A：「最少 1 手」✓）
    return min(n, max(pos - lot, 0))


def sell_target(buy_px: float, pct: Optional[float] = None) -> float:
    """T 卖出价 ✓ = 买入价 × (1 + target%)"""
    p = target_pct() if pct is None else float(pct)
    return round(float(buy_px or 0) * (1 + p / 100.0), 3)


def buyback_price(last_sell_px: float, pct: Optional[float] = None) -> float:
    """回补价 ✓ = 上次卖价 × (1 − buyback%)（默认 0% ⇒ 回到卖价即可 ✓）"""
    p = buyback_pct() if pct is None else float(pct)
    return round(float(last_sell_px or 0) * (1 - p / 100.0), 3)


def decide(price: float, position: int, state: Dict[str, Any], day: str,
           cost: float = 0.0) -> Tuple[str, str]:
    """纯决策 ✓ ⇒ ("sell"|"buy"|"hold", 原因)

    规则（全部来自语料 ✓）：
      · 卖出带 ✓：现价 ≥ 锚 × (1 + target%) ⇒ 卖掉 T 仓 ✓
          锚 = 上次 T 买入价；若无 ⇒ 用**持仓成本**（"目标 3-4%、实际 2% 收工" 是相对成本 ✓）
      · 回补带 ✓：已卖出 ⇒ 现价 ≤ 上次卖价 × (1 − buyback%) ⇒ 买回 ✓（"回到卖出位置才补"✓）
      · 每票每天最多一轮 ✓（`WOLF_T_SLEEVE_ONCE`）
      · T 仓不足 1 手 ⇒ 不动 ✓
    ⚠️ 状态**不能因为缺 day 就清空** ✗（我第一版就这么写错 ✓）—— 只在 day **存在且不同**时重置 ✓
    """
    if not enabled():
        return "hold", "开关关(WOLF_T_SLEEVE_BAND=0)"
    st = dict(state or {})
    d0 = str(st.get("day") or "")
    if d0 and d0 != str(day):
        st = {"day": str(day)}                 # 跨日才重置 ✓
    elif not d0:
        st["day"] = str(day)                   # 缺 day ⇒ 补上，**不清空** ✓
    pos = int(position or 0)
    if pos <= 0:
        return "hold", "无持仓"
    if once_per_day() and st.get("done"):
        return "hold", "今日已完成一轮 T（WOLF_T_SLEEVE_ONCE=1）"
    sn = sleeve_shares(pos)
    if sn <= 0:
        return "hold", "T 仓不足 1 手（持仓 %d）" % pos
    px = float(price or 0)
    if px <= 0:
        return "hold", "无价格"
    # ① 已卖出 ⇒ 等回补 ✓
    if st.get("sold") and not st.get("bought"):
        lp = float(st.get("last_sell_px") or 0)
        if lp > 0 and px <= buyback_price(lp):
            return "buy", ("T 回补：现价 %.3f ≤ 上次卖价 %.3f×(1−%.1f%%) = %.3f ✓（%d 股）"
                           % (px, lp, buyback_pct(), buyback_price(lp), sn))
        return "hold", "已卖出，等回补（卖价 %.3f 回补价 %.3f 现价 %.3f）" % (
            lp, buyback_price(lp) if lp else 0.0, px)
    # ② 未卖出 ⇒ 看是否到卖出带 ✓
    anchor = float(st.get("last_buy_px") or 0) or float(cost or 0)
    if anchor > 0:
        tp = sell_target(anchor)
        if px >= tp:
            return "sell", ("T 卖出：现价 %.3f ≥ 锚 %.3f×(1+%.1f%%) = %.3f ✓（卖 %d 股）"
                            % (px, anchor, target_pct(), tp, sn))
        return "hold", "等卖出带（锚 %.3f 目标 %.3f 现价 %.3f）" % (anchor, tp, px)
    return "hold", "无锚（既无上次买价也无成本）"


# ── 状态读写 ✓（账户级一份 JSON，落在运行根 ✓）──────────────────────
def _state_path() -> str:
    root = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
    return os.path.join(root, "t_sleeve_state.json")


def load_state(account: str, symbol: str, day: str) -> Dict[str, Any]:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            allst = json.load(f) or {}
    except Exception:
        allst = {}
    st = ((allst.get(account) or {}).get(symbol)) or {}
    if str(st.get("day") or "") != str(day):
        st = {"day": str(day)}
    return st


def save_state(account: str, symbol: str, st: Dict[str, Any]) -> None:
    try:
        p = _state_path()
        try:
            with open(p, encoding="utf-8") as f:
                allst = json.load(f) or {}
        except Exception:
            allst = {}
        allst.setdefault(account, {})[symbol] = dict(st or {})
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(allst, f, ensure_ascii=False)
    except Exception:
        pass
