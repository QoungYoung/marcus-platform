# -*- coding: utf-8 -*-
"""wolf_addon_cap.py — **加仓口径**（狼大"两批建仓"）与**本轮建仓日**回放。

## 语料（逐字）
- 2026-06-15：「突破下跌趋势**第一根开头我加一半** 突破后的**第二根红K尾盘 打满**
  然后之后就**只等卖点** 中间会有调仓 但是**没有减仓**。」
- 2026-03-06：「用**第一根或者前两根**顶板红K的开盘价和收盘价做指标计算进场位置和止损位置」
- 2025-06-26：「个股的话到**前两根红K的0.618位置**就值得大仓位了」
⇒ "打满"是**两批**完成的（第一根半仓 + 第二根尾盘满），此后**不再加仓**；他反复强调"这些东西我重复了N次了"。

## 为什么需要（实测证据）
y26 的 SH603773（取整口径）：信号 2026-03-05（+10.01%），
**我们的进场 0306 @38.581 与他 0.618 位 38.55 几乎完全一致**，
但我们在 **0311（当日收涨停 +9.99%）/0312/0316/0317 于 41.68~42.48 追高加仓**，
把均价从 39.02 抬到 **42.19（高 8.1%）**；同一波下跌里，他的止损（红K开盘×0.97=36.72）在 0323 触发 = **−6.5%**，
我们 0324 破位离场 = **−14.2%** ⇒ 差额几乎全部来自"成本被追高抬高"。

## 口径（本模块）
定义**加仓 = 使本轮持仓量创新高的买入**（做T买回不创新高 ⇒ 不受限，避免把做T一并掐死）。
两道上限（都可配，`WOLF_ADDON_CAP=1` 时才生效）：
  · `WOLF_ADDON_MAX_BUYS=2`    本轮**创新高买入**最多 N 次（含首次建仓；语料"两批"⇒2）
  · `WOLF_ADDON_MAX_CALDAYS=3` 创新高买入距**本轮首次买入**不得超过 N 个**自然日**
    （语料是"第一根 + 第二根"=相邻两根K；此处用自然日近似，**自设待校**）

## 本轮（round）的定义
从**最近一次持仓归零之后的第一笔买入**起算；本轮 = 该点之后的所有买卖行。
（这样"卖了又买回"会开新的一轮，与他"清仓后重新找机会"的语义一致。）

· **序列末尾持仓为 0**（= 正准备建仓/买回的那一刻）⇒ `round_start_idx` 返回 `len(seq)`，
  即"**本轮尚未开始**"：没有峰值、没有首笔价基准，本次买入按**新轮第一笔**处理（一律放行）。
  ——2026-09-21 修复；旧实现此处返回 0（整段历史当本轮），会把**上一个已结束轮次**的峰值与首笔价
  当成本轮基准，造成假拦截与假分类（实测见 `round_start_idx` docstring）。
"""
from __future__ import annotations
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


def enabled() -> bool:
    return os.getenv("WOLF_ADDON_CAP", "0").strip().lower() in ("1", "true", "yes", "on")


def _env_i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt)) or dflt))
    except Exception:
        return dflt


def max_buys() -> int:
    return max(0, _env_i("WOLF_ADDON_MAX_BUYS", 2))


def max_caldays() -> int:
    return max(0, _env_i("WOLF_ADDON_MAX_CALDAYS", 3))


def round_fix_on() -> bool:
    """**2026-09-21 修复开关**：`round_start_idx` 在「序列末尾持仓已归零」时的语义。

    · `0`（**库内默认**）= 旧行为：返回 0（把整段历史当本轮）—— 为兼容保留，生产与在跑的回测零变化；
    · `1`（回测 pins 打开）= 修复后：返回 `len(seq)`（本轮尚未开始，空窗口）。

    为什么默认关：这是**行为改变**（实测 drabj13 有 87/104 条「加仓口径」拦截会因此放行，
    j13b 49/58，最早受影响日 2026-01-09），按项目惯例「改动开关默认关、回测默认开、生产零影响」。
    """
    return os.getenv("WOLF_ADDON_ROUND_FIX", "0").strip().lower() in ("1", "true", "yes", "on")


