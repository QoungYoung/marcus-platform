#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成交流水方向词表的唯一口径（读写两端共用）。

为什么需要它
------------
`paper_trades.direction` 历史上混用中文与英文：
  · engine 路径（apps/paper-trading/paper_engine.py 自带 Direction 枚举）= 中文「买入/卖出」；
  · VN.PY 桥接路径（vnpy_listeners）= 中文；
  · 人工/脚本直插（例：apps/main_line/add_pos_512480.py）= 曾写英文 `buy`。
而读取端一度只判中文（`direction == '买入'`），英文行会被**静默忽略**：既不进持仓、
也不进已实现/胜率，且不报错——生产上曾因此让 15,400 股 SH512480 凭空消失、现金口径错位。

约定
----
· 写库统一用中文（`to_canonical`）；
· 读取一律用 `is_buy/is_sell` 或 SQL 片段 `SQL_BUY/SQL_SELL`，兼容历史英文行；
· 出现表外词表时记录 warning 并由 jobs/recon_account_cash.py 的对账兜底。
"""
import logging
from typing import Optional

BUY = "买入"
SELL = "卖出"

# 兼容集合（读侧）：中文为准，英文/大小写/多空别名一并接受
BUY_WORDS = ("买入", "buy", "BUY", "Buy", "long", "LONG")
SELL_WORDS = ("卖出", "sell", "SELL", "Sell", "short", "SHORT")

# 内联 SQL 片段（读侧）：配合 lower() 使用，例：f"... AND lower(direction) IN {SQL_BUY}"
SQL_BUY = "('买入', 'buy')"
SQL_SELL = "('卖出', 'sell')"

_BUY_SET = {w.lower() for w in BUY_WORDS}
_SELL_SET = {w.lower() for w in SELL_WORDS}

_log = logging.getLogger(__name__)

_warned: set = set()


def is_buy(value: Optional[str]) -> bool:
    """是否买入方向（兼容中英文/大小写）。"""
    return value is not None and str(value).strip().lower() in _BUY_SET


def is_sell(value: Optional[str]) -> bool:
    """是否卖出方向（兼容中英文/大小写）。"""
    return value is not None and str(value).strip().lower() in _SELL_SET


def normalize(value: Optional[str]) -> str:
    """归一化为中文方向；未知词表抛 ValueError。"""
    if is_buy(value):
        return BUY
    if is_sell(value):
        return SELL
    raise ValueError(f"未知的成交方向: {value!r}")


def to_canonical(value: Optional[str]) -> str:
    """写库前归一化为中文方向；未知词表原样返回并告警一次（不阻塞落库）。"""
    try:
        return normalize(value)
    except ValueError:
        key = str(value)
        if key not in _warned:
            _warned.add(key)
            _log.warning("[trade_direction] 出现表外成交方向 %r，已原样写库；请检查写入方", value)
        return value if value is not None else ""


def validate(value: Optional[str]) -> bool:
    """是否属于已登记词表（对账/自检用）。"""
    return is_buy(value) or is_sell(value)
