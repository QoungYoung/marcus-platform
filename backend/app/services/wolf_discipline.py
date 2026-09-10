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
        # ③ 小赚兑现(2026-09-10 P0-3): 浮盈>=min_float_pct → 减 reduce_ratio 锁定。
        # 依据: 回测 T+5 收盘胜率 47% → 加 +3% 止盈 53%; rs>0 ∧ +3% 止盈 → 胜率 56%/中位 +1.01%。
        # 默认 enabled=False: 该规则会新增一条高频卖腿, 线上影响面大于选择层闸(rs),
        # 需先 dry-run 观察触发频次/与 board_half·defensive·roundtrip 的叠加再开启。
        "profit_take": {"enabled": False, "min_float_pct": 3.0, "reduce_ratio": 0.5},
        # ④ 常态仓位纪律(2026-09-10 P2-5)
        # 狼大**有原话**的: 2026-03-06「现在就是70%仓位」→ 总仓位上限 70%。
        # 狼大**无原话**的: 单票上限 / 集中度(前3大) / 底仓与T仓结构上限 → 属系统自设,
        #   默认一律 0(=不启用), 必须显式配置才生效, 以免冒充狼大规则(与 §5.2 清理同一条纪律)。
        # 2026-04-14「50%的底仓 30%左右做日内…剩下20%」是**他本人的持仓结构描述**, 非规则, 故默认关。
        "position_cap": {"enabled": True, "total_max_pct": 70.0,
                         "single_max_pct": 0.0, "top3_max_pct": 0.0,
                         "base_max_pct": 0.0, "t_max_pct": 0.0},
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

def profit_take(portfolio, now=None, cfg=None, quotes=None):
    """小赚兑现(P0-3, 2026-09-10): 持仓浮盈 >= min_float_pct → 减 reduce_ratio 锁定。

    狼大原话依据: 「吃一口减一半 安全第一」(2026-09-01)、「我最喜欢的就是这种小赚就走的」
    「兌现风格」回测: 固定持有到 T+5 收盘胜率 47%; 改 +3% 小止盈 → 53%;
    rs>0(强于主题) ∧ +3% 止盈 → 56%(均值 +0.56%/中位 +1.01%)。
    作用域: 与 board_half 同口径按持仓 avg_cost 判定; "减 reduce_ratio" 而非清仓, 底仓保护由卖出管道负责。
    返回 {active_sells:[{symbol,reason,reduce_ratio}], directive, enabled}。
    """
    cfg = cfg or _cfg(); pt = cfg.get("profit_take", {})
    if not pt.get("enabled"):
        return {"active_sells": [], "directive": "", "enabled": False}
    pos = _portfolio(portfolio)
    pos_list = (pos or {}).get("positions") or []
    min_float = float(pt.get("min_float_pct", 3.0))
    ratio = float(pt.get("reduce_ratio", 0.5))
    q = quotes or {}
    sells = []
    for p in pos_list:
        sym = str(p.get("symbol", ""))
        cost = float(p.get("avg_cost") or 0)
        vol = float(p.get("volume") or 0)
        if cost <= 0 or vol <= 0 or sym not in q:
            continue          # 无实时价则不硬判(与 board_half 同口径)
        cur = float(q[sym].get("current") or 0)
        if cur <= 0:
            continue
        float_pct = (cur / cost - 1) * 100
        if float_pct >= min_float:
            sells.append({"symbol": sym, "float_pct": round(float_pct, 2), "reduce_ratio": ratio,
                          "action": "profit_take_sell",
                          "reason": "浮盈%.2f%%≥%.1f%% → 小赚兑现减%.0f%%(保留底仓)" % (float_pct, min_float, ratio * 100)})
    directive = ("⚠️ 小赚兑现：持仓浮盈≥%.1f%% 的标的 → 减%.0f%%锁定(保留底仓)。"
                 % (min_float, ratio * 100)) if pt.get("enabled") else ""
    return {"active_sells": sells, "directive": directive, "enabled": pt.get("enabled", False)}


