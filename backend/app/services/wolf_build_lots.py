# -*- coding: utf-8 -*-
"""wolf_build_lots.py — **建仓/加仓后持仓不得只剩 1 手**（用户 2026-09-22 拍板"修 2b"）。

## 为什么（一条被数据坐实的结构性缺陷）

底仓 floor 的口径是「**持仓 < 2 手 ⇒ 整仓即底仓**」：
`t_base_floor._rebase_floor()` 里 `if sel < 2 * min_lot: return sel`（min_lot=100）。
⇒ **一手仓（100 股）的 T 仓恒为 0** ⇒ `t_monitor` 卖腿量推导 `(sellable − floor) = 0` ⇒
   高抛 / 黄线 / 斐波 / 倒T / 确认制T出 这些**止盈类卖腿在这类持仓上永久失效**，
   只能等「破位 / 减仓 / 趋势转弱」类穿透底仓（`t_capacity.base_penetrate_allowed`，还要 struct_weak）。

实测代价（T5 / drabt5，2026-09-22 复盘）：
· 一手仓 **9 笔 / 占买入 21%**；
· 两条"底仓"机制共拦下 **872 条**卖腿，其中 **145 条**发生在持仓正好 100 股时；
· 用户点名的那笔：**兆易创新 603986** 0317 09:40 买 100 股 @307.00（涨停次日），
  0318 冲到 313.68，三条卖腿（黄线 308.20 / 高抛 311.00 / 倒T 312.09）**全被拦死**，
  拖到 0323 才靠破位腿割在 277.55 ⇒ **−2,987**（若 0318 在 311.00 走掉是 +400，差 ≈3,387）。

## 口径

`WOLF_BUILD_MIN_LOTS`（**库内默认 0 = 关**，生产逐位不变；回测由 pins 置 2）：
买入时若「**买完持仓**」落在 `(0, min_lots×100)` 区间 ⇒ **把这笔补到 min_lots 手**；
补完若超单笔/单标/总仓/现金上限，由网关既有校验拒单 ⇒
**实际效果 = 「要么买够 2 手，要么不买」，绝不再产生新的 1 手仓**。

为什么补而不是直接拒：拒单会连"本来能成交的小仓"一起消失（等于换了一条策略），
而补单只是"把同一笔买大一点"，风控口径仍由既有三道校验把关（越限就自然拒）。

**只作用于买入**。卖出侧"减到只剩 1 手"是同一族的另一半（`100 < pos < 200` 的残仓同样卖不动），
未在本模块处理，另行评估。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

LOT = 100


def min_lots() -> int:
    try:
        return max(int(float(os.getenv("WOLF_BUILD_MIN_LOTS", "0") or 0)), 0)
    except Exception:
        return 0


def enabled() -> bool:
    return min_lots() > 1


def topup_volume(pos: int, volume: int, lots: Optional[int] = None) -> Tuple[int, str]:
    """纯函数：`(现持 pos 股, 本笔 volume 股)` → `(买后股数, 说明)`。

    只有"买完落在一手仓"这一种情形会被抬高；关着、已 ≥2 手、或本来就不买(volume≤0) 一律原样返回。
    """
    v = int(volume or 0)
    p = int(pos or 0)
    k = min_lots() if lots is None else int(lots)
    if k <= 1 or v <= 0:
        return v, ""
    after = p + v
    if 0 < after < k * LOT:
        need = k * LOT - after
        new = v + ((need + LOT - 1) // LOT) * LOT
        return new, ("买后持仓 %d 股不足 %d 手（一手仓的 T 仓恒为 0 ⇒ 止盈腿永久失效，"
                     "见模块头）⇒ 本笔 %d→%d 股" % (after, k, v, new))
    return v, ""
