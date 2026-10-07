# -*- coding: utf-8 -*-
"""wolf_exrights.py — 回测里的**除权复权**（用户 2026-09-22 拍板"修 3"）。

## 问题（一条会把"正常除权"记成"巨亏"的记账缺陷）

数据源给的日线是**未复权**的：除权日（送转/派息）当天价格按新基准跳低，
`bars.pre_close` 会被交易所改成**除权参考价**（≠ 昨收）。例：
`SH603061` 2026-04-15 收 333.66 → 04-16 `pre_close=229.85`（10 送/转 ≈ 4.5 股）⇒ 价格一夜 −31%。

我们这边的持仓是**固定股数**记账：`paper_positions.volume` 不变、`avg_price` 不变 ⇒
· 权益：`股数 × 现价` 一夜蒸发 31%（假回撤）；
· 成交：`paper_engine` 的卖出盈亏是**按 `paper_trades` 的买入行做 FIFO** 算的 ⇒ 卖出时凭空记一笔
  「成本 319.28 → 卖 239.11」的 **−8,017 假亏损**。
实测 T5（drabt5，75 天）：闭环轮次 31 笔中**只有 1 笔**持仓期遇除权，但它是账面**最大单笔亏损**
（SH603061 2026-04-14→04-20，−5,960）⇒ 足以把整条臂的结论带偏。

## 口径（`WOLF_EXRIGHTS_ADJUST`，**库内默认 0**；回测由 pins 置 1）

除权日**盘前**（`t_monitor._daily_maintain` 每交易日首次轮询前调用一次）对每个持仓标的：

1. 取因子 `f = 前一日收盘 / 当日 pre_close`（>1 = 送转/派息；`|f−1| < 0.5%` 视为无事件）；
2. 目标股数 `exact = 持仓 × f`；**按手取整** `lot = ⌊exact/100⌋×100`（A 股按手成交，
   留零股会让"卖出量按手取整"把它永久锁死 ⇒ 见 `wolf_build_lots` 同族的"一手仓"问题）；
3. 零股折现：`cash_credit = (exact − lot) × pre_close` 记入 `paper_account_info.available_cash`
   （**保权益连续**：`股数×现价 + 现金` 在除权日前后不变）；
4. 成本口径：把**未平仓买入行**按 `k = lot / 原持仓` 缩放入股数、按 `1/k` 反缩价格，
   再从总成本里扣掉 `cash_credit` ⇒ FIFO 的每股成本 = 除权后的真实成本，
   卖出盈亏不再凭空变负；
5. `paper_positions.volume/avg_price` 同步（`frozen` 同比例），并写审计
   `data/exrights_adjust.jsonl`。

**先校验、后落库**：调整前先按 FIFO 重建未平仓持仓，若"重建结果 ≠ paper_positions.volume"或
调整后重建不上，则**整笔跳过并告警**（fail-safe：退回现状，绝不写出记账不一致的账）。

## 已知边界（写下来别当没看见）

· **零股按 `pre_close` 折现**，而真实世界里那部分股份会随行情涨跌 ⇒ 若之后股价大涨，
  本口径会**少记**一点收益（本例 45 股 × (239.11−229.85) ≈ 417 元，占该轮 ~13%）。
· 只覆盖**持仓期**遇除权；除权当日的**新买入**按数据里的新基准价成交，无需调整。
· 数据源目前是回测的 `BT_BARS_DB`（默认 `data/_bt_full/bars.sqlite`）；生产要启用需另接
  当日 `pre_close` 的实时日线源（未接，开关默认关 ⇒ 生产逐位不变）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
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


ENV = "WOLF_EXRIGHTS_ADJUST"
LOT = 100
MIN_DEV = 0.005                      # |f−1| < 0.5% 视为无事件（避免把数据噪声当除权）


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def _bars_db() -> str:
    return os.getenv("BT_BARS_DB") or os.path.join("data", "_bt_full", "bars.sqlite")


def _std(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if s[:2] in ("SH", "SZ") and s[2:].isdigit():
        return s[2:] + "." + s[:2]
    return s


def factor(ts_code: str, day8: str, db: Optional[str] = None) -> Optional[Dict[str, float]]:
    """除权因子；非除权日/数据缺失 → None（fail-open，绝不乱调）。

    `f = 前一交易日收盘 / 当日 pre_close`；同时回传 `pre_close` / `prev_close` 供审计与折现。
    """
    path = db or _bars_db()
    if not os.path.exists(path):
        return None
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            cur = con.execute("SELECT trade_date, pre_close, close FROM bars WHERE ts_code=? "
                              "AND trade_date<=? ORDER BY trade_date DESC LIMIT 2", (_std(ts_code), str(day8)))
            rows = cur.fetchall()
        finally:
            con.close()
    except Exception as e:
        print("[exrights] 取数失败(跳过): %s" % str(e)[:80], flush=True)
        return None
    if len(rows) < 2 or str(rows[0][0]) != str(day8):
        return None                                   # 当日无行情（停牌/非交易日）
    pc = rows[0][1]
    prev = rows[1][2]
    if not pc or not prev or float(pc) <= 0 or float(prev) <= 0:
        return None
    f = float(prev) / float(pc)
    if abs(f - 1.0) < MIN_DEV:
        return None
    return {"f": f, "pre_close": float(pc), "prev_close": float(prev)}


# ─────────────────────────── 纯计算（可单测） ───────────────────────────
def plan_position(volume: int, avg_price: float, f: float, pre_close: float) -> Dict[str, Any]:
    """纯函数：给定持仓与除权因子 → 调整方案（不落库）。

    返回 {ok, lot, exact, credit, new_cost, new_avg, reason}；
    `ok=False` 时 reason 说明为什么不动（不足一手 / 参数非法 / 调整幅度可忽略）。
    """
    v = int(volume or 0)
    ap = float(avg_price or 0)
    if v <= 0 or ap <= 0 or f <= 0 or pre_close <= 0:
        return {"ok": False, "reason": "参数非法/无持仓"}
    exact = v * float(f)
    lot = int(exact // LOT) * LOT
    if lot < LOT:
        return {"ok": False, "reason": "调整后不足一手（%d 股），不调整" % int(exact), "exact": exact}
    if lot == v:
        # 股数不变（例如 100 股遇 10 送 4.5 ⇒ 145 取整回 100）：仍要折现零股 + 摊薄成本
        credit = (exact - lot) * float(pre_close)
        if credit <= 0:
            return {"ok": False, "reason": "股数与零股折现都为 0", "exact": exact}
    else:
        credit = (exact - lot) * float(pre_close)
    cost = v * ap
    new_cost = cost - credit
    if new_cost <= 0:
        return {"ok": False, "reason": "折现后成本 ≤0（异常）", "exact": exact, "credit": credit}
    return {"ok": True, "lot": lot, "exact": exact, "credit": credit,
            "new_cost": new_cost, "new_avg": new_cost / float(lot),
            "f": float(f), "pre_close": float(pre_close)}


def restate_rows(lots: Sequence[Sequence[Any]], k: float, credit: float,
                 total_cost: float) -> List[Tuple[int, float, int]]:
    """纯函数：把**未平仓买入行** `[(row_id, price, volume)]` 按 `k` 缩股数、按 `1/k` 反缩价格，
    再按比例扣掉零股折现 `credit`。返回 `[(row_id, new_price, new_volume)]`（总成本守恒）。"""
    tot_v = sum(int(r[2] or 0) for r in lots)
    if tot_v <= 0 or k <= 0:
        return []
    out: List[Tuple[int, float, int]] = []
    left_v = int(round(tot_v * k))
    keep = max(1.0 - (float(credit) / float(total_cost)), 0.0) if total_cost > 0 else 1.0
    for i, (rid, price, vol) in enumerate(lots):
        v0 = int(vol or 0)
        if i == len(lots) - 1:
            v1 = left_v                                   # 末行吃掉取整残差，保证 Σ 与目标一致
        else:
            v1 = int(round(v0 * k))
            left_v -= v1
        if v1 <= 0:
            continue
        base = float(price or 0) * v0 * keep              # 该行分摊折现后的成本
        out.append((int(rid), base / float(v1), v1))
    return out


# ─────────────────────────── 落库 ───────────────────────────
def _open_lots(cur, account_id: str, symbol: str) -> List[List[Any]]:
    """按 `paper_engine` 同款 FIFO 重建**未平仓买入行** → [[row_id, price, volume], ...]。"""
    cur.execute("""SELECT id, price, volume FROM paper_trades
                   WHERE account_id=%s AND symbol=%s AND direction IN ('买入','buy')
                     AND (voided=0 OR voided IS NULL)
                   ORDER BY COALESCE(trade_date, created_at::date::text), id""", (account_id, symbol))
    buys = [[r[0], float(r[1] or 0), int(r[2] or 0)] for r in cur.fetchall()]
    cur.execute("""SELECT volume FROM paper_trades
                   WHERE account_id=%s AND symbol=%s AND direction IN ('卖出','sell')
                     AND (voided=0 OR voided IS NULL)
                   ORDER BY COALESCE(trade_date, created_at::date::text), id""", (account_id, symbol))
    sells = [int(r[0] or 0) for r in cur.fetchall()]
    for sv in sells:
        rem = sv
        while rem > 0 and buys:
            take = min(rem, buys[0][2])
            buys[0][2] -= take
            rem -= take
            if buys[0][2] <= 0:
                buys.pop(0)
    return [b for b in buys if b[2] > 0]


def _audit(rec: Dict[str, Any]) -> None:
    try:
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "exrights_adjust.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as _e_sil1:
        _silent_alert("wolf_exrights.py:187", _e_sil1)


def apply_day(account_id: str, day8: str, dry_run: bool = False) -> Dict[str, Any]:
    """除权日盘前调整：对每个持仓标的重算股数/成本/现金。返回摘要（也写审计）。"""
    out: Dict[str, Any] = {"on": enabled(), "day": day8, "account": account_id,
                           "adjusted": [], "skipped": [], "errors": []}
    if not enabled():
        return out
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL") or ""
        if not dsn:                      # 与全项目同源：回测/生产都由 app.database 解析 DSN
            from app.database import DATABASE_URL as _du
            dsn = _du
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        cur = conn.cursor()
    except Exception as e:
        out["errors"].append("连库失败: %s" % str(e)[:80])
        return out
    try:
        cur.execute("SELECT symbol, volume, avg_price, COALESCE(frozen,0) FROM paper_positions "
                    "WHERE account_id=%s AND volume > 0", (account_id,))
        poss = cur.fetchall()
        for symbol, volume, avg_price, frozen in poss:
            fac = factor(symbol, day8)
            if not fac:
                continue
            p = plan_position(int(volume or 0), float(avg_price or 0), fac["f"], fac["pre_close"])
            if not p.get("ok"):
                out["skipped"].append({"symbol": symbol, "why": p.get("reason"), "volume": int(volume or 0)})
                continue
            lots = _open_lots(cur, account_id, symbol)
            if sum(l[2] for l in lots) != int(volume or 0):
                out["skipped"].append({"symbol": symbol, "volume": int(volume or 0),
                                       "why": "FIFO 重建持仓(%d) ≠ paper_positions(%d)，跳过（fail-safe）"
                                              % (sum(l[2] for l in lots), int(volume or 0))})
                continue
            k = float(p["lot"]) / float(volume)
            new_rows = restate_rows(lots, k, p["credit"], p["new_cost"] + p["credit"])
            if not new_rows or sum(r[2] for r in new_rows) != p["lot"]:
                out["skipped"].append({"symbol": symbol, "why": "行重述后股数不守恒，跳过"})
                continue
            rec = {"symbol": symbol, "day": day8, "account": account_id, "f": round(fac["f"], 6),
                   "prev_close": fac["prev_close"], "pre_close": fac["pre_close"],
                   "volume": int(volume or 0), "volume_new": p["lot"],
                   "avg_old": round(float(avg_price or 0), 4), "avg_new": round(p["new_avg"], 4),
                   "credit": round(p["credit"], 2), "lots": len(new_rows),
                   "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            out["adjusted"].append(rec)
            if dry_run:
                continue
            try:
                for rid, px, vol in new_rows:
                    cur.execute("UPDATE paper_trades SET price=%s, volume=%s WHERE id=%s", (px, vol, rid))
                cur.execute("UPDATE paper_positions SET volume=%s, avg_price=%s, frozen=%s, updated_at=%s "
                            "WHERE account_id=%s AND symbol=%s",
                            (p["lot"], p["new_avg"],
                             int(round(int(frozen or 0) * k)),
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), account_id, symbol))
                if p["credit"] > 0:
                    cur.execute("UPDATE paper_account_info SET available_cash = available_cash + %s, "
                                "updated_at=%s WHERE account_id=%s",
                                (p["credit"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), account_id))
            except Exception as e:
                conn.rollback()
                out["errors"].append("%s 落库失败已回滚: %s" % (symbol, str(e)[:80]))
                out["adjusted"] = [r for r in out["adjusted"] if r["symbol"] != symbol]
                continue
            _audit(rec)
            print("[exrights] 除权复权 %s: %d→%d 股 · 成本 %.4f→%.4f · 零股折现 %+.2f（f=%.4f）"
                  % (symbol, int(volume or 0), p["lot"], float(avg_price or 0), p["new_avg"],
                     p["credit"], fac["f"]), flush=True)
        if not dry_run:
            conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception as _e_sil2:
            _silent_alert("wolf_exrights.py:267", _e_sil2)
        out["errors"].append("执行异常: %s" % str(e)[:120])
    finally:
        try:
            conn.close()
        except Exception as _e_sil3:
            _silent_alert("wolf_exrights.py:273", _e_sil3)
    return out
