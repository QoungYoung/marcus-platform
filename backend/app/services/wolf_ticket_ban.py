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


def cal_fix_on() -> bool:
    """甲（2026-09-22 用户拍板"都做"）：TTL 的日历口径修复开关。

    **库内默认 0 = 旧行为**（生产逐位不变）；回测由 pins 置 1。
    开 = ① 本地 bars 库精确区间 → ② 窗口日历 → ③ 生产 relay 日历 → ④ 都取不到时按自然日×5/7 保守估。
    """
    return str(os.getenv("WOLF_BAN_TTL_CAL_FIX", "0")).strip().lower() in ("1", "true", "yes", "on")


def _norm8(x: Any) -> str:
    d = "".join(ch for ch in str(x or "") if ch.isdigit())
    return d[:8] if len(d) >= 8 else ""


def _cal_days_between(d1: str, d2: str) -> Optional[int]:
    """`d1 → d2` 之间的交易日数（买=含 d2、不含 d1）。多源取数，取不到返回 None。

    ⚠️ 2026-09-22 修 bug（用户问"兆易创新 0409 止跌企稳为什么没买"时查出）：
      原实现只走 `wolf_weekend_hedge.recent_trade_days()` —— 那个日历的窗口是
      **今天−10 自然日 ~ 今天+20 自然日**；**登记日一旦滑出窗口**（删票 TTL 是 13 交易日 ≈ 19 自然日，
      必然会滑出）⇒ 本函数返回 None ⇒ `elapsed_td` **静默退化成自然日**，
      而自然日跑得比交易日快 ~1.4 倍 ⇒ **禁令提前约 4 个交易日解除**。
      实测（T5/drabt5，登记 20260323、TTL=13）：0403 算成 11（真实 9）、0407 算成 15（真实 10）
      ⇒ 603986 本应禁买到 0411，实际 0406/0407 就放开了。
    现在按优先级取：① 本地 bars 库区间查询（精确、便宜）→ ② 窗口版日历（两个日期都在窗内时）
    → ③ 生产 relay 的 `resolve_trade_days`。
    """
    a, b = _norm8(d1), _norm8(d2)
    if not a or not b or b < a:
        return None
    if a == b:
        return 0
    if not cal_fix_on():
        # ── 旧行为（默认；生产逐位不变）：只走"今天±(10/20)天"那个窗口日历，出窗即 None ──
        try:
            from app.services.wolf_weekend_hedge import recent_trade_days
            cal = [d for d in (recent_trade_days() or []) if d]
            if cal and a in cal and b in cal:
                return max(cal.index(b) - cal.index(a), 0)
        except Exception:
            pass
        return None
    # ① 本地 bars 库（回测 + 有本地库的环境）：一次区间扫描
    try:
        import os as _os
        import sqlite3 as _sq
        db = _os.getenv("BT_BARS_DB") or _os.path.join("data", "_bt_full", "bars.sqlite")
        if _os.path.exists(db):
            con = _sq.connect("file:%s?mode=ro" % db, uri=True)
            try:
                row = con.execute("SELECT COUNT(DISTINCT trade_date) FROM bars WHERE trade_date>? AND trade_date<=?",
                                  (a, b)).fetchone()
            finally:
                con.close()
            if row and row[0] is not None and int(row[0]) > 0:
                return int(row[0])
    except Exception:
        pass
    # ② 窗口版日历（两个日期都在窗口内才可用）
    try:
        from app.services.wolf_weekend_hedge import recent_trade_days
        cal = [d for d in (recent_trade_days() or []) if d]
        if cal and a in cal and b in cal:
            return max(cal.index(b) - cal.index(a), 0)
    except Exception:
        pass
    # ③ 生产：中继上的交易日历（可按区间取）
    try:
        from app.services.t_backtest_data import resolve_trade_days
        ds = resolve_trade_days(a, b) or []
        n = len([d for d in ds if _norm8(d) > a])
        if n > 0:
            return n
    except Exception:
        pass
    return None


def elapsed_td(since8: str, today8: Optional[str] = None) -> int:
    """自登记日以来的交易日数。日历取不到 ⇒ **保守按自然日×5/7 估**（绝不提前解除禁令）。"""
    import datetime as _dt
    t8 = today8 or _today8()
    n = _cal_days_between(since8, t8)
    if n is not None:
        return n
    try:
        cal = max((_dt.datetime.strptime(t8, "%Y%m%d") - _dt.datetime.strptime(_norm8(since8), "%Y%m%d")).days, 0)
    except Exception:
        return 0
    if cal_fix_on():
        # 修复后：日历取不到也**绝不提前解除**（交易日 ≤ 自然日×5/7）
        print("[BanList] 交易日历不可用，按自然日 %d 的 5/7=%d 保守估算（绝不提前解除）"
              % (cal, int(cal * 5 / 7)), flush=True)
        return int(cal * 5 / 7)
    # 旧行为（开关关，生产逐位一致）：直接按自然日 ⇒ 注意这会让禁令提前约 1/3 解除
    return cal


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