def max_premium_pct() -> float:
    """买回/加仓价相对**本轮首笔买入价**的上限（%）。0=不限。

    语料：2026-04-10「卖出去的怕忍不住买回来怎么 所有自己手上的票**找低位的线挂进去** 不管是黄金分割
    还是均线 **挂远一点**」；2025-04-03「冲上去一定不能追」；2026-06-15「第二根红K尾盘打满」后不再加。
    默认 4.0 = 由他的例子反推（603773：0.618 进场 38.55 → 第二根红K收盘 40.18 ≈ **+4.2%**）——**自设待校**。
    """
    try:
        return float(os.getenv("WOLF_ADDON_MAX_PREMIUM_PCT", "4.0") or 4.0)
    except Exception:
        return 4.0


def _d8(x: Any) -> str:
    return str(x or "").replace("-", "")[:8]


def round_start_idx(seq: Sequence[Sequence[Any]]) -> int:
    """seq=[(day8, direction, volume[, price])] 按时间升序 → 本轮起点下标（最近一次归零之后的第一笔买入）。

    ⚠️ **2026-09-21 修复（用户拍板，受开关 `WOLF_ADDON_ROUND_FIX` 控制）**：序列**末尾持仓已归零**时
    （= 正准备重新建仓的那一刻），**修复语义**返回 `len(seq)`（= **本轮尚未开始**，窗口为空）；
    开关默认关时仍返回 0（保留旧行为，生产/在跑回测零变化）。

    旧行为 `return 0 if start is None else start` 把**上一个已结束的轮次**当成了本轮，实测两个方向的错
    （2026-02-27 drabj13 同一天各一例）：
      ① **假拦截**：SZ002552 当天上午已高抛清仓，买回却被"本轮首笔价 19.920"的 +4% 上限拦下
         （那个"本轮"其实早已结束）；
      ② **假分类/假拦截**：SZ000555 旧轮峰值 2,800 股被当成本轮峰值 ⇒ 新买 2,300 股被判
         「不创新高 = 做T买回」直接放行（两批上限与 3 日窗口全跳过）；反之若新买 > 旧轮峰值，
         又会被"本轮创新高买入已达 2 次"拦下。
    修后语义与模块 docstring 一致（"卖了又买回会开新的一轮"）：空窗口 ⇒ 无峰值、无首笔价基准、
    首笔买入一律放行（规模由网关两分法决定，不归本闸管）。
    """
    i = round_start_idx_raw(seq)
    if i >= len(seq) and not round_fix_on():
        return 0                      # 旧行为（缺陷兼容，默认；见 round_fix_on）
    return i


def round_start_idx_raw(seq: Sequence[Sequence[Any]]) -> int:
    """**修后语义**（不读开关）：末尾已归零 ⇒ `len(seq)`（本轮尚未开始）。"""
    pos, start = 0, None
    for i, row in enumerate(seq):
        _d, dr, v = row[0], row[1], row[2]
        if dr in ("买入", "buy"):
            if pos == 0 and start is None:
                start = i
            pos += int(v)
        else:
            pos -= int(v)
            if pos <= 0:
                pos, start = 0, None
    return len(seq) if start is None else start


def round_start_day(seq: Sequence[Sequence[Any]]) -> Optional[str]:
    i = round_start_idx(seq)
    for row in seq[i:]:
        if row[1] in ("买入", "buy"):
            return row[0]
    return None


def round_first_price(seq: Sequence[Sequence[Any]]) -> Optional[float]:
    """本轮首笔买入价（买回价上限的基准）。"""
    i = round_start_idx(seq)
    for row in seq[i:]:
        if row[1] in ("买入", "buy") and len(row) > 3 and float(row[3] or 0) > 0:
            return float(row[3])
    return None


