# -*- coding: utf-8 -*-
"""个股离场规则（狼大语料口径）· 底仓锚/趋势约束的亏损豁免。

语料依据：
  - docs/wolf-playbook.md 纪律「破 5 日线丢」
  - docs/wolf-daily-log-xls2026.md「第一条铁律：不把挣大钱的票拿到亏本」
  - 止损语料：指数破位才止损 / 急跌不割肉 / 地量不割（故本模块**只**在破 5 日线时放行，
    不做固定百分比硬止损）

两条规则（都是"放行卖出"，不改买入侧）：
  ① WOLF_MA5_EXIT：浮亏 且 **收盘跌破 MA5** ⇒   允许卖底仓（底仓锚归零 + 趋势约束豁免）
  ② WOLF_BASE_FLOOR_LOSS_EXEMPT：浮亏 ≤ 阈值（默认 −3%）⇒ 底仓锚归零（不受底仓保护）

实测背景（data/_bt_q1 三个月回放，2026-01-05→03-31）：
  快克智能 SH603203 1 月持有到 3 月，卖出被「底仓锚 + 趋势约束」反复挡住
  （custom_vwap_sell blocked 10,600 / 仅底仓无T仓可卖 3,701 / 趋势约束 925），
  3 月已实现 −4,279（占当月已实现亏损 64%）。指数破位（WOLF_STOP_BY_INDEX_BREAK）
  直到 03-20 才打开，而 3 月亏损集中在 03-02→03-19 ⇒ 该窗口内没有任何规则允许卖出。

口径与安全：
  - 库内默认 **关**（两个开关默认 "0"）；回测驱动 jobs/bt_prod_run.py setdefault 打开
  - 数据源：PG mkt_bars_daily（**按钉住的当日日期过滤 trade_date <= 当日** ⇒ 无未来函数）
    + 现价优先由调用方传入；未传入时用最近一根日线收盘
  - 任何异常 fail-open（返回"不放行"，即原行为），并在 _STATS 计数
"""
import os
from datetime import datetime

from sqlalchemy import text

from app.database import SessionLocal

MA5_EXIT_ON = str(os.getenv("WOLF_MA5_EXIT", "0")).strip().lower() in ("1", "true", "yes", "on")
FLOOR_LOSS_ON = str(os.getenv("WOLF_BASE_FLOOR_LOSS_EXEMPT", "0")).strip().lower() in ("1", "true", "yes", "on")
# 浮亏阈值（≤）：破 5 日线丢按语料无阈值（0 即"只要浮亏"）；底仓锚豁免默认 −3%
MA5_EXIT_LOSS_PCT = float(os.getenv("WOLF_MA5_EXIT_LOSS_PCT", "0") or 0)
FLOOR_LOSS_PCT = float(os.getenv("WOLF_FLOOR_LOSS_PCT", "-3") or -3)
MA5_N = 5

_STATS = {"eval": 0, "ma5_hit": 0, "floor_hit": 0, "errors": 0}
_CLOSES = {}      # {(ts_code, day8): [close...]}  当日内缓存（回放=钉住日期，日切自然失效）
_SAMPLE = []


