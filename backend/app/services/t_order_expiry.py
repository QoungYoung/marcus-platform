# -*- coding: utf-8 -*-
"""挂单日终作废 → **释放冻结资金/冻结股**（2026-09-18 用户："建仓/冻结资金释放修复下"）。

【为什么必须有】A 股挂单**当日有效**（收盘未成交自动作废）；本系统 `paper_orders` 里没有这道收尾：
  实测（回测 drab_on/drab_off 两臂）**一笔未成交买单会把资金冻结到窗口结束**：
    · drab_on  `SZ002602 买入 1400@19.081`（2026-01-26 13:40 挂出，status=提交中）→ 冻结 26,726.76 元，
      直到 0203 收盘仍冻结 ⇒ 账户可用资金长期少 2.7 万、仓位被压到 ~11%；
    · drab_off `SH603203 买入 700@36.723`（2026-01-08 10:00）→ 冻结 25,718.95 元，同样整窗未释放；
    · 同一臂还有一笔卖单 `SH603203 卖出 100@38.781`（2026-01-19）卡在"提交中"⇒ 持仓 `frozen` 也不释放。
  这与用户早先"底仓到不了 65%"同源：**不是建仓腿不出手，而是出手后钱被挂单占着**。
  `backend/app/api/trades.py` 里"订单会自动超时取消"的注释在当前代码里**没有对应实现**（只有条件 expire），故在此补齐。

【口径】只动"**上一交易日及更早**仍处于 提交中/部分成交 的挂单"：
  · 买单：释放 `price × (volume − traded)`（未成交部分的名义金额）→ `available_cash += 释放额`、
    `frozen_cash = max(frozen_cash − 释放额, 0)`；
  · 卖单：按标释放 `volume − traded` 股 → `paper_positions.frozen = max(frozen − 释放股, 0)`；
  · 订单置 `已撤单`，reason 追加 `[日终作废(当日有效)]` 便于审计。
【开关】`WOLF_EXPIRE_STALE_ORDERS`，**库内默认 0**（生产行为逐位不变）；回测驱动 `bt_prod_run.py` 里 setdefault=1。
  生产要生效需显式置 1（`t_gateway.gateway_execute` 每进程每日一次的钩子会顺带生效）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


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


_DONE: Dict[str, str] = {}          # account_id -> day8（进程内"每日一次"）
_STATS: Dict[str, Any] = {"scanned": 0, "expired_buy": 0, "expired_sell": 0,
                          "released_cash": 0.0, "released_shares": 0, "errors": 0, "runs": 0}


def enabled() -> bool:
    return str(os.getenv("WOLF_EXPIRE_STALE_ORDERS", "0")).strip().lower() in ("1", "true", "yes", "on")


def stats() -> Dict[str, Any]:
    return {"enabled": enabled(), **_STATS}


def release_amount(price: float, volume: int, traded: int) -> float:
    """买单未成交部分的名义金额（冻结额）。非正数 → 0（不误释放）。"""
    try:
        rem = int(volume or 0) - int(traded or 0)
        return max(0.0, float(price or 0) * max(0, rem))
    except Exception:
        return 0.0


def remaining_volume(volume: int, traded: int) -> int:
    """未成交股数（卖单释放用）。"""
    try:
        return max(0, int(volume or 0) - int(traded or 0))
    except Exception:
        return 0


def expire_stale_orders(conn=None, account_id: Optional[str] = None, today: Optional[str] = None,
                        enabled_override: Optional[bool] = None) -> Dict[str, Any]:
    """作废"上一交易日及更早"的未成交挂单并释放冻结资金/股数。返回统计（失败 fail-open）。"""
    on = enabled() if enabled_override is None else bool(enabled_override)
    if not on:
        return {"skipped": "switch_off"}
    d8 = str(today or "").replace("-", "")[:8]
    if not d8:
        import datetime as _dt
        d8 = _dt.datetime.now().strftime("%Y%m%d")
    _iso = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
    own = conn is None
    out: Dict[str, Any] = {"day": d8, "scanned": 0, "expired_buy": 0, "expired_sell": 0,
                           "released_cash": 0.0, "released_shares": 0, "errors": 0}
    try:
        if own:
            import psycopg2
            conn = psycopg2.connect(os.getenv("DATABASE_URL", ""))
            conn.autocommit = True
        cur = conn.cursor()
        sql = ("SELECT orderid, account_id, symbol, direction, price, volume, COALESCE(traded,0) "
               "FROM paper_orders WHERE status IN ('提交中','部分成交') "
               "AND substr(created_at, 1, 10) < %s")
        args: list = [_iso]
        if account_id:
            sql += " AND account_id = %s"
            args.append(account_id)
        cur.execute(sql, tuple(args))
        rows = cur.fetchall()
        out["scanned"] = len(rows)
        _STATS["scanned"] += len(rows)
        for orderid, acc, symbol, direction, price, volume, traded in rows:
            try:
                _is_buy = str(direction) in ("买入", "buy")
                if _is_buy:
                    amt = release_amount(price, volume, traded)
                    if amt > 0:
                        cur.execute(
                            "UPDATE paper_account_info SET available_cash = COALESCE(available_cash,0) + %s, "
                            "frozen_cash = GREATEST(COALESCE(frozen_cash,0) - %s, 0), updated_at = now() "
                            "WHERE account_id = %s", (amt, amt, acc))
                        out["released_cash"] += amt
                        _STATS["released_cash"] += amt
                    out["expired_buy"] += 1
                    _STATS["expired_buy"] += 1
                else:
                    rem = remaining_volume(volume, traded)
                    if rem > 0:
                        cur.execute(
                            "UPDATE paper_positions SET frozen = GREATEST(COALESCE(frozen,0) - %s, 0) "
                            "WHERE account_id = %s AND symbol = %s", (rem, acc, symbol))
                        out["released_shares"] += rem
                        _STATS["released_shares"] += rem
                    out["expired_sell"] += 1
                    _STATS["expired_sell"] += 1
                cur.execute(
                    "UPDATE paper_orders SET status = '已撤单', updated_at = now(), "
                    "reason = COALESCE(reason, '') || ' [日终作废(当日有效)]' WHERE orderid = %s",
                    (orderid,))
            except Exception:
                out["errors"] += 1
                _STATS["errors"] += 1
    # 尾差清零（2026-09-18）：释放额按 price×剩余量 算，而冻结时通常**含手续费** ⇒ 会残留几元
    #   （实测 drab_on 残留 13.36、drab_off 残留 12.85）。若该账户**已无任何未终结挂单**，
    #   则冻结资金逻辑上应为 0 ⇒ 把尾差一并清零，避免"冻结资金永远挂着一点点"。
    # 条件里**不**要求"本次有作废"：账户已在早前跑次留下尾差时也要能清掉（开关开 = 声明"冻结资金应与挂单一致"）。
        try:
            if account_id:
                cur.execute("SELECT count(*) FROM paper_orders WHERE account_id = %s "
                            "AND status IN ('提交中','部分成交')", (account_id,))
                _left = int((cur.fetchone() or [0])[0] or 0)
            else:
                cur.execute("SELECT count(*) FROM paper_orders "
                            "WHERE status IN ('提交中','部分成交')")
                _left = int((cur.fetchone() or [0])[0] or 0)
            if _left == 0:
                _sql = ("UPDATE paper_account_info SET available_cash = COALESCE(available_cash,0) "
                        "+ COALESCE(frozen_cash,0), frozen_cash = 0, updated_at = now() "
                        "WHERE COALESCE(frozen_cash,0) > 0")
                if account_id:
                    _sql += " AND account_id = %s"
                    cur.execute(_sql, (account_id,))
                else:
                    cur.execute(_sql)
                out["cleared_residual"] = int(cur.rowcount or 0)
        except Exception:
            out["errors"] += 1
        if own:
            conn.commit()
    except Exception as e:
        out["error"] = str(e)[:120]
        _STATS["errors"] += 1
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception as _e_sil1:
                _silent_alert("t_order_expiry.py:153", _e_sil1)
    _STATS["runs"] += 1
    if out.get("scanned"):
        print("[订单作废] %s 账户=%s 扫描=%s 买腿作废=%s 卖腿作废=%s 释放资金=%.2f 释放股数=%s"
              % (d8, account_id or "ALL", out["scanned"], out["expired_buy"], out["expired_sell"],
                 out["released_cash"], out["released_shares"]), flush=True)
    return out


def expire_once_per_day(account_id: str, today: Optional[str] = None) -> Dict[str, Any]:
    """每进程每日只跑一次（网关热路径调用用）。开关关 → 直接返回。"""
    if not enabled():
        return {"skipped": "switch_off"}
    d8 = str(today or "").replace("-", "")[:8]
    if not d8:
        import datetime as _dt
        d8 = _dt.datetime.now().strftime("%Y%m%d")
    if _DONE.get(account_id) == d8:
        return {"skipped": "already_done", "day": d8}
    _DONE[account_id] = d8
    return expire_stale_orders(account_id=account_id, today=d8)
