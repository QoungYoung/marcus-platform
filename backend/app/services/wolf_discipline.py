# -*- coding: utf-8 -*-
"""wolf_discipline.py — 狼大纪律规则 (2026-09-03 新增)
① 周末降仓: 周五尾盘/午后若仓位占比>=阈值 → 降低 T 仓约一半(保留底仓)
② 板上减半止盈: 持仓当日触及/接近涨停(10%板→>=9.5%, 20%板→>=19.5%) 且本轮浮盈>=阈值 → 板上减半锁定
纯函数模块, 供 trade_graph 注入 context + 合规检查; 不直接下单。
"""
import os, json

def _cfg():
    """读取 config/wolf_discipline.json(可缺省) 或内置默认。"""
    default = {
        "weekend_de_risk": {"enabled": True, "th_ratio": 0.5, "reduce_to": 0.5,
                            "windows": ["late_morning", "afternoon", "closing"]},
        "board_half": {"enabled": True, "min_float_pct": 3.0,
                       "board_threshold_pct": 9.5, "board_threshold_20": 19.5},
    }
    try:
        p = os.path.join(os.environ.get("DATA_DIR", "data"), "wolf_discipline.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            for k in default:
                if k in d and isinstance(d[k], dict):
                    default[k] = {**default[k], **d[k]}
    except Exception:
        pass
    return default

def _portfolio(portfolio):
    if portfolio is None:
        return None
    if isinstance(portfolio, str):
        try:
            return json.loads(portfolio)
        except Exception:
            return None
    return portfolio

def weekend_de_risk(portfolio, now, window=None, cfg=None):
    """周五 + 指定窗口 + 仓位占比>=th_ratio → active。
    返回 {active, ratio, threshold, reduce_to, reason, directive}。"""
    cfg = cfg or _cfg(); w = cfg.get("weekend_de_risk", {})
    if not w.get("enabled"):
        return {"active": False, "reason": "rule_disabled"}
    if now.weekday() != 4:
        return {"active": False, "reason": "not_friday"}
    if window and window not in (w.get("windows") or []):
        return {"active": False, "reason": "window_not_match"}
    pos = _portfolio(portfolio)
    if not pos:
        return {"active": False, "reason": "no_portfolio"}
    cash = float(pos.get("cash") or 0)
    total = float(pos.get("total_asset_market") or pos.get("total_asset") or 0)
    if total <= 0:
        return {"active": False, "reason": "no_asset"}
    ratio = (total - cash) / total
    th = float(w.get("th_ratio") or 0.5)
    active = ratio >= th
    return {"active": active, "ratio": round(ratio, 3), "threshold": th,
            "reduce_to": w.get("reduce_to", 0.5),
            "reason": "周五+仓位%.1f%%>=%.1f%%" % (ratio * 100, th * 100) if active else "仓位未达阈值",
            "directive": ("⚠️ 周末降仓：今日周五，仓位%.1f%%≥阈值%.1f%%。请于尾盘前将 T 仓(持仓-100的T部分)降低约一半，"
                          "保留底仓不卖，目标总仓位≤%.0f%%。不得新开仓/加仓。" % (ratio * 100, th * 100, w.get("reduce_to", 0.5) * 100)) if active else ""}

def board_half(portfolio, now, cfg=None, quotes=None):
    """板上减半: 对每个持仓判断是否接近涨停且浮盈达标。
    quotes: {symbol: {'current':..,'pre_close':..}} 可选; 无则返回含所有持仓的指令(由agent用实时行情判断)。
    返回 {active_sells:[{symbol,reason}], directive, enabled}。"""
    cfg = cfg or _cfg(); b = cfg.get("board_half", {})
    if not b.get("enabled"):
        return {"active_sells": [], "directive": "", "enabled": False}
    pos = _portfolio(portfolio)
    pos_list = (pos or {}).get("positions") or []
    min_float = float(b.get("min_float_pct", 3.0))
    t10 = float(b.get("board_threshold_pct", 9.5))
    t20 = float(b.get("board_threshold_20", 19.5))
    q = quotes or {}
    sells = []
    for p in pos_list:
        sym = str(p.get("symbol", ""))
        cost = float(p.get("avg_cost") or 0)
        vol = float(p.get("volume") or 0)
        if cost <= 0 or vol <= 0:
            continue
        cur = None
        if sym in q:
            cur = float(q[sym].get("current") or 0)
        if cur is None:
            continue  # 无实时价则不硬判, 由 agent 用实时数据
        pre = float(q[sym].get("pre_close") or 0)
        gain_pct = (cur / pre - 1) * 100 if pre else 0
        float_pct = (cur / cost - 1) * 100 if cost else 0
        # 判别板: 代码 30/68 开头 → 20% 板, 其余 10% 板
        is20 = any(sym.startswith(p) for p in ("30", "68", "SZ30", "SZ68", "SH68"))
        thr = t20 if is20 else t10
        if gain_pct >= thr and float_pct >= min_float:
            sells.append({"symbol": sym, "gain_pct": round(gain_pct, 2), "float_pct": round(float_pct, 2),
                          "action": "board_half_sell", "reason": "今涨%.2f%%(≥%.0f%%板)且浮盈%.2f%%≥%.0f%% → 板上减半锁定" % (gain_pct, thr, float_pct, min_float)})
    directive = "⚠️ 板上减半：对持仓中『今日触及/接近涨停(10%%板≥%.1f%%, 20%%板≥%.1f%%) 且 本轮浮盈≥%.1f%%』的标的 → 减半锁定(卖出持仓的一半, 底仓/芯片类按 T 仓处理)。" % (t10, t20, min_float) if b.get("enabled") else ""
    return {"active_sells": sells, "directive": directive, "enabled": b.get("enabled", True)}

def discipline_context(portfolio=None, now=None, window=None, quotes=None):
    """返回注入 prompt 的纪律规则上下文块(周末降仓 + 板上减半)。"""
    now = now or __import__("datetime").datetime.now()
    wd = weekend_de_risk(portfolio, now, window=window)
    bh = board_half(portfolio, now, quotes=quotes)
    parts = []
    if wd.get("active"):
        parts.append(wd["directive"])
    if bh.get("enabled") and bh.get("directive"):
        parts.append(bh["directive"])
    for s in bh.get("active_sells", []):
        parts.append("  - " + s["reason"])
    return ("\n## 狼大纪律规则\n" + "\n".join(parts) + "\n") if parts else ""
