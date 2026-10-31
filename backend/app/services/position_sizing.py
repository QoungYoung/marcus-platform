# -*- coding: utf-8 -*-
"""仓位/T 仓计算层 —— 底仓 floor、T 可卖、目标仓位都在这里。

t_gateway 只负责执行硬校验；不再计算/收敛底仓 floor。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from app.services.t_base_floor import base_floor_shares

_SELL_FLOOR_ENFORCE = os.getenv("WOLF_SELL_FLOOR_ENFORCE", "0").strip().lower() in ("1", "true", "yes", "on")


def sell_floor_applies(reason: str = "", is_stop_loss: bool = False, kind: str = "") -> bool:
    """底仓 floor 只作用于止盈类；止损/破位/减仓/未知一律放行。"""
    if is_stop_loss:
        return False
    txt = str(kind or reason or "")
    if not txt:
        return False
    try:
        from app.services import wolf_base_hold as _bh
        return bool(_bh.is_take_reason(txt))
    except Exception:
        return False


def sell_floor_cap(account_id: str, symbol: str, volume: int,
                   sellable: Optional[int] = None,
                   reason: str = "", is_stop_loss: bool = False,
                   kind: str = "", force: bool = False) -> Tuple[int, str]:
    """把止盈类卖腿量收敛到 T 仓空间；止损腿永不 force。"""
    v = int(volume or 0)
    if v <= 0:
        return v, "底仓floor未介入（无量）"
    if not _SELL_FLOOR_ENFORCE:
        return v, "底仓floor未介入（WOLF_SELL_FLOOR_ENFORCE=0）"
    if is_stop_loss and force:
        force = False
    if not force and not sell_floor_applies(reason, is_stop_loss, kind):
        return v, "底仓floor未介入（非止盈类：止损/破位/减仓/未知一律放行）"
    try:
        if sellable is None:
            from app.services.t_gateway import get_sellable_ledger, ledger_item
            led = get_sellable_ledger(account_id)
            item = ledger_item(led, symbol) or {}
            sellable = int(item.get("sellable") or 0)
        sel = int(sellable or 0)
        if sel <= 0:
            return v, "底仓floor未介入（无券可卖）"
        floor = int(base_floor_shares(account_id, symbol, volume=sel) or 0)
        room = max(sel - floor, 0)
        room = (room // 100) * 100
        if v > room:
            return room, ("底仓floor：可卖 %d − 底仓 %d ⇒ 只放行 T 仓 %d 股（原报 %d）"
                          % (sel, floor, room, v))
        return v, "底仓floor：在 T 仓空间内（可卖 %d − 底仓 %d = %d ≥ %d）" % (sel, floor, room, v)
    except Exception as e:
        return v, "底仓floor取数失败(放行原值): %s" % str(e)[:60]


def t_sellable(account_id: str, symbol: str, sellable: Optional[int] = None) -> int:
    """做 T 可卖量 = 可卖 − 底仓 floor（策略层用，不传给网关强制）。"""
    if sellable is None:
        from app.services.t_gateway import get_sellable_ledger, ledger_item
        led = get_sellable_ledger(account_id)
        item = ledger_item(led, symbol) or {}
        sellable = int(item.get("sellable") or 0)
    sel = int(sellable or 0)
    if sel <= 0:
        return 0
    floor = int(base_floor_shares(account_id, symbol, volume=sel) or 0)
    return max(sel - floor, 0) // 100 * 100


def resolve_sell_cap(symbol: str, account_id: str = "stock") -> int:
    """系统可卖上限（做 T 口径：可卖 − 底仓）。"""
    try:
        from app.services.t_gateway import get_sellable_ledger, ledger_item
        led = get_sellable_ledger(account_id)
        item = ledger_item(led, symbol) or {}
        return t_sellable(account_id, symbol, int(item.get("sellable") or 0))
    except Exception as e:
        print(f"[position_sizing] resolve_sell_cap 失败: {e}")
        return 0
