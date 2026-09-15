# -*- coding: utf-8 -*-
"""wolf_ticket_ban.py — G3：破线卖出后**「删票」**（移出观察池，带 TTL）（2026-09-14 落地）。

狼大原话:
  · 2025-02-06「这些我都是**定线**的 **最下面那根线一旦破了 卖出然后删票**」
  · 2025-04-03「我说一下机器人排除法 包含今天在内的三天内 **如果破之前新低的，直接删票**」
  · 2021-01-22「今天踩144就可以上一点啊 另一部分挂在13就行，**这两根破了这个标我就不看了**」
  · 同族 2021-07-14「我是多账户操作 所以都是**挂线条件单**…到了自动就会跳出来给我确认操作了」

口径:
  · 触发登记：**破线类**卖出成交后（止损/破位清仓/被动止盈线跌破），把该 (账户, 标的) 记入观察池黑名单；
  · **TTL 用交易日**（默认 13 —— 与他的"13 日周期"一致；WOLF_BAN_TTL_TD 可调）；
    他说「我就不看了」没有给期限 → 这里**明示为我们的代理**：默认 13 个交易日，到期自动解除；
  · 作用域：**买入侧候选过滤**（rotation_switch_arm 布腿 + wolf_confirm_pick 选票）；
    **不影响卖出**（止损/止盈/被动止盈照旧，保护不能因为"删票"而失效）；
  · 开关：WOLF_BAN_LIST=0 关闭（只记录不拦）。

纯状态模块（可单测）；调用点：t_monitor 卖出成交后登记 + 两个买入侧候选过滤。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DATA = os.environ.get("DATA_DIR", "/app/data")
STATE_FILE = os.path.join(DATA, "wolf_ticket_ban.json")


def enabled() -> bool:
    return os.getenv("WOLF_BAN_LIST", "1").strip() not in ("0", "false", "no")


def ttl_td() -> int:
    """TTL（交易日）。默认 13（他的时间周期）；⚠️ 期限本身是我们的代理，不是他的原话。"""
    try:
        # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
        return max(int(float(os.getenv("WOLF_BAN_TTL_TD", "13"))), 1)
    except (TypeError, ValueError):
        return 13


def key_of(account: str, symbol: str) -> str:
    return "%s:%s" % (account or "stock", str(symbol).replace(" ", "").upper())


def _today8() -> str:
    import datetime as _dt
    return _dt.date.today().strftime("%Y%m%d")


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[BanList] 状态写盘失败: {e}")


def _trade_days_between(d1: str, d2: str) -> Optional[int]:
    try:
        from app.services.wolf_weekend_hedge import recent_trade_days
        cal = [d for d in (recent_trade_days() or []) if d]
        if not cal or d1 not in cal or d2 not in cal:
            return None
        return max(cal.index(d2) - cal.index(d1), 0)
    except Exception:
        return None


def elapsed_td(since8: str, today8: Optional[str] = None) -> int:
    """自登记日以来的交易日数（取不到日历 → 回退自然日）。"""
    import datetime as _dt
    t8 = today8 or _today8()
    n = _trade_days_between(since8, t8)
    if n is not None:
        return n
    try:
        return max((_dt.datetime.strptime(t8, "%Y%m%d") - _dt.datetime.strptime(since8, "%Y%m%d")).days, 0)
    except Exception:
        return 0


def ban(account: str, symbol: str, reason: str = "", today8: Optional[str] = None) -> Optional[str]:
    """登记"删票"（幂等：同一天多次只更新原因）。"""
    k = key_of(account, symbol)
    if not enabled():
        return None
    t8 = today8 or _today8()
    d = _load()
    st = d.get(k) or {}
    if st.get("since") != t8:
        st = {"since": t8, "symbol": str(symbol).upper(), "account": account}
    st["reason"] = str(reason)[:160]
    st["ttl_td"] = ttl_td()
    d[k] = st
    _save(d)
    print(f"[BanList] 删票 {k}（{str(reason)[:60]}）TTL={st['ttl_td']} 交易日")
    return k


def is_banned(account: str, symbol: str, today8: Optional[str] = None) -> bool:
    """是否仍在"删票"期内（到期自动解除）。"""
    if not enabled():
        return False
    k = key_of(account, symbol)
    st = _load().get(k)
    if not st:
        return False
    t8 = today8 or _today8()
    return elapsed_td(str(st.get("since") or ""), t8) < int(st.get("ttl_td") or ttl_td())


def banned_symbols(accounts: Optional[List[str]] = None, today8: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """{symbol: state} —— 给买入侧候选过滤用（自动剔除已到期项并落盘清理）。"""
    if not enabled():
        return {}
    t8 = today8 or _today8()
    d = _load()
    out, changed = {}, False
    for k, st in list(d.items()):
        acct = str(st.get("account") or k.split(":")[0])
        if accounts and acct not in accounts:
            continue
        if elapsed_td(str(st.get("since") or ""), t8) >= int(st.get("ttl_td") or ttl_td()):
            d.pop(k, None)
            changed = True
            continue
        out[str(st.get("symbol") or k.split(":")[-1])] = st
    if changed:
        _save(d)
    return out


def unban(account: str, symbol: str) -> None:
    d = _load()
    k = key_of(account, symbol)
    if k in d:
        d.pop(k, None)
        _save(d)


def dump() -> dict:
    return _load()
