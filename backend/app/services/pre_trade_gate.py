# -*- coding: utf-8 -*-
"""pre_trade_gate.py — 共享买入前门（选股后、calc_position 前）。

组成：P2 统一门（wave/systemic/macro）+ B4/B5（主线/避险）+ B8（回踩三态）。
只对买入意图生效；卖出/减仓/止损直接放行（不拦止血）。

用法：
  from app.services.pre_trade_gate import pre_trade_gate
  g = pre_trade_gate("SZ600000", intent="new_base", source="stock_agent_position")
  if not g["allow"]: ...
  if g["multiplier"] < 1: 降级
"""
import datetime as _dt, json, os

GATED_INTENTS = {"new_base", "add_base", "refill_base", "t_refill", "probe", "add",
                 "build", "open", "t_buy", "buy"}
PASS_INTENTS = {"sell", "reduce", "exit", "stop", "close", "stop_loss", "take_profit",
                "watch", "observe", "hold", "none"}


def _path(name):
    try:
        from app.config import get_settings
        ws = get_settings().workspace_path
    except Exception:
        ws = os.environ.get("WORKSPACE_PATH", "/app")
    return os.path.join(str(ws), "data", name)


def normalize_ts_code(symbol):
    s = str(symbol or "").strip().upper()
    digits = "".join(ch for ch in s if ch.isdigit())
    c = digits[-6:] if len(digits) >= 6 else s[:6]
    if len(c) != 6:
        return s
    exch = "SH" if c[0] == "6" else ("BJ" if c[0] in "489" else "SZ")
    return c + "." + exch


def _log(row):
    try:
        p = _path("pre_trade_gate.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def pre_trade_gate(symbol, intent="new_base", ts_code=None, source="api"):
    """返回 {allow, hard_block, multiplier, reasons, b4b5, b8, wave, macro, systemic}。"""
    sym = str(symbol or "").strip().upper()
    tc = ts_code or normalize_ts_code(sym)
    it = str(intent or "new_base").strip().lower()
    base = {"symbol": sym, "ts_code": tc, "intent": it, "source": source,
            "as_of": _dt.datetime.now().isoformat(timespec="seconds")}
    if it in PASS_INTENTS:
        out = {**base, "allow": True, "hard_block": False, "multiplier": 1.0,
               "reasons": ["非买入意图(%s)：不拦止血" % it]}
        _log(out)
        return out
    # 未知 intent：按买入处理（安全侧）
    try:
        from app.services.p2_entry_gate import p2_gate_check
        g = p2_gate_check(symbol=sym, ts_code=tc)
    except Exception as e:
        out = {**base, "allow": True, "hard_block": False, "multiplier": 1.0,
               "reasons": ["pre_trade_gate 计算失败(fail-open): " + str(e)[:120]]}
        _log(out)
        return out
    out = {**base,
           "allow": not bool(g.get("hard_block")),
           "hard_block": bool(g.get("hard_block")),
           "multiplier": float(g.get("multiplier") or 1.0),
           "reasons": list(g.get("reasons") or []),
           "wave": g.get("wave"), "macro": g.get("macro"), "systemic": g.get("systemic"),
           "b4b5": g.get("b4b5"), "b8": g.get("b8")}
    _log(out)
    return out
