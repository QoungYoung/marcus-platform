# -*- coding: utf-8 -*-
"""sell_gate.py — 卖出前门（agent → /trades 之间）：狼大止损/止盈口径 + 重复卖出拦截。

只对 sell 生效；买入走 pre_trade_gate。
- stop_loss / 减仓：必须触发狼大止损线（MA5 黑K / MA34 带量 / MA60 / 指数大级别），
  且收盘确认、非 13:00-14:30、非地量、非 B8 假跌破/回踩不破。
- take_profit / 高抛：只做重复卖出拦截，不做 Wolf 硬拦（避免误杀他的主动兑现）。
- T 卖 / 防御 / 清仓：只做重复卖出拦截。
- 重复：当日同标的卖出 ≥2 笔（狼大 2025-02-07「一个票最多买2笔 卖2笔」）、
  v3 当日已动作、同 action_key 已记录 → 拦。
"""
from __future__ import annotations
import datetime as _dt, hashlib, json, os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STOP_WORDS = ("止损", "破位", "破线", "跌穿", "斩仓", "清仓")
REDUCE_WORDS = ("减仓", "减半", "防御", "避险")
TP_WORDS = ("止盈", "高抛", "兑现", "目标", "前高", "0.618", "整数", "BOLL", "上轨",
            "放量滞涨", "吃一口", "被动止盈", "减半")
T_WORDS = ("做T", "正T", "反T", "T出", "t出", "T仓", "t仓", "低吸", "回补")


def classify_intent(intent: Optional[str], reason: str = "") -> str:
    s = str(intent or "").strip().lower()
    if s in ("stop_loss", "stop", "止损"):
        return "stop_loss"
    if s in ("take_profit", "high_sell", "profit_take", "止盈", "高抛"):
        return "take_profit"
    if s in ("t_sell", "t_buy_back", "做t", "t"):
        return "t_sell"
    if s in ("defense", "exit", "reduce", "清仓", "防御"):
        return s if s in ("defense", "exit") else "reduce"
    r = str(reason or "")
    if any(k in r for k in STOP_WORDS):
        return "stop_loss"
    if any(k in r for k in REDUCE_WORDS):
        return "reduce"
    if any(k in r for k in TP_WORDS):
        return "take_profit"
    if any(k in r for k in T_WORDS):
        return "t_sell"
    return "other"


@dataclass
class SellContext:
    symbol: str
    price: float
    volume: int
    intent: str
    reason: str = ""
    account: str = "stock"
    now: Optional[_dt.datetime] = None
    pnl: Optional[float] = None
    ma5: Optional[float] = None
    ma13: Optional[float] = None
    ma34: Optional[float] = None
    ma60: Optional[float] = None
    last_open: Optional[float] = None
    last_close: Optional[float] = None
    vol_ratio: Optional[float] = None
    b8_state: str = ""
    index_break: bool = False
    sells_today: int = 0
    v3_today: bool = False
    action_key_dup: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)


def _time_ok(now: _dt.datetime) -> tuple:
    hm = now.strftime("%H:%M")
    if "13:00" <= hm < "14:30":
        return False, "13:00-14:30 不执行止损（狼大 2026-03-23「要么早上卖 要么尾盘卖」）"
    return True, ""


def _close_confirmed(now: _dt.datetime) -> bool:
    # 收盘确认：14:55 之后；指数大级别破位例外由 index_break 放行
    return now.strftime("%H:%M") >= "14:55"