def decide(seq: Sequence[Sequence[Any]], add_volume: int, day: Any,
           mb: Optional[int] = None, mc: Optional[int] = None,
           add_price: Optional[float] = None, mp: Optional[float] = None) -> Tuple[bool, str]:
    """纯函数：本轮再买 add_volume 是否越过"两批"口径 → (allow, reason)。

    做T买回（不使持仓创新高）一律放行（他天天做T，两批说的是"加仓"）。
    """
    mb = max_buys() if mb is None else int(mb)
    mc = max_caldays() if mc is None else int(mc)
    add_volume = int(add_volume or 0)
    if add_volume <= 0:
        return True, ""
    if mb <= 0 and mc <= 0:
        return True, ""
    i0 = round_start_idx(seq)
    cur = seq[i0:]
    pos = 0
    peak = 0
    newhigh = 0
    first_buy = None
    for row in cur:
        d, dr, v = row[0], row[1], row[2]
        v = int(v)
        if dr in ("买入", "buy"):
            pos += v
            if first_buy is None:
                first_buy = d
            if pos > peak:
                peak = pos
                newhigh += 1
        else:
            pos -= v
    after = pos + add_volume
    d = _d8(day)
    # ① **买回/加仓价上限**（对本轮首笔买入价）——适用于**所有**买入（含做T买回）：
    #    语料 2026-04-10「怕忍不住买回来…找低位的线挂进去 挂远一点」/ 2025-04-03「冲上去一定不能追」
    _mp = max_premium_pct() if mp is None else float(mp)
    _fp = round_first_price(seq)
    if _mp > 0 and add_price and _fp and float(add_price) > _fp * (1 + _mp / 100.0):
        return False, ("加仓口径：买入价 %.3f 高于本轮首笔买入价 %.3f 的 +%.1f%%（狼大 2026-04-10「怕忍不住买回来…"
                       "找低位的线挂进去 挂远一点」/ 2025-04-03「冲上去一定不能追」）"
                       % (float(add_price), _fp, _mp))
    # ② 不创新高 ⇒ 是**做T买回**（价格已在①的上限内）⇒ 放行。
    #    这样不会把"他天天做T"一并掐死——两批说的是**加仓**。
    if after <= peak and peak > 0:
        return True, ""
    # ③ 创新高 = **加仓** ⇒ 受两批口径约束（次数 + 建仓时窗）
    if mb > 0 and newhigh >= mb:
        return False, ("加仓口径：本轮创新高买入已达 %d 次（上限 %d，狼大 2026-06-15「第一根加一半 + "
                       "第二根尾盘打满，之后不再加」）" % (newhigh, mb))
    if mc > 0 and first_buy:
        try:
            dd = (datetime.strptime(d, "%Y%m%d") - datetime.strptime(_d8(first_buy), "%Y%m%d")).days
        except Exception:
            dd = 0
        if dd > mc:
            return False, ("加仓口径：距本轮首次买入 %d 个自然日 > %d（狼大 2026-06-15「第二根红K尾盘打满」后不再加）"
                           % (dd, mc))
    return True, ""


def _fetch_seq(account_id: str, symbol: str) -> List[Tuple[str, str, int, float]]:
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT trade_date, direction, volume, price FROM paper_trades "
            "WHERE account_id = :a AND symbol = :s AND (voided = 0 OR voided IS NULL) "
            "ORDER BY created_at, id"), {"a": account_id, "s": symbol}).fetchall()
        return [(_d8(r[0]), str(r[1]), int(r[2] or 0), float(r[3] or 0)) for r in rows]
    finally:
        db.close()


def check(account_id: str, symbol: str, add_volume: int, day: Any,
          add_price: Optional[float] = None) -> Tuple[bool, str]:
    """DB 版：读本轮流水后调 decide()。关开关或异常一律 fail-open（不拦）。"""
    if not enabled():
        return True, ""
    try:
        return decide(_fetch_seq(account_id, symbol), add_volume, day, add_price=add_price)
    except Exception as e:                                  # pragma: no cover
        print("[addon_cap] 查询失败(fail-open): %s" % str(e)[:90], flush=True)
        return True, ""
