# -*- coding: utf-8 -*-
"""position_discipline.py — 持仓层「去弱留强」（P1-6, 2026-09-10）

狼大原话:
  · 2026-04-23「你们一定要记住一点 **反弹的时候卖弱的 留强的 不要搞反了**
    **不要觉得哪个反弹多就卖 留那种没波动的**。」
  · 2025-02-06「…按之前的分类做不同的线，指数票 容量票做通道，其他做轮动 **去弱留强**」
  · 2025-07-11（换票阈值）「低于换后的票 3%…直接割肉换过去」

**判据取向（按用户"更贴合狼大的方向"）**: 以**反弹幅度**为主判据。
依据就是上面那句反直觉的话 —— 「不要觉得哪个反弹多就卖」= **反弹多的是强的, 要留**;
「留那种没波动的」= 没波动的才是弱票。故按**反弹幅度升序**卖最弱的。
（备选 rs=个股−主题相对强度 作次序键, 见 WOLF_POSITION_DISC_RS; 默认关闭, 因为狼大字面指向反弹幅度。）

**反面提示（必须保留）**: 狼大 2026-05-26「机构目的就是**逼大家趋弱留强** 能不能理解？这是**明牌**」
→ 该动作在**下跌段**执行等于替机构接盘。故本模块只在"结构确认过的反弹"语境下输出建议,
且要求强弱分化足够(默认 ≥3%), 持仓数足够(默认 ≥3), 每次最多卖 2 只。

**作用域**: 只建议**减 T 仓**(复用既有 wolf_defensive_t_reduce 管道), **不动底仓** ——
底仓的离场由 P1-1(风向标死 L2)与破位腿负责。纯函数, 不直接下单。

环境变量:
  WOLF_POSITION_DISC=0        关闭(默认 1 启用)
  WOLF_POSITION_DISC_GAP      强弱分化门槛%(默认 3.0, 依狼大 2025-07-11 的 3%)
  WOLF_POSITION_DISC_MIN      最少持仓数(默认 3)
  WOLF_POSITION_DISC_MAX_SELL 每次最多卖几只(默认 2)
  WOLF_POSITION_DISC_HIGH=0   关闭 G5 高位方向分支(默认 1 = 高位方向走"卖强留弱")
  WOLF_POSITION_DISC_RS       (既有)用 rs 作次序键(默认关)
"""
import os

DEFAULT_WINDOW = 5          # 反弹窗口(交易日)
DEFAULT_GAP_PCT = 3.0       # 强弱分化门槛, 依狼大 2025-07-11「低于换后的票 3%」
DEFAULT_MIN_POS = 3
DEFAULT_MAX_SELL = 2


def rebound_pct(prev_days, window=DEFAULT_WINDOW):
    """反弹幅度% = 窗口内最后收盘 / 窗口内最低 low − 1。

    prev_days 接受**两种**形态：
      · {YYYYMMDD: {"close","high","low","vol"}}（原契约，按 key 排序）;
      · [{"close","high","low","vol"}, ...]（**已按时间升序**的序列）。
    取"从低点反弹了多少"而非"区间涨幅", 正是狼大说的"反弹多/没波动"那个维度。
    数据不足(<2 根)返回 None。

    ⚠️ 为什么要兼容 list（2026-09-11 生产事故）：t_monitor._prev_daily 返回的是**list**（丢掉了日期 key），
    而本函数原先直接 `sorted(prev_days)` → 对 list 会去**比较 dict 本身** →
    抛 `TypeError: '<' not supported between instances of 'dict' and 'dict'`，
    被 `_check_position_discipline` 的 except 吞掉 → **P1-6 去弱留强在生产中从未真正生效**（静默失效第 8 例）。
    """
    if not prev_days:
        return None
    if isinstance(prev_days, dict):
        rows = [prev_days[k] for k in sorted(prev_days)][-max(2, int(window)):]
    else:
        rows = list(prev_days)[-max(2, int(window)):]      # list：保持既有顺序（调用方保证时间升序）
    if len(rows) < 2:
        return None
    lows = [float(r.get("low") or 0) for r in rows if float(r.get("low") or 0) > 0]
    last = float(rows[-1].get("close") or 0)
    if not lows or last <= 0:
        return None
    lo = min(lows)
    if lo <= 0:
        return None
    return round((last / lo - 1) * 100, 2)


