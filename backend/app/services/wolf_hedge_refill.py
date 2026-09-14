# -*- coding: utf-8 -*-
"""wolf_hedge_refill.py — G9 避险的**回补腿**（C2b，2026-09-14，用户拍板开发）。

狼大原话（"卖完之后怎么拿回来"，四句都对得上）:
  · 2026-08-21 14:20「2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半 防止周末出利空
    **这样周一再拿回来**。出于仓位安全考虑 65%仓位过周末。」
  · 2026-08-21 14:43「…等**周一确认安全**再说 **万一低开 那就等于做了个反T** 万一高开 那**没吃到就没吃到了 不纠结**」
  · 2025-09-24「你出于什么原因出去避险 **那这个避险逻辑结束后 是不是应该补回来** 在个股逻辑没变的情况下」
  · 2025-04-29「以后是节假日出今日 甭管当时指数什么行情 尽量做到 **早盘卖 尾盘买的反T**」

口径（逐条对应原话，可 env 覆盖）:
  · **只补回卖出的那一份**（等量）——「这样周一再拿回来」，不放大仓位；
  · **不追高**：回补价 ≤ 卖出价 ×(1 + WOLF_REFILL_CHASE_MAX，默认 0.01)
    ——「万一高开…没吃到就没吃到了 不纠结」；
  · **低开更好**：低于卖出价一律可补 ——「万一低开 那就等于做了个反T」；
  · **个股逻辑没变**：命中负事件（wolf_early_stop.negative_event）→ 放弃回补（2025-09-24 的前提）；
  · **回补窗口**：卖出后的**下一个交易日**起 WOLF_REFILL_DAYS（默认 2）个交易日内；超窗 → 过期（不纠结）；
  · **时点**：默认 WOLF_REFILL_FROM=0935（低开就补回，等于做了个反T）；若只想要他 2025-04-29
    「尾盘买」的概率优势 → 设 WOLF_REFILL_FROM=1400。

纯状态模块（不直接下单）：下单由 TMonitor._check_hedge_refill() 走 gateway 唯一通道。
"""
import json
import os
from datetime import date, datetime
from typing import Optional, Tuple

STATE_FILE = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "wolf_hedge_refill.json")
MAX_AGE_DAYS = 10          # 状态保留天数（自然日），过期条目清理


def enabled() -> bool:
    return os.getenv("WOLF_HEDGE_REFILL", "1").strip() not in ("0", "false", "no")


def chase_max() -> float:
    """不追高上限：回补价 ≤ 卖出价 ×(1+该值)。默认 1%（「高开没吃到就没吃到了」）。"""
    try:
        return float(os.getenv("WOLF_REFILL_CHASE_MAX", "0.01"))
    except (TypeError, ValueError):
        return 0.01


def window_days() -> int:
    """回补窗口（交易日）：卖出后的下一个交易日起算，默认 2 个交易日内。"""
    try:
        return max(int(os.getenv("WOLF_REFILL_DAYS", "2")), 1)
    except (TypeError, ValueError):
        return 2


def from_hm(holiday: bool = False) -> str:
    """回补起始时点（HH:MM）：

      · 普通（周末避险后的周一）：**09:35 起** —— 他 2026-08-21「这样周一再拿回来／万一低开 那就等于做了个反T」；
      · **长假后**：**14:00 起（尾盘买）** —— 他 2025-04-29「早盘卖 **尾盘买**的反T。从概率上来说都是对的」
        → `WOLF_REFILL_FROM_HOLIDAY`（默认 1400）。
    """
    if holiday:
        return (os.getenv("WOLF_REFILL_FROM_HOLIDAY", "1400") or "1400").strip()
    return (os.getenv("WOLF_REFILL_FROM", "0935") or "0935").strip()


# ── 状态 ────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[Refill] 状态写盘失败: {e}")


def norm_sym(symbol: str) -> str:
    return str(symbol).replace(" ", "").upper()


def _key(account: str, symbol: str) -> str:
    return "%s:%s" % (account or "stock", norm_sym(symbol))


def _today8() -> str:
    return date.today().strftime("%Y%m%d")


def record_sell(symbol: str, price: float, volume: int, account: str = "stock",
                trade_date: Optional[str] = None, kind: str = "") -> Optional[str]:
    """G9 避险卖出成交后登记**待回补额度**（幂等累加同日同标的）。"""
    if not enabled() or volume <= 0 or price <= 0:
        return None
    td = trade_date or _today8()
    k = _key(account, symbol)
    d = _load()
    st = d.get(k) or {}
    if st.get("sell_date") != td:
        st = {"sell_date": td, "qty": 0, "notional": 0.0, "done": False}
    st["kind"] = str(kind or st.get("kind") or "")
    st["qty"] = int(st.get("qty") or 0) + int(volume)
    st["notional"] = round(float(st.get("notional") or 0) + float(price) * int(volume), 2)
    st["sell_px"] = round(float(st["notional"]) / max(int(st["qty"]), 1), 4)
    st["account"] = account or "stock"
    st["symbol"] = norm_sym(symbol)
    d[k] = st
    _save(d)
    print(f"[Refill] 登记待回补 {k} {volume}股@{price}（卖出均价 {st['sell_px']}）")
    return k


