# -*- coding: utf-8 -*-
"""暴跌应对（狼大语料）：**A 指数层·反抽失败降仓** + **B 主题/个股层·避开高标**（2026-09-18 用户"做出来"）。

语料依据（`docs/wolf-daily-log-xls2026.md:335`，**2026-01-30 当天原话**）：
  「指数跌破 4100、恐慌盘出来，**他等反抽；反抽上不了 4126 就把仓位减到 40% 以内**。
   收盘落在 4126 附近，只要 **2 点半 GJD 不砸盘**就还在原区间，**没有指数级风险**，**避开高标、控制仓位**、等大手调整结束。」
  ＋ 同日志 :351「**急跌 不割肉**，等反弹不行 重新下跌再考虑割」。

为什么需要（2026-01-29 实测教训）：**上证 +0.16%，但全市场中位 −0.95%、65% 个股下跌**，
  我方持仓 002429 −6.72% / 603203 −4.17% ⇒ 账户当天掉约 2%，而**我们所有规则都以指数为触发**
  （指数破位／周末避险）⇒ **系统判定"无事发生"**。⇒ 需要：指数层的反抽失败降仓（A）+ 主题/个股层的避开高标（B）。
开关 `WOLF_DERISK_A` / `WOLF_DERISK_B`（默认关；回测侧默认开）。
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
INDEX_CSV = os.getenv("WOLF_INDEX_CSV",
                      os.path.join(REPO, "data", "指数数据", "index_daily", "000001.SH.csv"))
A_ON = str(os.getenv("WOLF_DERISK_A", "0")).strip().lower() in ("1", "true", "yes", "on")
B_ON = str(os.getenv("WOLF_DERISK_B", "0")).strip().lower() in ("1", "true", "yes", "on")
CUT_TO = float(os.getenv("WOLF_DERISK_CUT_TO", "0.40"))     # 反抽失败 → 总仓降到 ≤40%
B_THRESH = float(os.getenv("WOLF_DERISK_B_THRESH", "-3.0"))  # 持仓组合或高标单日跌幅阈值（%）
_STATS: Dict[str, Any] = {"a_trigger": 0, "b_trigger": 0, "a_state": False, "b_state": False,
                          "reason": "", "cut_symbols": []}
_CACHE: Dict[str, List[Tuple[str, float]]] = {}


def stats() -> Dict[str, Any]:
    return {"a_on": bool(A_ON), "b_on": bool(B_ON), "cut_to": CUT_TO, "b_thresh": B_THRESH, **dict(_STATS)}


def stats_reset() -> None:
    for _k in ("a_trigger", "b_trigger"):
        _STATS[_k] = 0
    _STATS.update({"a_state": False, "b_state": False, "reason": "", "cut_symbols": []})


def _series(path: str = "") -> List[Tuple[str, float]]:
    p = path or INDEX_CSV
    if p in _CACHE:
        return _CACHE[p]
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    out.append((str(r["trade_date"]).replace("-", ""), float(r["close"])))
                except Exception as _e_sil1:
                    _silent_alert("t_derisk.py:53", _e_sil1)
                    continue
    except Exception:
        out = []
    out.sort(); _CACHE[p] = out
    return out


def index_state(day: str, path: str = "") -> Dict[str, Any]:
    """给定额度日：返回 {close, pre_close, chg_pct, level_broken(整数关口), above_pre}。"""
    ser = _series(path)
    d = str(day or "").replace("-", "")
    idx = None
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= d:
            idx = i; break
    if idx is None or idx < 1:
        return {"ok": False}
    close, pre = ser[idx][1], ser[idx - 1][1]
    return {"ok": True, "close": close, "pre_close": pre,
            "chg_pct": round((close / pre - 1) * 100, 3) if pre else 0.0,
            "level_broken": round(pre / 100) * 100 if close < pre else None,   # 跌破的整数关口
            "above_pre": close >= pre}


def plan_a(day: str, path: str = "", enabled: Optional[bool] = None) -> Dict[str, Any]:
    """**A 指数层**：昨日跌破整数关口（或收跌）→ 今日**反抽是否站回前收**；站不回 ⇒ 降总仓到 ≤CUT_TO。

    "反抽上不了 4126" 的可计算化：**4126 ≈ 跌破当日的前收** ⇒ 判据＝今日收盘是否 ≥ 昨日前收。
    """
    on = A_ON if enabled is None else bool(enabled)
    out = {"active": False, "cut_to": CUT_TO, "reason": ""}
    if not on:
        out["reason"] = "A 开关关"
        return out
    ser = _series(path)
    d = str(day or "").replace("-", "")
    idx = None
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= d:
            idx = i; break
    if idx is None or idx < 2:
        out["reason"] = "无指数数据"
        return out
    prev_close, prev_pre = ser[idx - 1][1], ser[idx - 2][1]
    today_close = ser[idx][1]
    broke_level = prev_close < prev_pre                      # 昨日收跌 = 破了关口的近似
    rebound_ok = today_close >= prev_close                   # 今日站回昨收 = 反抽成功
    if broke_level and not rebound_ok:
        _STATS["a_trigger"] += 1
        _STATS["a_state"] = True
        out.update({"active": True,
                    "reason": "指数昨收 %.2f 跌破前一收 %.2f（关口破位），今日 %.2f 反抽未站回 ⇒ 降总仓到 ≤%.0f%%（狼大 01-30 口径 4100/4126）"
                              % (prev_close, prev_pre, today_close, CUT_TO * 100)})
    else:
        out["reason"] = ("昨日未破位" if not broke_level else "今日反抽已站回（%.2f ≥ %.2f）" % (today_close, prev_close))
    _STATS["reason"] = out["reason"]
    return out


def plan_b(held_moves: Dict[str, float], index_chg: float,
           enabled: Optional[bool] = None, thresh: Optional[float] = None) -> Dict[str, Any]:
    """**B 主题/个股层·避开高标**：**指数未破位**但**持仓组合单日跌幅超阈值** ⇒ 降跌幅最大的高标仓位。

    held_moves: {symbol: 当日涨跌%}；返回 {active, cut_symbols, reason}。
    """
    on = B_ON if enabled is None else bool(enabled)
    th = B_THRESH if thresh is None else float(thresh)
    out = {"active": False, "cut_symbols": [], "reason": ""}
    if not on:
        out["reason"] = "B 开关关"
        return out
    mv = {s: float(v) for s, v in (held_moves or {}).items()}
    if not mv:
        out["reason"] = "无持仓行情"
        return out
    worst = sorted(mv.items(), key=lambda kv: kv[1])          # 跌幅最大在前
    avg = sum(mv.values()) / len(mv)
    hit = [s for s, v in worst if v <= th]
    if hit and float(index_chg) > -0.5:                       # 指数未明显下跌（未破位近似）
        _STATS["b_trigger"] += 1
        _STATS["b_state"] = True
        _STATS["cut_symbols"] = hit
        out.update({"active": True, "cut_symbols": hit,
                    "reason": "指数仅 %+.2f%%（未破位）但持仓组合均值 %+.2f%%、%d 只跌破 %.1f%% ⇒ 避开高标：减 %s（狼大 01-30「避开高标、控制仓位」）"
                              % (index_chg, avg, len(hit), th, ",".join(hit[:5]))})
    else:
        out["reason"] = "组合均值 %+.2f%%、跌破阈值 %d 只（指数 %+.2f%%）" % (avg, len(hit), index_chg)
    if not _STATS["reason"]:
        _STATS["reason"] = out["reason"]
    return out
