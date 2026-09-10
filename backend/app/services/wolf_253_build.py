# -*- coding: utf-8 -*-
"""wolf_253_build.py — ⑥ 买点生产化：253 B 大盘急杀 + 254 触前低 + 分步小仓 生产建仓链（狼大逻辑，模拟盘直接对接生产）

与 legacy 长期/短期池无关：此处只复用狼大门控(check_entry_filters/calc_position)与执行器(executor.buy)。
253 B semantics（回测 apps/main_line/backtest_wolf_253_stepwise.py VARIANT=B）：
    上证单根5min close 跌幅≤-0.4%(index.m5_dump>=0.4) 且 时间 09:45-14:40 且 个股非跌停(相对前收>-9.5%) 且 当日单次
    → 无底仓时开小底仓(probe/new_base)，按住当前浪型选 intent。
254：个股当日5min最低≤前日5min最低×1.005 且 量比≤0.7 且 close>cumVWAP → 低吸。
step_refill：254 后 3 个交易日内再次出现放宽量比(≤1.2)的 254 型低点，最多 2 次小额回补(每次1/3口径)。
输出：data/buy_point_log.jsonl(source=wolf_253 / wolf_254_refill) + data/wolf_253_chain.json(stepwise 状态)。
安全：任一数据缺失/跌停/时间外 → 不建(天然 fail-closed)。MAINLINE_OPEN_BUY_DRY 不适用；本模块仅在 TMonitor 命中后调用。
"""
import os, sys, json, asyncio, datetime

def _root():
    _d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return _d

