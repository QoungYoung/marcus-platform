# -*- coding: utf-8 -*-
"""wolf_ma_line_entry.py — 「跌到 13/34/60/144 线上挂单买」的**影子**（2026-09-15 round 12）。

狼大原话：「好票**跌到事先画好的线上（13/34/60/144）**…波段起头时把自选里**跌下来的都挂上**」（4 条线来自原话）。

离线验收（`jobs/eval_fib_ma_entry.py`，总账 §18）：这条**口径明确**，但属**同类替换**而非新增收益来源 ——
成交率 25.9%（现行 254 是 52%），**每条已挂腿期望 −0.151% @h5 / −0.181% @h10**，
优于现行 `254 前低挂单`（−0.293% / −0.210%），但**不如 `253` 次日收盘**（−0.045% / +0.398%）。
→ 所以本模块**只记录**（影子），把"如果挂在最近均线上会怎样"落到生产文件里，供拍板"是否用均线挂单替换 254"。

口径：
  · 线集合 `LINES=(13,34,60,144)`（**他的话**）；触发价 = 当日**收盘价下方最近**的一条均线（= 等回踩到线上挂单）；
  · 记录每条腿的：收盘、最近线（周期与值）、距该线的跌幅%、以及"若挂在线上、次日触及则成交"的静态信息
    （真实成交要等次日，故只记挂单价与距离）。
  · **不改任何决策**：不新增买腿、不改 253/254 的表达式。

开关：`WOLF_MA_LINE_SHADOW` 默认 **1**（只记录）｜`WOLF_MA_LINE_ENTRY` 默认 **0**（保留：将来若要真挂腿）
自检：`python apps/main_line/wolf_ma_line_entry.py`
"""
from __future__ import annotations

import json
import os
import time
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


DATA = os.environ.get("DATA_DIR", "/app/data")
LINES: Tuple[int, ...] = (13, 34, 60, 144)      # 他的话
STATE_PREFIX = "ma_line_shadow_"


def shadow_enabled() -> bool:
    return os.getenv("WOLF_MA_LINE_SHADOW", "1").strip() not in ("0", "false", "no")


def entry_enabled() -> bool:
    """是否真的把均线挂单当成一条买腿（默认 0；离线证据只支持"同类替换"，需用户拍板）。"""
    return os.getenv("WOLF_MA_LINE_ENTRY", "0").strip() in ("1", "true", "yes")


def nearest_line_below(closes: Sequence[float], px: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """当日收盘价**下方最近**的一条均线 → {"k": 周期, "v": 均线值, "dist_pct": 距线的跌幅%}；没有则 None。"""
    vals = [float(x) for x in (closes or []) if x is not None and float(x) == float(x)]
    if len(vals) < min(LINES):
        return None
    px = float(vals[-1]) if px is None else float(px)
    best = None
    for k in LINES:
        if len(vals) < k:
            continue
        ma = sum(vals[-k:]) / k
        if ma <= 0 or ma > px:
            continue
        if best is None or ma > best["v"]:
            best = {"k": k, "v": round(ma, 4), "dist_pct": round((px / ma - 1.0) * 100.0, 3)}
    return best


def leg_info(symbol: str, closes: Sequence[float], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单条腿的影子信息。"""
    vals = [float(x) for x in (closes or []) if x is not None and float(x) == float(x)]
    px = vals[-1] if vals else None
    line = nearest_line_below(vals, px)
    rec = {"symbol": symbol, "close": None if px is None else round(px, 4), "bars": len(vals),
           "line": line, "would_place": bool(line)}
    if extra:
        rec.update(extra)
    return rec


def shadow_record(chain: str, legs: Sequence[Dict[str, Any]]) -> Optional[str]:
    """把某条链本轮选出的腿逐条写入 `data/ma_line_shadow_<date>.json`（按链覆盖，累加多条链）。"""
    if not shadow_enabled():
        return None
    d8 = time.strftime("%Y%m%d")
    fn = os.path.join(DATA, STATE_PREFIX + d8 + ".json")
    rec: Dict[str, Any] = {"date": d8, "mode": "shadow", "lines": list(LINES), "chains": {}}
    try:
        if os.path.exists(fn):
            try:
                cur = json.load(open(fn, encoding="utf-8")) or {}
                if isinstance(cur, dict) and cur.get("chains"):
                    rec["chains"] = cur["chains"]
            except Exception as _e_sil1:
                _silent_alert("wolf_ma_line_entry.py:85", _e_sil1)
        rec["chains"][chain] = {"legs": list(legs), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rec["updated_at"] = rec["chains"][chain]["ts"]
        os.makedirs(DATA, exist_ok=True)
        tmp = fn + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fn)
    except Exception as e:
        print("[MALine] 影子写盘失败: %s" % str(e)[:80])
        return None
    n = sum(len(v.get("legs") or []) for v in rec["chains"].values())
    print("MA_LINE_SHADOW chain=%s 本条链 %d 腿（当日累计 %d 腿）"
          % (chain, len(legs), n))
    return fn


if __name__ == "__main__":
    demo = [100.0 + i for i in range(160)]          # 上行序列（在线下方应找不到线）
    print(json.dumps({"lines": list(LINES), "shadow": shadow_enabled(), "entry": entry_enabled(),
                      "demo_nearest": nearest_line_below(demo)},
                     ensure_ascii=False, indent=1))

def _bars_db() -> str:
    """与闸门用的**分钟档同一复权空间**的日线库：分钟目录含 `_adj` ⇒ bars_adj.sqlite，否则 bars.sqlite。"""
    md = str(os.getenv("WOLF_MINS_DIR") or "")
    adj = ("_adj" in md) or (not md and str(os.getenv("WOLF_ADJ_PRICE", "0")) in ("1", "true", "yes", "on"))
    name = "bars_adj.sqlite" if adj else "bars.sqlite"
    for cand in (os.getenv("BT_BARS_ADJ_DB" if adj else "BT_BARS_DB") or "",
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "data", "_bt_full", name)):
        if cand and os.path.exists(cand):
            return cand
    return ""


def daily_closes(symbol: str, day: str, n: int = 150) -> List[float]:
    """≤day 的最近 n 个日线收盘（升序）；库缺/查不到 ⇒ []（fail-open，调用方据此不介入）。"""
    db = _bars_db()
    if not db:
        return []
    s = str(symbol or "").upper()
    code = (s[2:] + "." + s[:2]) if (s[:2] in ("SH", "SZ") and len(s) >= 8) else s
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        con.execute("PRAGMA temp_store=MEMORY")
        rows = con.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date<=? AND close>0 "
                           "ORDER BY trade_date DESC LIMIT ?", (code, str(day).replace("-", "")[:8], int(n))).fetchall()
        con.close()
        return [float(r[0]) for r in reversed(rows)]
    except Exception as e:
        print("[ma_line] daily_closes 失败: %s: %s" % (type(e).__name__, str(e)[:60]), flush=True)
        return []


def line_values(symbol: str, day: str, px: Optional[float] = None) -> List[float]:
    """13/34/60/144 四条均线的**当前值**（供闸门"到线"判据用）；数据不足 ⇒ 只返回能算的那几条。"""
    vals = daily_closes(symbol, day)
    if not vals:
        return []
    cur = float(px) if px else float(vals[-1])
    _all = list(vals) + [cur]          # 用"现价"作为最后一个收盘的代理
    out = []
    for k in LINES:
        if len(_all) >= k:
            out.append(sum(_all[-k:]) / k)
    return out
