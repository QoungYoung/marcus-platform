# -*- coding: utf-8 -*-
"""bt_exits.py — **出场层（卖侧）回放**：把生产 `t_monitor` 的卖出规则搬到回测里（只改回测，不碰生产）。

生产卖侧的规则清单（`backend/app/services/t_monitor.py` 与相关模块）与本文覆盖情况：

| 规则 | 生产实现 | 本文 |
|---|---|---|
| 黄线离场 `custom_vwap_sell` | 表达式 `quote.vwap_break == True`（现价 < 分时均价） | ✅ 自算 `quote.vwap_break` |
| 高抛 `high_sell` | 表达式 `minute.m5.t_sell == True`（`t_monitor._build_minute_snapshot` → `_t_signals_from_m5`，狼大 `t_signal` 逻辑） | ✅ 复用生产 `_t_signals_from_m5` |
| 破位离场 `custom_support_sell` | 表达式 `quote.break_support == True`（现价 ≤ 支撑 `support_l1`，`support_resistance.compute_levels`） | ✅ 复用生产 `support_resistance`（日线走 pack） |
| 止损 `stop_loss` | `t_monitor._check_stop_loss`：现价 ≤ `stop_loss_price`；止损价在 `t_build` 里按 `avg×(1−max(0.03, amp_med/100×0.55))` 推导 | ✅ 同式推导（amp_med 用 pack 日线振幅中位数） |
| 倒T卖出 `wolf_dao_t_sell` | `wolf_t_rules.dao_t_sell_quote(quote, prev_days)` | ✅ 复用 |
| 防守减仓 `wolf_defensive_t_reduce` | `wolf_t_rules.defensive_t_reduce_quote(quote, prev_days, wave_op)` | ✅ 复用 |
| 等量换手兑现 `roundtrip_sell` | `roundtrip_sell.sell_up_for(sym)`（个股 3% / ETF 2%）→ 目标卖价 = 低吸均价 ×(1+up) | ✅ 复用（默认减半兑现，与生产"保留底仓"一致） |
| 趋势止损 `WOLF_TREND_STOP` | `wolf_trend_stop`（默认开） | ⏳ 待接（API 需再确认） |
| 布林中轨离场 `WOLF_BOLL_MID_EXIT` | `wolf_boll_levels`（默认关：`WOLF_BOLL_MID_EXIT=0`） | ⏳ 待接（默认关，影响小） |
| 周末避险 `WOLF_WEEKEND_HEDGE` | 独立任务 `jobs/…weekend…` | ⏳ 待接 |

口径：所有判定只用**当根 bar 及之前**的数据（`bars_up_to`），**不引入未来**。
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


_HAS = {}


_ALIASES = {"roundtrip_sell": "app.services.roundtrip_sell",
            "roundtrip_priority": "app.services.roundtrip_priority",
            "support_resistance": "app.services.support_resistance"}


def _mod(name):
    if name not in _HAS:
        last = None
        for cand in (name, _ALIASES.get(name)):
            if not cand:
                continue
            try:
                _HAS[name] = importlib.import_module(cand)
                last = None
                break
            except Exception as e:
                last = e
        if name not in _HAS:
            print("[exits] 模块 %s 不可用：%s" % (name, str(last)[:80]), file=sys.stderr)
            _HAS[name] = None
    return _HAS[name]


def _sma(vals, n):
    v = [float(x) for x in vals[-n:] if x is not None]
    return (sum(v) / len(v)) if v else 0.0


def amp_median(daily_rows, day: str, look: int = 20):
    """近 `look` 个已完成交易日的振幅中位数（%），用于推导止损价（与 `t_build` 同式）。"""
    prev = [r for r in daily_rows if str(r.get("trade_date")) < day][-look:]
    amps = []
    for r in prev:
        try:
            c = float(r.get("close") or 0)
            h, l = float(r.get("high") or 0), float(r.get("low") or 0)
            if c > 0 and h > 0 and l > 0:
                amps.append((h - l) / c * 100.0)
        except (TypeError, ValueError) as _e_sil1:
            _silent_alert("bt_exits.py:72", _e_sil1)
            continue
    if not amps:
        return 3.0
    amps.sort()
    return amps[len(amps) // 2]


def stop_price(avg_cost: float, amps_med: float) -> float:
    """生产 `t_build` 的止损价推导（下限 3%；可更紧不可更宽）。"""
    return round(avg_cost * (1 - max(0.03, (amps_med or 3.0) / 100.0 * 0.55)), 2)


def _patch_levels_daily(daily_rows):
    """把 `support_resistance.get_daily_bars` 指到回测的 pack 日线（避免它去打生产数据源）。"""
    sr = _mod("support_resistance")
    if sr is None:
        return False
    try:
        def _get(symbol, n=70):
            return [dict(r) for r in daily_rows][-n:]
        sr.get_daily_bars = _get
        return True
    except Exception:
        return False


# 每条出场规则的卖出比例：止血类（止损/破位）全卖；兑现/减仓类减半（生产"减半/保留底仓"语义）
EXIT_FRACTION = {"stop_loss": 1.0, "custom_support_sell": 1.0,
                 "custom_vwap_sell": 0.5, "roundtrip_sell": 0.5, "high_sell": 0.5,
                 "wolf_dao_t_sell": 0.5, "wolf_defensive_t_reduce": 0.5,
                 "wolf_board_half_sell": 0.5, "wolf_boll_mid_exit": 0.5}


def _sell_up(symbol: str) -> float:
    """兑现幅度：优先用生产 `roundtrip_sell.sell_up_for`（个股 3% / ETF 2%），否则 3%。"""
    rs = _mod("roundtrip_sell")
    try:
        return float(rs.sell_up_for(symbol)) if rs is not None else 0.03
    except Exception:
        return 0.03


_FRAC_OVERRIDE = {}


def exits_for_bar(symbol: str, bars_up_to, day: str, avg_cost: float, daily_rows,
                  prev_days_bars, wave_op: str = "t_only", support_l1: float = 0.0,
                  streak: int = 0, buy_date: str = ""):
    """对"当前 bar"评估全部出场规则 → [(kind, reason)]（同一 bar 命中多条时按生产优先级排序）。

    优先级（与生产一致：止损/破位这类"止血"优先于"兑现"）：
      1. stop_loss（现价 ≤ 止损价）  2. custom_support_sell（破位）
      3. custom_vwap_sell（黄线）    4. high_sell（分时T出）
      5. roundtrip（等量换手兑现）   6. dao_t_sell（倒T）  7. defensive_t_reduce（防守减仓）
    """
    hits = []
    today = [b for b in bars_up_to if str(b["time"])[:10].replace("-", "") == day]
    if not today:
        return hits
    cur = float(today[-1]["close"])
    cum_v = sum(float(b.get("vol") or 0) for b in today)
    cum_a = sum(float(b.get("amount") or 0) for b in today)
    avg = (cum_a / cum_v) if cum_v > 0 else 0.0

    quote = {"current": cur, "average": avg, "high": max(float(b["high"]) for b in today),
             "low": min(float(b["low"]) for b in today),
             "pre_close": float(prev_days_bars[-1]["close"]) if prev_days_bars else 0.0}

    # 1) 止损：**生产六层链**（不再用自造公式）
    #    · 基础值 = `t_build` 的 `stop_loss_price`（同式：均价×(1−max(3%, 振幅中位/100×0.55))）
    #    · ①④ 阶段化 `wolf_early_stop.resolve_stop`：建仓 ≤13 交易日 → **建仓日为锚的波段低点 ×(1−3%)**；
    #      有持有期利空（黑天鹅）→ 退回 stop_loss_price
    #    · ② 趋势线 `wolf_trend_stop.evaluate`（`WOLF_TREND_STOP=1` 默认开）
    _base_stop = stop_price(avg_cost, amp_median(daily_rows, day))
    sp, _src, _why = _base_stop, "base", ""
    try:
        es = _mod("app.services.wolf_early_stop")
        if es is not None and hasattr(es, "resolve_stop"):
            _bars40 = [{"date": str(r.get("trade_date")), "high": r.get("high"), "low": r.get("low"),
                        "close": r.get("close"), "vol": r.get("vol")}
                       for r in daily_rows if str(r.get("trade_date")) <= day][-40:]
            _sp2, _src, _why = es.resolve_stop(_base_stop, _bars40, buy_date, symbol=symbol, today=day)
            if _sp2:
                sp = float(_sp2)
    except Exception as e:
        print("[exits] early_stop 解析失败 %s: %s" % (symbol, str(e)[:60]), file=sys.stderr)
    try:
        TS = _mod("app.services.wolf_trend_stop")
        if TS is not None and hasattr(TS, "evaluate"):
            _bars90 = [{"date": str(r.get("trade_date")), "high": r.get("high"), "low": r.get("low"),
                        "close": r.get("close"), "vol": r.get("vol")}
                       for r in daily_rows if str(r.get("trade_date")) <= day][-90:]
            _st = TS.evaluate(_bars90)
            _act, _rw = (TS.decision(_st) if hasattr(TS, "decision") else (None, ""))
            if _act in ("stop", "sell", "exit") or (_st or {}).get("action") in ("stop", "sell"):
                hits.append(("stop_loss", "趋势线止损：%s" % str(_rw or _act)[:70]))
    except Exception as e:
        print("[exits] trend_stop 失败 %s: %s" % (symbol, str(e)[:60]), file=sys.stderr)
    if cur > 0 and cur <= sp:
        # 卖量分级（生产 `_stop_exit_volume`）：≥14:55 收盘确认 → **清仓**；盘中 → 减半
        _hm_min = int(str(today[-1]["time"])[11:13]) * 60 + int(str(today[-1]["time"])[14:16])
        _mode = "close_clear" if _hm_min >= 14 * 60 + 55 else "half"
        hits.append(("stop_loss", "现价 %.2f ≤ 止损价 %.2f（%s %s；卖量=%s）"
                     % (cur, sp, _src, str(_why)[:40], _mode)))
        _FRAC_OVERRIDE["stop_loss"] = 1.0 if _mode == "close_clear" else 0.5

    # 2) 破位（现价 ≤ 支撑 l1）
    if support_l1 and cur > 0 and cur <= support_l1:
        hits.append(("custom_support_sell", "现价 %.2f ≤ 支撑 %.2f" % (cur, support_l1)))

    # 3) 黄线离场：**复用生产 `roundtrip_priority.roundtrip_decision`**
    #    （幅度：现价 ≤ 黄线×(1−0.5%)；持续：连续 WOLF_VWAP_BREAK_ROUNDS=2 轮；
    #      另有时间窗 09:45-10:00/14:00-14:30 与"到点决断 14:00"语义）
    #    ⚠️ 生产一轮 = 30s 轮询 → 回测按 5min bar 当一轮（口径已标注）。
    rp = _mod("roundtrip_priority")
    if rp is not None and hasattr(rp, "roundtrip_decision"):
        try:
            hm = int(str(today[-1]["time"])[11:13]) * 60 + int(str(today[-1]["time"])[14:16])
            act, why, streak2 = rp.roundtrip_decision(
                cur, avg_cost, avg or None, float(_sell_up(symbol)), streak=streak, hm=hm)
            if act and act != "wait":
                hits.append(("custom_vwap_sell" if "vwap" in str(why) or "黄线" in str(why) else "roundtrip_sell",
                             "%s（%s）" % (str(why)[:70], act)))
        except Exception as e:
            print("[exits] roundtrip_decision 失败 %s: %s" % (symbol, str(e)[:60]), file=sys.stderr)

    # 4) 高抛（分时T出，生产 `_t_signals_from_m5`）
    tm = _mod("app.services.t_monitor")
    if tm is not None and hasattr(tm, "_t_signals_from_m5"):
        try:
            m5 = [{"time": str(b["time"]), "close": float(b["close"]), "high": float(b["high"]),
                   "low": float(b["low"]), "vol": float(b.get("vol") or 0)} for b in bars_up_to
                  if str(b["time"])[:10].replace("-", "") == day]
            _t1, _tsell = tm._t_signals_from_m5(m5)
            if _tsell:
                hits.append(("high_sell", "分时T出信号（第一次高点后停量+二次拉升无量不过前高）"))
        except Exception as e:
            print("[exits] t_sell 计算失败 %s: %s" % (symbol, str(e)[:60]), file=sys.stderr)

    # 5) 等量换手兑现（低吸均价 ×(1+档位)）
    rs = _mod("roundtrip_sell")
    if rs is not None and hasattr(rs, "sell_up_for"):
        try:
            up = float(_sell_up(symbol))
            target = avg_cost * (1.0 + up)
            if cur >= target:
                hits.append(("roundtrip_sell", "现价 %.2f ≥ 兑现目标 %.3f（+%.1f%%）" % (cur, target, up * 100)))
        except Exception as _e_sil2:
            _silent_alert("bt_exits.py:220", _e_sil2)

    # 6/7/8) T 循环（**stock 账户上的做T风格规则**，不是"做T账户"）：
    #        `wolf_dao_t_sell` 倒T卖出 / `wolf_defensive_t_reduce` 防守减仓 / `wolf_zheng_t_buy` 正T回补（买）
    #        —— 生产 `t_monitor` 对**持仓账户**执行这些规则（本项目持仓都在 stock 账户）→ 默认开。
    #        `BT_EXIT_T_RULES=0` 可关（仅用于对照研究）。
    wr = _mod("app.services.wolf_t_rules") if str(os.getenv("BT_EXIT_T_RULES", "1")).strip() in ("1", "true", "yes") else None
    if wr is not None:
        prev = [{"time": str(b["time"]), "close": float(b["close"]), "high": float(b["high"]),
                 "low": float(b["low"]), "vol": float(b.get("vol") or 0)} for b in prev_days_bars]
        m5_today = [{"time": str(b["time"]), "close": float(b["close"]), "high": float(b["high"]),
                     "low": float(b["low"]), "vol": float(b.get("vol") or 0)} for b in today]
        try:
            # ⚠️ 生产 `wolf_t_rules` 的 quote['vol'] 是**当日累计量（手）**，函数内部再 ×100；
            #    分钟 bar 的 vol 是**股** → 必须 /100。不传 vol 会让"量能不足"恒真、过度触发。
            q = dict(quote, vol=(cum_v / 100.0) if cum_v > 0 else 0.0,
                     high=max(float(b["high"]) for b in today))
            if hasattr(wr, "dao_t_sell_quote"):
                flag, why = wr.dao_t_sell_quote(q, prev)
                if flag:
                    hits.append(("wolf_dao_t_sell", str(why)[:80]))
            if hasattr(wr, "defensive_t_reduce_quote"):
                flag, why = wr.defensive_t_reduce_quote(q, prev, wave_op)
                if flag:
                    hits.append(("wolf_defensive_t_reduce", str(why)[:80]))
            # 正T回补（买）：日内回撤≥2.5% 或 触前低 + 温和缩量 + 振幅≥3%
            if hasattr(wr, "zheng_t_buy_quote"):
                _qb, _whyb = wr.zheng_t_buy_quote(q, prev)
                if _qb:
                    hits.append(("wolf_zheng_t_buy", "正T买点：" + str(_whyb)[:70]))
        except Exception as e:
            print("[exits] wolf_t_rules 调用失败 %s: %s" % (symbol, str(e)[:60]), file=sys.stderr)
    return hits


def support_level(symbol: str, daily_rows, day: str, look: int = 20, cur: float = 0.0):
    """支撑位（`quote.support_l1`）。

    优先用生产 `support_resistance.compute_levels(symbol)`（日线数据用 pack 覆盖）；
    **必须做合理性校验**：支撑位要 > 0 且 ≤ 现价，否则视为不可用 → 退化为"近 look 日最低价"。
    （踩坑：用空 symbol 调生产函数拿到 38.5 这种与现价 22.9 无关的支撑 → 破位规则每根 bar 都触发）
    """
    try:
        if _patch_levels_daily(daily_rows):
            sr = _mod("support_resistance")
            lv = sr.compute_levels(symbol, force=True) if sr else None
            if isinstance(lv, dict):
                sup = lv.get("support") or []
                if sup:
                    v = float(sup[0].get("price") or sup[0].get("level") or 0)
                    if v > 0 and (cur <= 0 or v <= cur * 1.02):
                        return v
    except Exception as _e_sil3:
        _silent_alert("bt_exits.py:273", _e_sil3)
    prev = [float(r["low"]) for r in daily_rows if str(r.get("trade_date")) < day and r.get("low")][-look:]
    return min(prev) if prev else 0.0