def _ts_code(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    return s


def _day8() -> str:
    return datetime.now().strftime("%Y%m%d")


def recent_closes(symbol: str, n: int = 6):
    """最近 n 根日线收盘（升序，末端=最近**已收盘**交易日）。严格过滤 trade_date < as-of 当日。"""
    code, d8 = _ts_code(symbol), _day8()
    key = (code, d8, n)
    if key in _CLOSES:
        return _CLOSES[key]
    out = []
    try:
        db = SessionLocal()
        try:
            rows = db.execute(text(
                # 严格 < 当日：钉住的 as-of 当天日线尚未收盘，取到就是未来函数
                "SELECT close FROM mkt_bars_daily WHERE ts_code=:c AND trade_date<:d "
                "ORDER BY trade_date DESC LIMIT :n"), {"c": code, "d": d8, "n": int(n)}).fetchall()
            out = [float(r[0]) for r in rows if r and r[0]]
            out.reverse()
        finally:
            db.close()
    except Exception as e:
        _STATS["errors"] += 1
        print("[wolf-exit] 日线取数失败 %s: %s" % (symbol, str(e)[:80]), flush=True)
    _CLOSES[key] = out
    return out


def avg_cost(account_id: str, symbol: str) -> float:
    try:
        db = SessionLocal()
        try:
            r = db.execute(text(
                "SELECT avg_price FROM paper_positions WHERE account_id=:a AND symbol=:s"),
                {"a": account_id, "s": str(symbol)}).fetchone()
            return float((r[0] if r else 0) or 0)
        finally:
            db.close()
    except Exception:
        _STATS["errors"] += 1
        return 0.0


def evaluate(account_id: str, symbol: str, cur_price: float = 0.0) -> dict:
    """→ {'pnl': %, 'px': 现价, 'ma5': MA5, 'ma5_break': bool, 'on': bool}"""
    info = {"pnl": 0.0, "px": 0.0, "ma5": 0.0, "ma5_break": False, "on": bool(MA5_EXIT_ON or FLOOR_LOSS_ON)}
    if not info["on"]:
        return info
    try:
        _STATS["eval"] += 1
        closes = recent_closes(symbol, MA5_N + 1)
        px = float(cur_price or 0) or (closes[-1] if closes else 0.0)
        ma5 = (sum(closes[-MA5_N:]) / float(MA5_N)) if len(closes) >= MA5_N else 0.0
        cost = avg_cost(account_id, symbol)
        info.update(px=px, ma5=ma5,
                    pnl=((px / cost - 1) * 100) if (cost > 0 and px > 0) else 0.0,
                    ma5_break=bool(ma5 > 0 and px > 0 and px < ma5))
    except Exception as e:
        _STATS["errors"] += 1
        print("[wolf-exit] 评估失败 %s: %s" % (symbol, str(e)[:80]), flush=True)
    return info


def _note(symbol, info, reason):
    if len(_SAMPLE) < 40:
        _SAMPLE.append("%s pnl=%.2f%% px=%.3f ma5=%.3f %s" % (symbol, info.get("pnl", 0), info.get("px", 0), info.get("ma5", 0), reason))
    print("[wolf-exit] %s：%s（浮亏 %.2f%%，收盘 %.3f / MA5 %.3f）"
          % (symbol, reason, info.get("pnl", 0), info.get("px", 0), info.get("ma5", 0)), flush=True)


def floor_exempt(account_id: str, symbol: str, cur_price: float = 0.0) -> bool:
    """底仓锚是否失效（→ floor 归零）。命中 ① 破5日线丢 或 ② 浮亏超阈值 之一即 True。"""
    if not (MA5_EXIT_ON or FLOOR_LOSS_ON):
        return False
    info = evaluate(account_id, symbol, cur_price)
    if info.get("pnl", 0) >= 0:
        return False                                    # 盈利票：底仓锚照旧（保护"挣大钱的票"）
    if MA5_EXIT_ON and info.get("ma5_break") and info["pnl"] <= MA5_EXIT_LOSS_PCT:
        _STATS["ma5_hit"] += 1
        _note(symbol, info, "破5日线丢⇒底仓锚失效(允许卖底仓)")
        return True
    if FLOOR_LOSS_ON and info["pnl"] <= FLOOR_LOSS_PCT:
        _STATS["floor_hit"] += 1
        _note(symbol, info, "浮亏超阈值⇒底仓锚失效")
        return True
    return False


def ma5_break_exempt(symbol: str, cur_price: float, avg_cost_price: float = 0.0) -> bool:
    """趋势约束豁免（MA5>MA20 时本会阻止卖出）：浮亏 且 收盘跌破 MA5 ⇒ 放行。"""
    if not MA5_EXIT_ON:
        return False
    try:
        closes = recent_closes(symbol, MA5_N + 1)
        if len(closes) < MA5_N or not cur_price:
            return False
        ma5 = sum(closes[-MA5_N:]) / float(MA5_N)
        if not (cur_price < ma5):
            return False
        if avg_cost_price and avg_cost_price > 0 and (cur_price / avg_cost_price - 1) * 100 > MA5_EXIT_LOSS_PCT:
            return False
        _STATS["ma5_hit"] += 1
        print("[wolf-exit] %s：破5日线丢⇒趋势约束豁免（收盘 %.3f < MA5 %.3f）" % (symbol, cur_price, ma5), flush=True)
        return True
    except Exception:
        _STATS["errors"] += 1
        return False


def stats() -> dict:
    return dict(_STATS, sample=list(_SAMPLE), on=bool(MA5_EXIT_ON or FLOOR_LOSS_ON))
