# -*- coding: utf-8 -*-
"""mainline_open_buy.py — ⑧ 买点生产化：E01/E02 intent_open 开盘主线候选建仓/probe 生产通道（模拟盘直接对接生产）

语义：回测 apps/main_line/backtest_wolf_extra_events.py 的「intent_open」
    = 主线候选 + P3 new_base/probe 通道放行后，于当日首根 5min bar 一次性建仓。
生产实现（复用狼大门控与落单链，不依赖 legacy 长期/短期池 monitor）：
    main_line_state(主题) → THEME_CONCEPTS(概念) → get_component_stocks(成分 symbol)
      → check_entry_filters(intent, mainline_dir=True, rel_low=...)   # wolf 入场过滤 + P3 三仓档位
      → calc_position(intent, tier="probe", mainline_dir=True)        # 三仓档位 cap / 现金底线
      → executor.buy(paper stock) → log_buy_point(source="intent_open") + StrategyChain().add_trade

防重复：data/mainline_open_state.json 记录 last_run_date，当日只跑一次。
fail-closed：main_line_state 缺 / candidates 空 / wave=defense|exit → 不下单不写 buy_point_log。
安全：MAINLINE_OPEN_BUY_DRY=1 只算不执行（写 data/mainline_open_<date>.json 诊断）。
用法：python -u jobs/mainline_open_buy.py
"""
import os, sys, json, asyncio, datetime


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


