# -*- coding: utf-8 -*-
"""wolf_profit_cushion.py — C1 利润垫建模（2026-09-11）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（XLS **2026-01-27**，调整期四步的第①步里，逐字）:
  「那这种阶段应该怎么度过我之前分享过，首先，就是**尽量减少仓位**，这个时候
    **3-2垫出来的利润**就起大用处了，**有了大的利润垫我的仓位就敢留得多**，
    其实也不是仓位留得多，而是**我前面赚了钱，这个钱取一半还能留一半在里面**，
    这样其实**仓位比例没变大，但是仓位就比没有利润垫要大了**。
    再减少到合理的仓位后，第二个重点就是，如何能找到「大家跌我跌少一点，大家反弹我抢先反弹」这种方向。」
  （3-2 = 前面讲过的仓位手法代号；他这里强调的是"**垫出来的利润**"= **已实现**盈利。）
═══════════════════════════════════════════════════════════════════════════

**口径（照他的话拆）**
  · 利润垫 = **累计已实现盈利**（我们落 `t_daily_state.realized_pnl` 按日累加；浮盈不算"垫"）
  · 「**取一半留一半**」→ 已实现盈利的一半落袋（不再承担回撤），另一半**留作风险基数**
  · ⇒ **风险基数 = 本金 + 0.5 × 累计已实现盈利**；按分档比例持仓 → 允许的持仓市值 =
    风险基数 × 档位目标%，这就是他说的"仓位比例没变大，但仓位比没有利润垫要大"
  · 「敢留得多」是**放宽上限**（允许），不是"要求加仓" → 本模块只**放宽**，绝不主动抬仓
  · **开关**：`WOLF_CUSHION_CAP=1` 才真正参与 `position_cap` 的上限计算（默认 **0**：
    只把这些数算出来、在纪律上下文里展示"若开启上限可到 X%"，由用户决定是否启用）。
    理由：这是会改变实盘上限的机制，先观察再开，避免"新机制一上线就改实盘仓位"。
  · 本金来源优先级：portfolio 里的 `principal`/`initial_capital` → env `WOLF_CUSHION_PRINCIPAL`
    → 近似 `total_asset − 累计已实现`（**标注为近似**，不静默）。
开关 `WOLF_PROFIT_CUSHION=0` 可整体关闭。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


def enabled() -> bool:
    return os.getenv("WOLF_PROFIT_CUSHION", "1").strip() not in ("0", "false", "no")


def cap_enabled() -> bool:
    return os.getenv("WOLF_CUSHION_CAP", "0").strip() in ("1", "true", "yes")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


# ⚠️ **必须带 TTL 缓存**：`discipline_context()` 会被 TMonitor 每 30s 轮询调用，
#    而这里要查库 —— 无缓存时一次 DB 卡顿就会拖住整个轮询（本地实测一次连不上就等了 4 分钟）。
_CACHE: Dict[str, Any] = {"at": 0.0, "acct": None, "value": None, "ttl": 0.0}


def _ttl() -> float:
    try:
        return float(os.getenv("WOLF_CUSHION_TTL", "300"))
    except (TypeError, ValueError):
        return 300.0


def _fail_ttl() -> float:
    try:
        return float(os.getenv("WOLF_CUSHION_FAIL_TTL", "60"))
    except (TypeError, ValueError):
        return 60.0


def _db_timeout() -> float:
    try:
        return float(os.getenv("WOLF_CUSHION_DB_TIMEOUT", "3"))
    except (TypeError, ValueError):
        return 3.0


def _read_realized(account: str) -> Optional[float]:
    """**权威口径 = `paper_trades.profit` 合计**（成交事实来源）。

    ⚠️ `t_daily_state.realized_pnl` 在 2026-09-11 之前**从来没被写过**（生产实测 10 个交易日全 0，
    同期 paper_trades 有 22 笔非零 profit），所以**不能拿它当主口径**；本轮已同时修好写入方
    （t_gateway._update_daily_ledger 现按 paper_trades 补写），这里仍以 paper_trades 为准。
    返回 (值, 来源)。
    """
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        # ⚠️ paper_trades.voided 是 **integer**（不是 boolean）→ 必须用 0，否则 DatatypeMismatch
        row = db.execute(text(
            "SELECT COALESCE(SUM(profit), 0) FROM paper_trades "
            "WHERE account_id = :a AND COALESCE(voided, 0) = 0"
        ), {"a": account}).fetchone()
        v = float(row[0] or 0) if row else 0.0
        return v, "paper_trades.profit"
    except Exception as e:
        print(f"[cushion] 读 paper_trades 失败: {type(e).__name__}: {str(e)[:70]}")
        try:
            db.rollback()          # 失败后事务已 abort → 不 rollback 的话兜底查询必挂（实测踩过）
        except Exception:
            pass
    try:
        row = db.execute(text(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM t_daily_state WHERE account_id = :a"
        ), {"a": account}).fetchone()
        return (float(row[0] or 0) if row else 0.0), "t_daily_state.realized_pnl(兜底)"
    except Exception as e:
        print(f"[cushion] 读 t_daily_state 失败: {type(e).__name__}: {str(e)[:70]}")
        return None, None
    finally:
        try:
            db.close()
        except Exception:
            pass


def realized_total(account: str = "t", force: bool = False) -> Optional[float]:
    """累计已实现盈利（`t_daily_state` 按日累加）。

    **有界**（守护线程 + `WOLF_CUSHION_DB_TIMEOUT`，默认 3s）+ **TTL 缓存**（默认 300s）：
    因为 `discipline_context()` 会被 TMonitor 每 30s 轮询调用，DB 一旦卡顿就会拖住整个轮询
    （本地实测一次连不上要等 4 分钟）。失败也做**短缓存**（`WOLF_CUSHION_FAIL_TTL`，默认 60s），
    避免每轮都去撞一个已经坏掉的库。
    """
    import threading
    import time as _t
    now = _t.time()
    if not force and _CACHE["acct"] == account and now - _CACHE["at"] < _CACHE.get("ttl", 0):
        return _CACHE["value"]          # 命中（含"上次失败"的短缓存）
    box: Dict[str, Any] = {}

    def _w():
        try:
            box["v"], box["src"] = _read_realized(account)
        except Exception as e:          # _read_realized 内部已兜底，这里再兜一层
            print(f"[cushion] 读库异常: {type(e).__name__}: {str(e)[:60]}")
    th = threading.Thread(target=_w, daemon=True)
    th.start()
    th.join(_db_timeout())
    if th.is_alive():
        print(f"[cushion] 读已实现盈利超过 {_db_timeout()}s → 放弃（按数据不可用处理）")
        _CACHE.update({"at": now, "acct": account, "value": None, "ttl": _fail_ttl()})
        return None
    v = box.get("v")
    _CACHE.update({"at": now, "acct": account, "value": v, "src": box.get("src"),
                   "ttl": _ttl() if v is not None else _fail_ttl()})
    return v


def realized_src() -> Optional[str]:
    """上次读取用的数据源（写进快照，便于核查口径）。"""
    return _CACHE.get("src")


def _principal(portfolio: Optional[Dict[str, Any]], realized: float,
               total_asset: float) -> tuple:
    """(本金, 来源标注)。"""
    p = portfolio or {}
    for k in ("principal", "initial_capital", "initial_asset", "base_capital"):
        v = p.get(k)
        try:
            if v and float(v) > 0:
                return float(v), f"portfolio.{k}"
        except (TypeError, ValueError):
            pass
    env = _env_f("WOLF_CUSHION_PRINCIPAL", 0)
    if env > 0:
        return env, "env WOLF_CUSHION_PRINCIPAL"
    if total_asset > 0:
        return max(0.0, total_asset - realized), "近似(total_asset − 累计已实现)"
    return 0.0, "none"


def snapshot(portfolio: Optional[Dict[str, Any]] = None,
             realized: Optional[float] = None) -> Dict[str, Any]:
    """利润垫快照 → 风险基数 / 上限乘数 / 允许持仓市值。"""
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    p = portfolio or {}
    try:
        total_asset = float(p.get("total_asset_market") or p.get("total_asset") or 0)
    except (TypeError, ValueError):
        total_asset = 0.0
    if realized is None:
        realized = realized_total()
    if realized is None:
        return {"ok": False, "reason": "no_realized"}
    keep = _env_f("WOLF_CUSHION_KEEP", 0.5)          # 「取一半留一半」→ 留 50%
    principal, psrc = _principal(p, realized, total_asset)
    risk_base = principal + keep * realized if principal > 0 else 0.0
    mult = round(risk_base / principal, 4) if principal > 0 else None
    out = {"ok": True, "realized": round(realized, 2), "realized_src": realized_src(), "keep_ratio": keep,
           "kept": round(keep * realized, 2), "taken": round((1 - keep) * realized, 2),
           "principal": round(principal, 2), "principal_src": psrc,
           "total_asset": round(total_asset, 2),
           "risk_base": round(risk_base, 2), "cap_mult": mult,
           "cushion_pct": round(realized / principal * 100, 2) if principal > 0 else None,
           "cap_enabled": cap_enabled()}
    return out


def cap_multiplier(portfolio: Optional[Dict[str, Any]] = None) -> float:
    """给 `position_cap` 用的乘数：未开启 → 1.0（完全不影响现值）。"""
    if not enabled() or not cap_enabled():
        return 1.0
    s = snapshot(portfolio)
    if not s.get("ok") or not s.get("cap_mult"):
        return 1.0
    return max(1.0, float(s["cap_mult"]))            # 只在有利润垫时**放宽**，绝不收紧


def directive(portfolio: Optional[Dict[str, Any]] = None) -> str:
    s = snapshot(portfolio)
    if not s.get("ok"):
        # **不静默**：数据取不到时给一行可见说明（否则等于机制悄悄消失）
        if s.get("reason") == "no_realized":
            return "💰 利润垫：**数据不可用**（t_daily_state 读取失败）→ 本项暂不参与判断"
        return ""
    if s["realized"] <= 0:
        return ("💰 利润垫：当前**无已实现盈利**（累计 %+.2f）→ 按他 2026-01-27"
                "「有了大的利润垫我的仓位就敢留得多」的反面：**没有垫就该按分档保守留仓**"
                % s["realized"])
    tail = ("已开启 WOLF_CUSHION_CAP → 分档上限放宽 ×%.3f" % s["cap_mult"]) if s["cap_enabled"] else \
           ("未开启（默认）：仅展示。若开启 WOLF_CUSHION_CAP=1 → 分档上限可放宽 ×%.3f" % (s["cap_mult"] or 1))
    return ("💰 利润垫（狼大 2026-01-27「**这个钱取一半留一半**…仓位比例没变大，但仓位就比没有利润垫大了」）："
            "累计已实现 %+.2f（占本金 %s%%）→ 落袋 %+.2f / 留作风险基数 %+.2f；本金来源=%s；%s"
            % (s["realized"], s["cushion_pct"], s["taken"], s["kept"], s["principal_src"], tail))
