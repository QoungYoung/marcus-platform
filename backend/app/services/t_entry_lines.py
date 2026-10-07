# -*- coding: utf-8 -*-
"""入场线（狼大语料）：**买腿挂在下方最近的 13/34/60/144 均线** + **只在下跌里买** + **破线撤腿**。

语料原文（docs/wolf-behavior-blueprint.md:230 / 200 / 450，2021 与 2026-08/09）：
  · 「今天踩144就可以上一点啊 另一部分挂在13就行，**这两根破了这个标我就不看了**」
  · 「要买是上周买 **跌的时候买**，今天这个开盘拉的速度正常就不是给你买的」（2026-08-31）
  · 「**高开不追**是基本常识」（2026-09-03）
  · 「事先给出的条件被触发（跌破某位、或先于对手方向企稳放量上）；**只在下跌里买**」

设计（把他的话固化成可回测的确定规则）：
  1. `entry_line_target(symbol, price)`：取 **现价下方最近的 13/34/144 均线**作为第一笔挂价；
     次近的一根作为第二笔（他"踩144上一点、另一部分挂13"就是这个意思）。价格在全部均线下方时 → 返回 None（不挂）。
  2. `decline_only_ok(current, prev_close)`：**只在下跌里买** —— 现价必须低于前收。
  3. `line_broken(current, line)`：跌破所挂线超过 `BREAK_PCT` → 该标的当日**撤腿**（不是止损）。
开关 `WOLF_T_ENTRY_LINES`（回测侧默认开，见 `jobs/bt_prod_run.py`；生产不加载本模块的开关语义）。
数据：日线取 `data/_bt_full/bars.sqlite`（自 20250102 起，2026-01-05 前有 243 个交易日 ⇒ MA144 可算）。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BARS_DB = os.getenv("BT_BARS_DB", os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))

LINES: Tuple[int, ...] = (13, 34, 60, 144)  # 狼大均线体系：蓝图 218 行「13/34/60/144」（此前漏了 60）
ENTRY_LINES = str(os.getenv("WOLF_T_ENTRY_LINES", "0")).strip().lower() in ("1", "true", "yes", "on")
DECLINE_ONLY = str(os.getenv("WOLF_T_DECLINE_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")
BREAK_PCT = float(os.getenv("WOLF_T_LINE_BREAK_PCT", "1.0"))   # 跌破幅度（%）超过它 → 撤腿
_STATS: Dict[str, int] = {"target_line": 0, "no_line": 0, "decline_block": 0, "line_break": 0}
_CACHE: Dict[Tuple[str, str], List[float]] = {}


def stats() -> Dict[str, Any]:
    return {"enabled": bool(ENTRY_LINES), "decline_only": bool(DECLINE_ONLY),
            "break_pct": BREAK_PCT, **dict(_STATS)}


def stats_reset() -> None:
    for _k in _STATS:
        _STATS[_k] = 0


def to_ts_code(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    return s


def moving_averages(symbol: str, day: str = "", db: str = "") -> Dict[int, float]:
    """≤ `day`（不含当日）的 13/34/144 收盘均线；缺数据时该均线不出现。"""
    code = to_ts_code(symbol)
    key = (code, str(day or ""))
    if key in _CACHE:
        return _CACHE[key]
    out: Dict[int, float] = {}
    try:
        con = sqlite3.connect(db or BARS_DB)
        cur = con.cursor()
        sql = "SELECT close FROM bars WHERE ts_code=? AND close>0"
        args: List[Any] = [code]
        if day:
            sql += " AND trade_date < ?"
            args.append(str(day))
        sql += " ORDER BY trade_date DESC LIMIT ?"
        args.append(max(LINES))
        rows = [float(r[0]) for r in cur.execute(sql, args)]
        con.close()
        for n in LINES:
            if len(rows) >= n:
                out[n] = round(sum(rows[:n]) / float(n), 3)
    except Exception:
        out = {}
    _CACHE.clear()          # 单进程内按 (code, day) 命中即可；避免跨日缓存膨胀
    _CACHE[key] = out
    return out


def nearest_lines_below(price: float, mas: Dict[int, float]) -> List[Tuple[int, float]]:
    """现价**下方**的均线，按从高到低排序（第一根=第一笔挂价）。"""
    p = float(price or 0)
    if p <= 0:
        return []
    return sorted([(n, v) for n, v in (mas or {}).items() if 0 < v < p],
                  key=lambda kv: -kv[1])


def entry_line_target(symbol: str, price: float, day: str = "", db: str = "") -> Optional[float]:
    """买腿第一笔挂价 = 现价下方最近的那根均线；全部均线都在现价上方 → None（不挂）。"""
    lines = nearest_lines_below(price, moving_averages(symbol, day, db))
    if not lines:
        _STATS["no_line"] += 1
        return None
    _STATS["target_line"] += 1
    return float(lines[0][1])


def entry_line_targets(symbol: str, price: float, day: str = "", db: str = "") -> List[float]:
    """两笔挂价（最近、次近），对应语料"踩144上一点、另一部分挂13"。"""
    return [float(v) for _n, v in nearest_lines_below(price, moving_averages(symbol, day, db))[:2]]


# ══════════════════════════════════════════════════════════════════════════
# 「入场线」再受**不追高上限**夹住（账本 §9.748 ✓，用户 2026-10-07 选 A ✓）
#
# 为什么需要 ✗：
#   `entry_line_target` 已是「现价下方最近的那根均线」✓，但它**没有上限** ✗ ——
#   若现价远高于成本，那根"最近的均线"可能仍**高于 成本×1.04** ✗
#   ⇒ 条件一触发就被「加仓口径（高于本轮首笔买入价 +4.0%）」拦 ✗
#   ⇒ 实测 ✗：`wolf_zheng_t_buy` 触发 37 次 ⇒ **30 次 blocked**，其中
#      **24 次（65%）触发价 > 首笔×1.04** ✓（成交仅 2 笔 ✓）
#
# 依据（**双份原文** ✓，不自己编 ✗）：
#   · 狼大：「**怕忍不住买回来…找低位的线挂进去 挂远一点**」（2026-04-10）
#          「**冲上去一定不能追**」（2025-04-03）
#   · 我们自己的提示词（`prompt_seeds.py:973-976` ✓）：
#        「★ **关键位优先（用户口径原文）**：好票跌到事先画好的线（13/34/60/144 等）…
#          选最贴近现价的那条线；**严禁挂现价 / 市价 / 成本附近** ✗」
#   ⇒ 二者合起来 = 挂关键位 ✓ **且**不超过"成本×1.04" ✓
#
# 开关 ✓：`WOLF_T_BUY_CAP_104`（**库内默认 0 ⇒ 生产逐位不变** ✓；回测由 pins 置 1 ✓）
#        上限比例 `WOLF_T_BUY_CAP_PCT`（默认 4.0 ✓ = 既有「加仓口径」同值 ✓）
# 关掉时 ✓：本函数逐字返回 `entry_line_target(...)` ✓（行为完全不变 ✓）
# ══════════════════════════════════════════════════════════════════════════
BUY_CAP_104 = str(os.getenv("WOLF_T_BUY_CAP_104", "0")).strip().lower() in ("1", "true", "yes", "on")
BUY_CAP_PCT = float(os.getenv("WOLF_T_BUY_CAP_PCT", "4.0") or 4.0)


def capped_line_target(symbol: str, price: float, cost: float,
                       day: str = "", db: str = "") -> Optional[float]:
    """入场线挂价，但**再受」成本×(1+cap%)「夹住** ⇒ `min(最近均线, 上限)` ✓

    · 开关关 ✓ ⇒ 与 `entry_line_target` **逐字相同** ✓（生产零影响 ✓）
    · 上限取 `cost × (1 + BUY_CAP_PCT/100)` ✓（默认 4.0% ✓，与「加仓口径」同值 ✓）
    · 线与上限都取不到 ⇒ `None` ✓（调用方回退成本公式 ✓，与旧行为一致 ✓）
    """
    line = entry_line_target(symbol, price, day=day, db=db)
    if not BUY_CAP_104:
        return line
    c = float(cost or 0)
    if c <= 0:
        return line
    cap = round(c * (1.0 + BUY_CAP_PCT / 100.0), 2)
    if line is None:
        return None
    if float(line) > cap:
        _STATS["capped_104"] = int(_STATS.get("capped_104", 0)) + 1
        return cap
    return line


def decline_only_ok(current: float, prev_close: float,
                    enabled: Optional[bool] = None) -> bool:
    """**只在下跌里买**：现价必须低于前收。数据缺失 → 放行（fail-open，不因取数问题拦真实买腿）。"""
    on = DECLINE_ONLY if enabled is None else bool(enabled)
    if not on:
        return True
    c, pc = float(current or 0), float(prev_close or 0)
    if c <= 0 or pc <= 0:
        return True
    ok = c < pc
    if not ok:
        _STATS["decline_block"] += 1
    return ok


def line_broken(current: float, line: float, pct: Optional[float] = None) -> bool:
    """跌破所挂线超过 `pct`% → True（该标的当日撤腿；语料"这两根破了这个标我就不看了"）。"""
    c, ln = float(current or 0), float(line or 0)
    if c <= 0 or ln <= 0:
        return False
    p = BREAK_PCT if pct is None else float(pct)
    if c < ln * (1.0 - p / 100.0):
        _STATS["line_break"] += 1
        return True
    return False
