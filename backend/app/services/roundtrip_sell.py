# -*- coding: utf-8 -*-
"""B模型·等量换手做T（roundtrip_sell）状态与规则（2026-09-08 用户拍板落地）。

语义：当日低吸成交 N 股(254/253/低吸类, stock 账户经 gateway buy)后，
允许在反弹 ≥低吸均价×1.008 时分批卖出 ≤N 股旧仓(不动当日买入/底仓floor)——
净持仓不变、当日完成一轮T；当日没到卖点则次日解锁后继续监控(两日窗口)；
超过窗口仍未完成 → stale 提醒，由人工决策(那部分已成被动加仓)。
纯状态模块：卖出动作由 TMonitor._check_roundtrip_sell 执行(gateway 唯一放行)。
"""
import json
import os
from datetime import date, datetime
from typing import Optional

ROUNDTRIP_SELL_UP = 0.008      # 卖点 = 低吸均价 × 1.008
ROUNDTRIP_ENABLED = str(os.environ.get("WOLF_ROUNDTRIP_SELL", "1")) == "1"
STATE_FILE = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "roundtrip_state.json")
MAX_AGE_DAYS = 5               # 状态保留天数（自然日）


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
        print(f"[RoundT] 状态写盘失败: {e}")


def norm_sym(symbol: str) -> str:
    return str(symbol).replace(" ", "").upper()


def today8() -> str:
    return date.today().strftime("%Y%m%d")


def record_buy(symbol: str, price: float, volume: int,
               account: str = "stock", trade_date: Optional[str] = None) -> None:
    """gateway 低吸买入成交后登记等量换手额度。幂等累加(同日多笔加权均价)。"""
    if not ROUNDTRIP_ENABLED:
        return
    if account != "stock" or volume <= 0 or price <= 0:
        return
    sym = norm_sym(symbol)
    td = trade_date or today8()
    d = _load()
    st = d.get(sym) or {}
    if st.get("date") != td:
        st = {"date": td, "buy_qty": 0, "buy_avg": 0.0, "sold_qty": 0, "stale": False, "notified": False}
    old_qty = int(st.get("buy_qty") or 0)
    old_avg = float(st.get("buy_avg") or 0)
    new_qty = old_qty + int(volume)
    st["buy_avg"] = round((old_avg * old_qty + float(price) * int(volume)) / new_qty, 4)
    st["buy_qty"] = new_qty
    st["account"] = account
    d[sym] = st
    _save(d)
    print(f"[RoundT] 登记等量换手 {sym} 低吸{volume}股@{price} → "
          f"目标卖@{st['buy_avg'] * (1 + ROUNDTRIP_SELL_UP):.3f} 总额度{new_qty}")


def remaining(symbol: str) -> int:
    st = (_load().get(norm_sym(symbol)) or {})
    return max(int(st.get("buy_qty") or 0) - int(st.get("sold_qty") or 0), 0)


def mark_sold(symbol: str, volume: int) -> None:
    sym = norm_sym(symbol)
    d = _load()
    st = d.get(sym)
    if not st:
        return
    st["sold_qty"] = int(st.get("sold_qty") or 0) + int(volume)
    st["last_sell"] = today8()
    d[sym] = st
    _save(d)
    print(f"[RoundT] 换手卖出 {sym} {volume}股, 剩余额度"
          f"{max(int(st['buy_qty']) - int(st['sold_qty']), 0)}")


def pending_symbols(today: Optional[str] = None) -> list:
    """当日/昨日的等量换手待卖标的(两日窗口)；超窗置 stale(转人工)。"""
    td = today or today8()
    d = _load()
    out = []
    for sym, st in d.items():
        rem = max(int(st.get("buy_qty") or 0) - int(st.get("sold_qty") or 0), 0)
        if rem <= 0 or st.get("stale"):
            continue
        age = _age_days(str(st.get("date") or ""), td)
        if age <= 1:
            out.append((sym, st))
        elif not st.get("notified"):
            st["stale"] = True
            st["notified"] = True
            d[sym] = st
            _save(d)
            print(f"[RoundT] ⚠️ {sym} 低吸{st.get('buy_qty')}股两日窗口未完成换手(剩{rem}), "
                  f"已转人工决策(被动加仓)")
    rm = [s for s, st in d.items() if _age_days(str(st.get("date") or ""), td) > MAX_AGE_DAYS]
    if rm:
        for s in rm:
            d.pop(s, None)
        _save(d)
    return out


def buy_avg(symbol: str) -> float:
    st = (_load().get(norm_sym(symbol)) or {})
    return float(st.get("buy_avg") or 0)


def _age_days(d8: str, td: str) -> int:
    try:
        return max((datetime.strptime(td, "%Y%m%d") - datetime.strptime(d8, "%Y%m%d")).days, 0)
    except Exception:
        return 99


def dump() -> dict:
    return _load()
