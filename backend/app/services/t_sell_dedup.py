# -*- coding: utf-8 -*-
"""t_sell_dedup.py — 同价重复兑现互斥（用户 2026-09-22 拍板 "B′-1"）。

## 问题（实测）

2026-01-07 09:35:00（T6/drabt6 SZ002156 通富微电），**同一分钟、同一价 39.98** 三条腿各自成交：

| 腿 | 股数 | 语义 |
|---|---|---|
| `wolf_profit_take_sell` | 500 | 高抛兑现 |
| `wolf_fib_target_sell` | 600 | 斐波目标兑现 |
| `wolf_defensive_t_reduce` | 300 | 防守减仓 |

三条腿是**三个不同模板**碰巧在同一价位同时命中 ⇒ 一次"兑现机会"被执行了三次，共 1,400 股（持仓 1,800）。
后果：该票两天清仓（0107 卖 1,400 + 0108 卖 400），而它 0119 到 46.86、0120 到 51.01
（对照 T5：同期只卖 700 股、留住大头 ⇒ 该票已实现 **+11,003**，T6 只有 +2,427）。

为什么会同时命中：**腿是各自独立评估的**，谁也不看别人是否刚卖过；
`t_monitor._sold_this_round`（同轮互斥）只盖 `_round` 的**条件腿**，wolf 腿走
`_check_wolf_t_rules` → 写触发 → agent 逐条执行，**不受它约束**。

## 口径（B′-1：只执行一条）

**止盈类**卖腿（结构化腿型白名单）在「**同标的 + N 分钟内（默认 15）+ 同价（±0.5%）已成交过一条**」
时，**本次不执行**，理由写"同价重复兑现"（触发状态 blocked，审计可查）。
· 谁先到谁执行（先到者成交，后来者被拦）—— 不由"哪条腿优先"另定规则；
· **只作用于止盈类**：止损/破位/风控（`t_protect`）与防守减仓等**不**受本闸约束
  （它们各自还有 G6 笔数上限与卖出时点门管着）；
· 低于窗口/超过容差/无历史成交 ⇒ 放行；取数失败 ⇒ 放行（fail-open）。

开关：`WOLF_SELL_DEDUP`（**库内默认 0** = 生产零影响；回测由 pins 置 1）、
`WOLF_SELL_DEDUP_MIN`（默认 15 分钟）、`WOLF_SELL_DEDUP_PCT`（默认 0.5%）、
`WOLF_SELL_DEDUP_KINDS`（默认四条止盈腿）。

⚠️ 已知边界：三条腿若被**并发**执行（同一轮里同时下单），本闸依赖"已成交"记录，
存在极窄的竞态窗口；实测这批是顺序执行的（同一分钟但先后落库），故有效。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


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


ENV = "WOLF_SELL_DEDUP"
DEFAULT_KINDS = "wolf_profit_take_sell,wolf_fib_target_sell,high_sell,wolf_confirm_sell"
_STATS: Dict[str, Any] = {"checked": 0, "blocked": 0, "errors": 0}


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def window_min() -> int:
    try:
        return max(int(float(os.getenv("WOLF_SELL_DEDUP_MIN", "15") or 15)), 0)
    except Exception:
        return 15


def tol_pct() -> float:
    try:
        return max(float(os.getenv("WOLF_SELL_DEDUP_PCT", "0.5") or 0.5), 0.0)
    except Exception:
        return 0.5


def kinds() -> set:
    raw = os.getenv("WOLF_SELL_DEDUP_KINDS", DEFAULT_KINDS) or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def is_take(trigger_kind: Optional[str]) -> bool:
    k = str(trigger_kind or "").strip().lower()
    return bool(k) and k in kinds()


def stats() -> Dict[str, Any]:
    return {"on": enabled(), "min": window_min(), "pct": tol_pct(), "kinds": sorted(kinds()), **_STATS}


def _recent_sells(account_id: str, symbol: str, limit: int = 8):
    """该账户该标的最近的卖出成交（倒序）：[(created_at, price, volume)]；失败 → []（fail-open）。"""
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL") or ""
        if not dsn:
            from app.database import DATABASE_URL as _du
            dsn = _du
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT created_at, price, volume FROM paper_trades "
                    "WHERE account_id=%s AND symbol=%s AND direction IN ('卖出','sell') "
                    "AND COALESCE(voided,0)=0 ORDER BY created_at DESC, id DESC LIMIT %s",
                    (account_id, symbol, int(limit)))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        _STATS["errors"] += 1
        print("[sell-dedup] 取最近卖出失败(放行): %s" % str(e)[:80], flush=True)
        return []


def check(account_id: str, symbol: str, price: float, trigger_kind: Optional[str] = None,
          reason: str = "", now: Optional[datetime] = None) -> Tuple[bool, str]:
    """同价重复兑现互斥：返回 (ok, why)。ok=False ⇒ 本次不执行（重复）。"""
    if not enabled():
        return True, "WOLF_SELL_DEDUP=0（同价去重关闭）"
    if not is_take(trigger_kind):
        return True, "非止盈类腿型（%s）不受同价去重约束" % (trigger_kind or "?")
    try:
        px = float(price or 0)
    except Exception:
        px = 0.0
    if px <= 0:
        return True, "价格不可用→放行"
    _STATS["checked"] += 1
    try:
        rows = _recent_sells(account_id, symbol)
    except Exception as e:                      # 取数异常 ⇒ 放行（fail-open，绝不因统计失败拦止血动作）
        _STATS["errors"] += 1
        print("[sell-dedup] 取数异常(放行): %s" % str(e)[:80], flush=True)
        rows = []
    if not rows:
        return True, "无历史卖出→放行"
    t0 = now or datetime.now()
    w = window_min()
    tol = tol_pct() / 100.0
    for ts, p, v in rows:
        try:
            tp = float(p or 0)
        except Exception as _e_sil1:
            _silent_alert("t_sell_dedup.py:134", _e_sil1)
            continue
        if tp <= 0:
            continue
        try:
            tt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        except Exception as _e_sil2:
            _silent_alert("t_sell_dedup.py:140", _e_sil2)
            continue
        mins = (t0 - tt).total_seconds() / 60.0
        if mins < 0 or mins > w:
            continue
        if abs(tp - px) / px <= tol:
            _STATS["blocked"] += 1
            return False, ("同价重复兑现：%d 分钟内已在 %s 以 %.3f 卖出 %s 股 ⇒ 本次视为重复（只执行一条）；"
                           "狼大 2025-02-07「一个票最多买 2 笔 卖 2 笔…越动收益越低」"
                           % (int(mins), tt.strftime("%H:%M"), tp, v))
    return True, "窗口内无同价卖出→放行"