for _p in ("/app/app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# §9.770（用户 2026-10-08：「开始修复」）：★ 原来只加了 `apps/main_line` ✗，
# 但脚本在 make_executor() 里要 `from paper_engine import PaperTradingEngine` ✓，
# 而 `paper_engine.py` 在 **`apps/paper-trading/`** ✗ ⇒ 该目录不在 sys.path ⇒
# 每次执行必然 `ModuleNotFoundError: No module named 'paper_engine'` ✗
# ⇒ ⇒ 这是「生产腿数只有回测约 1/4」的关键原因之一（**建仓腿根本没产出来** ✗）。
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("apps/main_line", "apps/paper-trading", "apps", "core"):
    _p = os.path.join(_root, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
# ★ §9.771（2026-10-08）：**仓库根必须排在 sys.path 最前** —— 上面把 `/app/app` 也插进来了 ✓，
#   而 `/app/app/core/` 是一个**真的 `core` 包**（backend 的 core 子包 ✓）✗
#   ⇒ `import core` 命中它 ✗ ⇒ `from core.realtime_indicators import …` 永远
#     `ModuleNotFoundError: No module named 'core.realtime_indicators'` ✗
#   （容器内实测复现 ✓：`core.__path__=['/app/app/core']` ✗，警告原文即此 ✓）
#   ⇒ 本行把它压到最前 ⇒ `core` = 仓库根的 `core/` ✓（backend 侧 `app.core.*` 不受影响 ✓）
if _root not in sys.path:
    sys.path.insert(0, _root)

DATA = os.environ.get("DATA_DIR", "data")

def dstr():
    return datetime.date.today().strftime("%Y%m%d")

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
    except Exception as _e_sil1:
        _silent_alert("mainline_open_buy.py:44", _e_sil1)

def dry():
    return os.getenv("MAINLINE_OPEN_BUY_DRY", "0").strip() in ("1", "true", "yes")

def _resolve_intent(wave_op):
    op = (wave_op or "side").lower()
    if op in ("build", "side"):
        return "new_base"
    if op in ("t_only",):
        return "probe"
    return None

def enumerate_symbols(ml_state):
    try:
        from market_reference import get_component_stocks
    except Exception:
        from app.services.market_reference import get_component_stocks
    try:
        from fusion_mainline import THEME_CONCEPTS
    except Exception:
        from apps.main_line.fusion_mainline import THEME_CONCEPTS
    themes = [ml_state.get("main_line")] + list(ml_state.get("candidates") or [])
    themes = [t for t in themes if t]
    out, seen = [], set()
    for th in themes:
        for cname in (THEME_CONCEPTS.get(th) or []):
            for ts in (get_component_stocks(cname, "concept", limit=10) or []):
                if ts and ts not in seen:
                    seen.add(ts); out.append(ts)
    return out

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

def make_executor():
    try:
        from workspace_detector import DATA_DIR as _dd
        ddir = str(_dd)
    except Exception:
        ddir = DATA
    from paper_engine import PaperTradingEngine
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    engine = PaperTradingEngine(data_dir=ddir, account_id="stock")
    return MarcusVNPyExecutor(engine=engine, account_id="stock")

def main():
    today = dstr()
    st = load("mainline_open_state.json")
    if st.get("last_run_date") == today:
        print("[intent_open] 当日已跑过，跳过（last_run_date=%s）" % today)
        return
    ml = load(os.getenv("MAINLINE_LINE_STATE_FILE", "main_line_state.json"))
    if not ml or not ml.get("main_line") or not ml.get("candidates"):
        print("[intent_open] main_line_state 缺失/为空 → fail-closed 不建仓")
        return
    wave = load("wave_state.json")
    op = (wave.get("operation") or "side").lower()
    intent = _resolve_intent(op)
    if not intent:
        print("[intent_open] wave op=%s 非建仓档(defense/exit) → 不新建" % op)
        return
    symbols = enumerate_symbols(ml)
    if not symbols:
        print("[intent_open] 主线候选枚举为空 → fail-closed 不建仓")
        return
    print("[intent_open] op=%s intent=%s 主线=%s 候选=%d" % (op, intent, ml.get("main_line"), len(symbols)))
    if dry():
        save("mainline_open_%s.json" % today, {"date": today, "op": op, "intent": intent,
                                               "main_line": ml.get("main_line"), "symbols": symbols[:50]})
        print("[intent_open] DRY_RUN，写入诊断文件，不执行")
        return
    from app.models.indicator import EntryCheckRequest, CalcPositionRequest
    from app.api.indicator import check_entry_filters, calc_position
    from app.services.buy_point_log import log_buy_point
    executor = make_executor()
    diag = {"date": today, "op": op, "intent": intent, "main_line": ml.get("main_line"),
            "reasons": {}, "built": []}
    for i, ts in enumerate(symbols[:15]):
        sym = ts
        try:
            req = EntryCheckRequest(symbol=sym, intent=intent, mainline_dir=True,
                                    rel_low=None, account_id="stock")
            res = run_async(check_entry_filters(req))
            if getattr(res, "data_unavailable", None):
                diag["reasons"][sym] = "data_unavailable:" + ",".join(res.data_unavailable); continue
            if getattr(res, "hard_block", False) or getattr(res, "downgrade_multiplier", 1.0) <= 0:
                diag["reasons"][sym] = "hard_block:" + (getattr(res, "final_decision", "") or ""); continue
            if getattr(res, "final_grade", "") not in ("pass", "probe_only"):
                diag["reasons"][sym] = "grade:" + getattr(res, "final_grade", ""); continue
            if getattr(res, "three_tier_allowed", None) is False:
                diag["reasons"][sym] = "p3_tier_not_allowed"; continue
            precio = float(getattr(res.tech, "current_price", 0) or 0)
            if precio <= 0:
                diag["reasons"][sym] = "no_price"; continue
            pos = run_async(calc_position(CalcPositionRequest(
                symbol=sym, intent=intent, tier="probe", stance="green",
                signal_strength="medium", mainline_dir=True, account_id="stock")))
            if not getattr(pos, "all_pass", False):
                diag["reasons"][sym] = "calc_position_fail"; continue
            vol = int(getattr(pos.quantity, "probe_shares", 0) or 0)
            if vol < 100:
                diag["reasons"][sym] = "vol_below_100"; continue
            buy = executor.buy(symbol=sym, price=precio, volume=vol,
                               reason="[intent_open] 开盘主线候选建仓 mainline=%s" % ml.get("main_line"))
            if buy.get("status") in ("executed", "filled", "matched"):
                log_buy_point(source="intent_open", symbol=sym, name="", account="stock",
                              intent=intent, price=precio, volume=vol,
                              amount=round(precio * vol, 2), pct=getattr(pos.quantity, "probe_pct", None),
                              grade=getattr(res, "final_grade", ""), tier_cap=getattr(res, "three_tier_cap_pct", None),
                              trigger="E01_open_mainline", reason="开盘主线候选建仓")
                try:
                    from strategy_chain import StrategyChain
                    StrategyChain().add_trade({"timestamp": datetime.datetime.now().isoformat(),
                                               "symbol": sym, "action": "买入", "price": precio,
                                               "volume": vol, "strategy_ref": "intent_open/E01"})
                except Exception as _e:
                    print("[intent_open] strategy_history 写入失败: %s" % _e)
                diag["built"].append({"symbol": sym, "price": round(precio, 3), "volume": vol})
            else:
                diag["reasons"][sym] = "buy_fail:" + str(buy.get("reason") or "")[:80]
        except Exception as e:
            diag["reasons"][sym] = "error:" + str(e)[:80]
    save("mainline_open_%s.json" % today, diag)
    save("mainline_open_state.json", {"last_run_date": today})
    print("[intent_open] done built=%d skip=%d" % (len(diag["built"]), len(diag["reasons"])))

if __name__ == "__main__":
    main()
