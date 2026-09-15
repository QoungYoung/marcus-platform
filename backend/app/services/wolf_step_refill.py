# -*- coding: utf-8 -*-
"""wolf_step_refill.py — C6「**分步回补**」的条件（2026-09-15 round 20 落地为影子）。

狼大原话（C4/C6 口径从 xlsx 补齐，总账 §29）：
  · 2025-04-15（他的做T方法·**条件 6**）「如果当日开盘高开快速拉升，或者低开快速拉升**想追进去的**，
    **在下午 2.00-2.30 这个时间段进行回补**，这个时候确保**分时上涨放量，回调缩量**的情况下，
    去补**进攻板块里面涨得还不多的**」
  · 2025-06-10「既然不是黑天鹅，这里就不能抄，只能减，**下去了缩量再买回来**」
  · 2025-06-27「周一**低开再买回来**」
  · 2026-06-18「我黄金今早已经开始**分批进场**了…按我的技术策略**慢慢增加**多单」
  · E10「先观察上午收盘能不能收新低，**一步一步走**」
  → **回补/加仓的条件** = **在 14:00–14:30 窗口内 ∧ 缩量**（＋分批、低开更好——分批由每日次数上限与逐笔量控，低开由
    `wolf_hedge_refill` 的"不追高/低开可补"承担）。

⚠️ **与既有实现的关系**（勿重复造）：
  · **时间窗** = A5 `wolf_trade_window.allowed()`（2025-04-15 条件 2 的 9.45-10.00 / 14.00-2.30，**默认开**）；
  · **缩量** = `wolf_gap_open.volume_ratio()`（今日累计量 ÷ 昨日同期累计量，`kind='shrink'`）；
  · **回补腿本身** = C2b `wolf_hedge_refill`（**只补等量、不追高、超窗放弃**）+ `TMonitor._check_hedge_refill()`；
  · 本模块只提供**附加条件**（窗口 ∧ 缩量），接在 C2b 的决策之后，**不改变已有判别**。

⚠️ **时间窗必须默认可不叠加（重要）**：A5 的时间窗在本项目**已被证据否掉并由用户关闭**
  （总账 §23.3：条件化口径下"窗内 vs 窗外"= **44.9% vs 47.0%** / **45.9% vs 48.6%**，**窗内更差约 2pp**；
  用户据此设 `.env: WOLF_TRADE_WINDOW=0`）。所以本模块**默认只用"缩量"**这一半；
  窗口作为**可选子开关** `WOLF_STEP_REFILL_WINDOW`（默认 **0**）保留，需要用他的话原样时再打开，
  影子会**分别记录**两个分量，便于以后单独量窗口的价值。

开关：`WOLF_STEP_REFILL`（默认 **0** = 不加条件，行为不变）｜`WOLF_STEP_REFILL_SHADOW`（默认 **1** = 只记录）
      ｜`WOLF_STEP_REFILL_WINDOW`（默认 **0** = 不叠加时间窗，只用缩量）
语义：`allow()` 为 False 时**只拦"回补/加仓"**，不影响卖出与止损。**数据缺失一律 fail-open**。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

DATA = os.environ.get("DATA_DIR", "/app/data")


def enabled() -> bool:
    """闸门：置 1 才真的用"窗口 ∧ 缩量"拦回补。"""
    return os.getenv("WOLF_STEP_REFILL", "0").strip() in ("1", "true", "yes")


def window_required() -> bool:
    """是否把「14:00–14:30」也作为条件。**默认 0**：该窗口已被我们自己的证据否掉（见模块 docstring）。"""
    return os.getenv("WOLF_STEP_REFILL_WINDOW", "0").strip() in ("1", "true", "yes")


def shadow_enabled() -> bool:
    return os.getenv("WOLF_STEP_REFILL_SHADOW", "1").strip() not in ("0", "false", "no")


def _window(now: Optional[str] = None) -> Dict[str, Any]:
    """**按窗口本身**判定是否在 09:45–10:00 / 14:00–14:30（13:00 等禁止区间另论）。

    ⚠️ 不复用 A5 的 `allowed()`：那是"**A5 整体开关**打开时才拦"的语义，而生产实测
    `.env: WOLF_TRADE_WINDOW=0`（A5 关着）→ 若复用会把"未启用"误判成"在窗口内"。
    这里只取 A5 的**窗口定义**（`windows()`，可用 env 覆盖），独立判定成员关系；
    取不到定义 → in_window=None（fail-open）。
    """
    try:
        from app.services.wolf_trade_window import windows
        ws = windows() or []
        hhmm = (now or time.strftime("%H%M")).strip()
        inside = any(str(a) <= hhmm <= str(b) for a, b in ws)
        return {"in_window": bool(inside), "why": "HHMM=%s 窗口=%s" % (hhmm, ws), "windows": ws}
    except Exception as e:
        return {"in_window": None, "why": "窗口不可用: %s" % str(e)[:60], "windows": None}


def _vol() -> Dict[str, Any]:
    """复用 A9/A6 的量能实时对比（今日累计 ÷ 昨日同期）。取不到 → kind=None（fail-open）。"""
    try:
        from app.services.wolf_gap_open import volume_ratio
        v = volume_ratio() or {}
        return {"vol_ratio": v.get("ratio"), "vol_kind": v.get("kind"), "at": v.get("at")}
    except Exception as e:
        return {"vol_ratio": None, "vol_kind": None, "at": None, "err": str(e)[:60]}


def state() -> Dict[str, Any]:
    w = _window()
    v = _vol()
    shrink = None if v.get("vol_kind") is None else (v.get("vol_kind") == "shrink")
    need_win = window_required()
    if shrink is None or (need_win and w.get("in_window") is None):
        allow, why = True, "数据不全 → 放行(fail-open)"
    elif need_win and not w["in_window"]:
        allow, why = False, "不在他的回补时间窗内（09:45-10:00 / 14:00-14:30；本项默认不叠加）"
    elif not shrink:
        allow, why = False, "不是缩量（他的话：下去了**缩量**再买回来）"
    elif need_win:
        allow, why = True, "窗口内 ∧ 缩量 → 允许回补"
    else:
        allow, why = True, "缩量 → 允许回补（时间窗按用户决定不叠加）"
    return {"in_window": w.get("in_window"), "window_required": need_win,
            "window_why": w.get("why"), "windows": w.get("windows"),
            "vol_ratio": v.get("vol_ratio"), "vol_kind": v.get("vol_kind"), "shrink": shrink,
            "allow": allow, "why": why, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


def allow(st: Optional[Dict[str, Any]] = None) -> bool:
    st = st if st is not None else state()
    return bool(st.get("allow", True))


def shadow_record(extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """影子：写 `data/step_refill_shadow_<date>.json`（状态 + 当日回补候选，供机会成本核算）。"""
    if not shadow_enabled():
        return None
    st = state()
    d8 = time.strftime("%Y%m%d")
    fn = os.path.join(DATA, "step_refill_shadow_%s.json" % d8)
    rec: Dict[str, Any] = {"date": d8, "mode": "shadow", "gate": enabled(), "state": st,
                           "extra": extra or {}, "ts": st["ts"]}
    try:
        os.makedirs(DATA, exist_ok=True)
        cur = {}
        if os.path.exists(fn):
            try:
                cur = json.load(open(fn, encoding="utf-8")) or {}
            except Exception:
                cur = {}
        # 同一交易日多次轮询 → 追加成序列（保留最后 200 条），便于看"窗口内/外"的分布
        seq = (cur.get("seq") or [])
        seq.append({"ts": st["ts"], "in_window": st["in_window"], "window_required": st["window_required"],
                    "vol_kind": st["vol_kind"],
                    "vol_ratio": st["vol_ratio"], "allow": st["allow"], "why": st["why"],
                    "candidates": (extra or {}).get("candidates")})
        rec["seq"] = seq[-200:]
        tmp = fn + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fn)
    except Exception as e:
        print("[StepRefill] 影子写盘失败: %s" % str(e)[:80])
        return None
    return fn


if __name__ == "__main__":
    print(json.dumps({"gate": enabled(), "shadow": shadow_enabled(), "state": state()},
                     ensure_ascii=False, indent=1))
