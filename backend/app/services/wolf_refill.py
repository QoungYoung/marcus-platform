# -*- coding: utf-8 -*-
"""wolf_refill.py — 条件6「14:00–14:30 回补」（A8，2026-09-11）。

═══════════════════════════════════════════════════════════════════════
狼大原文（XLS **2025-04-15** 条件6，逐字）:
  「如果当日开盘**高开快速拉升，或者低开快速拉升想追进去的**，在**下午2.00-2.30**这个
    时间段进行**回补**，这个时候确保**分时上涨放量，回调缩量**的情况下，去补**进攻板块
    里面涨得还不多的**」
═══════════════════════════════════════════════════════════════════════

**四个要素 → 落点**（逐条对齐，不额外发明）：
  ① 「当日开盘高开/低开快速拉升」→ 复用 A6 `wolf_gap_open.gap_state` 的开盘形态
     + 开盘后 30 分钟（09:35–10:00）指数累计涨幅 ≥ 阈值（他未给数 → 参数 `WOLF_REFILL_SURGE_PCT`）
  ② 「下午 2.00–2.30」→ 复用 A5 `wolf_trade_window`（已把 14:00–14:30 设为做T窗）
  ③ 「分时上涨放量，回调缩量」→ 个股 m5：**上涨根均量 > 下跌根均量**（这正是该短语的字面含义）
  ④ 「进攻板块里面涨得还不多的」→ 候选需（a）属进攻方向（调用方给的主题/板块）
     （b）当日涨幅处于同批的**低分位**（沿用本仓 `pct_rank` 平均名次口径，取 ≤50 分位）

**边界（重要）**：他这句的主语是"**想追进去的**"——意愿在人。系统没有"我想追"这个输入，
所以本模块**只产出条件判定与候选清单（决策支持）**，**不自动生成下单腿**。
把它当"提示层"，并在 §11.3 里如实标注。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def enabled() -> bool:
    return os.getenv("WOLF_REFILL", "1").strip() not in ("0", "false", "no")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def open_surge(date8: Optional[str] = None, pct: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """① 开盘形态 + 开盘后 30 分钟"快速拉升" → {'kind','open_pct','surge','win_pct'} 或 None。

    「快速拉升」他未给数 → 阈值是可调参数（默认 +0.5%），**由回测校准**，不是语料里读出来的。
    """
    try:
        from app.services.wolf_gap_open import gap_state, index_m5, split_days
    except Exception:
        return None
    g = gap_state(date8=date8)
    if not g or g.get("kind") not in ("gap_up", "low_open"):
        return None
    bars = index_m5()
    days = split_days(bars)
    tb = days.get(g.get("date")) or []
    win = [b for b in tb if "0935" <= str(b.get("time"))[8:12] <= "1000"]
    if len(win) < 2:
        return None
    o = float(win[0].get("open") or 0)
    c = float(win[-1].get("close") or 0)
    if o <= 0:
        return None
    wp = (c - o) / o * 100.0
    thr = _env_f("WOLF_REFILL_SURGE_PCT", 0.5) if pct is None else float(pct)
    return {"kind": g["kind"], "open": g["open"], "open_pct": round(wp, 3),
            "win_pct": round(wp, 3), "thr": thr, "surge": wp >= thr,
            "time": str(win[-1].get("time")), "date": g.get("date")}


def m5_pattern(bars: List[dict], ratio: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """③ 分时「上涨放量，回调缩量」：上涨根（close>open）均量 > 下跌根均量。

    → {'up_n','down_n','up_vol_avg','down_vol_avg','ratio','ok'}；样本不足 → None。
    这是该短语的**字面**判据（无需另立"量价配合"的复杂模型）。
    """
    if not bars:
        return None
    up = [float(b.get("vol") or 0) for b in bars if float(b.get("close") or 0) > float(b.get("open") or 0)]
    dn = [float(b.get("vol") or 0) for b in bars if float(b.get("close") or 0) < float(b.get("open") or 0)]
    if len(up) < 2 or len(dn) < 2:
        return None
    ua, da = sum(up) / len(up), sum(dn) / len(dn)
    if da <= 0:
        return None
    r = ua / da
    thr = _env_f("WOLF_REFILL_VOL_RATIO", 1.0) if ratio is None else float(ratio)
    return {"up_n": len(up), "down_n": len(dn), "up_vol_avg": round(ua, 1),
            "down_vol_avg": round(da, 1), "ratio": round(r, 3), "thr": thr, "ok": r > thr}


def _pct_rank(vals: List[float], v: float) -> float:
    """平均名次分位（0–100）：**并列取平均** —— 与 P1-5b 修 `pct_rank` 同一口径。"""
    if not vals:
        return 50.0
    less = sum(1 for x in vals if x < v)
    eq = sum(1 for x in vals if x == v)
    return (less + (eq - 1) / 2.0) / max(1, len(vals) - 1) * 100.0 if len(vals) > 1 else 50.0


def pick_candidates(cands: List[Dict[str, Any]], pct_max: Optional[float] = None) -> List[Dict[str, Any]]:
    """④ 从候选里挑「涨得还不多的」 → 按当日涨幅低分位过滤。

    cands: [{'symbol','pct_chg','bars'?, 'attack'?: bool}]；`bars` 给了就一并做 ③ 的分时校验。
    `attack=False` 的会被剔除（他明确写"**进攻板块**里面"）。
    返回带 `rank_pct` / `pattern` 的清单（涨幅升序）。
    """
    thr = _env_f("WOLF_REFILL_RANK_PCT", 50.0) if pct_max is None else float(pct_max)
    pool = [c for c in (cands or []) if c.get("attack", True) and c.get("pct_chg") is not None]
    if not pool:
        return []
    vals = [float(c["pct_chg"]) for c in pool]
    out = []
    for c in pool:
        rp = _pct_rank(vals, float(c["pct_chg"]))
        if rp > thr:
            continue                        # "涨得还不多的" = 低分位
        rec = {"symbol": c.get("symbol"), "pct_chg": float(c["pct_chg"]), "rank_pct": round(rp, 1)}
        if c.get("bars"):
            pat = m5_pattern(c["bars"])
            rec["pattern"] = pat
            if pat is not None and not pat["ok"]:
                continue                    # ③ 不满足 → 不入清单
        out.append(rec)
    return sorted(out, key=lambda x: x["pct_chg"])


def pm_window() -> Optional[tuple]:
    """条件6 的时段 = **A5 定义的下午窗**（默认 14:00–14:30）。不另设一套窗口口径。"""
    try:
        from app.services.wolf_trade_window import windows
        ws = windows() or []
        return ws[1] if len(ws) > 1 else (ws[0] if ws else None)   # windows() 是先 AM 后 PM 的列表
    except Exception:
        return None


def evaluate(date8: Optional[str] = None, now: Optional[str] = None) -> Dict[str, Any]:
    """当前是否处于条件6的**回补窗口**（14:00–14:30 ∧ 指数开盘快速拉升）。"""
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    import datetime as _dt
    pm = pm_window()
    hm = (now or _dt.datetime.now().strftime("%H%M"))
    in_pm = bool(pm and str(pm[0]) <= hm < str(pm[1]))
    sur = open_surge(date8=date8)
    return {"ok": True, "pm_window": pm, "now": hm, "in_pm_window": in_pm,
            "surge": sur, "ready": bool(in_pm and sur and sur.get("surge"))}


def directive() -> str:
    """给纪律上下文的提示块（条件6 的窗口/形态状态 + 用法）。"""
    ev = evaluate()
    if not ev.get("ok"):
        return ""
    sur = ev.get("surge") or {}
    if not sur:
        return ""
    if ev.get("ready"):
        head = "✅ 条件6 回补窗口成立（指数开盘%s30分钟拉升%.2f%% ∧ %s–%s）" % (
            "高开" if sur.get("kind") == "gap_up" else "低开", sur.get("win_pct") or 0,
            (ev.get("pm_window") or ["", ""])[0], (ev.get("pm_window") or ["", ""])[1])
    else:
        head = "…条件6 回补窗口未成立（开盘%s 30分钟%.2f%% / 阈值%.2f%%；现%s，下午窗%s）" % (
            "高开跳空" if sur.get("kind") == "gap_up" else "低开",
            sur.get("win_pct") or 0, sur.get("thr") or 0, ev.get("now") or "?",
            "%s–%s" % tuple(ev.get("pm_window") or ["?", "?"]))
    return ("↩ %s｜他的要求：分时**上涨放量、回调缩量**，补**进攻板块里涨得还不多的**"
            "（本模块只给条件与候选，不自动下单）" % head)