def validate_stop_loss(ctx: SellContext) -> tuple:
    """返回 (ok, reasons)。只认狼大止损线；不认固定百分比/agent 主观。"""
    reasons: List[str] = []
    if ctx.index_break:
        return True, ["指数大级别破位（狼大 2026-08-27「只看指数大级别…转下跌1浪就止损」）"]
    if not any((ctx.ma5, ctx.ma34, ctx.ma60)):
        return True, ["止损线数据缺失(fail-open)：不阻断止血"]
    if ctx.b8_state in ("hold", "false_break"):
        return False, ["B8 %s：回踩不破/假跌破，等次日（不在这里止损）" % ctx.b8_state]
    if ctx.vol_ratio is not None and ctx.vol_ratio < 0.8:
        return False, ["地量不割肉（狼大 2026-08-21；量比 %.2f<0.8）" % ctx.vol_ratio]
    ok_t, why_t = _time_ok(ctx.now or _dt.datetime.now())
    if not ok_t:
        return False, [why_t]
    # 收盘确认 = **上一根已收盘 K** 已跌破该线；盘中刚跌破不算（狼大 2026-01-29「收盘跌破我才出」）。
    # 上一根收盘已破 ⇒ 次日早上可以卖（狼大 2026-03-23「要么早上卖 要么尾盘卖」）。
    now = ctx.now or _dt.datetime.now()
    after_close = _close_confirmed(now)
    ref = float(ctx.price or 0) if after_close else float(ctx.last_close or ctx.price or 0)
    px = float(ctx.price or 0)
    hit60 = bool(ctx.ma60 and ref < ctx.ma60)
    hit34 = bool(ctx.ma34 and ref < ctx.ma34 and (ctx.vol_ratio is None or ctx.vol_ratio >= 1.0))
    hit5 = bool(ctx.ma5 and ref < ctx.ma5 and ctx.last_open and ctx.last_close and ctx.last_close < ctx.last_open)
    if hit60:
        reasons.append("收盘 < MA60（狼大 2021-01-28「60日是我的底线」）")
    elif hit34:
        reasons.append("收盘 < MA34 且带量（狼大 2021-01-28「没站稳34日带量下穿的我都会砍掉」）")
    elif hit5:
        reasons.append("收黑K跌破 MA5（狼大 2026-01-12「减仓避一下」）→ 只减仓，不清仓")
    else:
        if (not after_close) and any((
            ctx.ma60 and px < ctx.ma60,
            ctx.ma34 and px < ctx.ma34,
            ctx.ma5 and px < ctx.ma5,
        )):
            return False, ["盘中跌破、收盘未确认 → 等收盘（狼大 2026-01-29「收盘跌破我才出」）"]
        return False, ["未触发狼大止损线（MA5 黑K / MA34 带量 / MA60 / 指数大级别）"]
    return True, reasons


def validate_sell(ctx: SellContext, cap: int = 2) -> tuple:
    """返回 (allow, reasons, intent)。"""
    intent = ctx.intent or classify_intent("", ctx.reason)
    reasons: List[str] = []
    # 重复卖出（所有意图都拦）
    if cap > 0 and int(ctx.sells_today or 0) >= cap:
        return False, ["当日同标的已卖 %d 笔 ≥ 上限 %d（狼大 2025-02-07「一个票最多买2笔 卖2笔」）"
                       % (int(ctx.sells_today or 0), cap)], intent
    if ctx.v3_today:
        return False, ["v3 止损层当日已对该标的动作（防重复卖）"], intent
    if ctx.action_key_dup:
        return False, ["同一 action_key 已记录（防重复卖）"], intent
    if intent == "stop_loss":
        ok, why = validate_stop_loss(ctx)
        if not ok:
            return False, why, intent
        reasons.extend(why)
    elif intent == "take_profit":
        reasons.append("止盈/高抛：放行（只做重复卖出拦截）")
    elif intent in ("t_sell", "defense", "exit", "reduce"):
        reasons.append("%s：放行（只做重复卖出拦截）" % intent)
    else:
        reasons.append("未识别意图：只做重复卖出拦截")
    return True, reasons, intent