def pending(today8: Optional[str] = None) -> list:
    """待回补清单（含未到窗口的；调用方用 refill_decision 判定动作）。顺带清理过期条目。"""
    td = today8 or _today8()
    d = _load()
    out, changed = [], False
    for k, st in list(d.items()):
        if st.get("done"):
            continue
        if _cal_days(str(st.get("sell_date") or ""), td) > MAX_AGE_DAYS:
            d.pop(k, None)
            changed = True
            continue
        out.append({"key": k, "symbol": st.get("symbol") or k.split(":")[-1],
                    "account": st.get("account") or "stock",
                    "qty": int(st.get("qty") or 0), "sell_px": float(st.get("sell_px") or 0),
                    "sell_date": st.get("sell_date"), "buy_qty": int(st.get("buy_qty") or 0),
                    "holiday": str(st.get("kind") or "") == "holiday"})
    if changed:
        _save(d)
    return out


def mark_done(key: str, volume: int) -> None:
    """回补成交后登记（可多次部分成交）。"""
    d = _load()
    st = d.get(key)
    if not st:
        return
    st["buy_qty"] = int(st.get("buy_qty") or 0) + int(volume)
    st["done_at"] = _today8()
    if int(st["buy_qty"]) >= int(st.get("qty") or 0):
        st["done"] = True
    d[key] = st
    _save(d)
    print(f"[Refill] 回补 {key} {volume}股（累计 {st['buy_qty']}/{st.get('qty')}）")


def expire(key: str, reason: str = "") -> None:
    d = _load()
    st = d.get(key)
    if not st:
        return
    st["done"] = True
    st["expired"] = True
    st["expire_reason"] = reason[:120]
    d[key] = st
    _save(d)
    print(f"[Refill] 放弃回补 {key}: {reason[:80]}")


def dump() -> dict:
    return _load()


# ── 判定（纯函数，便于单测） ─────────────────────────────────────────────
def refill_decision(sell_px: float, price: float, elapsed_td: int, hhmm: str,
                    neg_event: bool = False, chase: Optional[float] = None,
                    window: Optional[int] = None, start_hm: Optional[str] = None) -> Tuple[str, str]:
    """返回 (action, reason)，action ∈ {buy, wait, expire}。

    · elapsed_td = 卖出日到今日的**交易日**数（1 = 下一个交易日，即他说的"周一"）；
    · hhmm = 当前时刻 "HHMM"；neg_event = 个股出现负事件（逻辑变了）。
    """
    cm = chase_max() if chase is None else float(chase)
    wd = window_days() if window is None else int(window)
    sh = (start_hm or from_hm()).replace(":", "")
    if neg_event:
        return ("expire", "个股逻辑变了（负事件）→ 不补回（2025-09-24 的前提）")
    if elapsed_td < 1:
        return ("wait", "还没到下一个交易日（他：周一再拿回来）")
    if elapsed_td > wd:
        return ("expire", "超过 %d 个交易日窗口仍未补回 → 不纠结（2026-08-21）" % wd)
    if str(hhmm)[:4] < str(sh)[:4]:
        return ("wait", "未到回补起始时点 %s" % (start_hm or from_hm()))
    cap = float(sell_px) * (1.0 + cm)
    if price > cap:
        if elapsed_td >= wd:
            return ("expire", "价格始终 > 卖出价×(1+%.0f%%) → 高开没吃到，不纠结（2026-08-21）" % (cm * 100))
        return ("wait", "价 %.3f > 不追高上限 %.3f（卖出价×(1+%.0f%%)）" % (price, cap, cm * 100))
    tag = "低开=反T 更好" if price < float(sell_px) else "平价回补"
    return ("buy", "%s：回补价 %.3f ≤ 卖出价 %.3f×(1+%.0f%%)（狼大 2026-08-21「周一再拿回来」）"
            % (tag, price, float(sell_px), cm * 100))


def _cal_days(d8: str, td: str) -> int:
    try:
        return max((datetime.strptime(td, "%Y%m%d") - datetime.strptime(d8, "%Y%m%d")).days, 0)
    except Exception:
        return 0


def trade_days_between(d1: str, d2: str) -> Optional[int]:
    """两个日期之间的**交易日**数（用 idx_quote/交易日历；取不到返回 None，由调用方回退自然日）。"""
    try:
        from app.services.wolf_weekend_hedge import recent_trade_days
        cal = [d for d in (recent_trade_days() or []) if d]
        if not cal or d1 not in cal or d2 not in cal:
            return None
        return max(cal.index(d2) - cal.index(d1), 0)
    except Exception:
        return None


def elapsed_td(sell_date: str, today8: str) -> int:
    """卖出日 → 今日的交易日数（取不到日历时回退自然日，最少 0）。"""
    n = trade_days_between(sell_date, today8)
    if n is not None:
        return n
    return _cal_days(sell_date, today8)
