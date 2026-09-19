#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓台账口径（唯一真相）+ VN.PY 持仓事件写回护栏。

事故（生产 2026-09-19）
----------------------
backend 容器里的 VN.PY 模拟盘引擎只认「走 gateway 的成交」；直连 DB 路径写下的成交
（orderid 形如 `stock_order0000xx` / `ORD0000xx`，由 apps/paper-trading 等直接落库）
它的内存持仓完全看不见。而 `vnpy_listeners.PositionEventListener` 会**无条件**把引擎
内存持仓写回 `paper_positions`，且 VN.PY paper 的 `timer_interval=3` ⇒ 每 3 秒一次。

实测：台账（paper_trades 未作废、≥09-01 基线）FIFO = SH588170 2900 股/均价 0.919，
而库里的行被引擎 09-18 10:36 的快照反复盖成 7000 股/均价 0.9087439（= 09-18 10:36
FIFO 播种值 8200 减去唯一那笔走 gateway 的卖出 1200）。后果：

  ① `jobs/recon_account_cash.py` 持仓线对账失败（fifo 2900 / db 7000，现金漂移其实是 0）；
  ② 卖腿/止损按 `paper_positions` 推量 ⇒ 可能卖出并不存在的 4100 股（幽灵持仓，
     与 2026-09-07「连卖超卖」、fd1d524「幽灵持仓重复止损」同族）。

口径（与既有约定一致，不再新增口径）
------------------------------------
· `paper_trades`（未作废）是该账户持仓的**唯一真相**：同 `t_gateway._fifo_net_position`
  （2026-09-07 修复注释「volume 以 paper_trades FIFO 净持仓为权威」）与
  `VNPyBridge._compute_seed_positions_from_trades` 的 FIFO 重放；
· 只有「该标的没有任何成交流水」时（外部同步仓/种子仓），才回退用引擎快照。

开关：`VNPY_POSITION_LEDGER_GUARD`（默认开；置 0/false/no 退回旧行为，方便回滚）。
"""
import logging
import os
from typing import Iterable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNT = "stock"

try:  # core 在 sys.path 时用统一词表（读写两端同口径）
    from trade_direction import is_buy as _is_buy, is_sell as _is_sell  # noqa: E402
except Exception:  # pragma: no cover - core 不在路径时退化为内置词表
    def _is_buy(value) -> bool:
        return str(value or "").strip().lower() in ("买入", "buy")

    def _is_sell(value) -> bool:
        return str(value or "").strip().lower() in ("卖出", "sell")


def guard_enabled() -> bool:
    """台账护栏是否启用（默认开）。"""
    return os.getenv("VNPY_POSITION_LEDGER_GUARD", "1").strip().lower() not in (
        "0", "false", "no", "off", "")


def fifo_position(rows: Iterable[Sequence]) -> Optional[Tuple[int, float]]:
    """FIFO 重放成交流水 → (净持仓, 剩余成本均价)。

    rows: 按 id **正序**的 (direction, price, volume)；无成交（空序列/全表外方向词表）
    返回 None —— 调用方据此判断「台账是否覆盖该标的」。
    """
    lots = []
    seen = False
    for row in rows:
        try:
            direction, price, volume = row[0], float(row[1]), int(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        if volume <= 0:
            continue
        if _is_buy(direction):
            seen = True
            lots.append([price, volume])
        elif _is_sell(direction):
            seen = True
            remaining, i = volume, 0
            while remaining > 0 and i < len(lots):
                used = min(lots[i][1], remaining)
                lots[i][1] -= used
                remaining -= used
                if lots[i][1] == 0:
                    lots.pop(i)
                else:
                    i += 1
            # 超卖：台账卖出多于在手买入（历史脏数据）→ 余量按 0 成本不建仓，仅记账
    if not seen:
        return None
    total_vol = sum(lot[1] for lot in lots)
    if total_vol <= 0:
        return (0, 0.0)
    total_cost = sum(lot[0] * lot[1] for lot in lots)
    return (total_vol, total_cost / total_vol)


def query_ledger_position(cur, symbol: str, account_id: str = DEFAULT_ACCOUNT):
    """查台账并按 FIFO 重放（一次索引查询；调用方复用已有 cursor）。

    返回 (volume, avg_price)；该标的无成交流水返回 None。
    """
    cur.execute(
        "SELECT direction, price, volume FROM paper_trades "
        "WHERE account_id = %s AND symbol = %s AND (voided = 0 OR voided IS NULL) "
        "ORDER BY id",
        (account_id, symbol),
    )
    return fifo_position(cur.fetchall())


_log_state: dict = {}


def log_once(key: str, message: str) -> bool:
    """同一 key 只在内容变化时打日志（每 3 秒一条会把日志刷爆）。"""
    if _log_state.get(key) == message:
        return False
    _log_state[key] = message
    if len(_log_state) > 500:  # 防御：极端情况下别无限增长
        _log_state.clear()
        _log_state[key] = message
    logger.warning("[PositionListener] %s", message)
    return True
