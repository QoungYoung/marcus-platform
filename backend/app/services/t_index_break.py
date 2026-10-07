# -*- coding: utf-8 -*-
"""指数破位判定（狼大语料，2026-09-18 用户"落地"）。

口径**不是猜的**：拿他 5 个发言日、用当日数据逐日重算 MA 全档反推出来的（实测全部吻合）：
  · 2026-01-26「指数**没破位**」→ 收盘 4132.60，5/10/13/20/34/55/60/144/200 **全部站上**
  · 2026-01-30「指数**跌破 4100**」→ 收盘 4117.95，只跌破 **5/10/13**（20 以上全站上）
      ⇒ **"跌破某个点位" ≠ "破位"**，他用词刻意区分
  · 2026-03-23「4000 **破位后**…跌破 3800」→ 收盘 3813.28，**全部均线跌破**
  · 2026-03-24「**破了 144 三天**」→ 收盘 3881.28，跌破 5…144、**仅站上 MA200**；
      逐日核对 **03-20 / 03-23 / 03-24 = 正好三天跌破 MA144**（03-18、03-19 仍站上）
      ⇒ **"三天"是收盘价口径 ⇒ 收盘确认**（与 2026-01-29「收盘跌破我才出」、蓝图 154 一致）
  · 2026-08-27「只看指数**大级别**…转下跌 1 浪」→ 收盘 3956.57，**跌破 60/144/200**、站上 5~55
      ⇒ **"大级别"实测就是 60 / 144 / 200 这一档**
⇒ 判据：**收盘同时跌破大级别均线中至少 N 根（默认 2）并连续 D 日（默认 1）**。
语义（语料）：破位**对应"持仓的止损/减仓评估"，不是禁止买入**——2026-08-24 破位当天他照样
「跌破了 按计划打入」；`daily_decision.py:258-260` 已写明曾误写成 blocker 并改正。

⚠️ 适用范围：由 2026-01~03、08 的 5 个发言日反推，**样本小、未做大样本验证**；
属策略判据 ⇒ 开关 `WOLF_INDEX_BREAKDOWN` **默认关**，启用前应做同窗口 A/B。
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional, Tuple


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
BIG_LINES: Tuple[int, ...] = tuple(int(x) for x in
                                   (os.getenv("WOLF_IDX_BREAK_LINES", "60,144,200").split(",")))
MIN_BROKEN = int(os.getenv("WOLF_IDX_BREAK_MIN", "2"))     # 至少破几根大级别线
CONFIRM_DAYS = int(os.getenv("WOLF_IDX_BREAK_DAYS", "1"))  # 连续几天（收盘口径）
SWITCH = str(os.getenv("WOLF_INDEX_BREAKDOWN", "0")).strip().lower() in ("1", "true", "yes", "on")
_CACHE: Dict[str, List[Tuple[str, float]]] = {}


def enabled() -> bool:
    return SWITCH


def _series(path: str = "") -> List[Tuple[str, float]]:
    p = path or INDEX_CSV
    if p in _CACHE:
        return _CACHE[p]
    out: List[Tuple[str, float]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    out.append((str(r["trade_date"]).replace("-", ""), float(r["close"])))
                except Exception as _e_sil1:
                    _silent_alert("t_index_break.py:53", _e_sil1)
                    continue
    except Exception:
        out = []
    out.sort()
    _CACHE[p] = out
    return out


def _fmt(day: str) -> str:
    d = str(day or "").replace("-", "")
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else str(day)


def index_mas(day: str, lines: Optional[Tuple[int, ...]] = None,
              path: str = "") -> Dict[int, Optional[float]]:
    """**截至当日（含）**的大级别均线（收盘口径）。"""
    want = tuple(lines or BIG_LINES)
    ser = _series(path)
    ds = [d for d, _ in ser]
    tgt = str(day or "").replace("-", "")
    idx = None
    for i in range(len(ds) - 1, -1, -1):
        if ds[i] <= tgt:
            idx = i
            break
    if idx is None:
        return {n: None for n in want}
    out: Dict[int, Optional[float]] = {}
    for n in want:
        out[n] = round(sum(c for _, c in ser[idx + 1 - n:idx + 1]) / n, 2) if idx + 1 >= n else None
    return out


def index_close(day: str, path: str = "") -> Optional[float]:
    ser = _series(path)
    tgt = str(day or "").replace("-", "")
    for d, c in reversed(ser):
        if d <= tgt:
            return c
    return None


def index_breakdown(day: str, lines: Optional[Tuple[int, ...]] = None,
                    min_broken: Optional[int] = None, confirm_days: Optional[int] = None,
                    path: str = "") -> Dict[str, Any]:
    """当日是否"指数破位"（收盘口径 + 连续确认）。返回 {broken, below, above, close, mas, streak}。"""
    want = tuple(lines or BIG_LINES)
    need = MIN_BROKEN if min_broken is None else int(min_broken)
    days = max(1, CONFIRM_DAYS if confirm_days is None else int(confirm_days))
    ser = _series(path)
    ds = [d for d, _ in ser]
    tgt = str(day or "").replace("-", "")
    pos = None
    for i in range(len(ds) - 1, -1, -1):
        if ds[i] <= tgt:
            pos = i
            break
    if pos is None:
        return {"broken": False, "below": [], "above": [], "close": None, "mas": {},
                "streak": 0, "reason": "无指数数据（fail-open，不因此判破位）"}
    below: List[int] = []
    above: List[int] = []
    streak = 0
    for k in range(days):
        i = pos - k
        if i < 0:
            break
        dayk, closek = ser[i]
        mas = {n: (round(sum(c for _, c in ser[i + 1 - n:i + 1]) / n, 2) if i + 1 >= n else None)
               for n in want}
        bl = [n for n in want if mas.get(n) and closek < mas[n]]
        if k == 0:
            below, above = bl, [n for n in want if mas.get(n) and closek >= mas[n]]
        if len(bl) >= need:
            streak += 1
        else:
            break
    close = ser[pos][1]
    mas = index_mas(day, want, path)
    broken = streak >= days
    return {"broken": broken, "below": below, "above": above, "close": close, "mas": mas,
            "streak": streak,
            "reason": ("收盘 %.2f 跌破大级别 %s 中 %d 根（≥%d）且连续 %d 日 ⇒ 破位"
                       % (close, "/".join("MA%d" % n for n in want), len(below), need, streak))
                      if broken else
                      ("收盘 %.2f 未达破位（跌破 %s，需 ≥%d 根，连续 %d 日）"
                       % (close, below or "无", need, days))}
