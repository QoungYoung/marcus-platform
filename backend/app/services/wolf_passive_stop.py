# -*- coding: utf-8 -*-
"""wolf_passive_stop.py — G4：被动止盈线「**只上移、不破不卖**」（2026-09-14 落地）。

狼大原话:
  · 2025-06-09「持仓，下方**被动止盈位提高到 3373，不破不卖**。」
  · 2025-07-17「所以用我的方法 做通道 做好仓位成本后按通道做，**不破被动止盈根本不会卖**。」
  · 2026-08-19「这些**都设置好被动止盈** 哪个跌破出场哪个」＋「我肯定按计划做的 然后**设定好止损**就行了」
  · 2026-07-01「当你减完仓**浮盈过 100%** 后 你设置 **13 或者中轨**不就行了」
  · 2025-05-13（同族）「其实核心就是他只能涨，**我的被动止盈不断往上**，不接受损失」

口径（可算化；与已被删除的"自造移动止盈"的区别：这条**他有原话**，且语义是**只上移**）:
  · 候选线 = 近 WOLF_PASSIVE_WIN（默认 13）个交易日的**最低价**（"线往上提"的落点）；
  · 浮盈 > WOLF_PASSIVE_HIGH_PCT（默认 100%）时，候选 = max(候选, **MA13**, **BOLL 中轨**)
    —— 对应他 2026-07-01 那句；
  · **更新规则：新线 = max(旧线, 候选)**（只上移，永不下降）；
  · **触发**：现价 < 线 → 卖（**T 仓**，保留底仓）；**要求有浮盈**（止盈语义，亏损侧交给六层止损）；
  · 时点：受 ④ 门约束（13:00–14:30 不执行，复用 t_monitor._stop_time_ok）。

纯函数模块（可单测）；执行在 t_monitor._check_passive_stop()。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

DATA = os.environ.get("DATA_DIR", "/app/data")
STATE_FILE = os.path.join(DATA, "wolf_passive_stop.json")


def enabled() -> bool:
    return os.getenv("WOLF_PASSIVE_STOP", "1").strip() not in ("0", "false", "no")


def _env_i(name: str, d: float) -> int:
    try:
        return int(float(os.getenv(name, str(d))))
    except (TypeError, ValueError):
        return int(d)


def _env_f(name: str, d: float) -> float:
    try:
        return float(os.getenv(name, str(d)))
    except (TypeError, ValueError):
        return d


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[PassiveStop] 状态写盘失败: {e}")


def key_of(account: str, symbol: str) -> str:
    return "%s:%s" % (account or "stock", str(symbol).replace(" ", "").upper())


def ma(bars: List[dict], n: int, field: str = "close") -> Optional[float]:
    if len(bars) < n:
        return None
    win = [_f(b.get(field)) for b in bars[-n:]]
    return sum(win) / len(win) if win else None


def boll_mid(bars: List[dict], n: int = 20) -> Optional[float]:
    return ma(bars, n, "close")


def candidate_line(bars: List[dict], profit_pct: float = 0.0) -> Optional[float]:
    """候选被动止盈线：近 N 日最低价；浮盈过高时改用 max(该低点, MA13, 中轨)。"""
    if not bars:
        return None
    win = _env_i("WOLF_PASSIVE_WIN", 13)
    lows = [_f(b.get("low")) for b in bars[-win:]]
    lows = [x for x in lows if x > 0]
    if not lows:
        return None
    cand = min(lows)
    hi_pct = _env_f("WOLF_PASSIVE_HIGH_PCT", 100.0)
    if profit_pct > hi_pct:
        m13 = ma(bars, 13)
        mid = boll_mid(bars, 20)
        cand = max([cand] + [x for x in (m13, mid) if x])
    return round(cand, 4)


def update_line(old: Optional[float], candidate: Optional[float]) -> Optional[float]:
    """**只上移**：新线 = max(旧线, 候选)。（狼大「被动止盈位提高到…」「被动止盈不断往上」）"""
    if candidate is None:
        return old
    if old is None:
        return round(float(candidate), 4)
    return round(max(float(old), float(candidate)), 4)


def _today8() -> str:
    import datetime as _dt
    return _dt.date.today().strftime("%Y%m%d")


def get_line(account: str, symbol: str) -> Optional[float]:
    return (_load().get(key_of(account, symbol)) or {}).get("line")


def sync_line(account: str, symbol: str, bars: List[dict], profit_pct: float = 0.0) -> Optional[float]:
    """按当日数据刷新并落盘（只上移）。"""
    k = key_of(account, symbol)
    d = _load()
    st = d.get(k) or {}
    old = st.get("line")
    new = update_line(old, candidate_line(bars, profit_pct))
    if new is None:
        return old
    if old != new:
        st.update({"line": new, "updated": _today8(), "symbol": str(symbol).upper(), "account": account})
        d[k] = st
        _save(d)
        print(f"[PassiveStop] {k} 线 {old} → {new}（只上移）")
    return new


def drop(account: str, symbol: str) -> None:
    """清仓后移除状态（避免长期残留）。"""
    k = key_of(account, symbol)
    d = _load()
    if k in d:
        d.pop(k, None)
        _save(d)


def passive_decision(price: float, line: Optional[float], cost: float = 0.0,
                     time_ok: bool = True) -> Tuple[str, str]:
    """(action, reason)：跌破被动止盈线且有浮盈 → sell；否则 wait。"""
    if not time_ok:
        return ("wait", "止损/止盈时点门（13:00–14:30 不执行; 狼大 2026-03-23）")
    if not line or line <= 0:
        return ("wait", "尚无被动止盈线")
    if price <= 0:
        return ("wait", "无有效现价")
    if cost > 0 and price <= cost:
        return ("wait", "无浮盈（止盈语义；亏损侧交给六层止损）")
    if price < float(line):
        return ("sell", "现价 %.3f < 被动止盈线 %.3f（狼大 2025-06-09「不破不卖」/2025-07-17「不破被动止盈根本不会卖」）"
                % (price, float(line)))
    return ("wait", "现价 %.3f ≥ 线 %.3f（不破不卖）" % (price, float(line)))


def dump() -> dict:
    return _load()