def _log(row: Dict[str, Any]) -> None:
    try:
        try:
            from app.config import get_settings
            ws = str(get_settings().workspace_path)
        except Exception:
            ws = os.environ.get("WORKSPACE_PATH", "/app")
        p = os.path.join(ws, "data", "sell_gate.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _ts_code(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    d = "".join(ch for ch in s if ch.isdigit())[-6:]
    return d + ("." + ("SH" if d[:1] == "6" else ("BJ" if d[:1] in "489" else "SZ"))) if d else s


def _load_ctx(symbol: str, price: float, volume: int, intent: str, reason: str,
              account: str) -> SellContext:
    ctx = SellContext(symbol=symbol, price=float(price or 0), volume=int(volume or 0),
                      intent=intent, reason=reason, account=account, now=_dt.datetime.now())
    try:
        from app.services import wolf_exit_rules as WX
        closes = WX.recent_closes(symbol, 61)
        if closes:
            def ma(n):
                return sum(closes[-n:]) / float(n) if len(closes) >= n else None
            ctx.ma5, ctx.ma13, ctx.ma34, ctx.ma60 = ma(5), ma(13), ma(34), ma(60)
            ctx.last_close = closes[-1]
        ctx.pnl = None
        try:
            cost = WX.avg_cost(account, symbol)
            if cost > 0 and price:
                ctx.pnl = (float(price) / cost - 1) * 100
        except Exception:
            pass
    except Exception:
        pass
    # 最近一根 open + 20日量比
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        code = _ts_code(symbol)
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT open, close, vol FROM mkt_bars_daily WHERE ts_code=:c AND trade_date<:d "
                "ORDER BY trade_date DESC LIMIT 21"), {"c": code, "d": _dt.datetime.now().strftime("%Y%m%d")}).fetchall()
        finally:
            db.close()
        if rows:
            ctx.last_open = float(rows[0][0] or 0) or None
            ctx.last_close = float(rows[0][1] or 0) or ctx.last_close
            vols = [float(r[2] or 0) for r in rows[1:] if float(r[2] or 0) > 0]
            cur_v = float(rows[0][2] or 0)
            if vols and cur_v > 0:
                ctx.vol_ratio = cur_v / (sum(vols) / len(vols))
    except Exception:
        pass
    # B8 假跌破/回踩不破
    try:
        import sys as _sys
        _p = os.path.join(os.environ.get("WORKSPACE_PATH", "/app"), "apps", "main_line")
        if not os.path.isdir(_p):
            _p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "apps", "main_line"))
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
        import breakout_context as _bc
        ctx.b8_state = (_bc.b8_state(symbol, _bc.prev_trade_day()) or {}).get("state", "")
    except Exception:
        pass
    # 指数大级别
    try:
        from app.services import wave_store as _ws
        w = _ws.get_wave() or {}
        op = str(w.get("operation") or "").lower()
        lvl = str(w.get("level") or "").lower()
        ctx.index_break = bool(op in ("defense", "exit") or lvl == "down")
    except Exception:
        pass
    # 重复
    try:
        from app.services import t_gateway as _tg
        ctx.sells_today = int(_tg._count_today_trades(account, symbol, "sell") or 0)
    except Exception:
        ctx.sells_today = 0
    try:
        from app.services import wolf_v3_production as _vp
        ctx.v3_today = bool(_vp.pg_symbol_action_today(symbol, _dt.datetime.now().strftime("%Y%m%d")))
    except Exception:
        ctx.v3_today = False
    # exact action key（本地 JSONL，防同一价格/同一意图重复）
    try:
        key = "%s|%s|%s|%.2f" % (_dt.datetime.now().strftime("%Y%m%d"), symbol, intent, float(price or 0))
        p = os.path.join(os.environ.get("WORKSPACE_PATH", "/app"), "data", "sell_gate_actions.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if json.loads(line).get("key") == key:
                        ctx.action_key_dup = True
                        break
        ctx.extras["action_key"] = key
    except Exception:
        pass
    return ctx


def sell_gate(symbol: str, price: float, volume: int, intent: Optional[str] = None,
              reason: str = "", account: str = "stock", source: str = "trades_api") -> Dict[str, Any]:
    mode = str(os.getenv("WOLF_SELL_GATE", "enforce")).strip().lower()
    it = classify_intent(intent, reason)
    if mode in ("0", "off", "false", "no", ""):
        return {"allow": True, "hard_block": False, "intent": it, "reasons": ["sell_gate off"],
                "mode": mode}
    try:
        ctx = _load_ctx(symbol, price, volume, it, reason, account)
        allow, reasons, it2 = validate_sell(ctx)
    except Exception as e:
        allow, reasons, it2 = True, ["sell_gate 计算失败(fail-open): " + str(e)[:120]], it
    out = {"allow": bool(allow) or mode == "shadow",
           "hard_block": (not allow) and mode != "shadow",
           "intent": it2, "reasons": reasons, "mode": mode, "source": source,
           "sells_today": getattr(locals().get("ctx", None), "sells_today", None),
           "v3_today": getattr(locals().get("ctx", None), "v3_today", None),
           "b8_state": getattr(locals().get("ctx", None), "b8_state", None)}
    _log({**out, "symbol": symbol, "price": price, "volume": volume, "account": account,
          "at": _dt.datetime.now().isoformat(timespec="seconds")})
    if out["allow"] and allow and getattr(locals().get("ctx", None), "extras", {}).get("action_key"):
        try:
            p = os.path.join(os.environ.get("WORKSPACE_PATH", "/app"), "data", "sell_gate_actions.jsonl")
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": ctx.extras["action_key"], "at": _dt.datetime.now().isoformat()},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass
    return out