def select_weak(items, gap_pct=None, min_positions=None, max_sell=None):
    """从持仓中挑出"应该卖掉的票"——**按方向高低位分两套相反规则**（G5, 2026-09-14）。

    items: [{"symbol":..., "rebound": float|None, "dir_pos": "HIGH"|"LOW"|"MID"|"UNKNOWN", ...}]
           （额外字段原样带回, 便于上层记录理由）

    狼大 2026-09-04 15:07 分类型总纲：
      · **高位方向**（大科技那类）：**卖强的 留弱的**（"拉升后都走"）→ 分支 `high_sell_strong`
        —— 按反弹幅度**降序**卖最强的；
      · **低位/中位/未知方向**：**留强丢弱**（原 P1-6 行为）→ 分支 `low_sell_weak`
        —— 按反弹幅度**升序**卖最弱的（2026-04-23「反弹的时候卖弱的 留强的 不要搞反了」）。

    判定顺序（任一不满足即不动作 —— 狼大自己提示这动作可能是机构设的局, 故门槛偏严）:
      1. 有效样本(rebound 非 None)数量 >= min_positions
      2. **在每个分支内部**最强与最弱反弹幅度差 >= gap_pct（没有分化就不该动）+ 该分支至少 2 只
      3. 高位分支优先占用卖出额度（"拉升后都走"是主动离场），其余额度给低位分支的弱者
    返回 {"sells":[...], "skip": reason或None, "spread": float|None, "branches": {...}}
    """
    gap = float(gap_pct if gap_pct is not None else os.getenv("WOLF_POSITION_DISC_GAP", DEFAULT_GAP_PCT))
    mn = int(min_positions if min_positions is not None else os.getenv("WOLF_POSITION_DISC_MIN", DEFAULT_MIN_POS))
    ms = int(max_sell if max_sell is not None else os.getenv("WOLF_POSITION_DISC_MAX_SELL", DEFAULT_MAX_SELL))
    high_on = os.getenv("WOLF_POSITION_DISC_HIGH", "1").strip() not in ("0", "false", "no")

    valid = [dict(it) for it in (items or []) if it.get("rebound") is not None]
    if len(valid) < mn:
        return {"sells": [], "skip": "持仓有效样本 %d < 门槛 %d" % (len(valid), mn), "spread": None,
                "branches": {}}
    hi_idx = {i for i, it in enumerate(valid)
              if high_on and str(it.get("dir_pos") or "").upper() == "HIGH"}
    groups = []          # [(branch, sorted_group, 是否优先占用额度)]
    if hi_idx:
        groups.append(("high_sell_strong",
                       sorted((valid[i] for i in hi_idx), key=lambda it: -it["rebound"]), True))
    rest = [it for i, it in enumerate(valid) if i not in hi_idx]
    if rest:
        groups.append(("low_sell_weak", sorted(rest, key=lambda it: it["rebound"]), False))

    sells, branches, first_spread, skips = [], {}, None, []
    for branch, grp, _prio in groups:
        if len(grp) < 2:
            branches[branch] = {"n": len(grp), "spread": None, "picked": 0,
                                "skip": "该分支持仓 %d < 2（无可比强弱）" % len(grp)}
            continue
        _rb = [it["rebound"] for it in grp]
        sp = round(max(_rb) - min(_rb), 2)   # 与排序方向无关（高位分支按降序排）
        branches[branch] = {"n": len(grp), "spread": sp, "picked": 0, "skip": None}
        if sp < gap:
            branches[branch]["skip"] = "分支内强弱分化 %.2f%% < 门槛 %.1f%%" % (sp, gap)
            skips.append(branch)
            continue
        if first_spread is None:
            first_spread = sp
        # **每组必留一只**：低位分支留最强(sorted 升序的最后一个)、高位分支留最弱(降序的最后一个)
        # → 候选 = grp[:-1]（"卖强留弱"/"留强丢弱"字面都是"留一个"，不能把该方向清空）
        for it in grp[:-1][:ms]:
            if len(sells) >= ms:
                break
            it["branch"] = branch
            sells.append(it)
            branches[branch]["picked"] += 1
    if not sells:
        why = ("强弱分化不足: " + "; ".join("%s(%s)" % (k, v.get("skip")) for k, v in branches.items())) \
            if branches else "无可比样本"
        return {"sells": [], "skip": why, "spread": first_spread, "branches": branches}
    return {"sells": sells, "skip": None, "spread": first_spread, "branches": branches}


def directive(sells, spread):
    """给 agent/日志的指令文本（本模块不直接下单）。"""
    if not sells:
        return ""
    hi = [s for s in sells if s.get("branch") == "high_sell_strong"]
    lo = [s for s in sells if s.get("branch") != "high_sell_strong"]
    parts = []
    if hi:
        parts.append("高位方向**卖强留弱**（狼大 2026-09-04「高位方向…卖强的 留弱的 拉升后都走」）："
                     "减 T 仓最强 %s" % ", ".join("%s(反弹%+.2f%%)" % (s.get("symbol"), s.get("rebound") or 0)
                                                for s in hi))
    if lo:
        parts.append("低位/中位方向**留强丢弱**（狼大 2026-04-23「反弹的时候卖弱的 留强的 不要搞反了」）："
                     "减 T 仓最弱 %s" % ", ".join("%s(反弹%+.2f%%)" % (s.get("symbol"), s.get("rebound") or 0)
                                                for s in lo))
    return "⚠️ 分方向去弱留强（强弱分化 %.2f%%）：%s；底仓不动。" % (spread or 0, "；".join(parts))