def position_cap(portfolio, cfg=None):
    """常态仓位纪律（P2-5, 2026-09-10）→ {"allowed", "ratio", "exposure", "reason", "directive"}。

    **狼大有原话的部分**:
      2026-03-06「现在就是 **70%仓位**」→ 总仓位上限 70%（`total_max_pct`）。
    **狼大无原话、属系统自设的部分**（默认 0 = 不启用，需显式配置才生效）:
      · `single_max_pct`  单票市值占净值上限
      · `top3_max_pct`    前 3 大持仓合计上限（集中度）
      · `base_max_pct` / `t_max_pct` 底仓/T 仓结构上限
        （2026-04-14「50%的底仓 30%左右做日内…剩下20%」是他**本人的持仓结构描述**, 不是规则 → 默认关）

    口径与 weekend_de_risk 一致: 持仓占比 = (总资产 − 现金) / 总资产。
    本函数只**判定并给出建议**, 不直接下单、不做硬拦（是否拦截由调用方决定）。
    """
    cfg = cfg or _cfg(); c = cfg.get("position_cap", {})
    if not c.get("enabled"):
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "rule_disabled", "directive": ""}
    pos = _portfolio(portfolio)
    if not pos:
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "no_portfolio", "directive": ""}
    cash = float(pos.get("cash") or 0)
    total = float(pos.get("total_asset_market") or pos.get("total_asset") or 0)
    if total <= 0:
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "no_asset", "directive": ""}
    ratio = (total - cash) / total * 100.0
    tot_max = float(c.get("total_max_pct") or 0)
    reasons = []
    if tot_max > 0 and ratio > tot_max:
        reasons.append("总仓位%.1f%% > 上限%.0f%%" % (ratio, tot_max))

    # 系统自设项: 仅当配置 >0 才计算(默认不启用, 避免冒充狼大规则)
    exp = {"single_max": None, "top3": None}
    single_max = float(c.get("single_max_pct") or 0)
    top3_max = float(c.get("top3_max_pct") or 0)
    if (single_max > 0 or top3_max > 0):
        vals = []
        for p in (pos.get("positions") or []):
            mv = float(p.get("market_value") or 0)
            if mv <= 0:
                mv = float(p.get("volume") or 0) * float(p.get("price") or p.get("current") or 0)
            if mv > 0:
                vals.append(mv)
        vals.sort(reverse=True)
        if vals:
            top1_pct = vals[0] / total * 100.0
            top3_pct = sum(vals[:3]) / total * 100.0
            exp["single_max"] = round(top1_pct, 1)
            exp["top3"] = round(top3_pct, 1)
            if single_max > 0 and top1_pct > single_max:
                reasons.append("单票%.1f%% > 上限%.0f%%" % (top1_pct, single_max))
            if top3_max > 0 and top3_pct > top3_max:
                reasons.append("前三集中度%.1f%% > 上限%.0f%%" % (top3_pct, top3_max))

    allowed = not reasons
    d = ""
    if not allowed:
        d = ("⚠️ 仓位纪律：%s。狼大 2026-03-06「现在就是70%%仓位」→ 不新开/不加仓, "
             "优先降 T 仓至阈值内。" % "；".join(reasons))
    return {"allowed": allowed, "ratio": round(ratio, 1), "exposure": exp,
            "reason": "；".join(reasons) if reasons else "仓位在阈值内",
            "directive": d}


def discipline_context(portfolio=None, now=None, window=None, quotes=None):
    """返回注入 prompt 的纪律规则上下文块(周末降仓 + 板上减半 + 小赚兑现 + 仓位纪律)。"""
    now = now or __import__("datetime").datetime.now()
    wd = weekend_de_risk(portfolio, now, window=window)
    bh = board_half(portfolio, now, quotes=quotes)
    pt = profit_take(portfolio, now, quotes=quotes)
    pc = position_cap(portfolio)
    parts = []
    if pc.get("directive"):
        parts.append(pc["directive"])
    if wd.get("active"):
        parts.append(wd["directive"])
    if bh.get("enabled") and bh.get("directive"):
        parts.append(bh["directive"])
    if pt.get("enabled") and pt.get("directive"):
        parts.append(pt["directive"])
    for s in bh.get("active_sells", []) + pt.get("active_sells", []):
        parts.append("  - " + s["reason"])
    return ("\n## 狼大纪律规则\n" + "\n".join(parts) + "\n") if parts else ""
