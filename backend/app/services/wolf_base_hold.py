# -*- coding: utf-8 -*-
"""wolf_base_hold.py — **止盈类卖腿只卖 T 仓（底仓不动）**（用户 2026-09-21 拍板方案③）。

═══════════════════════════════════════════════════════════════════════════
## 语料
· 2026-08-05 10:38「所以我一直说 **做 T 套利的仓位是做 T 套利的 底仓是底仓**。」
· 2026-08-05 13:30「**分不清做 T 仓位和底仓的区别就是『贪』**。」
· 2026-09-03 14:14「**仓位不会低于 65% 收盘，日内做 T 仓位 20%**。」
· 2026-04-14「**50% 的底仓 30% 左右做日内**…剩下 20% 应对黑天鹅。」
⇒ 兑现（高抛/斐波止盈）作用的**只是 T 仓**；底仓的处置是另一套（破位/减仓语义，见
  `t_capacity.WOLF_SELL_BASE_EXEMPT` 与 2025-11-23 / 2025-08-13 他减底仓的原话）。

## 为什么需要（实测证据，drabj13 0105→0428）
· 现状：平均仓位 **15.2%**、中位 10.5%、**最高 55.2%**、≥60% 天数 **0**（两分法底仓目标 65%）。
· 卖出结构：**高抛/斐波 103 笔**，单笔卖量中位 = 当时该标的持仓的 **50%**，其中 **25% 卖光**；
  轮次持有中位 **2 天**，≤3 天占 **72%** ⇒ 「建仓→高抛→再建仓」，仓位永远回不到目标。
· 只读叠加（事件集=实际 74 次买入，只改卖量保留 + 单笔步长）：
  | 方案 | 平均仓位 | 期末权益 | 最大回撤 |
  |---|---|---|---|
  | 实际 | 15.2% | 259,274 | −5.0% |
  | 底仓不动 + 缺口÷3 | 41.8% | 253,629 | −9.0% |
  | **底仓不动 + 缺口÷2（本方案③）** | **51.7%** | **264,790** | **−11.7%** |
  | 底仓不动 + 一次打满 | 61.6% | 307,066 | −18.6% |

## 口径
· `WOLF_TAKE_SELL_T_ONLY`（库内默认 **0**）：止盈类卖腿（reason 含 高抛/fib/profit_take/止盈）
  的可卖量上限 = **近 N 个交易日（不含当日）买入的份额**（= 真 T 仓弹药）。
· `WOLF_TAKE_SELL_T_DAYS`（默认 **3**）：上面那个 N。
· **不作用**于破位/减仓/止损类（他确实会减底仓，别一刀切）。
· 关掉时逐字返回旧行为（生产零影响）。
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import os
from datetime import datetime
from typing import Any, Iterable, List, Optional, Sequence, Tuple

_TAKE_KW = ("高抛", "fib", "profit_take", "止盈")


def enabled() -> bool:
    return os.getenv("WOLF_TAKE_SELL_T_ONLY", "0").strip().lower() in ("1", "true", "yes", "on")


def days() -> int:
    try:
        return max(0, int(float(os.getenv("WOLF_TAKE_SELL_T_DAYS", "3") or 3)))
    except Exception:
        return 3


def is_take_reason(reason: Any) -> bool:
    """止盈/高抛类卖腿（做T兑现）⇒ 只动 T 仓。破位/减仓/止损类不走这条。"""
    r = str(reason or "")
    return any(k in r for k in _TAKE_KW)


def _today8() -> str:
    """当前交易日 YYYYMMDD（回测里 `bt_prod_run._pin_clock_dynamic()` 已把 datetime 钉到回放日）。"""
    try:
        return datetime.now().strftime("%Y%m%d")
    except Exception:
        return ""


def market_days(today: Optional[str] = None, n: Optional[int] = None) -> List[str]:
    """今天（含）往前 n 个交易日（升序，末位=今天）；取不到日历 → 返回 []（调用方走回落口径）。

    口径与 `wolf_weekend_hedge.recent_trade_days` / `wolf_buy_line.bars_sqlite` 一致：**本地
    `BT_BARS_DB`（默认 data/_bt_full/bars.sqlite）的 distinct trade_date**。交易日历是公开日历信息
    （知道 20260112 是交易日不构成未来函数），回测里同样可用；生产取不到时回落标的自有成交日。
    """
    t = str(today or _today8())
    k = int(n or days()) + 1                     # 含今天
    if not t or k <= 0:
        return []
    try:
        import os as _os
        import sqlite3 as _sq
        db = _os.getenv("BT_BARS_DB") or "data/_bt_full/bars.sqlite"
        if not _os.path.exists(db):
            return []
        lo = _shift_days(t, -(k * 3 + 12))       # 交易日 ≤ 自然日，*3 足够富余
        con = _sq.connect("file:%s?mode=ro" % db, uri=True)
        try:
            rows = con.execute(
                "SELECT DISTINCT trade_date FROM bars WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
                (lo, t)).fetchall()
        finally:
            con.close()
        ds = sorted({str(r[0]).replace("-", "")[:8] for r in rows if str(r[0]).replace("-", "")[:8].isdigit()})
        return ds[-k:] if len(ds) >= k else (ds or [])
    except Exception as e:
        print("[base_hold] 交易日历取数失败(回落标的自有成交日): %s" % str(e)[:70], flush=True)
        return []


def _shift_days(day8: str, delta: int) -> str:
    import datetime as _dt
    d = _dt.datetime.strptime(str(day8)[:8], "%Y%m%d").date()
    return (d + _dt.timedelta(days=int(delta))).strftime("%Y%m%d")


def sleeve_shares(seq: Sequence[Sequence[Any]], days_n: Optional[int] = None,
                  today: Optional[str] = None, calendar: Optional[Sequence[str]] = None) -> int:
    """纯函数：`seq=[(day8, direction, volume), ...]`（按时间升序）→ 近 N 个交易日（**不含当日**）的买入份额。

    ⚠️ 2026-09-22 修 bug（用户点名兆易创新 603986 那笔亏损的根因之一）：
      **原实现把"序列里的最大日期"当成"当日"** —— 而序列只含**该标的自己的成交日**，
      于是"昨天买、今天还没再成交"的票，那笔买入会被当成"当日买入(T+1 不可卖)"排除 ⇒ T 仓=0 ⇒
      **止盈/高抛/倒T 卖腿在买入后的第二天全部被拦**（实测兆易创新 0317 买 307 → 0318 冲到 313.68，
      三条卖腿全被拦死，拖到 0323 才靠破位腿割在 277.55，−2,987）。
    现在：**"当日"= 真实当前交易日**（`today`/钉钟）；"近 N 个交易日"优先用**真实交易日历**
    （`calendar`/`market_days`），日历取不到才回落"标的自有成交日里 < 当日的最后 N 个"。
    """
    n = days() if days_n is None else int(days_n)
    if n <= 0 or not seq:
        return 0
    t = str(today or _today8())
    ds = sorted({str(r[0]) for r in seq})
    cal = (list(calendar)[-(n + 1):] if calendar else market_days(t, n))
    if cal:
        keep = set(cal) - {t}                    # 日历末位=今天 ⇒ 去掉今天即"近 n 个交易日，不含当日"
    else:
        if not t:
            return 0
        keep = set([d for d in ds if d < t][-n:])   # 回落：标的自有成交日（仍是 < 当日，不再把昨天当今天）
    tot = 0
    for r in seq:
        d, dr, v = str(r[0]), str(r[1]), int(r[2] or 0)
        if d in keep and (dr.startswith("买") or dr == "buy"):
            tot += v
    return tot


def sleeve_from_db(account_id: str, symbol: str) -> int:
    """从 paper_trades 取「近 N 个交易日（不含当日）买入份额」= T 仓弹药。异常 → 0（调用方按"不能卖"处理）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT trade_date, direction, volume FROM paper_trades "
                "WHERE account_id = :a AND symbol = :s AND (voided = 0 OR voided IS NULL) "
                "ORDER BY created_at, id"), {"a": account_id, "s": symbol}).fetchall()
            seq = [(str(r[0]).replace("-", "")[:8], str(r[1]), int(r[2] or 0)) for r in rows]
            return sleeve_shares(seq)
        finally:
            db.close()
    except Exception as e:
        print("[base_hold] 查询 T 仓失败(按 0 处理): %s" % str(e)[:80], flush=True)
        return 0


def cap_volume(volume: int, sleeve: int) -> Tuple[int, str]:
    """(可卖量, 原因)。开关关 ⇒ 原样返回。"""
    v = int(volume or 0)
    if not enabled():
        return v, ""
    s = max(int(sleeve or 0), 0)
    if s <= 0:
        return 0, ("底仓不动：近 %d 个交易日无买入 ⇒ T 仓=0，本次止盈腿不执行"
                   "（狼大 2026-08-05「做T套利的仓位是做T套利的 底仓是底仓」）" % days())
    if v > s:
        return s, ("底仓不动：止盈腿只卖 T 仓 %d 股（近 %d 个交易日买入），收敛 %d→%d"
                   % (s, days(), v, s))
    return v, "底仓不动：在 T 仓 %d 股之内" % s
