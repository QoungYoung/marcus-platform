# -*- coding: utf-8 -*-
"""t_buy_dedup.py — **建仓类买腿**的同日同价重复互斥（账本 §9.702）。

## 问题（用户 2026-10-07 回测复盘发现）

汇成股份 SH688403 **2026-01-21 两笔完全相同的买单**：

| 时间 | 方向 | 价格 | 股数 | 理由 |
|---|---|---|---|---|
| 14:00 | 买入 | 23.05 | 2400 | 条件命中自动执行（trend_break_buy） |
| 14:05 | 买入 | 23.05 | 2400 | 条件命中自动执行（trend_break_buy） |

**同价、同量、相隔 5 分钟** ⇒ 一次建仓被执行两次（多买 2400 股）。
同类还有用户指出的「伟测科技埋伏仓又触发一次 `wolf_ambush_buy`，而没有转正」。

为什么现有闸没拦住：
· G6「一个票最多买 2 笔」按**笔数**算 ⇒ 恰好 2 笔**在上限之内**，放行；
· `t_sell_dedup`（同价重复兑现互斥）**只作用于卖出侧**；
· `t_monitor._sold_this_round` 只盖 `_round` 的条件腿，且只对**卖出**。

## 口径（与卖出侧同款）

**建仓类**买腿（结构化腿型白名单）在「**同标的 + N 分钟内（默认 5） + 同价（±0.5%）已成交过一笔**」
时**本次不执行**，理由写"同价重复建仓"（触发状态 blocked，审计可查）。
· 谁先到谁成交（先到者执行，后来者被拦）；
· **只作用于建仓类**：转正加仓 / 做T回补（`wolf_zheng_t_buy` / `custom_prevlow` …）**不受本闸约束**
  —— 它们本来就是"同一标的同一天买第二笔"的合法场景，各自另有笔数上限与价格闸；
· 低于窗口 / 超过容差 / 无历史成交 / 取数失败 ⇒ **放行**（fail-open，绝不误拦）。

开关：`WOLF_BUY_DEDUP`（**库内默认 0** = 生产零影响；回测由 pins 置 1）、
`WOLF_BUY_DEDUP_MIN`（默认 **5** 分钟）、`WOLF_BUY_DEDUP_PCT`（默认 0.5%）、
`WOLF_BUY_DEDUP_KINDS`（默认六条建仓类腿）。

⚠️ 已知边界（与卖出侧相同）：若两笔**并发**下单（同一轮同时提交），本闸依赖"已成交"记录，
存在极窄竞态窗口；实测这批是顺序落库（相差 5 分钟），故有效。
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


ENV = "WOLF_BUY_DEDUP"
DEFAULT_KINDS = ("trend_break_buy,wolf_ambush_buy,buy_253,buy_254,custom_buy,wolf_build")
_STATS: Dict[str, Any] = {"checked": 0, "blocked": 0, "errors": 0}


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def window_min() -> int:
    try:
        return max(int(float(os.getenv("WOLF_BUY_DEDUP_MIN", "5") or 5)), 0)
    except Exception:
        return 5


def tol_pct() -> float:
    try:
        return max(float(os.getenv("WOLF_BUY_DEDUP_PCT", "0.5") or 0.5), 0.0)
    except Exception:
        return 0.5


def kinds() -> set:
    raw = os.getenv("WOLF_BUY_DEDUP_KINDS", DEFAULT_KINDS) or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def is_build(trigger_kind: Optional[str]) -> bool:
    k = str(trigger_kind or "").strip().lower()
    return bool(k) and k in kinds()


def stats() -> Dict[str, Any]:
    return {"on": enabled(), "min": window_min(), "pct": tol_pct(), "kinds": sorted(kinds()), **_STATS}


def _recent_buys(account_id: str, symbol: str, limit: int = 8):
    """该账户该标的最近的**买入**成交（倒序）：[(created_at, price, volume)]；失败 → []（fail-open）。"""
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
                    "WHERE account_id=%s AND symbol=%s AND direction IN ('买入','buy') "
                    "AND COALESCE(voided,0)=0 ORDER BY created_at DESC, id DESC LIMIT %s",
                    (account_id, symbol, int(limit)))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        _STATS["errors"] += 1
        print("[buy-dedup] 取最近买入失败(放行): %s" % str(e)[:80], flush=True)
        return []


def check(account_id: str, symbol: str, price: float, trigger_kind: Optional[str] = None,
          reason: str = "", now: Optional[datetime] = None) -> Tuple[bool, str]:
    """同价重复建仓互斥：返回 (ok, why)。ok=False ⇒ 本次不执行（重复）。"""
    if not enabled():
        return True, "WOLF_BUY_DEDUP=0（同价去重关闭）"
    if not is_build(trigger_kind):
        return True, "非建仓类腿型（%s）不受同价去重约束" % (trigger_kind or "?")
    try:
        px = float(price or 0)
    except Exception:
        px = 0.0
    if px <= 0:
        return True, "价格不可用→放行"
    _STATS["checked"] += 1
    try:
        rows = _recent_buys(account_id, symbol)
    except Exception as e:                      # 取数异常 ⇒ 放行（fail-open）
        _STATS["errors"] += 1
        print("[buy-dedup] 取数异常(放行): %s" % str(e)[:80], flush=True)
        rows = []
    if not rows:
        return True, "无历史买入→放行"
    t0 = now or datetime.now()
    w = window_min()
    tol = tol_pct() / 100.0
    for ts, p, v in rows:
        try:
            tp = float(p or 0)
        except Exception as _e_sil1:
            _silent_alert("t_buy_dedup.py:133", _e_sil1)
            continue
        if tp <= 0:
            continue
        try:
            tt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        except Exception as _e_sil2:
            _silent_alert("t_buy_dedup.py:139", _e_sil2)
            continue
        mins = (t0 - tt).total_seconds() / 60.0
        if mins < 0 or mins > w:
            continue
        if abs(tp - px) / px <= tol:
            _STATS["blocked"] += 1
            return False, ("同价重复建仓：%d 分钟内已在 %s 以 %.3f 买入 %s 股 ⇒ 本次视为重复（只执行一条）；"
                           "用户 2026-10-07 复盘：「汇成股份 2026-01-21 同价同量买了两笔」"
                           % (int(mins), tt.strftime("%H:%M"), tp, v))
    return True, "窗口内无同价买入→放行"