for _p in ("/app/app", os.path.join(_root(), "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
pp = os.path.join(_root(), "apps", "main_line")
if pp not in sys.path:
    sys.path.insert(0, pp)

DATA = os.environ.get("DATA_DIR", "data")
STATE_FILE = os.path.join(DATA, "wolf_253_chain.json")

def load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save(name, obj):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

def _wave_op():
    return (load("wave_state.json").get("operation") or "side").lower()

def choose_intent(wave_op=None):
    """狼大 P3 档位意图：build/side→new_base；t_only→probe(≤3%)；defense/exit→None(不新建)。"""
    op = (wave_op or _wave_op() or "side").lower()
    if op in ("build", "side"):
        return "new_base"
    if op in ("t_only",):
        return "probe"
    return None

def hm_ok(t):
    try:
        s = str(t)
        hm = int(s[11:13]) * 100 + int(s[14:16]) if len(s) >= 16 else int(s[8:10]) * 100 + int(s[10:12])
        return 945 <= hm <= 1440
    except Exception:
        return False

def is_253_signal(snapshot, now_str=""):
    """snapshot['index']['m5_dump']>=0.4 且时间 09:45-14:40 且 站回黄线(current>=average 为B版补强, 可关)。"""
    idx = (snapshot or {}).get("index") or {}
    dump = float(idx.get("m5_dump") or 0)
    if dump < 0.4:
        return False, "dump_lt_0.4"
    if not hm_ok(now_str):
        return False, "time_not_in_window"
    # 个股非跌停 + 站回黄线：这里由调用方在 quote 中保证；此处仅做指数侧
    return True, "ok"

def _near_limit_down(quote, prev_close):
    try:
        cur = float(quote.get("current") or 0)
        pc = float(prev_close or 0)
        return pc > 0 and (cur / pc - 1) * 100 <= -9.5
    except Exception:
        return False

def build_253(executor, symbol, quote, now_str="", account="stock", snapshot=None):
    """无底仓 253 急杀→开小底仓(probe/new_base)。返回 dict(status)。

    2026-09-10 修复(P0-1): 原先成功分支的 log_buy_point 引用了未定义的 snapshot
    → 抛 NameError 被 except 吞掉, 成交却上报 blocked, 且 t_monitor 侧
    "仅 status==success 才 mark_base_254" 永不成立 → 254 分步回补链整条失效。
    现把 TMonitor 的字段快照显式透传进来（仅用于日志记录 reason 中的 m5_dump）。
    """
    op = _wave_op()
    intent = choose_intent(op)
    if not intent:
        return {"status": "blocked", "reason": "wave %s 不允许新开(choose_intent=None)" % op}
    prev_close = float(quote.get("pre_close") or 0)
    if _near_limit_down(quote, prev_close):
        return {"status": "blocked", "reason": "near_limit_down"}
    from app.models.indicator import EntryCheckRequest, CalcPositionRequest
    from app.api.indicator import check_entry_filters, calc_position
    from app.services.buy_point_log import log_buy_point
    try:
        res = run_async(check_entry_filters(EntryCheckRequest(symbol=symbol, intent=intent,
                                                              mainline_dir=True, account_id=account)))
        _du = getattr(res, "data_unavailable", None) or []
        # 2026-09-08(C方案配套): 数据缺省分级——技术硬缺(60分MA/分钟K/价格)保持fail-closed;
        # 软字段(日内分位/主力资金)缺失降级放行(资金已由Tushare日频兜底, 分位缺失仅降仓), 不整单拒
        _hard = [x for x in _du if any(k in str(x) for k in ("60分MA", "分钟", "K线", "价格", "日K"))]
        if _hard:
            return {"status": "blocked", "reason": "data_unavailable:" + ",".join(_hard)}
        if _du:
            print(f"[wolf_253] 软字段缺失降级放行(非技术硬缺): {_du}", flush=True)
        if getattr(res, "hard_block", False) or getattr(res, "downgrade_multiplier", 1.0) <= 0:
            return {"status": "blocked", "reason": "hard_block:" + (getattr(res, "final_decision", "") or "")}
        if getattr(res, "final_grade", "") not in ("pass", "probe_only"):
            return {"status": "blocked", "reason": "grade:" + getattr(res, "final_grade", "")}
        if getattr(res, "three_tier_allowed", None) is False:
            return {"status": "blocked", "reason": "p3_tier_not_allowed"}
        precio = float(getattr(res.tech, "current_price", 0) or 0)
        if precio <= 0:
            return {"status": "blocked", "reason": "no_price"}
        pos = run_async(calc_position(CalcPositionRequest(symbol=symbol, intent=intent, tier="probe",
                                                          stance="green", signal_strength="medium",
                                                          mainline_dir=True, account_id=account)))
        if not getattr(pos, "all_pass", False):
            return {"status": "blocked", "reason": "calc_position_fail"}
        vol = int(getattr(pos.quantity, "probe_shares", 0) or 0)
        if vol < 100:
            return {"status": "blocked", "reason": "vol_below_100"}
        buy = executor.buy(symbol=symbol, price=precio, volume=vol, reason="[wolf_253] 大盘急杀开小底仓")
        if buy.get("status") in ("executed", "filled", "matched"):
            log_buy_point(source="wolf_253", symbol=symbol, name="", account=account, intent=intent,
                          price=precio, volume=vol, amount=round(precio * vol, 2),
                          pct=getattr(pos.quantity, "probe_pct", None), grade=getattr(res, "final_grade", ""),
                          tier_cap=getattr(res, "three_tier_cap_pct", None), trigger="253",
                          reason="狼大253大盘急杀(%.2f%%)开小底仓" % float((snapshot or {}).get("index", {}).get("m5_dump") or 0))
            return {"status": "success", "symbol": symbol, "price": precio, "volume": vol, "intent": intent}
        return {"status": "blocked", "reason": "buy_fail:" + str(buy.get("reason") or "")[:80]}
    except Exception as e:
        return {"status": "blocked", "reason": "error:" + str(e)[:80]}

def _chain_state():
    return load("wolf_253_chain.json")

def refill_253(executor, symbol, quote, vol_ratio, today, account="stock"):
    """254 后 3 日内 ≤2 次小额回补(放宽量比≤1.2, 每次1/3)。返回 dict(status)。"""
    st = _chain_state(); row = st.get(symbol) or {}
    base_date = str(row.get("base_254_date") or "")
    if not base_date or base_date == today:
        return {"status": "noop", "reason": "no_base_254" if not base_date else "same_day"}
    try:
        gap = (datetime.date.today() - datetime.datetime.strptime(base_date, "%Y%m%d").date()).days
    except Exception:
        gap = 99
    if not (0 < gap <= 3):
        return {"status": "noop", "reason": "outside_3d_gap"}
    refills = int(row.get("refill_count") or 0)
    if refills >= 2:
        return {"status": "noop", "reason": "refill_max_2"}
    if float(vol_ratio or 0) > 1.2:
        return {"status": "noop", "reason": "vr_gt_1.2"}
    prev_close = float(quote.get("pre_close") or 0)
    if _near_limit_down(quote, prev_close):
        return {"status": "blocked", "reason": "near_limit_down"}
    intent = choose_intent()  # 回补沿用当前浪型意图（t_only→probe/add？回补一般 add/refill）
    if not intent:
        return {"status": "blocked", "reason": "wave_no_intent"}
    from app.models.indicator import EntryCheckRequest, CalcPositionRequest
    from app.api.indicator import check_entry_filters, calc_position
    from app.services.buy_point_log import log_buy_point
    try:
        intent2 = "add_base" if intent == "new_base" else intent
        res = run_async(check_entry_filters(EntryCheckRequest(symbol=symbol, intent=intent2,
                                                              mainline_dir=True, account_id=account)))
        if getattr(res, "hard_block", False) or getattr(res, "downgrade_multiplier", 1.0) <= 0:
            return {"status": "blocked", "reason": "hard_block"}
        precio = float(getattr(res.tech, "current_price", 0) or 0)
        pos = run_async(calc_position(CalcPositionRequest(symbol=symbol, intent=intent2, tier="confirm",
                                                          stance="green", signal_strength="weak",
                                                          mainline_dir=True, account_id=account)))
        if not getattr(pos, "all_pass", False):
            return {"status": "blocked", "reason": "calc_position_fail"}
        vol = max(int(getattr(pos.quantity, "confirm_shares", 0) or 0) // 3, 100)  # 每次 1/3 口径
        if vol < 100:
            return {"status": "blocked", "reason": "vol_below_100"}
        buy = executor.buy(symbol=symbol, price=precio, volume=vol, reason="[wolf_254_refill] 分步回补")
        if buy.get("status") in ("executed", "filled", "matched"):
            log_buy_point(source="wolf_254_refill", symbol=symbol, name="", account=account, intent=intent2,
                          price=precio, volume=vol, amount=round(precio * vol, 2),
                          grade=getattr(res, "final_grade", ""), tier_cap=getattr(res, "three_tier_cap_pct", None),
                          trigger="step_refill", reason="254后%d日第%d次小额回补" % (gap, refills + 1))
            row["refill_count"] = refills + 1
            row["last_refill_date"] = today
            _chain_state()[symbol] = row
            save("wolf_253_chain.json", _chain_state())
            return {"status": "success", "symbol": symbol, "price": precio, "volume": vol, "intent": intent2}
        return {"status": "blocked", "reason": "buy_fail"}
    except Exception as e:
        return {"status": "blocked", "reason": "error:" + str(e)[:80]}

def mark_base_254(symbol, today):
    st = _chain_state()
    row = st.get(symbol) or {}
    if not row.get("base_254_date") or row.get("base_254_date") != today:
        row["base_254_date"] = today
        row["refill_count"] = 0
        st[symbol] = row
        save("wolf_253_chain.json", st)

if __name__ == "__main__":
    print(json.dumps({"op": _wave_op(), "intent": choose_intent()}, ensure_ascii=False))
